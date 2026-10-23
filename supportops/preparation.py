"""Durable batch preparation using the same tenant, budget and verifier checks."""

from datetime import timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update, func
from supportops.auth import Auth, Identity, audit, identifier, org_dict
from supportops.billing import settle
from supportops.db import (
    GmailConnection,
    Membership,
    Organization,
    OutboundReply,
    PreparationJob,
    ReplyPolicy,
    Ticket,
    User,
    utcnow,
)
from supportops.delivery import automatic_safe, sender_ready
from supportops.registration import lock
from supportops.schemas import AnalyzeInput, ReviewInput
from supportops.tickets import analyze_ticket, review_ticket


class ModeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["human", "automatic"] = "human"


def policy_dict(db, config, actor):
    row = db.get(ReplyPolicy, actor.organization_id)
    return {
        "mode": row.mode if row else "human",
        "sender_ready": sender_ready(db, config, actor.organization_id),
        "can_configure": actor.role in {"owner", "admin"},
        "queued": db.scalar(
            select(func.count())
            .select_from(PreparationJob)
            .where(
                PreparationJob.organization_id == actor.organization_id,
                PreparationJob.status.in_(["pending", "processing"]),
            )
        ),
    }


def set_mode(db, config, actor, mode):
    if mode == "automatic" and not sender_ready(db, config, actor.organization_id):
        raise HTTPException(
            409, "Reconnect Gmail and enable permission to send replies first."
        )
    row = db.get(ReplyPolicy, actor.organization_id)
    if row is None:
        row = ReplyPolicy(
            organization_id=actor.organization_id, updated_by=actor.user_id
        )
        db.add(row)
    row.mode, row.updated_by, row.updated_at = mode, actor.user_id, utcnow()
    audit(db, actor, "replies.delivery_mode_changed", {"mode": mode})


def reply_router(auth, sessions, config):
    router = APIRouter(prefix="/api")

    @router.get("/reply-settings")
    def get_settings(actor: Identity = Depends(auth.current)):
        with sessions() as db:
            return policy_dict(db, config, actor)

    @router.put("/reply-settings")
    def settings(
        body: ModeInput, request: Request, actor: Identity = Depends(auth.admin)
    ):
        auth.ensure_verified(actor)
        auth.throttle(request, "reply-settings", actor.organization_id, limit=20)
        with sessions() as db:
            lock(db)
            set_mode(db, config, actor, body.mode)
            db.commit()
            return policy_dict(db, config, actor)

    @router.post("/tickets/prepare-all")
    def prepare_all(
        body: ModeInput, request: Request, actor: Identity = Depends(auth.current)
    ):
        auth.ensure_verified(actor)
        auth.throttle(request, "prepare-all", actor.organization_id, limit=10)
        with sessions() as db:
            lock(db)
            policy = db.get(ReplyPolicy, actor.organization_id)
            if body.mode == "automatic":
                if actor.role not in {"owner", "admin"} and (
                    not policy or policy.mode != "automatic"
                ):
                    raise HTTPException(
                        403, "An administrator must enable automatic delivery first."
                    )
                if not sender_ready(db, config, actor.organization_id):
                    raise HTTPException(
                        409, "Reconnect Gmail with permission to send replies."
                    )
            if actor.role in {"owner", "admin"}:
                set_mode(db, config, actor, body.mode)
            rows = db.execute(
                select(Ticket.id, Ticket.revision)
                .where(
                    Ticket.organization_id == actor.organization_id,
                    Ticket.status == "new",
                )
                .order_by(Ticket.created_at)
            ).all()
            queued = 0
            for ticket_id, revision in rows:
                if db.scalar(
                    select(PreparationJob.id).where(
                        PreparationJob.organization_id == actor.organization_id,
                        PreparationJob.ticket_id == ticket_id,
                    )
                ):
                    continue
                if not db.execute(
                    update(Ticket)
                    .where(
                        Ticket.organization_id == actor.organization_id,
                        Ticket.id == ticket_id,
                        Ticket.status == "new",
                        Ticket.revision == revision,
                    )
                    .values(status="queued", revision=revision + 1, updated_at=utcnow())
                ).rowcount:
                    continue
                db.add(
                    PreparationJob(
                        id=identifier(),
                        organization_id=actor.organization_id,
                        ticket_id=ticket_id,
                        actor_id=actor.user_id,
                        mode=body.mode,
                    )
                )
                queued += 1
            audit(
                db, actor, "replies.batch_queued", {"count": queued, "mode": body.mode}
            )
            db.commit()
            return {"queued": queued, "mode": body.mode}

    @router.post("/tickets/refresh-inbox")
    def refresh_inbox(request: Request, actor: Identity = Depends(auth.current)):
        auth.ensure_verified(actor)
        auth.throttle(request, "refresh-inbox", actor.organization_id, limit=30)
        with sessions() as db:
            row = db.get(GmailConnection, actor.organization_id)
            gmail_queued = bool(row and row.status == "active")
            if gmail_queued:
                row.next_sync_at = utcnow()
            db.commit()
        # API sources push to the service; they have no remote inbox to poll.
        return {"gmail_queued": gmail_queued, "api_requests_available": True}

    @router.post("/tickets/{ticket_id}/retry-delivery")
    def retry_delivery(ticket_id: str, actor: Identity = Depends(auth.current)):
        auth.ensure_verified(actor)
        with sessions() as db:
            lock(db)
            row = db.scalar(
                select(OutboundReply).where(
                    OutboundReply.organization_id == actor.organization_id,
                    OutboundReply.ticket_id == ticket_id,
                )
            )
            if not row or row.status != "failed":
                raise HTTPException(
                    409,
                    "Only a confirmed failed delivery can be retried. Check uncertain deliveries in Gmail Sent.",
                )
            if not sender_ready(db, config, actor.organization_id):
                raise HTTPException(409, "Reconnect Gmail with send permission first.")
            ticket = db.get(Ticket, (actor.organization_id, ticket_id))
            ticket.status, ticket.revision, ticket.updated_at = (
                "sending",
                ticket.revision + 1,
                utcnow(),
            )
            row.status, row.last_error, row.actor_id, row.mode = (
                "pending",
                None,
                actor.user_id,
                "human",
            )
            row.ticket_revision = ticket.revision
            audit(db, actor, "reply.retry_requested", {"ticket_id": ticket_id})
            db.commit()
        return {"queued": True}

    return router


