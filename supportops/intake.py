"""Business request types, staff routing, public forms and scoped inbound API tokens."""

import hashlib
import hmac
import secrets
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from supportops.auth import Identity, audit, digest, identifier
from supportops.billing import consume
from supportops.db import (
    Intake,
    Membership,
    Organization,
    RequestContext,
    RequestType,
    Ticket,
    utcnow,
)
from supportops.schemas import TicketInput


class TypeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(default="", max_length=1000)
    instructions: str = Field(default="", max_length=3000)
    visibility: str = Field(default="internal", pattern="^(internal|public)$")
    active: bool = True


class IntakeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=100)
    kind: str = Field(pattern="^(form|api)$")
    request_type_id: str | None = None


class RoutingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_type_id: str | None = None
    assigned_to: str | None = None
    revision: int = Field(ge=0)


class Incoming(TicketInput):
    external_id: str | None = Field(default=None, max_length=160)
    website: str = Field(default="", max_length=200)
    captcha_token: str = Field(default="", max_length=4096)


def type_dict(t):
    return {
        k: getattr(t, k)
        for k in ("id", "name", "description", "instructions", "visibility", "active")
    }


def validate_routing(db, org, type_id=None, assigned=None):
    if type_id:
        t = db.get(RequestType, (org, type_id))
        if not t or not t.active:
            raise HTTPException(
                422, "Choose an active request type from this workspace."
            )
    if assigned:
        member = db.get(Membership, (org, assigned))
        if not member or not member.active:
            raise HTTPException(422, "Choose an active member from this workspace.")


def create_context(
    db, org, ticket_id, type_id=None, assigned=None, intake_id=None, external_id=None
):
    validate_routing(db, org, type_id, assigned)
    context = RequestContext(
        organization_id=org,
        ticket_id=ticket_id,
        request_type_id=type_id,
        assigned_to=assigned,
        intake_id=intake_id,
        external_id=external_id,
    )
    db.add(context)
    return context


def context_dict(t):
    c = t.context
    return {
        "request_type_id": c.request_type_id if c else None,
        "assigned_to": c.assigned_to if c else None,
        "intake_id": c.intake_id if c else None,
        "external_id": c.external_id if c else None,
    }


