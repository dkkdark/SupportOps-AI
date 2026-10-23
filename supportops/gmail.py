"""Workspace OAuth and restart-safe, leased, read-only Gmail message imports."""

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from supportops.auth import Identity, audit, aware, digest, identifier
from supportops.billing import consume
from supportops.db import GmailConnection, GmailOAuthState, Membership, Ticket, utcnow
from supportops.gmail_api import (
    API,
    CALLBACK,
    GmailError,
    Google,
    access_token,
    authorization_url,
    email_text,
    history_id,
    ready,
    seal,
    unseal,
    valid_id,
)
from supportops.intake import create_context, validate_routing
from supportops.registration import lock


class OAuthInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label_name: str = Field(default="INBOX", min_length=1, max_length=100)
    lookback_days: Literal[0, 7, 30] = 0
    request_type_id: str | None = Field(default=None, max_length=40)
    authorized: Literal[True]


def public_connection(row):
    if not row:
        return None
    result = {
        key: getattr(row, key)
        for key in (
            "id",
            "mailbox",
            "label_name",
            "request_type_id",
            "status",
            "phase",
            "last_synced_at",
            "last_error",
            "imported_count",
            "skipped_count",
            "created_at",
        )
    } | {
        "syncing": bool(row.lease_until and aware(row.lease_until) > utcnow()),
        "pending": len(row.pending_ids),
        "import_since": row.import_since,
    }
    return {
        key: aware(value).isoformat() if isinstance(value, datetime) else value
        for key, value in result.items()
    }


def token_context(row):
    return {
        "purpose": "gmail_refresh",
        "organization": row.organization_id,
        "connection": row.id,
        "mailbox": row.mailbox,
    }


class OAuthLogFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            if isinstance(args[2], str) and args[2].split("?", 1)[0] == CALLBACK:
                args[2] = CALLBACK
                record.args = tuple(args)
        return True


