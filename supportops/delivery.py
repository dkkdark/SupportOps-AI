"""Tenant-scoped Gmail outbox. Ambiguous sends are reconciled, never blindly replayed."""

import base64
import hashlib
import re
from datetime import timedelta, date
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import parseaddr
from fastapi import HTTPException
from sqlalchemy import select, update
from supportops.auth import audit, identifier
from supportops.db import (
    Document,
    AuditEvent,
    EmailOrigin,
    GmailConnection,
    KnowledgeSource,
    Membership,
    OutboundReply,
    ReplyPolicy,
    ReplyTemplate,
    Ticket,
    utcnow,
)
from supportops.gmail_api import (
    EMAIL,
    ID,
    SEND_SCOPE,
    Google,
    GmailError,
    SendFailure,
    access_token,
    header,
    seal,
    unseal,
)


def address(value):
    if not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
        return ""
    _, parsed = parseaddr(value)
    return (
        parsed
        if parsed == value.strip() and len(parsed) <= 160 and EMAIL.fullmatch(parsed)
        else ""
    )


def remember_origin(db, connection, ticket, message_id, message):
    headers = {
        h.get("name", "").casefold(): h.get("value", "")
        for h in message.get("payload", {}).get("headers", [])
    }
    rfc_id = headers.get("message-id", "").strip()
    _, reply_to = parseaddr(header(headers.get("reply-to", "")))
    db.add(
        EmailOrigin(
            organization_id=ticket.organization_id,
            ticket_id=ticket.id,
            mailbox=connection.mailbox,
            message_id=message_id,
            thread_id=message.get("threadId")
            if isinstance(message.get("threadId"), str)
            and ID.fullmatch(message["threadId"])
            else None,
            rfc_message_id=rfc_id
            if re.fullmatch(r"<[^<>\s]{1,500}>", rfc_id)
            else None,
            reply_to=address(reply_to),
            auto_reply_blocked=bool(
                headers.get("auto-submitted", "no").strip().casefold() != "no"
                or headers.get("list-id")
                or headers.get("precedence", "").strip().casefold()
                in {"bulk", "junk", "list"}
                or headers.get("x-auto-response-suppress")
                or message.get("_body_unavailable")
            ),
        )
    )


def sender_ready(db, config, org_id):
    from supportops.gmail_api import ready

    row = db.get(GmailConnection, org_id)
    return bool(ready(config) and row and row.status == "active" and row.send_enabled)


def automatic_safe(db, ticket):
    result = ticket.result or {}
    verdict = result.get("verifier") or {}
    if (
        result.get("reason")
        or not isinstance(result.get("draft"), str)
        or not 10 <= len(result["draft"].strip()) <= 10000
        or result.get("manifest", {}).get("engine") != "openai-agent-v1"
        or verdict.get("supported") is not True
        or verdict.get("needs_live_data") is not False
        or verdict.get("unsafe_action") is not False
        or verdict.get("issues")
        or not result.get("sources")
        or not result.get("checks")
        or not all(c.get("passed") is True for c in result["checks"])
    ):
        return False
    for source in result["sources"]:
        current = db.get(Document, (ticket.organization_id, source.get("id")))
        day = date.today().isoformat()
        if (
            not current
            or not current.enabled
            or current.version != source.get("version")
            or current.text != source.get("text")
            or current.effective_from > day
            or (current.effective_to and current.effective_to <= day)
        ):
            return False
        if current.source_id:
            parent = db.get(
                KnowledgeSource, (ticket.organization_id, current.source_id)
            )
            if not parent or parent.archived:
                return False
    template = result.get("manifest", {}).get("template")
    if template:
        current = db.get(ReplyTemplate, (ticket.organization_id, template.get("id")))
        if (
            not current
            or not current.active
            or current.revision != template.get("revision")
        ):
            return False
    return True