def intake_router(auth, sessions, config, web_path):
    router = APIRouter()

    @router.get("/api/directory")
    def directory(actor: Identity = Depends(auth.current)):
        from supportops.db import User

        with sessions() as db:
            return [
                {"id": u.id, "name": u.name, "role": m.role}
                for u, m in db.execute(
                    select(User, Membership)
                    .join(Membership, Membership.user_id == User.id)
                    .where(
                        Membership.organization_id == actor.organization_id,
                        Membership.active.is_(True),
                    )
                ).all()
            ]

    @router.get("/api/request-types")
    def types(actor: Identity = Depends(auth.current)):
        with sessions() as db:
            return [
                type_dict(t)
                for t in db.scalars(
                    select(RequestType)
                    .where(RequestType.organization_id == actor.organization_id)
                    .order_by(RequestType.created_at)
                ).all()
            ]

    @router.post("/api/request-types", status_code=201)
    def add_type(body: TypeInput, actor: Identity = Depends(auth.admin)):
        with sessions() as db:
            row = RequestType(
                organization_id=actor.organization_id,
                id=identifier(),
                **body.model_dump(),
            )
            db.add(row)
            db.flush()
            audit(db, actor, "request_type.created", {"id": row.id})
            db.commit()
            return type_dict(row)

    @router.patch("/api/request-types/{id}")
    def edit_type(id: str, body: TypeInput, actor: Identity = Depends(auth.admin)):
        with sessions() as db:
            row = db.get(RequestType, (actor.organization_id, id))
            if not row:
                raise HTTPException(404, "Request type not found.")
            for k, v in body.model_dump().items():
                setattr(row, k, v)
            audit(db, actor, "request_type.updated", {"id": id})
            db.commit()
            return type_dict(row)

    @router.patch("/api/tickets/{id}/routing")
    def route(id: str, body: RoutingInput, actor: Identity = Depends(auth.current)):
        with sessions() as db:
            t = db.get(Ticket, (actor.organization_id, id))
            if not t:
                raise HTTPException(404, "Request not found.")
            if t.status == "processing":
                raise HTTPException(
                    409, "Wait for the current analysis before changing routing."
                )
            if actor.role == "agent" and body.assigned_to not in (None, actor.user_id):
                raise HTTPException(
                    403,
                    "Agents can assign a request to themselves. An administrator manages other assignments.",
                )
            validate_routing(
                db, actor.organization_id, body.request_type_id, body.assigned_to
            )
            if not db.execute(
                update(Ticket)
                .where(
                    Ticket.organization_id == actor.organization_id,
                    Ticket.id == id,
                    Ticket.revision == body.revision,
                )
                .values(revision=Ticket.revision + 1, updated_at=utcnow())
            ).rowcount:
                raise HTTPException(409, "revision_conflict")
            c = db.get(RequestContext, (actor.organization_id, id)) or create_context(
                db, actor.organization_id, id
            )
            c.request_type_id = body.request_type_id
            c.assigned_to = body.assigned_to
            audit(
                db,
                actor,
                "request.routed",
                {
                    "id": id,
                    "assigned_to": body.assigned_to,
                    "request_type_id": body.request_type_id,
                },
            )
            db.commit()
            return {"ok": True}

    def connection_dict(c):
        return {
            "id": c.id,
            "name": c.name,
            "kind": c.kind,
            "slug": c.slug,
            "request_type_id": c.request_type_id,
            "active": c.active,
            "url": config.public_base_url.rstrip("/")
            + ("/forms/" if c.kind == "form" else "/api/inbound/")
            + c.slug,
            "created_at": c.created_at.isoformat(),
        }

    @router.get("/api/intakes")
    def connections(actor: Identity = Depends(auth.admin)):
        with sessions() as db:
            return [
                connection_dict(c)
                for c in db.scalars(
                    select(Intake)
                    .where(Intake.organization_id == actor.organization_id)
                    .order_by(Intake.created_at)
                ).all()
            ]

    @router.post("/api/intakes", status_code=201)
    def connect(body: IntakeInput, actor: Identity = Depends(auth.admin)):
        auth.ensure_verified(actor)
        if actor.organization["is_demo"]:
            raise HTTPException(
                403, "Register your business before connecting a real request source."
            )
        if body.kind == "form" and (
            not config.public_intake_enabled
            or (
                config.app_env == "production"
                and not (config.turnstile_site_key and config.turnstile_secret_key)
            )
        ):
            raise HTTPException(
                503,
                "Public forms require the operator to enable intake and configure spam protection.",
            )
        with sessions() as db:
            validate_routing(db, actor.organization_id, body.request_type_id)
            t = (
                db.get(RequestType, (actor.organization_id, body.request_type_id))
                if body.request_type_id
                else None
            )
            if body.kind == "form" and t and t.visibility != "public":
                raise HTTPException(
                    422,
                    "Public forms can use only a public request type. Mark it public explicitly first.",
                )
            token = secrets.token_urlsafe(32) if body.kind == "api" else None
            c = Intake(
                organization_id=actor.organization_id,
                id=identifier(),
                name=body.name,
                kind=body.kind,
                slug=secrets.token_urlsafe(18),
                request_type_id=body.request_type_id,
                secret_hash=digest(token) if token else None,
            )
            db.add(c)
            db.flush()
            audit(db, actor, "intake.created", {"id": c.id, "kind": c.kind})
            db.commit()
            return {**connection_dict(c), "token": token}

    @router.post("/api/intakes/{id}/revoke")
    def revoke(id: str, actor: Identity = Depends(auth.admin)):
        with sessions() as db:
            c = db.get(Intake, (actor.organization_id, id))
            if not c:
                raise HTTPException(404, "Connection not found.")
            c.active = False
            audit(db, actor, "intake.revoked", {"id": id})
            db.commit()
            return {"ok": True}

    @router.get("/forms/{slug}")
    def form_page(slug: str):
        return FileResponse(web_path / "intake.html")

    @router.get("/api/forms/{slug}")
    def form_config(slug: str):
        if not config.public_intake_enabled:
            raise HTTPException(404, "Form unavailable.")
        with sessions() as db:
            c = db.scalar(
                select(Intake).where(
                    Intake.slug == slug, Intake.kind == "form", Intake.active.is_(True)
                )
            )
            if not c:
                raise HTTPException(404, "This form is no longer available.")
            org = db.get(Organization, c.organization_id)
            t = (
                db.get(RequestType, (org.id, c.request_type_id))
                if c.request_type_id
                else None
            )
            if t and (not t.active or t.visibility != "public"):
                raise HTTPException(404, "This form is no longer available.")
            return {
                "business_name": org.name,
                "title": c.name,
                "request_type": t.name if t else "General request",
                "captcha_site_key": config.turnstile_site_key,
            }

    @router.post("/api/forms/{slug}", status_code=202)
    def receive_form(slug: str, body: Incoming, request: Request):
        return receive(slug, body, request, "form")

    @router.post("/api/inbound/{slug}", status_code=202)
    def receive_api(slug: str, body: Incoming, request: Request):
        return receive(slug, body, request, "api")

    def receive(slug, body, request, kind):
        if kind == "form" and not config.public_intake_enabled:
            raise HTTPException(404, "Form unavailable.")
        if body.website:
            raise HTTPException(422, "Unable to submit this request.")
        with sessions() as db:
            c = db.scalar(
                select(Intake).where(
                    Intake.slug == slug, Intake.kind == kind, Intake.active.is_(True)
                )
            )
            if not c:
                raise HTTPException(404, "Connection unavailable.")
            if kind == "api":
                raw = request.headers.get("Authorization", "")
                if not raw.startswith("Bearer ") or not hmac.compare_digest(
                    digest(raw[7:]), c.secret_hash or ""
                ):
                    raise HTTPException(401, "Invalid inbound API credential.")
            auth.throttle(
                request, "intake-" + kind, c.id, limit=10 if kind == "form" else 300
            )
            if kind == "form" and (
                config.turnstile_secret_key or config.app_env == "production"
            ):
                from supportops.captcha import verify

                verify(
                    config,
                    body.captcha_token,
                    request,
                    mandatory=config.app_env == "production",
                )
            t = (
                db.get(RequestType, (c.organization_id, c.request_type_id))
                if c.request_type_id
                else None
            )
            if t and (not t.active or (kind == "form" and t.visibility != "public")):
                raise HTTPException(404, "Request type unavailable.")
            if body.assigned_to or body.template_id or body.request_type_id:
                raise HTTPException(
                    422,
                    "Inbound routing is configured by the workspace, not by the sender.",
                )
            key = (
                hashlib.sha256((c.id + ":" + body.external_id).encode()).hexdigest()
                if body.external_id
                else None
            )
            values = body.model_dump(
                exclude={
                    "request_type_id",
                    "assigned_to",
                    "external_id",
                    "website",
                    "captcha_token",
                }
            )
            values["channel"] = "web" if kind == "form" else "api"
            if key:
                old = db.scalar(
                    select(Ticket).where(
                        Ticket.organization_id == c.organization_id,
                        Ticket.idempotency_key == key,
                    )
                )
                if old:
                    if any(getattr(old, k) != v for k, v in values.items()):
                        raise HTTPException(
                            409,
                            "The external request ID was already used with different content.",
                        )
                    return {"received": True, "reference": old.id, "duplicate": True}
            consume(db, c.organization_id, "requests")
            item = Ticket(
                organization_id=c.organization_id,
                id="REQ-" + uuid.uuid4().hex[:10].upper(),
                idempotency_key=key,
                **values,
            )
            db.add(item)
            db.flush()
            create_context(
                db,
                c.organization_id,
                item.id,
                c.request_type_id,
                intake_id=c.id,
                external_id=body.external_id,
            )
            db.commit()
            return {
                "received": True,
                "reference": item.id,
                "message": "Your request has been received. The team will review it.",
            }

    return router