def gmail_router(auth, sessions, config):
    router = APIRouter(prefix="/api/integrations/gmail")
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, OAuthLogFilter) for item in logger.filters):
        logger.addFilter(OAuthLogFilter())

    def configured():
        if not ready(config):
            raise HTTPException(
                503,
                "Gmail connection is temporarily unavailable. Contact support for help.",
            )

    @router.get("")
    def status(actor: Identity = Depends(auth.admin)):
        with sessions() as db:
            return {
                "available": ready(config),
                "sync_interval_seconds": config.gmail_sync_interval_seconds,
                "connection": public_connection(
                    db.get(GmailConnection, actor.organization_id)
                ),
            }

    @router.post("/connect")
    def connect(
        body: OAuthInput, request: Request, actor: Identity = Depends(auth.admin)
    ):
        configured()
        auth.ensure_verified(actor)
        if actor.organization["is_demo"]:
            raise HTTPException(403, "Connect Gmail from your business workspace.")
        auth.throttle(request, "gmail-connect", actor.user_id, limit=10)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        context = {
            "purpose": "gmail_oauth",
            "organization": actor.organization_id,
            "user": actor.user_id,
            "session": actor.session_hash,
        }
        with sessions() as db:
            lock(db)
            validate_routing(db, actor.organization_id, body.request_type_id)
            previous = db.get(GmailConnection, actor.organization_id)
            options = body.model_dump() | {
                "generation": previous.generation if previous else 0
            }
            db.execute(
                delete(GmailOAuthState).where(
                    GmailOAuthState.organization_id == actor.organization_id
                )
            )
            db.add(
                GmailOAuthState(
                    token_hash=digest(state),
                    organization_id=actor.organization_id,
                    user_id=actor.user_id,
                    session_hash=actor.session_hash,
                    verifier_encrypted=seal(config, verifier, context),
                    options=options,
                    expires_at=utcnow() + timedelta(minutes=10),
                )
            )
            db.commit()
        return {"url": authorization_url(config, state, verifier)}

    @router.get("/callback")
    def callback(
        request: Request,
        state: str = Query(min_length=20, max_length=200),
        code: str = Query(default="", max_length=2048),
        error: str = Query(default="", max_length=200),
        actor: Identity = Depends(auth.user),
    ):
        configured()
        with sessions() as db:
            pending = db.get(GmailOAuthState, digest(state))
            if (
                not pending
                or pending.used
                or pending.user_id != actor.user_id
                or pending.session_hash != actor.session_hash
                or pending.organization_id != actor.organization_id
                or actor.role not in ("owner", "admin")
                or aware(pending.expires_at) <= utcnow()
            ):
                raise HTTPException(
                    400,
                    "This Gmail connection link expired or belongs to another session. Connect Gmail again.",
                )
            options, organization_id = pending.options, pending.organization_id
            context = {
                "purpose": "gmail_oauth",
                "organization": organization_id,
                "user": actor.user_id,
                "session": actor.session_hash,
            }
            try:
                verifier = unseal(config, pending.verifier_encrypted, context)
            except GmailError:
                raise HTTPException(
                    400, "Connect Gmail again to renew this link."
                ) from None
            if not db.execute(
                update(GmailOAuthState)
                .where(
                    GmailOAuthState.token_hash == pending.token_hash,
                    GmailOAuthState.used.is_(False),
                )
                .values(used=True)
            ).rowcount:
                raise HTTPException(
                    400, "This Gmail connection link has already been used."
                )
            db.commit()
        if error or not code:
            with sessions() as db:
                db.execute(
                    delete(GmailOAuthState).where(
                        GmailOAuthState.token_hash == digest(state)
                    )
                )
                db.commit()
            return RedirectResponse("/?gmail=cancelled#sources", status_code=303)
        google = Google(config, getattr(auth, "gmail_transport", None))
        try:
            tokens = google.exchange(code, verifier)
            access = access_token(tokens, require_scope=True)
            refresh = tokens.get("refresh_token")
            if not isinstance(refresh, str) or not 1 <= len(refresh) <= 8192:
                raise GmailError("gmail_reconnect_required")
            mailbox, cursor = google.profile(access)
            label_id, label_name = google.label(access, options["label_name"])
            now = utcnow()
            with sessions() as db:
                lock(db)
                claim = db.get(GmailOAuthState, digest(state))
                if not claim or not claim.used or aware(claim.expires_at) <= now:
                    raise HTTPException(
                        409,
                        "This Gmail connection was cancelled or replaced. Connect again.",
                    )
                member = db.get(Membership, (organization_id, actor.user_id))
                if (
                    not member
                    or not member.active
                    or member.role not in ("owner", "admin")
                ):
                    raise HTTPException(
                        403, "Workspace access has changed. Connect Gmail again."
                    )
                validate_routing(db, organization_id, options["request_type_id"])
                row = db.get(GmailConnection, organization_id)
                if (row.generation if row else 0) != options["generation"]:
                    raise HTTPException(
                        409, "The Gmail connection changed. Refresh and connect again."
                    )
                if not row:
                    row = GmailConnection(
                        organization_id=organization_id, id=identifier()
                    )
                    db.add(row)
                elif row.mailbox != mailbox:
                    row.imported_count, row.skipped_count, row.last_synced_at = (
                        0,
                        0,
                        None,
                    )
                row.mailbox, row.label_id, row.label_name = (
                    mailbox,
                    label_id,
                    label_name,
                )
                row.request_type_id, row.connected_by = (
                    options["request_type_id"],
                    actor.user_id,
                )
                row.generation = options["generation"] + 1
                row.refresh_token_encrypted = seal(config, refresh, token_context(row))
                row.status, row.phase, row.history_id = "active", "initial", cursor
                row.import_since = now - timedelta(days=options["lookback_days"])
                row.scan_before, row.next_sync_at = now, now
                row.pending_ids, row.batch_final = [], False
                row.page_token = row.target_history_id = row.lease_token = (
                    row.lease_until
                ) = None
                row.last_error, row.failures, row.updated_at = None, 0, now
                audit(
                    db,
                    actor,
                    "gmail.connected",
                    {"mailbox": mailbox, "label": label_name},
                )
                db.commit()
        except GmailError as exc:
            outcome = (
                "label_missing" if exc.code == "gmail_label_not_found" else "failed"
            )
            return RedirectResponse("/?gmail=" + outcome + "#sources", status_code=303)
        finally:
            google.close()
            with sessions() as db:
                db.execute(
                    delete(GmailOAuthState).where(
                        GmailOAuthState.token_hash == digest(state)
                    )
                )
                db.commit()
        return RedirectResponse("/?gmail=connected#sources", status_code=303)

    @router.post("/sync")
    def sync(request: Request, actor: Identity = Depends(auth.admin)):
        configured()
        auth.throttle(request, "gmail-sync", actor.organization_id, limit=10)
        with sessions() as db:
            row = db.get(GmailConnection, actor.organization_id)
            if not row or row.status != "active":
                raise HTTPException(409, "Connect Gmail before requesting an import.")
            row.next_sync_at = utcnow()
            db.commit()
        return {"queued": True}

    @router.post("/disconnect")
    def disconnect(actor: Identity = Depends(auth.admin)):
        with sessions() as db:
            lock(db)
            row = db.get(GmailConnection, actor.organization_id)
            if row:
                row.status, row.refresh_token_encrypted = "disconnected", None
                row.generation += 1
                row.lease_token = row.lease_until = row.page_token = row.last_error = (
                    None
                )
                row.pending_ids, row.batch_final = [], False
                row.updated_at = utcnow()
                audit(db, actor, "gmail.disconnected", {"mailbox": row.mailbox})
            db.execute(
                delete(GmailOAuthState).where(
                    GmailOAuthState.organization_id == actor.organization_id
                )
            )
            db.commit()
        return {"ok": True}

    return router


