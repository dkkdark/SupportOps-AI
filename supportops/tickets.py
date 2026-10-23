"""Shared request preparation and review for HTTP and durable workers."""

import logging
import time
import uuid
from datetime import timedelta, timezone
from fastapi import HTTPException
from langgraph.types import Command
from opentelemetry import trace
from sqlalchemy import update
from supportops.auth import audit, aware
from supportops.billing import reserve, settle
from supportops.db import CreditReservation, Review, Ticket, PreparationJob, utcnow
from supportops.intake import context_dict


def ticket_dict(ticket):
    fields = (
        "id",
        "customer",
        "email",
        "subject",
        "message",
        "order_id",
        "channel",
        "priority",
        "status",
        "category",
        "result",
        "revision",
        "template_id",
    )
    return {
        **{key: getattr(ticket, key) for key in fields},
        **context_dict(ticket),
        "created_at": aware(ticket.created_at).isoformat(),
        "updated_at": aware(ticket.updated_at).isoformat(),
        "delivery": {
            "status": ticket.delivery.status,
            "recipient": ticket.delivery.recipient,
            "error": ticket.delivery.last_error,
        }
        if ticket.delivery
        else None,
        "preparation": {
            "status": ticket.preparation.status,
            "error": ticket.preparation.last_error,
        }
        if ticket.preparation
        else None,
        "reply_email": (ticket.email_origin.reply_to if ticket.email_origin else "")
        or ticket.email,
    }


def find(db, actor, id):
    item = db.get(Ticket, (actor.organization_id, id))
    if not item:
        raise HTTPException(404, "Ticket not found.")
    return item


def analyze_ticket(config, sessions, graphs, auth, id, body, actor, job=None):
    if not actor.organization["is_demo"]:
        auth.ensure_verified(actor)
    if body.fault != "none" and (
        not config.fault_injection_enabled or not actor.organization["is_demo"]
    ):
        raise HTTPException(
            403, "Fault scenarios are available only in enabled demo workspaces."
        )
    with sessions() as db:
        t = find(db, actor, id)
        if t.status in ("approved", "escalated", "review", "sending", "sent"):
            return ticket_dict(t)
        if t.status == "queued" and job is None:
            raise HTTPException(409, "analysis_in_progress")
        if job is not None:
            current_job = db.get(PreparationJob, job.id)
            if (
                not current_job
                or current_job.status != "processing"
                or current_job.lease_token != job.lease_token
            ):
                raise HTTPException(409, "preparation_lease_lost")
        if t.status == "processing" and t.updated_at.replace(
            tzinfo=timezone.utc
        ) > utcnow() - timedelta(seconds=max(90, config.agent_timeout_seconds + 30)):
            raise HTTPException(409, "analysis_in_progress")
        reservation_id = (
            reserve(db, config, actor.organization_id, user_id=actor.user_id)
            if actor.organization["workflow_mode"] == "knowledge"
            and config.model_provider == "openai"
            else None
        )
        if job is not None:
            current_job.reservation_id = reservation_id
        if not db.execute(
            update(Ticket)
            .where(
                Ticket.organization_id == actor.organization_id,
                Ticket.id == id,
                Ticket.revision == t.revision,
                Ticket.status == t.status,
            )
            .values(status="processing", revision=t.revision + 1, updated_at=utcnow())
        ).rowcount:
            raise HTTPException(409, "revision_conflict")
        db.commit()
        db.refresh(t)
        processing_revision = t.revision
        thread = f"{actor.organization_id}:{id}:{t.revision}"
        from supportops.billing import reservation_budget

        initial = {
            "credit_budget_usd": reservation_budget(
                db.get(CreditReservation, reservation_id)
            )
            if reservation_id
            else config.agent_provider_cost_limit,
            "organization_id": actor.organization_id,
            "business_name": actor.organization["name"],
            "workflow_mode": actor.organization["workflow_mode"],
            "agent_name": actor.name,
            "subject": t.subject,
            "template_id": t.template_id,
            "ticket_id": id,
            "customer": t.customer,
            "message": t.message,
            "order_id": t.order_id,
            "fault": body.fault,
            "trace_id": uuid.uuid4().hex,
            "steps": [],
            "reason": None,
            "draft": None,
            "sources": [],
            "order": None,
            "usage": {},
        }
        started = time.perf_counter()
        try:
            with trace.get_tracer("supportops").start_as_current_span(
                "ticket.analyze"
            ) as span:
                span.set_attribute("ticket.id", id)
                span.set_attribute("organization.id", actor.organization_id)
                initial["trace_id"] = format(span.get_span_context().trace_id, "032x")
                graph = (
                    graphs.agent_graph
                    if actor.organization["workflow_mode"] == "knowledge"
                    and config.model_provider == "openai"
                    else graphs.graph
                )
                result = graph.invoke(
                    initial, config={"configurable": {"thread_id": thread}}
                )
            for key in ("__interrupt__", "message", "customer"):
                result.pop(key, None)
            result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            result["thread_id"] = thread
            t.result = result
            t.order_id = result.get("order_id", t.order_id)
            t.category = result.get("categories", ["other"])[0]
            t.status = "manual" if result.get("reason") else "review"
        except Exception as exc:
            logging.error(
                "workflow_failed organization_id=%s ticket_id=%s error_type=%s",
                actor.organization_id,
                id,
                type(exc).__name__,
            )
            t.result = {
                "reason": "workflow_failed",
                "trace_id": initial["trace_id"],
                "steps": [],
                "sources": [],
                "draft": None,
            }
            t.status = "manual"
        t.updated_at = utcnow()
        values = {
            name: getattr(t, name)
            for name in ("result", "order_id", "category", "status", "updated_at")
        }
        db.expunge(t)
        if not db.execute(
            update(Ticket)
            .where(
                Ticket.organization_id == actor.organization_id,
                Ticket.id == id,
                Ticket.revision == processing_revision,
                Ticket.status == "processing",
            )
            .values(**values)
        ).rowcount:
            db.rollback()
            settle(
                sessions,
                reservation_id,
                (t.result or {}).get("usage", {}),
                uncertain=True,
            )
            raise HTTPException(409, "revision_conflict")
        audit(
            db,
            actor,
            "ticket.analyzed",
            {"ticket_id": id, "status": t.status, "reason": t.result.get("reason")},
        )
        db.commit()
        settle(
            sessions,
            reservation_id,
            (t.result or {}).get("usage", {}),
            uncertain=bool(
                (t.result or {}).get("usage", {}).get("uncertain")
                or (t.result or {}).get("reason") == "workflow_failed"
            ),
        )
        return ticket_dict(db.get(Ticket, (actor.organization_id, id)))


