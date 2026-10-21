"""Email verification and single-use account recovery. Delivery is configured by the operator."""

from datetime import timedelta
import secrets
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, update, delete
from supportops.auth import PASSWORDS, Identity, digest
from supportops.db import RecoveryToken, LoginSession, User, AccountEmailJob, utcnow
from supportops.mailer import deliver, email_status
from supportops.email_jobs import enqueue, process, cancel


def send_token(config, sessions, user_id, purpose):
    if purpose not in ("verify", "reset"):
        raise ValueError("Unknown account email purpose")
    if not email_status(config)["ready"]:
        return False
    token = secrets.token_urlsafe(32)
    with sessions() as db:
        user = db.get(User, user_id)
        if not user or (purpose == "verify" and user.email_verified):
            return False
        # Earlier links stay usable if a resend fails or SMTP delivery is ambiguous.
        # Redeeming one link revokes all other links for the same user/purpose.
        db.add(
            RecoveryToken(
                token_hash=digest(token),
                user_id=user.id,
                purpose=purpose,
                expires_at=utcnow()
                + timedelta(hours=24 if purpose == "verify" else 0.5),
                used=False,
            )
        )
        db.commit()
        email = user.email
    url = (
        config.public_base_url.rstrip("/")
        + ("/#verify/" if purpose == "verify" else "/#reset/")
        + token
    )
    return deliver(
        config,
        email,
        "Verify your SupportOps email"
        if purpose == "verify"
        else "Reset your SupportOps password",
        (
            "Confirm your email address to activate AI processing.\n\n"
            if purpose == "verify"
            else "Use this one-time link to choose a new password. It expires in 30 minutes.\n\n"
        )
        + url
        + "\n\nIf you did not request this, ignore this email.",
    )


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(max_length=160, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class TokenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=200)


class ResetInput(TokenInput):
    password: str = Field(min_length=12, max_length=128)


def accounts_router(auth, sessions, config):
    router = APIRouter(prefix="/api/auth")

    @router.post("/forgot-password", status_code=202)
    def forgot(body: EmailInput, request: Request, tasks: BackgroundTasks):
        auth.throttle(request, "recovery", body.email.casefold(), limit=3)
        auth.throttle(request, "recovery-ip", "", limit=10)
        with sessions() as db:
            user = db.scalar(select(User).where(User.email == body.email.casefold()))
            if user:
                job_id = enqueue(db, user.id, "reset")
                db.commit()
                if job_id:
                    tasks.add_task(process, config, sessions, job_id)
        return {
            "message": "If an account exists and email delivery is configured, a recovery link will be sent."
        }

    @router.post("/reset-password")
    def reset(body: ResetInput, request: Request):
        auth.throttle(request, "reset", "", limit=15)
        with sessions() as db:
            token = db.get(RecoveryToken, digest(body.token))
            if not token or token.purpose != "reset":
                raise HTTPException(410, "This recovery link is invalid or expired.")
            user = db.scalar(
                select(User).where(User.id == token.user_id).with_for_update()
            )
            claimed = db.execute(
                update(RecoveryToken)
                .execution_options(synchronize_session=False)
                .where(
                    RecoveryToken.token_hash == token.token_hash,
                    RecoveryToken.used.is_(False),
                    RecoveryToken.expires_at > utcnow(),
                )
                .values(used=True)
            ).rowcount
            if not claimed:
                raise HTTPException(410, "This recovery link is invalid or expired.")
            db.execute(
                update(RecoveryToken)
                .where(
                    RecoveryToken.user_id == user.id, RecoveryToken.purpose == "reset"
                )
                .values(used=True)
            )
            user.password_hash = PASSWORDS.hash(body.password)
            cancel(db, user.id, "reset")
            db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
            db.commit()
        return {"message": "Password updated. Sign in with your new password."}

    @router.post("/verification-email", status_code=202)
    def resend(
        request: Request, tasks: BackgroundTasks, actor: Identity = Depends(auth.user)
    ):
        auth.throttle(request, "verify-email", actor.user_id, limit=3)
        with sessions() as db:
            job_id = enqueue(db, actor.user_id, "verify")
            db.commit()
        if job_id:
            tasks.add_task(process, config, sessions, job_id)
        return {
            "message": "If email delivery is configured, a verification link will be sent."
        }

    @router.post("/verify-email")
    def verify(body: TokenInput, request: Request):
        auth.throttle(request, "verify", "", limit=20)
        with sessions() as db:
            token = db.get(RecoveryToken, digest(body.token))
            if not token or token.purpose != "verify":
                raise HTTPException(
                    410, "This verification link is invalid or expired."
                )
            user = db.scalar(
                select(User).where(User.id == token.user_id).with_for_update()
            )
            claimed = db.execute(
                update(RecoveryToken)
                .execution_options(synchronize_session=False)
                .where(
                    RecoveryToken.token_hash == token.token_hash,
                    RecoveryToken.used.is_(False),
                    RecoveryToken.expires_at > utcnow(),
                )
                .values(used=True)
            ).rowcount
            if not claimed:
                raise HTTPException(
                    410, "This verification link is invalid or expired."
                )
            db.execute(
                update(RecoveryToken)
                .where(
                    RecoveryToken.user_id == user.id, RecoveryToken.purpose == "verify"
                )
                .values(used=True)
            )
            user.email_verified = True
            cancel(db, user.id, "verify")
            db.commit()
        return {
            "message": "Email verified. You can use AI processing and connect request sources."
        }

    @router.get("/email-deliveries")
    def deliveries(actor: Identity = Depends(auth.user)):
        with sessions() as db:
            return [
                {
                    "id": j.id,
                    "purpose": j.purpose,
                    "status": j.status,
                    "attempts": j.attempts,
                    "error": j.last_error,
                    "created_at": j.created_at,
                    "sent_at": j.sent_at,
                }
                for j in db.scalars(
                    select(AccountEmailJob)
                    .where(AccountEmailJob.user_id == actor.user_id)
                    .order_by(AccountEmailJob.created_at.desc())
                    .limit(20)
                )
            ]

    return router