class LeaseLost(Exception):
    pass


def locked_connection(db, snapshot):
    # First a conditional write: serializes SQLite and PostgreSQL with disconnect/reconnect.
    if not db.execute(
        update(GmailConnection)
        .where(
            GmailConnection.organization_id == snapshot.organization_id,
            GmailConnection.generation == snapshot.generation,
            GmailConnection.lease_token == snapshot.lease_token,
            GmailConnection.status == "active",
        )
        .values(updated_at=utcnow())
    ).rowcount:
        raise LeaseLost()
    return db.get(GmailConnection, snapshot.organization_id)


def page(google, access, row):
    if row.phase in {"initial", "reconcile"}:
        since = aware(row.import_since)
        if row.phase == "reconcile":
            # History is the primary feed. An overlapping, bounded Inbox scan
            # recovers recent matching mail absent from the event feed.
            since = max(since, aware(row.scan_before) - timedelta(minutes=15))
        params = {
            "maxResults": 10,
            "labelIds": ["INBOX"]
            if row.label_id == "INBOX"
            else ["INBOX", row.label_id],
            "q": f"after:{int(since.timestamp()) - 1} before:{int(aware(row.scan_before).timestamp()) + 1}",
            "includeSpamTrash": "false",
        }
        url = API + "/messages"
    else:
        params = {
            "maxResults": 10,
            "startHistoryId": row.history_id,
            "historyTypes": ["messageAdded", "labelAdded"],
            "labelId": "INBOX",
        }
        url = API + "/history"
    if row.page_token:
        params["pageToken"] = row.page_token
    result = google.request("GET", url, access, params=params)
    if row.phase in {"initial", "reconcile"}:
        ids = [valid_id(m.get("id")) for m in result.get("messages", [])]
        target = None
    else:
        ids = [
            valid_id(event.get("message", {}).get("id"))
            for item in result.get("history", [])
            for key in ("messagesAdded", "labelsAdded")
            for event in item.get(key, [])
        ]
        target = history_id(result.get("historyId"))
    ids = list(dict.fromkeys(ids))
    token = result.get("nextPageToken")
    if len(ids) > 10000 or (
        token is not None and (not isinstance(token, str) or len(token) > 4096)
    ):
        raise GmailError("gmail_invalid_response")
    return ids, token or None, target