def review_ticket(config, sessions, graphs, id, body, actor, delivery_mode="human"):
    with sessions() as db:
        t = find(db, actor, id)
        if t.revision != body.revision or t.status not in ("review", "manual"):
            raise HTTPException(409, "revision_conflict")
        if body.decision == "approve" and (
            (t.status != "review" and not body.manual) or len(body.text.strip()) < 10
        ):
            raise HTTPException(422, "draft_not_approvable")
        if body.send and body.decision != "approve":
            raise HTTPException(422, "Approve the reply before sending it.")
        rereview = (
            delivery_mode == "human"
            and t.delivery
            and t.delivery.status in {"failed", "cancelled"}
        )
        status = "approved" if body.decision == "approve" else "escalated"
        if not db.execute(
            update(Ticket)
            .where(
                Ticket.organization_id == actor.organization_id,
                Ticket.id == id,
                Ticket.revision == body.revision,
                Ticket.status.in_(["review", "manual"]),
            )
            .values(status=status, revision=body.revision + 1, updated_at=utcnow())
        ).rowcount:
            raise HTTPException(409, "revision_conflict")
        if body.send:
            from supportops.delivery import queue_reply

            queue_reply(db, config, actor, t, body.text, mode=delivery_mode)
        elif (
            body.decision == "approve"
            and t.delivery
            and t.delivery.status in {"failed", "cancelled"}
        ):
            t.delivery.status, t.delivery.last_error = (
                "cancelled",
                "approved_without_sending",
            )
        thread = (t.result or {}).get("thread_id")
        if thread:
            if (
                not thread.startswith(actor.organization_id + ":")
                and actor.organization_id != "legacy-demo"
            ):
                raise HTTPException(409, "Workflow workspace mismatch.")
            graph_config = {"configurable": {"thread_id": thread}}
            graph = (
                graphs.agent_graph
                if (t.result or {}).get("manifest", {}).get("engine")
                == "openai-agent-v1"
                else graphs.graph
            )
            snapshot = graph.get_state(graph_config)
            if snapshot.next:
                graph.invoke(Command(resume=body.model_dump()), config=graph_config)
            elif rereview:
                graph.update_state(graph_config, {"review": body.model_dump()})
            elif snapshot.values.get("review") != body.model_dump():
                raise HTTPException(409, "checkpoint_review_conflict")
        db.add(
            Review(
                id=uuid.uuid4().hex,
                organization_id=actor.organization_id,
                ticket_id=id,
                decision=body.decision,
                text=body.text,
                note=body.note,
                actor=actor.name[:80],
                actor_id=actor.user_id,
            )
        )
        audit(db, actor, "ticket." + status, {"ticket_id": id})
        db.commit()
        db.refresh(t)
        return ticket_dict(t)