def queue_reply(db, config, actor, ticket, text, mode="human"):
    if (
        config.app_env == "production"
        and config.require_verified_email
        and not actor.email_verified
    ):
        raise HTTPException(403, "Verify your email before sending replies.")
    if actor.organization.get("is_demo"):
        raise HTTPException(
            409, "Email sending is available in business workspaces only."
        )
    if not sender_ready(db, config, actor.organization_id):
        raise HTTPException(409, "Reconnect Gmail with permission to send replies.")
    connection = db.get(GmailConnection, actor.organization_id)
    origin = db.get(EmailOrigin, (actor.organization_id, ticket.id))
    recipient = (origin.reply_to if origin else "") or ticket.email
    if not address(recipient) or recipient.casefold() == connection.mailbox.casefold():
        raise HTTPException(422, "This request has no valid external reply address.")
    if origin and origin.mailbox != connection.mailbox:
        raise HTTPException(409, "Reconnect the mailbox that received this request.")
    if mode == "automatic" and (
        not automatic_safe(db, ticket) or (origin and origin.auto_reply_blocked)
    ):
        raise HTTPException(409, "This reply requires an agent's review.")
    existing = db.scalar(
        select(OutboundReply).where(
            OutboundReply.organization_id == actor.organization_id,
            OutboundReply.ticket_id == ticket.id,
        )
    )
    if existing and existing.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "A delivery already exists for this request.")
    if existing:
        row = existing
        row.actor_id, row.mode, row.ticket_revision = (
            actor.user_id,
            mode,
            ticket.revision,
        )
        row.mailbox, row.recipient, row.text = (
            connection.mailbox,
            recipient,
            text.strip(),
        )
        row.status, row.last_error, row.lease_token, row.lease_until = (
            "pending",
            None,
            None,
            None,
        )
    else:
        row = OutboundReply(
            id=identifier(),
            organization_id=actor.organization_id,
            ticket_id=ticket.id,
            actor_id=actor.user_id,
            mode=mode,
            ticket_revision=ticket.revision,
            mailbox=connection.mailbox,
            recipient=recipient,
            text=text.strip(),
        )
        db.add(row)
    ticket.status = "sending"
    ticket.updated_at = utcnow()
    audit(db, actor, "reply.queued", {"ticket_id": ticket.id, "mode": mode})
    return row


def rfc_id(row):
    return f"<supportops-{row.id}@{row.mailbox.split('@', 1)[1]}>"


def mime_reply(row, ticket, origin):
    message = EmailMessage(policy=SMTP)
    message["From"] = row.mailbox
    message["To"] = row.recipient
    subject = ticket.subject.replace("\r", " ").replace("\n", " ")
    message["Subject"] = (
        subject if subject.casefold().startswith("re:") else "Re: " + subject
    )
    message["Message-ID"] = rfc_id(row)
    message["Auto-Submitted"] = "auto-replied" if row.mode == "automatic" else "no"
    message["X-Auto-Response-Suppress"] = "All"
    if origin and origin.rfc_message_id:
        message["In-Reply-To"] = origin.rfc_message_id
        message["References"] = origin.rfc_message_id
    message.set_content(row.text)
    return base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode(), origin.thread_id if origin and origin.rfc_message_id else None


def finish_delivery(db, row, status, error=None, gmail_id=None):
    row.status, row.last_error, row.gmail_id = status, error, gmail_id
    row.lease_token, row.lease_until, row.updated_at = None, None, utcnow()
    db.add(
        AuditEvent(
            id=identifier(),
            organization_id=row.organization_id,
            user_id=row.actor_id,
            action="reply." + status,
            details={"ticket_id": row.ticket_id, "error": error},
        )
    )
    ticket = db.get(Ticket, (row.organization_id, row.ticket_id))
    if ticket and (
        ticket.status == "sending" or (status == "sent" and ticket.status == "manual")
    ):
        ticket.status = (
            "sent"
            if status == "sent"
            else "review"
            if status == "cancelled"
            else "manual"
        )
        ticket.revision += 1
        ticket.updated_at = utcnow()