def finish_batch(config, row):
    if not row.pending_ids and row.batch_final:
        if row.phase in {"initial", "reconcile"}:
            row.phase, row.last_synced_at = "history", row.scan_before
            row.next_sync_at = utcnow()
        else:
            row.history_id, row.last_synced_at = row.target_history_id, utcnow()
            if aware(row.scan_before) <= utcnow() - timedelta(
                seconds=config.gmail_sync_interval_seconds
            ):
                # Keep the completed history checkpoint during reconciliation.
                # Any changes arriving during the scan remain in the next feed.
                row.phase, row.scan_before = "reconcile", utcnow()
                row.next_sync_at = utcnow()
            else:
                row.next_sync_at = utcnow() + timedelta(
                    seconds=config.gmail_sync_interval_seconds
                )
        row.target_history_id, row.batch_final = None, False
    else:
        row.next_sync_at = utcnow() + timedelta(seconds=5)


def message_key(row, message_id):
    return (
        "gmail:" + hashlib.sha256((row.mailbox + ":" + message_id).encode()).hexdigest()
    )


def accept_message(config, sessions, snapshot, message_id, message):
    with sessions() as db:
        row = locked_connection(db, snapshot)
        member = db.get(Membership, (row.organization_id, row.connected_by))
        if not member or not member.active or member.role not in ("owner", "admin"):
            raise GmailError("gmail_owner_access_removed")
        key = message_key(row, message_id)
        existing = db.scalar(
            select(Ticket.id).where(
                Ticket.organization_id == row.organization_id,
                Ticket.idempotency_key == key,
            )
        )
        values = None
        if message:
            try:
                values = email_text(
                    message, row.mailbox, row.label_id, aware(row.import_since)
                )
            except GmailError as error:
                if error.code != "gmail_message_structure_limit":
                    raise
                metadata = dict(
                    message,
                    _body_unavailable=True,
                    payload={"headers": message.get("payload", {}).get("headers", [])},
                )
                values = email_text(
                    metadata, row.mailbox, row.label_id, aware(row.import_since)
                )
        if not existing and values:
            try:
                validate_routing(db, row.organization_id, row.request_type_id)
            except HTTPException:
                raise GmailError("gmail_routing_unavailable") from None
            consume(db, row.organization_id, "requests", config=config)
            item = Ticket(
                organization_id=row.organization_id,
                id="REQ-" + identifier()[:12].upper(),
                idempotency_key=key,
                created_by=row.connected_by,
                **values,
            )
            db.add(item)
            db.flush()
            create_context(
                db,
                row.organization_id,
                item.id,
                row.request_type_id,
                external_id="gmail:" + message_id,
            )
            row.imported_count += 1
        elif not existing:
            row.skipped_count += 1
        row.pending_ids = [value for value in row.pending_ids if value != message_id]
        db.commit()


