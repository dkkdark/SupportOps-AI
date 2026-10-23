"""Offline end-to-end bulk preparation, approvals, threading and delivery recovery."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from email import message_from_bytes
from email.policy import default
import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from supportops.billing import reserve
from supportops.db import (
    CreditReservation,
    DemoAccount,
    Document,
    Membership,
    PreparationJob,
    Ticket,
    utcnow,
)
from supportops.delivery import delivery_pending, deliver_one
from supportops.gmail_api import SCOPE, SEND_SCOPE
from supportops.preparation import preparation_one, preparation_pending
from tests.test_agent_semantic import publish
from tests.test_gmail import Mailbox, connect, org, run
from tests.test_workspaces import (
    TICKET,
    make_ticket,
    register,
    invitation,
    accept,
    account,
)


class DeliveryMailbox(Mailbox):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.fail_send = None

    def __call__(self, request):
        if request.url.path == "/gmail/v1/users/me/messages/send":
            self.calls.append(request)
            body = json.loads(request.content)
            message = message_from_bytes(
                base64.urlsafe_b64decode(body["raw"]), policy=default
            )
            if self.fail_send == "rejected":
                return httpx.Response(403, json={"error": {"code": 403}})
            self.sent.append((message, body))
            if self.fail_send == "timeout":
                raise httpx.ReadTimeout("Synthetic lost confirmation", request=request)
            return httpx.Response(200, json={"id": "sent-" + str(len(self.sent))})
        if request.url.path == "/gmail/v1/users/me/messages" and request.url.params.get(
            "q", ""
        ).startswith("in:sent"):
            self.calls.append(request)
            query = request.url.params["q"]
            matches = [
                {"id": "sent-" + str(i + 1)}
                for i, (message, _) in enumerate(self.sent)
                if message["Message-ID"].strip("<>") in query
            ]
            return httpx.Response(200, json={"messages": matches})
        response = super().__call__(request)
        if request.url.path == "/token":
            return httpx.Response(
                200, json={**response.json(), "scope": SCOPE + " " + SEND_SCOPE}
            )
        return response


@pytest.fixture
def mail_app(ai_client):
    client, models = ai_client
    publish(client)
    config = client.app.state.auth.config
    config.google_oauth_client_id = "synthetic-client"
    config.google_oauth_client_secret = "synthetic-secret"
    config.gmail_token_encryption_key = Fernet.generate_key().decode()
    mailbox = DeliveryMailbox()
    client.app.state.auth.gmail_transport = httpx.MockTransport(mailbox)
    connect(client, enable_sending=True)
    return client, models, mailbox


def prepare(client, mode="human"):
    response = client.post("/api/tickets/prepare-all", json={"mode": mode})
    assert response.status_code == 200, response.text
    preparation_pending(
        client.app.state.auth.config, client.app.state.sessions, client.app.state
    )
    return response.json()


def deliver(client, mailbox):
    delivery_pending(
        client.app.state.auth.config,
        client.app.state.sessions,
        httpx.MockTransport(mailbox),
    )


def get_ticket(client, ticket_id):
    response = client.get("/api/tickets/" + ticket_id)
    assert response.status_code == 200, response.text
    return response.json()


def test_bulk_human_review_prepares_once_and_sends_only_after_approval(mail_app):
    client, _, mailbox = mail_app
    first, second = make_ticket(client), make_ticket(client)
    assert prepare(client)["queued"] == 2
    assert all(t["status"] == "review" for t in client.get("/api/tickets").json())
    deliver(client, mailbox)
    assert mailbox.sent == []
    assert (
        client.post("/api/tickets/prepare-all", json={"mode": "human"}).json()["queued"]
        == 0
    )
    ticket = get_ticket(client, first["id"])
    response = client.post(
        "/api/tickets/" + ticket["id"] + "/review",
        json={
            "decision": "approve",
            "text": ticket["result"]["draft"],
            "revision": ticket["revision"],
            "send": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "sending"
    deliver(client, mailbox)
    deliver(client, mailbox)
    assert len(mailbox.sent) == 1
    assert str(mailbox.sent[0][0]["To"]) == TICKET["email"]
    assert get_ticket(client, first["id"])["status"] == "sent"
    assert get_ticket(client, second["id"])["status"] == "review"


def test_automatic_delivery_uses_real_agent_verifier_and_routes_rejections_to_agent(
    mail_app,
):
    client, models, mailbox = mail_app
    good = make_ticket(client)
    prepare(client, "automatic")
    assert get_ticket(client, good["id"])["status"] == "sending"
    deliver(client, mailbox)
    assert get_ticket(client, good["id"])["status"] == "sent"
    models.reject = True
    bad = make_ticket(client)
    prepare(client, "automatic")
    deliver(client, mailbox)
    rejected = get_ticket(client, bad["id"])
    assert (
        rejected["status"] == "manual"
        and rejected["result"]["reason"] == "grounding_failed"
    )
    assert len(mailbox.sent) == 1
    assert client.get("/api/metrics").json()["manual"] == 1


def test_gmail_reply_stays_in_thread_and_does_not_reply_to_an_auto_responder(mail_app):
    client, _, mailbox = mail_app
    mailbox.add("incoming")
    mailbox.add("auto-notification")
    mailbox.messages["incoming"]["payload"]["headers"].append(
        {"name": "Message-ID", "value": "<original@example.test>"}
    )
    mailbox.messages["auto-notification"]["payload"]["headers"].append(
        {"name": "Auto-Submitted", "value": "auto-generated"}
    )
    run(client, mailbox)
    prepare(client, "automatic")
    deliver(client, mailbox)
    assert len(mailbox.sent) == 1
    message, body = mailbox.sent[0]
    assert body["threadId"] == "thread-incoming"
    assert message["In-Reply-To"] == message["References"] == "<original@example.test>"
    assert message["Auto-Submitted"] == "auto-replied"
    assert not message["Cc"] and not message["Bcc"]
    assert sorted(t["status"] for t in client.get("/api/tickets").json()) == [
        "manual",
        "sent",
    ]


def test_switching_to_human_review_cancels_unsent_automatic_mail_and_allows_new_review(
    mail_app,
):
    client, _, mailbox = mail_app
    ticket_id = make_ticket(client)["id"]
    prepare(client, "automatic")
    assert client.put("/api/reply-settings", json={"mode": "human"}).status_code == 200
    deliver(client, mailbox)
    ticket = get_ticket(client, ticket_id)
    assert ticket["status"] == "review" and ticket["delivery"]["status"] == "cancelled"
    assert not mailbox.sent
    response = client.post(
        "/api/tickets/" + ticket_id + "/review",
        json={
            "decision": "approve",
            "text": "A person checked this updated response before sending.",
            "revision": ticket["revision"],
            "send": True,
        },
    )
    assert response.status_code == 200, response.text
    deliver(client, mailbox)
    assert get_ticket(client, ticket_id)["status"] == "sent" and len(mailbox.sent) == 1


def test_changed_knowledge_and_missing_email_never_send_automatically(mail_app):
    client, _, mailbox = mail_app
    first = make_ticket(client)
    no_email = client.post(
        "/api/tickets", json={**TICKET, "email": "", "channel": "internal"}
    ).json()
    prepare(client, "automatic")
    with client.app.state.sessions() as db:
        document = db.scalar(
            select(Document).where(Document.organization_id == org(client))
        )
        document.enabled = False
        db.commit()
    deliver(client, mailbox)
    assert not mailbox.sent
    assert get_ticket(client, first["id"])["status"] == "manual"
    assert get_ticket(client, no_email["id"])["status"] == "manual"


def test_ambiguous_send_is_reconciled_in_sent_without_another_post(mail_app):
    client, _, mailbox = mail_app
    ticket_id = make_ticket(client)["id"]
    prepare(client, "automatic")
    mailbox.fail_send = "timeout"
    deliver(client, mailbox)
    # The remote accepted the email but its response was lost. The worker's
    # read-only Sent lookup recovers its ID instead of sending another copy.
    assert get_ticket(client, ticket_id)["status"] == "sent"
    deliver(client, mailbox)
    assert len(mailbox.sent) == 1
    sends = [r for r in mailbox.calls if r.url.path.endswith("/messages/send")]
    assert len(sends) == 1


def test_definite_delivery_failure_requires_explicit_retry(mail_app):
    client, _, mailbox = mail_app
    ticket_id = make_ticket(client)["id"]
    prepare(client, "automatic")
    mailbox.fail_send = "rejected"
    deliver(client, mailbox)
    assert get_ticket(client, ticket_id)["delivery"]["status"] == "failed"
    deliver(client, mailbox)
    assert len([r for r in mailbox.calls if r.url.path.endswith("/messages/send")]) == 1
    mailbox.fail_send = None
    response = client.post("/api/tickets/" + ticket_id + "/retry-delivery")
    assert response.status_code == 200, response.text
    deliver(client, mailbox)
    assert get_ticket(client, ticket_id)["status"] == "sent"


def test_two_workers_cannot_send_the_same_approved_reply(mail_app):
    client, _, mailbox = mail_app
    make_ticket(client)
    prepare(client, "automatic")
    from supportops.db import OutboundReply

    with client.app.state.sessions() as db:
        reply_id = db.scalar(select(OutboundReply.id))

    def claim(_):
        return deliver_one(
            client.app.state.auth.config,
            client.app.state.sessions,
            reply_id,
            httpx.MockTransport(mailbox),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]
    assert len(mailbox.sent) == 1


def test_crash_during_send_is_not_replayed_when_sent_lookup_has_no_confirmation(
    mail_app,
):
    client, _, mailbox = mail_app
    ticket_id = make_ticket(client)["id"]
    prepare(client, "automatic")
    from supportops.db import OutboundReply

    with client.app.state.sessions() as db:
        row = db.scalar(select(OutboundReply))
        row.status, row.lease_until = "sending", utcnow() - timedelta(seconds=1)
        db.commit()
    deliver(client, mailbox)
    deliver(client, mailbox)
    ticket = get_ticket(client, ticket_id)
    assert ticket["status"] == "manual" and ticket["delivery"]["status"] == "unknown"
    assert not mailbox.sent
    assert (
        client.post("/api/tickets/" + ticket_id + "/retry-delivery").status_code == 409
    )


def test_exhausted_five_dollar_allowance_blocks_bulk_models_without_losing_requests(
    mail_app,
):
    client, models, mailbox = mail_app
    config = client.app.state.auth.config
    config.demo_release = True
    user_id = client.get("/api/auth/me").json()["user"]["id"]
    with client.app.state.sessions() as db:
        db.merge(
            DemoAccount(
                user_id=user_id,
                spent_microusd=5_000_000,
                reserved_microusd=0,
                limit_microusd=5_000_000,
            )
        )
        db.commit()
    ticket_id = make_ticket(client)["id"]
    calls = len(models.calls)
    prepare(client, "automatic")
    assert len(models.calls) == calls
    ticket = get_ticket(client, ticket_id)
    assert (
        ticket["status"] == "manual"
        and ticket["preparation"]["error"] == "ai_allowance_reached"
    )
    assert not mailbox.sent
    response = client.post(
        "/api/tickets/" + ticket_id + "/review",
        json={
            "decision": "approve",
            "manual": True,
            "text": "Here is a response written by a person.",
            "revision": ticket["revision"],
            "send": True,
        },
    )
    assert response.status_code == 200, response.text
    deliver(client, mailbox)
    assert get_ticket(client, ticket_id)["status"] == "sent"


def test_only_one_worker_can_prepare_a_job_and_reserved_runs_are_not_replayed_after_crash(
    mail_app,
):
    client, models, _ = mail_app
    first = make_ticket(client)
    assert (
        client.post("/api/tickets/prepare-all", json={"mode": "human"}).status_code
        == 200
    )
    with client.app.state.sessions() as db:
        job_id = db.scalar(select(PreparationJob.id))

    def claim(_):
        return preparation_one(
            client.app.state.auth.config,
            client.app.state.sessions,
            client.app.state,
            job_id,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]
    assert get_ticket(client, first["id"])["status"] == "review"
    second = make_ticket(client)
    client.post("/api/tickets/prepare-all", json={"mode": "human"})
    with client.app.state.sessions() as db:
        job = db.scalar(
            select(PreparationJob).where(PreparationJob.ticket_id == second["id"])
        )
        reservation_id = reserve(
            db, client.app.state.auth.config, org(client), user_id=job.actor_id
        )
        job.status, job.reservation_id, job.lease_until = (
            "processing",
            reservation_id,
            utcnow() - timedelta(seconds=1),
        )
        ticket = db.get(Ticket, (org(client), second["id"]))
        ticket.status = "processing"
        db.commit()
    calls = len(models.calls)
    preparation_pending(
        client.app.state.auth.config, client.app.state.sessions, client.app.state
    )
    assert len(models.calls) == calls
    assert get_ticket(client, second["id"])["status"] == "manual"
    with client.app.state.sessions() as db:
        assert db.get(CreditReservation, reservation_id).settled


def test_revoked_membership_stops_queued_deliveries(mail_app):
    client, _, mailbox = mail_app
    make_ticket(client)
    prepare(client, "automatic")
    user_id = client.get("/api/auth/me").json()["user"]["id"]
    with client.app.state.sessions() as db:
        db.get(Membership, (org(client), user_id)).active = False
        db.commit()
    deliver(client, mailbox)
    assert not mailbox.sent


def test_refresh_inbox_queues_gmail_and_leaves_api_requests_available_without_model_calls(
    mail_app,
):
    client, models, mailbox = mail_app
    incoming = make_ticket(client)
    calls = len(models.calls)
    response = client.post("/api/tickets/refresh-inbox")
    assert response.status_code == 200 and response.json()["gmail_queued"]
    assert get_ticket(client, incoming["id"])["status"] == "new"
    assert not mailbox.sent and len(models.calls) == calls


def test_bulk_controls_are_tenant_scoped_admin_gated_and_csrf_protected(platform):
    owner, other, employee = platform
    register(owner)
    b = register(other, "Other Business", "other@example.test")
    first, second = make_ticket(owner), make_ticket(other)
    invite = invitation(owner)
    assert accept(employee, invite).status_code == 200
    account(employee)
    assert (
        employee.put("/api/reply-settings", json={"mode": "automatic"}).status_code
        == 403
    )
    assert (
        employee.post(
            "/api/tickets/prepare-all", json={"mode": "automatic"}
        ).status_code
        == 403
    )
    assert (
        owner.post("/api/tickets/prepare-all", json={"mode": "automatic"}).status_code
        == 409
    )
    assert (
        owner.post(
            "/api/tickets/prepare-all",
            json={"mode": "human"},
            headers={"X-CSRF-Token": ""},
        ).status_code
        == 403
    )
    assert (
        owner.post(
            "/api/tickets/prepare-all",
            json={"mode": "human"},
            headers={"X-Workspace-ID": b["organization"]["id"]},
        ).status_code
        == 409
    )
    assert (
        owner.post("/api/tickets/prepare-all", json={"mode": "human"}).json()["queued"]
        == 1
    )
    assert get_ticket(owner, first["id"])["status"] == "queued"
    assert get_ticket(other, second["id"])["status"] == "new"
    assert owner.get("/api/tickets/" + second["id"]).status_code == 404
    assert owner.get("/api/reply-settings").json()["mode"] == "human"
