"""Workspace service checks, exports and public policy pages."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from supportops.auth import Identity
from supportops.mailer import email_status
from supportops.db import (
    KnowledgeSource,
    RequestType,
    RequestContext,
    Ticket,
    Review,
    AuditEvent,
    Membership,
    User,
)


def legal_ready(config):
    return bool(
        config.legal_business_name
        and config.legal_contact_email
        and config.legal_documents_approved
        and config.legal_terms_url.startswith("https://")
        and config.legal_privacy_url.startswith("https://")
    )


def launch_router(auth, sessions, config):
    router = APIRouter()

    @router.get("/api/launch-status")
    def launch_status(actor: Identity = Depends(auth.admin)):
        mail = email_status(config)
        return {
            "checks": [
                {
                    "name": "OpenAI models",
                    "ready": bool(config.openai_api_key or config.llm_api_key),
                    "action": "AI investigation, knowledge embeddings and reply verification.",
                },
                {
                    "name": "Public HTTPS address",
                    "ready": config.public_base_url.startswith("https://"),
                    "action": "A secure address for sign-in, verification and recovery links.",
                },
                {
                    "name": "Database",
                    "ready": config.database_url.startswith(("postgresql", "sqlite")),
                    "action": "Persistent business records and workflow progress.",
                },
                {
                    "name": "Qdrant vector search",
                    "ready": bool(config.qdrant_url),
                    "action": "A separate knowledge index for each business's current documents.",
                },
                {
                    "name": "Email verification and recovery",
                    "ready": mail["ready"],
                    "action": "Email delivery is temporarily unavailable. Contact support."
                    if mail["missing"]
                    else "Account verification and password recovery emails.",
                },
                *(
                    []
                    if config.demo_release
                    else [
                        {
                            "name": "Paid subscriptions",
                            "ready": bool(
                                config.stripe_secret_key
                                and config.stripe_webhook_secret
                                and config.stripe_starter_price_id
                                and config.stripe_growth_price_id
                                and config.stripe_scale_price_id
                            ),
                            "action": "Purchasing and subscription management.",
                        },
                    ]
                ),
                {
                    "name": "Public-form spam protection",
                    "ready": bool(
                        config.turnstile_site_key and config.turnstile_secret_key
                    ),
                    "action": "Verification of registration and public-form requests.",
                },
                {
                    "name": "Operator and policies",
                    "ready": bool(
                        config.legal_business_name
                        and config.legal_country
                        and config.legal_contact_email
                    ),
                    "action": "Published service terms, privacy notice and support contact.",
                },
            ],
            "note": "Configuration status only; connection and delivery checks are separate.",
        }

    @router.get("/api/email/status")
    def mail_status(actor: Identity = Depends(auth.admin)):
        return email_status(config)

    @router.get("/api/workspace/export")
    def export(actor: Identity = Depends(auth.admin)):
        if actor.role != "owner":
            raise HTTPException(
                403, "Only the workspace owner can export the business data."
            )
        with sessions() as db:
            data = {"business": actor.organization, "schema_version": "006"}
            for cls, label in [
                (KnowledgeSource, "knowledge"),
                (RequestType, "request_types"),
                (RequestContext, "routing"),
                (Ticket, "requests"),
                (Review, "reviews"),
                (AuditEvent, "activity"),
            ]:
                rows = db.scalars(
                    select(cls).where(cls.organization_id == actor.organization_id)
                ).all()
                data[label] = [
                    {
                        column.name: getattr(row, column.name)
                        for column in cls.__table__.columns
                    }
                    for row in rows
                ]
            data["team"] = [
                {"name": u.name, "email": u.email, "role": m.role, "active": m.active}
                for u, m in db.execute(
                    select(User, Membership)
                    .join(Membership, Membership.user_id == User.id)
                    .where(Membership.organization_id == actor.organization_id)
                ).all()
            ]
            return JSONResponse(
                jsonable_encoder(data),
                headers={
                    "Content-Disposition": f'attachment; filename="workspace-{actor.organization_id}.json"'
                },
            )

    def policy(kind):
        from supportops.policies import render_policy

        target = (
            config.legal_privacy_url if kind == "privacy" else config.legal_terms_url
        )
        local_url = config.public_base_url.rstrip("/") + "/" + kind
        if legal_ready(config) and target.rstrip("/") != local_url:
            return RedirectResponse(target, status_code=307)
        return HTMLResponse(render_policy(config, kind))

    @router.get("/privacy")
    def privacy():
        return policy("privacy")

    @router.get("/terms")
    def terms():
        return policy("terms")

    return router