def sync_one(
    config, sessions, organization_id, transport=None, on_progress=lambda: None
):
    now, lease = utcnow(), identifier()
    with sessions() as db:
        if not db.execute(
            update(GmailConnection)
            .where(
                GmailConnection.organization_id == organization_id,
                GmailConnection.status == "active",
                GmailConnection.next_sync_at <= now,
                (
                    GmailConnection.lease_until.is_(None)
                    | (GmailConnection.lease_until <= now)
                ),
            )
            .values(lease_token=lease, lease_until=now + timedelta(seconds=180))
        ).rowcount:
            db.rollback()
            return False
        db.commit()
        snapshot = db.get(GmailConnection, organization_id)
    google = Google(config, transport)
    try:
        with sessions() as db:
            member = db.get(Membership, (organization_id, snapshot.connected_by))
            if not member or not member.active or member.role not in ("owner", "admin"):
                raise GmailError("gmail_owner_access_removed")
        tokens = google.refresh(
            unseal(config, snapshot.refresh_token_encrypted, token_context(snapshot))
        )
        access = access_token(tokens)
        rotated = tokens.get("refresh_token")
        if rotated:
            if not isinstance(rotated, str) or len(rotated) > 8192:
                raise GmailError("gmail_invalid_response")
            with sessions() as db:
                row = locked_connection(db, snapshot)
                row.refresh_token_encrypted = seal(config, rotated, token_context(row))
                db.commit()
        if not snapshot.pending_ids and not snapshot.batch_final:
            try:
                ids, next_page, target = page(google, access, snapshot)
            except GmailError as exc:
                if exc.code == "gmail_page_expired":
                    with sessions() as db:
                        row = locked_connection(db, snapshot)
                        row.page_token, row.target_history_id = None, None
                        row.lease_token, row.lease_until, row.next_sync_at = (
                            None,
                            None,
                            utcnow(),
                        )
                        db.commit()
                    return True
                if exc.code != "gmail_not_found" or snapshot.phase != "history":
                    raise
                _, cursor = google.profile(access)
                with sessions() as db:
                    row = locked_connection(db, snapshot)
                    row.phase, row.history_id = "initial", cursor
                    row.page_token, row.target_history_id = None, None
                    row.batch_final, row.pending_ids, row.scan_before = (
                        False,
                        [],
                        utcnow(),
                    )
                    row.lease_token, row.lease_until, row.next_sync_at = (
                        None,
                        None,
                        utcnow(),
                    )
                    db.commit()
                return True
            with sessions() as db:
                row = locked_connection(db, snapshot)
                row.pending_ids, row.page_token, row.target_history_id = (
                    ids,
                    next_page,
                    target,
                )
                row.batch_final = next_page is None
                db.commit()
                snapshot = row
        deadline = time.monotonic() + 80
        for message_id in snapshot.pending_ids[:10]:
            if time.monotonic() > deadline:
                break
            on_progress()
            with sessions() as db:
                if not db.scalar(
                    select(GmailConnection.id).where(
                        GmailConnection.organization_id == organization_id,
                        GmailConnection.lease_token == lease,
                        GmailConnection.status == "active",
                    )
                ):
                    raise LeaseLost()
                existing = db.scalar(
                    select(Ticket.id).where(
                        Ticket.organization_id == organization_id,
                        Ticket.idempotency_key == message_key(snapshot, message_id),
                    )
                )
            message = None
            if not existing:
                try:
                    message = google.message(access, message_id)
                    if valid_id(message.get("id")) != message_id:
                        raise GmailError("gmail_invalid_response")
                except GmailError as exc:
                    if exc.code != "gmail_not_found":
                        raise
            accept_message(config, sessions, snapshot, message_id, message)
        with sessions() as db:
            row = locked_connection(db, snapshot)
            finish_batch(config, row)
            row.lease_token, row.lease_until, row.last_error, row.failures = (
                None,
                None,
                None,
                0,
            )
            db.commit()
        return True
    except LeaseLost:
        return False
    except (GmailError, HTTPException) as exc:
        code = (
            exc.code if isinstance(exc, GmailError) else "gmail_request_limit_reached"
        )
        with sessions() as db:
            row = db.get(GmailConnection, organization_id)
            if row and row.lease_token == lease:
                row.last_error, row.failures = code, row.failures + 1
                row.lease_token, row.lease_until = None, None
                row.next_sync_at = utcnow() + timedelta(
                    seconds=min(3600, 60 * 2 ** min(row.failures, 6))
                )
                if code in {
                    "gmail_reconnect_required",
                    "gmail_access_denied",
                    "gmail_credentials_unavailable",
                    "gmail_owner_access_removed",
                    "gmail_routing_unavailable",
                }:
                    row.status = "needs_reconnect"
                    row.refresh_token_encrypted = None
                db.commit()
        return True
    finally:
        google.close()


def poll_pending(config, sessions, transport=None, on_progress=lambda: None):
    if not ready(config):
        return 0
    with sessions() as db:
        ids = list(
            db.scalars(
                select(GmailConnection.organization_id)
                .where(
                    GmailConnection.status == "active",
                    GmailConnection.next_sync_at <= utcnow(),
                    (
                        GmailConnection.lease_until.is_(None)
                        | (GmailConnection.lease_until <= utcnow())
                    ),
                )
                .order_by(GmailConnection.next_sync_at)
                .limit(5)
            )
        )
    count = 0
    for organization_id in ids:
        on_progress()
        try:
            count += sync_one(config, sessions, organization_id, transport, on_progress)
        except Exception as exc:
            # Never log tokens, provider bodies, OAuth codes or message content.
            logging.getLogger(__name__).error(
                "gmail_sync_interrupted error_type=%s", type(exc).__name__
            )
    return count
