"""Database-backed sessions, workspace membership, CSRF and role checks."""

from typing import Annotated
from dataclasses import dataclass
from datetime import timedelta, timezone
import hashlib
import hmac
import re
import secrets
import uuid
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
)
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from supportops.registration import lock as lock_registration, require_capacity
from supportops.db import (
    AuditEvent,
    AuthThrottle,
    Invitation,
    LoginSession,
    Membership,
    Organization,
    ReplyTemplate,
    User,
    utcnow,
)

COOKIE = "supportops_session"
PASSWORDS = PasswordHasher()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
DEFAULT_TEMPLATE = "Hi {{customer_name}},\n\n{{facts}}\n\nPlease let us know if you need any further help.\nBest regards,\n{{business_name}} Support"


def identifier():
    return uuid.uuid4().hex


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginInput(Input):
    email: str = Field(max_length=160, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        min_length=1, max_length=128
    )

    @field_validator("email")
    @classmethod
    def normalized(cls, value):
        return value.casefold()


class RegisterInput(LoginInput):
    captcha_token: str = Field(default="", max_length=4096)
    password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        min_length=12, max_length=128
    )
    name: str = Field(min_length=2, max_length=100)
    business_name: str = Field(min_length=2, max_length=120)
    industry: str = Field(default="General business", min_length=2, max_length=80)


class OrganizationInput(Input):
    name: str = Field(min_length=2, max_length=120)
    industry: str = Field(default="General business", min_length=2, max_length=80)


class SwitchInput(Input):
    organization_id: str = Field(max_length=40)


class InviteInput(Input):
    email: str = Field(max_length=160, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    role: str = Field(default="agent", pattern=r"^(admin|agent)$")

    @field_validator("email")
    @classmethod
    def normalized(cls, value):
        return value.casefold()


class AcceptInviteInput(LoginInput):
    token: str = Field(min_length=20, max_length=200)
    name: str = Field(min_length=2, max_length=100)
    password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(
        min_length=12, max_length=128
    )


class MemberInput(Input):
    role: str = Field(pattern=r"^(admin|agent)$")
    active: bool = True


@dataclass
class Identity:
    user_id: str
    name: str
    email: str
    organization_id: str | None
    role: str | None
    organization: dict | None
    csrf_token: str
    session_hash: str
    email_verified: bool = False


def org_dict(org, role=None):
    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "industry": org.industry,
        "workflow_mode": org.workflow_mode,
        "is_demo": org.is_demo,
        "role": role,
    }


def audit(db, actor, action, details=None, organization_id=None):
    db.add(
        AuditEvent(
            id=identifier(),
            organization_id=organization_id or actor.organization_id,
            user_id=actor.user_id,
            action=action,
            details=details or {},
        )
    )


def create_organization(db, name, industry, user, is_demo=False):
    id = identifier()
    slug = (
        (re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:65] or "workspace")
        + "-"
        + id[:8]
    )
    org = Organization(
        id=id,
        name=name,
        slug=slug,
        industry=industry,
        is_demo=is_demo,
        workflow_mode="retail_demo" if is_demo else "knowledge",
    )
    db.add(org)
    db.flush()
    db.add(Membership(organization_id=id, user_id=user.id, role="owner", active=True))
    db.add(
        ReplyTemplate(
            organization_id=id,
            id=identifier(),
            name="Friendly support reply",
            category="other",
            body=DEFAULT_TEMPLATE,
            active=True,
            revision=1,
        )
    )
    return org