def job_actor(db, job, config):
    user = db.get(User, job.actor_id)
    member = db.get(Membership, (job.organization_id, job.actor_id))
    org = db.get(Organization, job.organization_id)
    if not user or not member or not member.active or not org:
        return None
    if (
        config.app_env == "production"
        and config.require_verified_email
        and not user.email_verified
    ):
        return None
    return Identity(
        user.id,
        user.name,
        user.email,
        org.id,
        member.role,
        org_dict(org, member.role),
        "",
        "",
        user.email_verified,
    )


def preparation_one(config, sessions, graphs, job_id):
    lease, now = identifier(), utcnow()
    with sessions() as db:
        job = db.get(PreparationJob, job_id)
        if not job or job.status not in {"pending", "processing"}:
            return False
        if not db.execute(
            update(PreparationJob)
            .execution_options(synchronize_session=False)
            .where(
                PreparationJob.id == job_id,
                (PreparationJob.status == "pending")
                | (
                    (PreparationJob.status == "processing")
                    & (PreparationJob.lease_until < now)
                ),
            )
            .values(
                status="processing",
                lease_token=lease,
                lease_until=now
                + timedelta(seconds=max(300, config.agent_timeout_seconds + 120)),
                updated_at=now,
            )
        ).rowcount:
            return False
        db.commit()
        db.refresh(job)
        actor = job_actor(db, job, config)
        ticket = db.get(Ticket, (job.organization_id, job.ticket_id))
        reservation_id = job.reservation_id
        if not actor or (ticket and ticket.status == "processing"):
            if ticket and ticket.status in {"processing", "queued"}:
                ticket.status, ticket.revision = "manual", ticket.revision + 1
                ticket.updated_at = utcnow()
            job.status, job.last_error = (
                "failed",
                "preparation_interrupted" if actor else "workspace_access_changed",
            )
            job.lease_token, job.lease_until = None, None
            db.commit()
            settle(sessions, reservation_id, {}, uncertain=True)
            return True
    try:
        if ticket and ticket.status == "queued":
            analyze_ticket(
                config,
                sessions,
                graphs,
                Auth(config, sessions),
                ticket.id,
                AnalyzeInput(),
                actor,
                job=job,
            )
        elif ticket and ticket.result and reservation_id:
            settle(sessions, reservation_id, ticket.result.get("usage", {}))
        with sessions() as db:
            current = db.get(PreparationJob, job_id)
            if current.lease_token != lease or current.status != "processing":
                return False
            ticket = db.get(Ticket, (current.organization_id, current.ticket_id))
            policy = db.get(ReplyPolicy, current.organization_id)
            automatic = (
                current.mode == "automatic" and policy and policy.mode == "automatic"
            )
            should_send = bool(automatic and ticket and ticket.status == "review")
            if should_send and not automatic_safe(db, ticket):
                ticket.status, ticket.revision = "manual", ticket.revision + 1
                current.last_error = "agent_review_required"
                should_send = False
            db.commit()
        if should_send:
            review_ticket(
                config,
                sessions,
                graphs,
                ticket.id,
                ReviewInput(
                    decision="approve",
                    text=ticket.result["draft"],
                    revision=ticket.revision,
                    send=True,
                    note="Automatic delivery enabled by the workspace administrator; independent grounding verification passed.",
                ),
                actor,
                delivery_mode="automatic",
            )
        with sessions() as db:
            current = db.get(PreparationJob, job_id)
            if current.lease_token == lease:
                (
                    current.status,
                    current.lease_token,
                    current.lease_until,
                    current.updated_at,
                ) = "completed", None, None, utcnow()
                db.commit()
    except HTTPException as error:
        with sessions() as db:
            current = db.get(PreparationJob, job_id)
            if current.lease_token != lease:
                return False
            current.status, current.last_error = (
                "failed",
                "ai_allowance_reached"
                if error.status_code in {402, 429}
                else "preparation_needs_attention",
            )
            current.lease_token, current.lease_until, current.updated_at = (
                None,
                None,
                utcnow(),
            )
            ticket = db.get(Ticket, (current.organization_id, current.ticket_id))
            if ticket and ticket.status in {"queued", "processing", "review"}:
                ticket.status, ticket.revision, ticket.updated_at = (
                    "manual",
                    ticket.revision + 1,
                    utcnow(),
                )
            db.commit()
    return True


def preparation_pending(config, sessions, graphs, on_progress=lambda: None):
    with sessions() as db:
        ids = list(
            db.scalars(
                select(PreparationJob.id)
                .where(
                    (PreparationJob.status == "pending")
                    | (
                        (PreparationJob.status == "processing")
                        & (PreparationJob.lease_until < utcnow())
                    )
                )
                .order_by(PreparationJob.created_at)
                .limit(3)
            )
        )
    for job_id in ids:
        on_progress()
        preparation_one(config, sessions, graphs, job_id)
    return len(ids)
