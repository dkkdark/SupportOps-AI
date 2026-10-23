"""Offline Gmail contracts: OAuth binding, encrypted grants and durable imports."""

import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event
from urllib.parse import parse_qs, urlparse
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from supportops.auth import digest
from supportops.db import GmailConnection, GmailOAuthState, Ticket, utcnow
from supportops.gmail import OAuthLogFilter, sync_one, poll_pending, token_context
from supportops.gmail_api import API, SCOPE, seal, unseal
from tests.test_workspaces import register, invitation, accept, account


class Mailbox:
    def __init__(self):
        self.email = "support@acme.test"
        self.calls = []
        self.messages = {}
        self.history_messages = []
        self.profile_cursor = "100"
        self.token_error = None
        self.expired = False
        self.page_size = 10
        self.message_hook = None
        self.fail_message = None

    def add(
        self,
        id,
        text="Please explain the cancellation rules for my appointment.",
        html=False,
    ):
        self.messages[id] = {
            "id": id,
            "threadId": "thread-" + id,
            "labelIds": ["INBOX", "Label_1"],
            "internalDate": str(
                int((utcnow() - timedelta(minutes=1)).timestamp() * 1000)
            ),
            "payload": {
                "mimeType": "text/html" if html else "text/plain",
                "headers": [
                    {"name": "From", "value": "Emma Wilson <emma@example.test>"},
                    {"name": "Subject", "value": "Appointment question"},
                ],
                "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()},
            },
        }

    def __call__(self, request):
        self.calls.append(request)
        path = request.url.path
        if path == "/token":
            if self.token_error:
                return httpx.Response(
                    self.token_error[0], json={"error": self.token_error[1]}
                )
            values = parse_qs(request.content.decode())
            result = {
                "access_token": "synthetic-access",
                "token_type": "Bearer",
                "scope": SCOPE,
            }
            if values["grant_type"] == ["authorization_code"]:
                assert len(values["code_verifier"][0]) >= 43
                result["refresh_token"] = "synthetic-refresh-secret"
            return httpx.Response(200, json=result)
        assert request.headers["Authorization"] == "Bearer synthetic-access"
        if path.endswith("/profile"):
            return httpx.Response(
                200, json={"emailAddress": self.email, "historyId": self.profile_cursor}
            )
        if path.endswith("/labels"):
            return httpx.Response(
                200,
                json={"labels": [{"id": "Label_1", "name": "Support", "type": "user"}]},
            )
        if path.endswith("/history"):
            if self.expired:
                return httpx.Response(404, json={"error": {"code": 404}})
            return httpx.Response(
                200,
                json={
                    "historyId": "101",
                    "history": [
                        {
                            "id": "101",
                            "messagesAdded": [
                                {"message": {"id": id}} for id in self.history_messages
                            ],
                        },
                    ],
                },
            )
        if path == "/gmail/v1/users/me/messages":
            ids = list(self.messages)
            offset = int(request.url.params.get("pageToken", "0"))
            result = {
                "messages": [{"id": id} for id in ids[offset : offset + self.page_size]]
            }
            if offset + self.page_size < len(ids):
                result["nextPageToken"] = str(offset + self.page_size)
            return httpx.Response(200, json=result)
        id = path.split("/")[-1]
        if self.message_hook:
            self.message_hook(id)
        if id == self.fail_message:
            return httpx.Response(503, json={"error": {"code": 503}})
        return httpx.Response(200, json=self.messages[id])


@pytest.fixture
def gmail(platform):
    owner, other, guest = platform
    register(owner)
    fake = Mailbox()
    config = owner.app.state.auth.config
    config.google_oauth_client_id = "synthetic-client.apps.googleusercontent.com"
    config.google_oauth_client_secret = "synthetic-client-secret"
    config.gmail_token_encryption_key = Fernet.generate_key().decode()
    owner.app.state.auth.gmail_transport = httpx.MockTransport(fake)
    return owner, other, guest, fake


def start(owner, **options):
    response = owner.post(
        "/api/integrations/gmail/connect",
        json={
            "authorized": True,
            "lookback_days": 7,
            **options,
        },
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlparse(response.json()["url"]).query)
    assert query["scope"] == [SCOPE] and query["code_challenge_method"] == ["S256"]
    return query["state"][0]


def connect(owner, **options):
    state = start(owner, **options)
    response = owner.get(
        "/api/integrations/gmail/callback",
        params={"state": state, "code": "synthetic-code"},
        follow_redirects=False,
    )
    assert (
        response.status_code == 303
        and "gmail=connected" in response.headers["location"]
    ), response.text
    return state


