"""Demo release: actual-dollar limits, shared account scope and launch markers."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from supportops.config import Settings
from supportops.main import create_app
from supportops.billing import reserve, settle, reservation_budget
from supportops.db import (
    DemoAccount,
    CreditReservation,
    IndexJob,
    Organization,
    UsageCounter,
    utcnow,
)
from tests.test_agent_semantic import Models
from tests.test_workspaces import register, make_ticket, POLICY


@pytest.fixture
def demo_client(tmp_path):
    models = Models()
    config = Settings(
        database_url=f"sqlite:///{tmp_path}/demo.db",
        demo_release=True,
        model_provider="openai",
        embedding_provider="openai",
        openai_api_key="synthetic",
        stripe_secret_key="should-not-be-used",
        stripe_webhook_secret="disabled",
        stripe_starter_price_id="disabled",
    )
    transport = httpx.MockTransport(models)
    app = create_app(config, llm_transport=transport, embedding_transport=transport)
    with TestClient(app) as client:
        info = register(client)
        yield client, models, config, info


def test_demo_hides_and_disables_payments_even_with_keys(demo_client):
    c, _, _, _ = demo_client
    assert c.get("/api/config").json()["demo_release"] is True
    assert c.get("/api/plans").json() == []
    assert c.post("/api/billing/checkout", json={"plan": "starter"}).status_code == 403
    assert c.post("/api/billing/portal").status_code == 403
    assert c.post("/api/webhooks/stripe", json={}).status_code == 503
    assert not any(
        x["name"] == "Paid subscriptions"
        for x in c.get("/api/launch-status").json()["checks"]
    )
    assert c.get("/api/billing").json()["limit_usd"] == 5


def test_budget_tracks_real_cost_across_workspaces_without_credit_rounding(demo_client):
    c, models, config, info = demo_client
    uid, oid = info["user"]["id"], info["organization"]["id"]
    assert c.post("/api/knowledge", json=POLICY).status_code == 201
    assert models.calls
    with c.app.state.sessions() as db:
        assert db.scalar(select(IndexJob)).requested_by == uid
        row = db.get(DemoAccount, uid)
        assert (
            row.spent_microusd == 6
        )  # 40 embedding tokens × $0.13 / million, rounded to microusd.
        rid = reserve(db, config, oid, user_id=uid)
        db.commit()
    settle(c.app.state.sessions, rid, {"cost_usd": 0.012345})
    settle(c.app.state.sessions, rid, {"cost_usd": 0.012345})  # idempotent
    before = c.get("/api/billing").json()
    assert before["spent_usd"] == pytest.approx(0.012351)
    assert before["reserved_usd"] == 0
    assert (
        c.post("/api/organizations", json={"name": "Another business"}).status_code
        == 201
    )
    current = c.get("/api/auth/me").json()
    c.headers["X-Workspace-ID"] = current["organization"]["id"]
    assert c.get("/api/billing").json()["spent_usd"] == before["spent_usd"]


def test_exhausted_budget_marks_user_and_blocks_only_paid_calls(demo_client):
    c, models, config, info = demo_client
    uid, oid = info["user"]["id"], info["organization"]["id"]
    c.post("/api/knowledge", json=POLICY)
    with c.app.state.sessions() as db:
        row = db.get(DemoAccount, uid)
        row.spent_microusd = 4_999_999
        db.commit()
        rid = reserve(db, config, oid, user_id=uid)
        assert reservation_budget(db.get(CreditReservation, rid)) == 0.000001
        db.commit()
    settle(c.app.state.sessions, rid, {"cost_usd": 0.000001})
    with c.app.state.sessions() as db:
        row = db.get(DemoAccount, uid)
        assert row.limit_reached_at and row.launch_email_requested_at
        assert row.launch_email_sent_at is None
        assert row.notification_reason == "demo_budget_exhausted"
    before = len(models.calls)
    ticket = make_ticket(c)
    r = c.post("/api/tickets/" + ticket["id"] + "/analyze", json={})
    assert r.status_code == 429 and "we'll email you" in r.json()["detail"]
    assert len(models.calls) == before
    policy = {**POLICY, "key": "second", "title": "Another policy"}
    assert c.post("/api/knowledge", json=policy).status_code == 201
    assert len(models.calls) == before
    docs = c.get("/api/knowledge").json()
    assert any(d["indexing"]["error"] == "demo_budget_exhausted" for d in docs)
    assert c.get("/api/tickets/" + ticket["id"]).status_code == 200
    assert c.get("/api/billing").json()["limit_reached"] is True
    # Server-side quota bypass is limited to the requested demo release.
    with c.app.state.sessions() as db:
        org = db.get(Organization, oid)
        org.created_at = utcnow() - timedelta(days=60)
        db.add(
            UsageCounter(
                organization_id=oid, period="trial", metric="requests", value=300
            )
        )
        db.commit()
    assert make_ticket(c)["id"]
    for index in range(4):
        assert (
            c.post(
                "/api/invitations", json={"email": f"team{index}@example.test"}
            ).status_code
            == 201
        )


def test_reservations_prevent_parallel_overspending_and_refund_known_usage(demo_client):
    c, _, config, info = demo_client
    uid, oid = info["user"]["id"], info["organization"]["id"]

    def claim(_):
        with c.app.state.sessions() as db:
            rid = reserve(db, config, oid, amount=600, user_id=uid)
            value = reservation_budget(db.get(CreditReservation, rid))
            db.commit()
            return rid, value

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert sum(value for _, value in claims) == 5
    with c.app.state.sessions() as db:
        with pytest.raises(HTTPException) as exc:
            reserve(db, config, oid, user_id=uid)
        assert exc.value.status_code == 429
        assert not db.get(DemoAccount, uid).limit_reached_at  # still in flight
    settle(c.app.state.sessions, claims[0][0], {"cost_usd": 0.01})
    settle(c.app.state.sessions, claims[1][0], {}, uncertain=True)
    result = c.get("/api/billing").json()
    assert result["spent_usd"] == pytest.approx(0.01 + claims[1][1])
    assert result["reserved_usd"] == 0


def test_notification_signup_is_idempotent_and_account_private(demo_client):
    c, _, _, info = demo_client
    assert c.post("/api/demo/launch-notification").status_code == 200
    with c.app.state.sessions() as db:
        stamp = db.get(DemoAccount, info["user"]["id"]).launch_email_requested_at
    assert c.post("/api/demo/launch-notification").status_code == 200
    with c.app.state.sessions() as db:
        row = db.get(DemoAccount, info["user"]["id"])
        assert (
            row.launch_email_requested_at == stamp
            and row.notification_reason == "launch_requested"
        )
    with TestClient(c.app) as other:
        second = register(other, "Second company", "olivia@second.test")
        balance = other.get("/api/billing").json()
        assert balance["spent_usd"] == 0 and not balance["launch_email_requested"]
        with c.app.state.sessions() as db:
            assert db.get(DemoAccount, second["user"]["id"]).spent_microusd == 0