def deliver_one(config, sessions, reply_id, transport=None):
    lease = identifier()
    with sessions() as db:
        row = db.get(OutboundReply, reply_id)
        if not row or row.status != "pending":
            return False
        if not db.execute(
            update(OutboundReply)
            .where(OutboundReply.id == reply_id, OutboundReply.status == "pending")
            .values(
                status="sending",
                lease_token=lease,
                lease_until=utcnow() + timedelta(seconds=120),
                updated_at=utcnow(),
            )
        ).rowcount:
            return False
        db.commit()
    google = Google(config, transport)
    try:
        with sessions() as db:
            row = db.get(OutboundReply, reply_id)
            ticket = db.get(Ticket, (row.organization_id, row.ticket_id))
            policy = db.get(ReplyPolicy, row.organization_id)
            connection = db.get(GmailConnection, row.organization_id)
            member = db.get(Membership, (row.organization_id, row.actor_id))
            connector = (
                db.get(Membership, (row.organization_id, connection.connected_by))
                if connection
                else None
            )
            if (
                not ticket
                or ticket.status != "sending"
                or ticket.revision != row.ticket_revision
            ):
                finish_delivery(db, row, "cancelled", "reply_changed")
                db.commit()
                return True
            if row.mode == "automatic" and (not policy or policy.mode != "automatic"):
                finish_delivery(db, row, "cancelled", "human_review_enabled")
                db.commit()
                return True
            if (
                not member
                or not member.active
                or not connector
                or not connector.active
                or connector.role not in {"owner", "admin"}
            ):
                raise SendFailure("workspace_access_changed", False)
            if (
                not sender_ready(db, config, row.organization_id)
                or connection.mailbox != row.mailbox
            ):
                raise SendFailure("gmail_send_access_denied", False)
            from supportops.gmail import token_context

            tokens = google.refresh(
                unseal(
                    config,
                    connection.refresh_token_encrypted,
                    token_context(connection),
                )
            )
            access = access_token(tokens)
            if tokens.get("refresh_token"):
                rotated = tokens["refresh_token"]
                if not isinstance(rotated, str) or not 1 <= len(rotated) <= 8192:
                    raise SendFailure("gmail_send_access_denied", False)
                connection.refresh_token_encrypted = seal(
                    config, rotated, token_context(connection)
                )
            if "scope" in tokens and SEND_SCOPE not in tokens["scope"].split():
                raise SendFailure("gmail_send_access_denied", False)
            origin = db.get(EmailOrigin, (row.organization_id, ticket.id))
            # Existing imports from before the delivery feature can recover
            # their original threading and auto-responder headers on demand.
            external = ticket.context.external_id if ticket.context else ""
            if not origin and external and external.startswith("gmail:"):
                message_id = external[6:]
                key = (
                    "gmail:"
                    + hashlib.sha256(
                        (row.mailbox + ":" + message_id).encode()
                    ).hexdigest()
                )
                if ticket.idempotency_key != key:
                    raise SendFailure("mailbox_mismatch", False)
                original = google.message(access, message_id)
                remember_origin(db, connection, ticket, message_id, original)
                db.flush()
                origin = db.get(EmailOrigin, (row.organization_id, ticket.id))
            if row.mode == "automatic" and (
                not automatic_safe(db, ticket) or (origin and origin.auto_reply_blocked)
            ):
                raise SendFailure("agent_review_required", False)
            raw, thread = mime_reply(row, ticket, origin)
            db.commit()
        # Recheck current policy/grant/revision immediately before the write.
        with sessions() as db:
            current = db.get(OutboundReply, reply_id)
            grant = db.get(GmailConnection, row.organization_id)
            policy = db.get(ReplyPolicy, row.organization_id)
            ticket = db.get(Ticket, (row.organization_id, row.ticket_id))
            actor_member = db.get(Membership, (row.organization_id, row.actor_id))
            connector = (
                db.get(Membership, (row.organization_id, grant.connected_by))
                if grant
                else None
            )
            if (
                current.status != "sending"
                or current.lease_token != lease
                or not grant
                or not grant.send_enabled
                or grant.status != "active"
                or grant.mailbox != row.mailbox
            ):
                raise SendFailure("gmail_send_access_denied", False)
            if (
                not actor_member
                or not actor_member.active
                or not connector
                or not connector.active
                or connector.role not in {"owner", "admin"}
            ):
                raise SendFailure("workspace_access_changed", False)
            if (
                not ticket
                or ticket.status != "sending"
                or ticket.revision != row.ticket_revision
            ):
                raise SendFailure("reply_changed", False)
            if row.mode == "automatic" and (not policy or policy.mode != "automatic"):
                finish_delivery(db, current, "cancelled", "human_review_enabled")
                db.commit()
                return True
            if row.mode == "automatic" and not automatic_safe(db, ticket):
                raise SendFailure("agent_review_required", False)
        gmail_id = google.send(access, raw, thread)
        with sessions() as db:
            current = db.get(OutboundReply, reply_id)
            if current.status == "sending" and current.lease_token == lease:
                finish_delivery(db, current, "sent", gmail_id=gmail_id)
                db.commit()
    except (SendFailure, GmailError) as error:
        with sessions() as db:
            row = db.get(OutboundReply, reply_id)
            if row and row.status == "sending" and row.lease_token == lease:
                finish_delivery(
                    db,
                    row,
                    "unknown"
                    if isinstance(error, SendFailure) and error.ambiguous
                    else "failed",
                    error.code,
                )
                db.commit()
    finally:
        google.close()
    return True