def org(owner):
    return owner.get("/api/auth/me").json()["organization"]["id"]


def run(owner, fake, count=1):
    id = org(owner)
    for _ in range(count):
        with owner.app.state.sessions() as db:
            row = db.get(GmailConnection, id)
            row.next_sync_at = utcnow() - timedelta(seconds=1)
            db.commit()
        sync_one(
            owner.app.state.auth.config,
            owner.app.state.sessions,
            id,
            httpx.MockTransport(fake),
        )


def test_oauth_state_is_session_bound_single_use_and_tokens_are_encrypted(gmail):
    owner, _, guest, fake = gmail
    state = start(owner)
    before = len(fake.calls)
    assert (
        guest.get(
            "/api/integrations/gmail/callback",
            params={"state": state, "code": "stolen"},
        ).status_code
        == 401
    )
    assert len(fake.calls) == before
    response = owner.get(
        "/api/integrations/gmail/callback",
        params={"state": state, "code": "synthetic-code"},
        follow_redirects=False,
    )
    assert (
        response.status_code == 303
        and response.headers["Referrer-Policy"] == "no-referrer"
    )
    assert (
        owner.get(
            "/api/integrations/gmail/callback",
            params={"state": state, "code": "replay"},
        ).status_code
        == 400
    )
    with owner.app.state.sessions() as db:
        assert db.get(GmailOAuthState, digest(state)) is None
        row = db.get(GmailConnection, org(owner))
        assert "synthetic-refresh" not in row.refresh_token_encrypted
        assert (
            unseal(
                owner.app.state.auth.config,
                row.refresh_token_encrypted,
                token_context(row),
            )
            == "synthetic-refresh-secret"
        )
    public = owner.get("/api/integrations/gmail").text
    assert all(
        secret not in public
        for secret in [
            "synthetic-refresh",
            "synthetic-access",
            "synthetic-client-secret",
            "verifier",
            "encrypted",
        ]
    )


def test_expired_state_denied_consent_and_invalid_label_do_not_connect(gmail):
    owner, _, _, fake = gmail
    state = start(owner)
    with owner.app.state.sessions() as db:
        db.get(GmailOAuthState, digest(state)).expires_at = utcnow() - timedelta(
            seconds=1
        )
        db.commit()
    assert (
        owner.get(
            "/api/integrations/gmail/callback",
            params={"state": state, "code": "expired"},
        ).status_code
        == 400
    )
    state = start(owner)
    response = owner.get(
        "/api/integrations/gmail/callback",
        params={"state": state, "error": "access_denied"},
        follow_redirects=False,
    )
    assert "gmail=cancelled" in response.headers["location"]
    assert fake.calls == []
    state = start(owner, label_name="Missing label")
    response = owner.get(
        "/api/integrations/gmail/callback",
        params={"state": state, "code": "synthetic-code"},
        follow_redirects=False,
    )
    assert "gmail=label_missing" in response.headers["location"]
    assert owner.get("/api/integrations/gmail").json()["connection"] is None