class Auth:
    def __init__(self, config, sessions):
        self.config, self.sessions = config, sessions

    def throttle(self, request, kind, identity="", limit=20):
        key = digest(
            kind
            + ":"
            + (request.client.host if request.client else "unknown")
            + ":"
            + identity
        )
        now = utcnow()
        cutoff = now - timedelta(minutes=15)
        with self.sessions() as db:
            row = db.get(AuthThrottle, key)
            if row is None:
                db.add(AuthThrottle(key=key, attempts=1, window_start=now))
                try:
                    db.commit()
                    return
                except IntegrityError:
                    db.rollback()
            db.execute(
                update(AuthThrottle)
                .execution_options(synchronize_session=False)
                .where(AuthThrottle.key == key, AuthThrottle.window_start < cutoff)
                .values(attempts=0, window_start=now)
            )
            db.execute(
                update(AuthThrottle)
                .execution_options(synchronize_session=False)
                .where(AuthThrottle.key == key)
                .values(attempts=AuthThrottle.attempts + 1)
            )
            db.commit()
            db.expire_all()
            if db.get(AuthThrottle, key).attempts > limit:
                raise HTTPException(
                    429,
                    "Too many attempts. Try again in 15 minutes.",
                    headers={"Retry-After": "900"},
                )

    def identity(self, request: Request, require_workspace=True, csrf=True):
        token = request.cookies.get(COOKIE, "")
        if not token or len(token) > 200:
            raise HTTPException(401, "Sign in to continue.")
        with self.sessions() as db:
            session = db.get(LoginSession, digest(token))
            if not session or aware(session.expires_at) <= utcnow():
                raise HTTPException(401, "Your session has expired. Sign in again.")
            user = db.get(User, session.user_id)
            if not user:
                raise HTTPException(401, "Sign in to continue.")
            if csrf and request.method not in ("GET", "HEAD", "OPTIONS"):
                if not hmac.compare_digest(
                    request.headers.get("X-CSRF-Token", "").encode(),
                    session.csrf_token.encode(),
                ):
                    raise HTTPException(
                        403, "Invalid CSRF token. Refresh and try again."
                    )
            membership = (
                db.get(Membership, (session.organization_id, user.id))
                if session.organization_id
                else None
            )
            org = (
                db.get(Organization, session.organization_id)
                if membership and membership.active
                else None
            )
            if require_workspace and not org:
                raise HTTPException(
                    403, "Workspace access has been removed. Choose another workspace."
                )
            return Identity(
                user.id,
                user.name,
                user.email,
                org.id if org else None,
                membership.role if org else None,
                org_dict(org, membership.role) if org else None,
                session.csrf_token,
                session.token_hash,
                user.email_verified,
            )

    def ensure_verified(self, actor):
        if (
            self.config.app_env == "production"
            and self.config.require_verified_email
            and not actor.email_verified
        ):
            raise HTTPException(
                403,
                "Verify your email address before using AI or connecting external request sources.",
            )

    def current(self, request: Request):
        actor = self.identity(request)
        if request.headers.get("X-Workspace-ID") != actor.organization_id:
            raise HTTPException(409, "Workspace changed. Refresh before continuing.")
        return actor

    def user(self, request: Request):
        return self.identity(request, require_workspace=False)

    def admin(self, request: Request):
        actor = self.current(request)
        if actor.role not in ("owner", "admin"):
            raise HTTPException(403, "Only workspace owners and admins can do this.")
        return actor

    def issue(self, db, response, user, org_id, request):
        old = request.cookies.get(COOKIE)
        if old:
            previous = db.get(LoginSession, digest(old))
            if previous:
                db.delete(previous)
        token = secrets.token_urlsafe(32)
        session = LoginSession(
            token_hash=digest(token),
            user_id=user.id,
            organization_id=org_id,
            csrf_token=secrets.token_hex(32),
            expires_at=utcnow() + timedelta(days=self.config.session_days),
        )
        db.add(session)
        db.commit()
        response.set_cookie(
            COOKIE,
            token,
            max_age=self.config.session_days * 86400,
            httponly=True,
            secure=self.config.app_env == "production",
            samesite="lax",
            path="/",
        )
        return {
            "user": {"id": user.id, "name": user.name, "email": user.email},
            "csrf_token": session.csrf_token,
            "organization_id": org_id,
        }

    def router(self, seed_demo):
        router = APIRouter(prefix="/api")

        @router.post("/auth/register", status_code=201)
        def register(
            body: RegisterInput,
            request: Request,
            response: Response,
            tasks: BackgroundTasks,
        ):
            with self.sessions() as db:
                require_capacity(db, self.config.workspace_limit)
            self.throttle(request, "register", limit=10)
            if (
                self.config.app_env == "production"
                and self.config.require_signup_captcha
            ):
                from supportops.captcha import verify

                verify(self.config, body.captcha_token, request)
            with self.sessions() as db:
                lock_registration(db)
                if db.scalar(select(User).where(User.email == body.email)):
                    raise HTTPException(
                        409,
                        "An account with this email already exists. Sign in instead.",
                    )
                require_capacity(db, self.config.workspace_limit)
                user = User(
                    id=identifier(),
                    name=body.name,
                    email=body.email,
                    password_hash=PASSWORDS.hash(body.password),
                )
                db.add(user)
                db.flush()
                org = create_organization(db, body.business_name, body.industry, user)
                try:
                    from supportops.email_jobs import enqueue, process

                    email_job_id = enqueue(db, user.id, "verify")
                    result = self.issue(db, response, user, org.id, request)
                    if email_job_id:
                        tasks.add_task(
                            process, self.config, self.sessions, email_job_id
                        )
                    return result
                except IntegrityError:
                    db.rollback()
                    raise HTTPException(
                        409, "This account already exists. Sign in instead."
                    )

        @router.post("/auth/login")
        def login(body: LoginInput, request: Request, response: Response):
            self.throttle(request, "login-ip", "", limit=40)
            self.throttle(request, "login", body.email, limit=10)
            with self.sessions() as db:
                user = db.scalar(select(User).where(User.email == body.email))
                try:
                    PASSWORDS.verify(
                        user.password_hash if user else DUMMY_HASH, body.password
                    )
                except (VerifyMismatchError, VerificationError):
                    raise HTTPException(401, "Email or password is incorrect.")
                if not user:
                    raise HTTPException(401, "Email or password is incorrect.")
                if PASSWORDS.check_needs_rehash(user.password_hash):
                    user.password_hash = PASSWORDS.hash(body.password)
                membership = db.scalar(
                    select(Membership)
                    .where(Membership.user_id == user.id, Membership.active.is_(True))
                    .order_by(Membership.created_at)
                )
                return self.issue(
                    db,
                    response,
                    user,
                    membership.organization_id if membership else None,
                    request,
                )

        @router.post("/auth/demo", status_code=201)
        def demo(request: Request, response: Response):
            if (
                self.config.app_env == "production"
                and not self.config.allow_demo_signup
            ):
                raise HTTPException(404, "Demo sign-in is disabled.")
            self.throttle(request, "demo", limit=20)
            with self.sessions() as db:
                lock_registration(db)
                require_capacity(db, self.config.workspace_limit)
                user = User(
                    id=identifier(),
                    name="Alex Morgan",
                    email="demo-" + identifier() + "@example.test",
                    password_hash=PASSWORDS.hash(secrets.token_urlsafe(32)),
                )
                db.add(user)
                db.flush()
                org = create_organization(
                    db, "Northstar Goods", "Retail demo", user, True
                )
                seed_demo(db, org.id)
                return self.issue(db, response, user, org.id, request)

        @router.get("/auth/me")
        def me(actor: Identity = Depends(self.user)):
            with self.sessions() as db:
                memberships = db.execute(
                    select(Organization, Membership)
                    .join(Membership, Membership.organization_id == Organization.id)
                    .where(
                        Membership.user_id == actor.user_id, Membership.active.is_(True)
                    )
                    .order_by(Membership.created_at)
                ).all()
                return {
                    "user": {
                        "id": actor.user_id,
                        "name": actor.name,
                        "email": actor.email,
                        "email_verified": actor.email_verified,
                    },
                    "organization": actor.organization,
                    "memberships": [org_dict(o, m.role) for o, m in memberships],
                    "csrf_token": actor.csrf_token,
                }

        @router.post("/auth/logout")
        def logout(response: Response, actor: Identity = Depends(self.user)):
            with self.sessions() as db:
                session = db.get(LoginSession, actor.session_hash)
                if session:
                    db.delete(session)
                    db.commit()
            response.delete_cookie(COOKIE, path="/")
            return {"ok": True}

        @router.post("/auth/switch")
        def switch(body: SwitchInput, actor: Identity = Depends(self.user)):
            with self.sessions() as db:
                membership = db.get(Membership, (body.organization_id, actor.user_id))
                if not membership or not membership.active:
                    raise HTTPException(404, "Workspace not found.")
                session = db.get(LoginSession, actor.session_hash)
                session.organization_id = body.organization_id
                db.commit()
            return {"ok": True}

        @router.post("/organizations", status_code=201)
        def new_organization(
            body: OrganizationInput,
            request: Request,
            actor: Identity = Depends(self.user),
        ):
            if actor.organization and actor.organization.get("is_demo"):
                raise HTTPException(
                    403,
                    "Register a business account first. Sample accounts cannot own a real business.",
                )
            self.throttle(request, "new-workspace", actor.user_id, limit=10)
            with self.sessions() as db:
                lock_registration(db)
                require_capacity(db, self.config.workspace_limit)
                user = db.get(User, actor.user_id)
                org = create_organization(db, body.name, body.industry, user)
                db.get(LoginSession, actor.session_hash).organization_id = org.id
                db.commit()
                return org_dict(org, "owner")

        @router.patch("/organization")
        def edit_organization(
            body: OrganizationInput, actor: Identity = Depends(self.admin)
        ):
            with self.sessions() as db:
                org = db.get(Organization, actor.organization_id)
                org.name, org.industry = body.name, body.industry
                audit(db, actor, "workspace.updated", {"name": org.name})
                db.commit()
                return org_dict(org, actor.role)

        @router.get("/members")
        def members(actor: Identity = Depends(self.admin)):
            with self.sessions() as db:
                rows = db.execute(
                    select(User, Membership)
                    .join(Membership, Membership.user_id == User.id)
                    .where(Membership.organization_id == actor.organization_id)
                ).all()
                return [
                    {
                        "user_id": u.id,
                        "name": u.name,
                        "email": u.email,
                        "role": m.role,
                        "active": m.active,
                        "created_at": m.created_at.isoformat(),
                    }
                    for u, m in rows
                ]

        @router.patch("/members/{user_id}")
        def edit_member(
            user_id: str, body: MemberInput, actor: Identity = Depends(self.admin)
        ):
            with self.sessions() as db:
                member = db.get(Membership, (actor.organization_id, user_id))
                if not member:
                    raise HTTPException(404, "Member not found.")
                if member.role == "owner" or user_id == actor.user_id:
                    raise HTTPException(
                        403,
                        "Owner access and your own membership cannot be changed here.",
                    )
                if actor.role != "owner" and (
                    member.role == "admin" or body.role == "admin"
                ):
                    raise HTTPException(
                        403, "Only the owner can manage administrators."
                    )
                if body.active and not member.active:
                    from supportops.billing import seat_available

                    seat_available(
                        db,
                        actor.organization_id,
                        db.get(User, user_id).email,
                        config=self.config,
                    )
                member.role, member.active = body.role, body.active
                audit(
                    db,
                    actor,
                    "member.updated",
                    {"user_id": user_id, "role": body.role, "active": body.active},
                )
                db.commit()
            return {"ok": True}

        @router.get("/invitations")
        def invitations(actor: Identity = Depends(self.admin)):
            with self.sessions() as db:
                rows = db.scalars(
                    select(Invitation)
                    .where(Invitation.organization_id == actor.organization_id)
                    .order_by(Invitation.expires_at.desc())
                ).all()
                return [
                    {
                        "id": i.id,
                        "email": i.email,
                        "role": i.role,
                        "expires_at": i.expires_at.isoformat(),
                        "accepted": i.accepted,
                        "revoked": i.revoked,
                    }
                    for i in rows
                ]

        @router.post("/invitations", status_code=201)
        def invite(body: InviteInput, actor: Identity = Depends(self.admin)):
            if body.role == "admin" and actor.role != "owner":
                raise HTTPException(403, "Only the owner can invite administrators.")
            with self.sessions() as db:
                from supportops.billing import seat_available

                seat_available(
                    db, actor.organization_id, body.email, config=self.config
                )
                member = db.scalar(
                    select(Membership)
                    .join(User, User.id == Membership.user_id)
                    .where(
                        Membership.organization_id == actor.organization_id,
                        Membership.active.is_(True),
                        User.email == body.email,
                    )
                )
                if member:
                    raise HTTPException(409, "This person is already a member.")
                for old in db.scalars(
                    select(Invitation).where(
                        Invitation.organization_id == actor.organization_id,
                        Invitation.email == body.email,
                        Invitation.accepted.is_(False),
                    )
                ).all():
                    old.revoked = True
                token = secrets.token_urlsafe(32)
                invitation = Invitation(
                    id=identifier(),
                    organization_id=actor.organization_id,
                    email=body.email,
                    role=body.role,
                    token_hash=digest(token),
                    created_by=actor.user_id,
                    expires_at=utcnow() + timedelta(days=7),
                    accepted=False,
                    revoked=False,
                )
                db.add(invitation)
                audit(
                    db,
                    actor,
                    "member.invited",
                    {"email": body.email, "role": body.role},
                )
                db.commit()
                return {
                    "id": invitation.id,
                    "token": token,
                    "email": body.email,
                    "expires_at": invitation.expires_at.isoformat(),
                    "delivery": "copy_link",
                }

        @router.post("/invitations/{id}/revoke")
        def revoke(id: str, actor: Identity = Depends(self.admin)):
            with self.sessions() as db:
                invitation = db.scalar(
                    select(Invitation).where(
                        Invitation.id == id,
                        Invitation.organization_id == actor.organization_id,
                    )
                )
                if not invitation:
                    raise HTTPException(404, "Invitation not found.")
                invitation.revoked = True
                audit(db, actor, "invitation.revoked", {"invitation_id": id})
                db.commit()
            return {"ok": True}

        @router.post("/auth/accept-invite")
        def accept(body: AcceptInviteInput, request: Request, response: Response):
            self.throttle(request, "accept", limit=15)
            with self.sessions() as db:
                lock_registration(db)
                invitation = db.scalar(
                    select(Invitation).where(
                        Invitation.token_hash == digest(body.token)
                    )
                )
                if (
                    not invitation
                    or invitation.accepted
                    or invitation.revoked
                    or aware(invitation.expires_at) <= utcnow()
                ):
                    raise HTTPException(
                        410,
                        "This invitation is invalid or expired. Ask your administrator for a new link.",
                    )
                if invitation.email != body.email:
                    raise HTTPException(
                        403, "Use the email address the invitation was sent to."
                    )
                user = db.scalar(select(User).where(User.email == body.email))
                if user:
                    try:
                        PASSWORDS.verify(user.password_hash, body.password)
                    except (VerifyMismatchError, VerificationError):
                        raise HTTPException(
                            401, "Enter the password for your existing account."
                        )
                else:
                    user = User(
                        id=identifier(),
                        name=body.name,
                        email=body.email,
                        password_hash=PASSWORDS.hash(body.password),
                    )
                    db.add(user)
                    db.flush()
                from supportops.billing import seat_available

                seat_available(
                    db, invitation.organization_id, body.email, config=self.config
                )
                # Single-use claim is atomic, even for simultaneous acceptance requests.
                claimed = db.execute(
                    update(Invitation)
                    .where(
                        Invitation.id == invitation.id,
                        Invitation.accepted.is_(False),
                        Invitation.revoked.is_(False),
                    )
                    .values(accepted=True)
                ).rowcount
                if not claimed:
                    raise HTTPException(410, "This invitation has already been used.")
                member = db.get(Membership, (invitation.organization_id, user.id))
                if member:
                    member.active = True
                    member.role = invitation.role
                else:
                    db.add(
                        Membership(
                            organization_id=invitation.organization_id,
                            user_id=user.id,
                            role=invitation.role,
                            active=True,
                        )
                    )
                db.flush()
                actor = Identity(
                    user.id,
                    user.name,
                    user.email,
                    invitation.organization_id,
                    invitation.role,
                    None,
                    "",
                    "",
                )
                audit(
                    db, actor, "invitation.accepted", {"invitation_id": invitation.id}
                )
                return self.issue(
                    db, response, user, invitation.organization_id, request
                )

        return router