def delivery_pending(config, sessions, transport=None, on_progress=lambda: None):
    with sessions() as db:
        # A killed process may have sent the email. Never resend its job.
        stale = db.scalars(
            select(OutboundReply)
            .where(
                OutboundReply.status == "sending", OutboundReply.lease_until < utcnow()
            )
            .limit(20)
        ).all()
        for row in stale:
            finish_delivery(db, row, "unknown", "delivery_unconfirmed")
        db.commit()
        ids = list(
            db.scalars(
                select(OutboundReply.id)
                .where(OutboundReply.status == "pending")
                .order_by(OutboundReply.created_at)
                .limit(5)
            )
        )
    for reply_id in ids:
        on_progress()
        deliver_one(config, sessions, reply_id, transport)
    with sessions() as db:
        uncertain = list(
            db.scalars(
                select(OutboundReply.id)
                .where(
                    OutboundReply.status == "unknown",
                    (OutboundReply.lease_until.is_(None))
                    | (OutboundReply.lease_until <= utcnow()),
                )
                .limit(2)
            )
        )
    for reply_id in uncertain:
        on_progress()
        reconcile_delivery(config, sessions, reply_id, transport)
    return len(ids)


def reconcile_delivery(config, sessions, reply_id, transport=None):
    lease = identifier()
    with sessions() as db:
        if not db.execute(
            update(OutboundReply)
            .where(
                OutboundReply.id == reply_id,
                OutboundReply.status == "unknown",
                (OutboundReply.lease_until.is_(None))
                | (OutboundReply.lease_until <= utcnow()),
            )
            .values(lease_token=lease, lease_until=utcnow() + timedelta(minutes=5))
        ).rowcount:
            return
        db.commit()
        row = db.get(OutboundReply, reply_id)
        connection = db.get(GmailConnection, row.organization_id)
        if (
            not connection
            or connection.status != "active"
            or connection.mailbox != row.mailbox
        ):
            return
        connector = db.get(Membership, (row.organization_id, connection.connected_by))
        if (
            not connector
            or not connector.active
            or connector.role not in {"owner", "admin"}
        ):
            return
        from supportops.gmail import token_context

        try:
            token = unseal(
                config, connection.refresh_token_encrypted, token_context(connection)
            )
        except GmailError:
            return
    google = Google(config, transport)
    try:
        access = access_token(google.refresh(token))
        gmail_id = google.find_sent(access, rfc_id(row))
        if gmail_id:
            with sessions() as db:
                current = db.get(OutboundReply, reply_id)
                if current.status == "unknown" and current.lease_token == lease:
                    finish_delivery(db, current, "sent", gmail_id=gmail_id)
                    db.commit()
    except GmailError:
        pass
    finally:
        google.close()
