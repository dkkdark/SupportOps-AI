"""Owner launch checks, workspace exports and transparent operator policy drafts."""

import html
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from supportops.auth import Identity
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
        return {
            "checks": [
                {
                    "name": "OpenAI models",
                    "ready": bool(config.openai_api_key or config.llm_api_key),
                    "action": "Set OPENAI_API_KEY and verify your model access.",
                },
                {
                    "name": "Public HTTPS address",
                    "ready": config.public_base_url.startswith("https://"),
                    "action": "Set PUBLIC_BASE_URL to your certificate-matching domain.",
                },
                {
                    "name": "Database",
                    "ready": config.database_url.startswith("postgresql"),
                    "action": "Use the private PostgreSQL/pgvector deployment and backup both app and checkpoint tables.",
                },
                {
                    "name": "Email verification and recovery",
                    "ready": bool(config.smtp_host and config.smtp_from),
                    "action": "Configure SMTP and test verification/password recovery with your own mailbox.",
                },
                {
                    "name": "Paid subscriptions",
                    "ready": bool(
                        config.stripe_secret_key
                        and config.stripe_webhook_secret
                        and config.stripe_starter_price_id
                        and config.stripe_growth_price_id
                        and config.stripe_scale_price_id
                    ),
                    "action": "Configure Stripe products, prices, webhook signing secret and Customer Portal.",
                },
                {
                    "name": "Public-form spam protection",
                    "ready": bool(
                        config.turnstile_site_key and config.turnstile_secret_key
                    ),
                    "action": "Configure Turnstile keys for your public domain.",
                },
                {
                    "name": "Operator and policies",
                    "ready": legal_ready(config),
                    "action": "Complete the operator details, publish your reviewed terms/privacy/refund policies, then set LEGAL_DOCUMENTS_APPROVED=true.",
                },
            ],
            "note": "Configuration checks do not prove cloud deployment, valid credentials, email delivery, legal suitability or successful payment processing. Complete the launch acceptance steps.",
        }

    @router.get("/api/workspace/export")
    def export(actor: Identity = Depends(auth.admin)):
        if actor.role != "owner":
            raise HTTPException(
                403, "Only the workspace owner can export the business data."
            )
        with sessions() as db:
            data = {"business": actor.organization, "schema_version": "003"}
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
        if legal_ready(config):
            return RedirectResponse(
                config.legal_privacy_url
                if kind == "privacy"
                else config.legal_terms_url,
                status_code=307,
            )
        operator = config.legal_business_name or "[Service operator — to be completed]"
        contact = config.legal_contact_email or "[Contact email — to be completed]"
        title = "Privacy notice" if kind == "privacy" else "Service terms"
        paragraphs = (
            [
                ("Who operates this service", operator + ". Contact: " + contact + "."),
                (
                    "Information processed",
                    "Business account and membership information, incoming request content, knowledge documents, templates, workflow results, audit history and usage/billing identifiers are stored to operate the workspace.",
                ),
                (
                    "AI processing",
                    "When AI is used, relevant request text, knowledge excerpts and templates are sent to OpenAI for embeddings, investigation and verification. The application requests store:false for Responses calls. This setting does not by itself define the provider’s complete data-retention policy.",
                ),
                (
                    "Payments and email",
                    "Stripe handles hosted checkout and subscription management; this application does not receive full payment-card data. A configured email provider delivers verification and recovery messages. Public forms can use Cloudflare Turnstile for spam protection.",
                ),
                (
                    "Business control and requests",
                    "The workspace owner controls team access and can export workspace records. Contact the operator for access, correction or deletion requests. The operator must publish retention periods, subprocessors, hosting regions, lawful bases and any applicable international-transfer arrangements before launch.",
                ),
            ]
            if kind == "privacy"
            else [
                (
                    "Service and accounts",
                    "SupportOps provides business request intake, knowledge retrieval, AI proposals, routing and human review. Workspace owners are responsible for authorized team access and the information they submit.",
                ),
                (
                    "Plans and AI credits",
                    "Subscriptions apply per workspace. Credits cover model use including indexing and verification; use varies with token volume and agent work. A temporary reservation is reconciled after processing. Unknown provider charges may require reconciliation. Trial credits are one-time; paid credits renew with the paid period, without rollover or automatic overage charges.",
                ),
                (
                    "Human decisions",
                    "AI outputs can be incomplete or incorrect. An authorized person must review results before relying on them or taking external actions. The service does not automatically send replies, issue payments, approve sensitive decisions or change external business records.",
                ),
                (
                    "Subscriptions and cancellation",
                    "Owners use the configured Stripe Customer Portal to manage a subscription. The operator must publish cancellation timing, refund rules, support commitments, taxes and applicable mandatory rights before accepting paid customers.",
                ),
                (
                    "Operator details and legal terms",
                    operator
                    + ". Contact: "
                    + contact
                    + ". The operator must complete governing law, dispute handling, acceptable use, liability and business-specific terms with appropriate legal review.",
                ),
            ]
        )
        content = "".join(
            "<section><h2>"
            + html.escape(h)
            + "</h2><p>"
            + html.escape(p)
            + "</p></section>"
            for h, p in paragraphs
        )
        draft = (
            ""
            if config.legal_documents_approved
            and config.legal_business_name
            and config.legal_contact_email
            else '<div class="notice-card">Draft for operator review. Business details and jurisdiction-specific policies must be completed before public paid launch.</div>'
        )
        return HTMLResponse(
            '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>'
            + title
            + ' · SupportOps</title><link rel="stylesheet" href="/assets/style.css"></head><body><main class="policy-document"><a href="/">← SupportOps AI</a><h1>'
            + title
            + "</h1>"
            + draft
            + content
            + "</main></body></html>"
        )

    @router.get("/privacy")
    def privacy():
        return policy("privacy")

    @router.get("/terms")
    def terms():
        return policy("terms")

    return router