def test_permissions_csrf_and_workspace_switch_cannot_take_over_connection(gmail):
    owner, other, employee, fake = gmail
    state = start(owner)
    register(other, "Other company", "other@example.test")
    assert (
        other.get(
            "/api/integrations/gmail/callback",
            params={"state": state, "code": "stolen"},
        ).status_code
        == 400
    )
    assert (
        owner.post(
            "/api/integrations/gmail/connect",
            json={"authorized": True},
            headers={"X-CSRF-Token": ""},
        ).status_code
        == 403
    )
    assert accept(employee, invitation(owner)).status_code == 200
    account(employee)
    assert employee.get("/api/integrations/gmail").status_code == 403
    assert employee.post("/api/integrations/gmail/disconnect").status_code == 403
    assert (
        owner.get(
            "/api/integrations/gmail/callback",
            params={"state": state, "code": "synthetic-code"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert other.get("/api/integrations/gmail").json()["connection"] is None


def test_initial_import_history_deduplication_and_html_text(gmail):
    owner, _, _, fake = gmail
    fake.add(
        "mail-1",
        '<html><head><script>bad()</script></head><body><p>Hello team, please help with this appointment.</p><img src="https://tracker.test/pixel"></body></html>',
        html=True,
    )
    connect(owner, label_name="Support")
    run(owner, fake)
    tickets = owner.get("/api/tickets").json()
    assert len(tickets) == 1
    item = tickets[0]
    assert item["created_at"].endswith("+00:00")
    assert (
        datetime.fromisoformat(item["created_at"]).timestamp()
        == int(fake.messages["mail-1"]["internalDate"]) / 1000
    )
    assert (
        item["channel"] == "email"
        and item["status"] == "new"
        and item["result"] is None
    )
    assert (
        "Hello team" in item["message"]
        and "script" not in item["message"]
        and "tracker" not in item["message"]
    )
    fake.add("mail-2")
    fake.history_messages = ["mail-1", "mail-2", "mail-2"]
    run(owner, fake, 2)
    assert len(owner.get("/api/tickets").json()) == 2
    assert (
        owner.get("/api/integrations/gmail").json()["connection"]["imported_count"] == 2
    )
    assert all(
        request.url.host in {"oauth2.googleapis.com", "gmail.googleapis.com"}
        for request in fake.calls
    )
    assert all(
        request.method == "GET" or request.url.path == "/token"
        for request in fake.calls
    )


def test_pagination_retry_and_reconnect_preserve_deduplication(gmail):
    owner, _, _, fake = gmail
    fake.page_size = 1
    fake.add("mail-1")
    fake.add("mail-2")
    connect(owner)
    run(owner, fake)
    fake.fail_message = "mail-2"
    run(owner, fake)
    status = owner.get("/api/integrations/gmail").json()["connection"]
    assert (
        status["pending"] == 1
        and status["last_error"] == "gmail_temporarily_unavailable"
    )
    fake.fail_message = None
    run(owner, fake)
    assert len(owner.get("/api/tickets").json()) == 2
    connect(owner)
    run(owner, fake, 2)
    assert len(owner.get("/api/tickets").json()) == 2


def test_expired_history_rebuilds_without_duplicate_requests(gmail):
    owner, _, _, fake = gmail
    fake.add("mail-1")
    connect(owner)
    run(owner, fake)
    fake.expired = True
    fake.profile_cursor = "200"
    run(owner, fake)
    assert (
        owner.get("/api/integrations/gmail").json()["connection"]["phase"] == "initial"
    )
    fake.expired = False
    fake.add("mail-2")
    run(owner, fake)
    assert len(owner.get("/api/tickets").json()) == 2


def test_disconnect_during_import_fences_old_worker_and_removes_credentials(gmail):
    owner, _, _, fake = gmail
    fake.add("mail-1")
    connect(owner)
    fake.message_hook = lambda _: owner.post(
        "/api/integrations/gmail/disconnect"
    ).raise_for_status()
    run(owner, fake)
    assert owner.get("/api/tickets").json() == []
    with owner.app.state.sessions() as db:
        row = db.get(GmailConnection, org(owner))
        assert row.status == "disconnected" and row.refresh_token_encrypted is None
        assert row.pending_ids == []
    assert (
        poll_pending(
            owner.app.state.auth.config,
            owner.app.state.sessions,
            httpx.MockTransport(fake),
        )
        == 0
    )


def test_only_one_worker_can_claim_a_mailbox(gmail):
    owner, _, _, fake = gmail
    fake.add("mail-1")
    connect(owner)
    entered, release = Event(), Event()

    def block(_):
        entered.set()
        assert release.wait(timeout=10)

    fake.message_hook = block
    config, sessions, id = (
        owner.app.state.auth.config,
        owner.app.state.sessions,
        org(owner),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(sync_one, config, sessions, id, httpx.MockTransport(fake))
        try:
            assert entered.wait(timeout=10)
            assert not sync_one(config, sessions, id, httpx.MockTransport(fake))
        finally:
            release.set()
        assert first.result(timeout=10)
    assert len(owner.get("/api/tickets").json()) == 1


def test_invalid_grant_and_token_swapping_require_reconnect_without_secret_leak(
    gmail, caplog
):
    owner, _, _, fake = gmail
    connect(owner)
    fake.token_error = (400, "invalid_grant")
    run(owner, fake)
    row = owner.get("/api/integrations/gmail").json()["connection"]
    assert (
        row["status"] == "needs_reconnect"
        and row["last_error"] == "gmail_reconnect_required"
    )
    fake.token_error = None
    connect(owner)
    with owner.app.state.sessions() as db:
        row = db.get(GmailConnection, org(owner))
        row.refresh_token_encrypted = seal(
            owner.app.state.auth.config,
            "another-workspace-token",
            {"organization": "foreign"},
        )
        db.commit()
    before = len(fake.calls)
    run(owner, fake)
    assert len(fake.calls) == before
    assert (
        owner.get("/api/integrations/gmail").json()["connection"]["last_error"]
        == "gmail_credentials_unavailable"
    )
    assert (
        "synthetic-refresh-secret" not in caplog.text
        and "another-workspace-token" not in caplog.text
    )


def test_oauth_callback_query_is_removed_from_access_logs():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %s',
        (
            "127.0.0.1",
            "GET",
            "/api/integrations/gmail/callback?code=secret-code&state=secret-state",
            "1.1",
            303,
        ),
        None,
    )
    OAuthLogFilter().filter(record)
    assert (
        "secret" not in record.getMessage() and "/callback HTTP/" in record.getMessage()
    )


def test_disconnect_cancels_a_first_oauth_exchange_already_in_flight(gmail):
    owner, _, _, fake = gmail
    state = start(owner)
    original = fake.__class__.__call__

    def handler(request):
        if request.url.path == "/token":
            owner.post("/api/integrations/gmail/disconnect").raise_for_status()
        return original(fake, request)

    owner.app.state.auth.gmail_transport = httpx.MockTransport(handler)
    response = owner.get(
        "/api/integrations/gmail/callback",
        params={"state": state, "code": "synthetic-code"},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert owner.get("/api/integrations/gmail").json()["connection"] is None


def test_mail_filters_routing_attachments_and_large_text(gmail):
    owner, _, _, fake = gmail
    request_type = owner.post(
        "/api/request-types", json={"name": "Mailbox requests"}
    ).json()
    fake.add("long-mail", "A detailed customer question. " * 500)
    fake.messages["long-mail"]["payload"] = {
        "mimeType": "multipart/mixed",
        "headers": fake.messages["long-mail"]["payload"]["headers"],
        "parts": [
            fake.messages["long-mail"]["payload"],
            {
                "mimeType": "application/pdf",
                "filename": "receipt.pdf",
                "body": {"attachmentId": "do-not-fetch"},
            },
        ],
    }
    fake.add("sent-mail")
    fake.messages["sent-mail"]["labelIds"].append("SENT")
    fake.add("unlabelled-mail")
    fake.messages["unlabelled-mail"]["labelIds"] = ["INBOX"]
    fake.add("old-mail")
    fake.messages["old-mail"]["internalDate"] = str(
        int((utcnow() - timedelta(days=60)).timestamp() * 1000)
    )
    connect(owner, label_name="Support", request_type_id=request_type["id"])
    run(owner, fake)
    tickets = owner.get("/api/tickets").json()
    assert len(tickets) == 1 and tickets[0]["request_type_id"] == request_type["id"]
    assert len(tickets[0]["message"]) <= 5000
    assert (
        "Attachments remain in Gmail" in tickets[0]["message"]
        and "Email shortened" in tickets[0]["message"]
    )
    assert not any("/attachments/" in request.url.path for request in fake.calls)


def test_revoked_workspace_access_stops_import_before_google_calls(gmail):
    from supportops.db import Membership

    owner, _, _, fake = gmail
    connect(owner)
    organization_id = org(owner)
    user_id = owner.get("/api/auth/me").json()["user"]["id"]
    with owner.app.state.sessions() as db:
        db.get(Membership, (organization_id, user_id)).active = False
        db.commit()
    before = len(fake.calls)
    sync_one(
        owner.app.state.auth.config,
        owner.app.state.sessions,
        organization_id,
        httpx.MockTransport(fake),
    )
    assert len(fake.calls) == before
    with owner.app.state.sessions() as db:
        row = db.get(GmailConnection, organization_id)
        assert row.status == "needs_reconnect" and row.refresh_token_encrypted is None


def test_expired_page_token_restarts_scan_and_keeps_exactly_once_import(gmail):
    owner, _, _, fake = gmail
    fake.page_size = 1
    fake.add("mail-1")
    fake.add("mail-2")
    connect(owner)
    run(owner, fake)
    original = fake.__class__.__call__

    def expired_page(request):
        if request.url.params.get("pageToken"):
            return httpx.Response(
                400, json={"error": {"code": 400, "message": "Expired page"}}
            )
        return original(fake, request)

    with owner.app.state.sessions() as db:
        row = db.get(GmailConnection, org(owner))
        row.next_sync_at = utcnow() - timedelta(seconds=1)
        db.commit()
    sync_one(
        owner.app.state.auth.config,
        owner.app.state.sessions,
        org(owner),
        httpx.MockTransport(expired_page),
    )
    with owner.app.state.sessions() as db:
        assert db.get(GmailConnection, org(owner)).page_token is None
    run(owner, fake, 2)
    assert len(owner.get("/api/tickets").json()) == 2
