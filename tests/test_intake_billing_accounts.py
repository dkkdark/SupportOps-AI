import hashlib
import hmac
import json
import time
from datetime import timedelta
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from supportops.config import Settings
from supportops.main import create_app
from supportops.db import RecoveryToken, User, CreditReservation, UsageCounter
from supportops.auth import digest
from tests.test_workspaces import register, account, TICKET, PASSWORD, platform


def test_public_form_and_api_sources_route_and_deduplicate_requests(platform):
    owner, stranger, _ = platform
    register(owner)
    kind = owner.post(
        "/api/request-types",
        json={
            "name": "Vendor inquiry",
            "visibility": "public",
            "description": "Procurement and vendors",
        },
    ).json()
    form = owner.post(
        "/api/intakes",
        json={
            "name": "Talk to Procurement",
            "kind": "form",
            "request_type_id": kind["id"],
        },
    )
    assert form.status_code == 201, form.text
    c = form.json()
    slug = c["slug"]
    assert stranger.get("/api/forms/" + slug).json()["business_name"] == "Acme Services"
    assert stranger.get("/forms/" + slug).status_code == 200
    r = stranger.post("/api/forms/" + slug, json={**TICKET, "external_id": "form-123"})
    assert r.status_code == 202, r.text
    ref = r.json()["reference"]
    assert owner.get("/api/tickets/" + ref).json()["request_type_id"] == kind["id"]
    assert (
        stranger.post(
            "/api/forms/" + slug, json={**TICKET, "external_id": "form-123"}
        ).json()["reference"]
        == ref
    )
    assert (
        stranger.post(
            "/api/forms/" + slug, json={**TICKET, "assigned_to": "foreign"}
        ).status_code
        == 422
    )
    api = owner.post("/api/intakes", json={"name": "CRM import", "kind": "api"}).json()
    assert "token" not in owner.get("/api/intakes").text
    assert stranger.post("/api/inbound/" + api["slug"], json=TICKET).status_code == 401
    r = stranger.post(
        "/api/inbound/" + api["slug"],
        json={**TICKET, "external_id": "crm-1"},
        headers={
            "Authorization": "Bearer " + api["token"],
            "Origin": "https://a-crm.example.test",
        },
    )
    assert r.status_code == 202, r.text
    assert owner.get("/api/tickets/" + r.json()["reference"]).json()["channel"] == "api"
    assert owner.post("/api/intakes/" + api["id"] + "/revoke").status_code == 200
    assert (
        stranger.post(
            "/api/inbound/" + api["slug"],
            json=TICKET,
            headers={"Authorization": "Bearer " + api["token"]},
        ).status_code
        == 404
    )
    assert stranger.get("/api/tickets/" + ref).status_code == 401


def test_intakes_and_routing_cannot_cross_workspace_boundaries(platform):
    a, b, _ = platform
    aa = register(a)
    register(b, "Bright Studio", "olivia@bright.test")
    private = a.post("/api/request-types", json={"name": "Internal IT request"}).json()
    assert (
        a.post(
            "/api/intakes",
            json={
                "name": "Bad public form",
                "kind": "form",
                "request_type_id": private["id"],
            },
        ).status_code
        == 422
    )
    assert (
        b.post(
            "/api/intakes",
            json={
                "name": "Other company type",
                "kind": "api",
                "request_type_id": private["id"],
            },
        ).status_code
        == 422
    )
    t = a.post("/api/tickets", json=TICKET).json()
    assert (
        b.patch(
            "/api/tickets/" + t["id"] + "/routing", json={"revision": t["revision"]}
        ).status_code
        == 404
    )
    assert (
        a.patch(
            "/api/tickets/" + t["id"] + "/routing",
            json={
                "revision": t["revision"],
                "request_type_id": private["id"],
                "assigned_to": aa["user"]["id"],
            },
        ).status_code
        == 200
    )
    assigned = a.get("/api/tickets/" + t["id"]).json()
    assert assigned["assigned_to"] == aa["user"]["id"]
    assert (
        a.patch(
            "/api/tickets/" + t["id"] + "/routing", json={"revision": 0}
        ).status_code
        == 409
    )
    assert all(p["id"] != aa["user"]["id"] for p in b.get("/api/directory").json())


class Stripe:
    def __init__(self):
        self.status = "active"
        self.customer = "cus_acme"
        self.calls = []

    def __call__(self, r):
        self.calls.append((r.method, r.url.path))
        if "/prices/" in r.url.path:
            return httpx.Response(
                200,
                json={
                    "id": "price_starter",
                    "currency": "usd",
                    "unit_amount": 4900,
                    "active": True,
                    "recurring": {"interval": "month", "interval_count": 1},
                },
            )
        if r.url.path.endswith("/customers"):
            return httpx.Response(200, json={"id": self.customer})
        if "/subscriptions/" in r.url.path:
            return httpx.Response(
                200,
                json={
                    "id": "sub_acme",
                    "customer": self.customer,
                    "status": self.status,
                    "items": {
                        "data": [
                            {
                                "price": {"id": "price_starter"},
                                "current_period_start": int(time.time()) - 10,
                                "current_period_end": int(time.time()) + 86400 * 30,
                            }
                        ]
                    },
                },
            )
        if "billing_portal" in r.url.path:
            return httpx.Response(
                200, json={"url": "https://billing.stripe.com/p/test"}
            )
        return httpx.Response(
            200,
            json={
                "id": "cs_acme",
                "url": "https://checkout.stripe.com/c/pay/test",
                "expires_at": int(time.time()) + 1800,
            },
        )


def signed(event, secret="test-webhook"):
    raw = json.dumps(event, separators=(",", ":")).encode()
    ts = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), ts.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()
    return raw, {
        "Stripe-Signature": f"t={ts},v1={signature}",
        "Content-Type": "application/json",
    }


def test_stripe_checkout_verified_webhooks_entitlements_and_idempotency(tmp_path):
    stripe = Stripe()
    cfg = Settings(
        database_url=f"sqlite:///{tmp_path}/billing.db",
        stripe_secret_key="test-stripe-key",
        stripe_webhook_secret="test-webhook",
        stripe_starter_price_id="price_starter",
    )
    with TestClient(
        create_app(cfg, billing_transport=httpx.MockTransport(stripe))
    ) as c:
        register(c)
        r = c.post("/api/billing/checkout", json={"plan": "starter"})
        assert r.status_code == 200, r.text
        assert r.json()["url"].startswith("https://checkout.stripe.com/")
        assert (
            c.get("/api/billing").json()["plan"]["plan"] == "pilot"
        )  # Redirect is never proof of payment.
        event = {
            "id": "evt_1",
            "type": "customer.subscription.created",
            "created": int(time.time()),
            "data": {"object": {"id": "sub_acme", "customer": "cus_acme"}},
        }
        raw, headers = signed(event)
        assert c.post("/api/webhooks/stripe", content=raw).status_code == 400
        assert (
            c.post("/api/webhooks/stripe", content=raw, headers=headers).status_code
            == 200
        )
        assert c.get("/api/billing").json()["plan"]["plan"] == "starter"
        assert c.post("/api/webhooks/stripe", content=raw, headers=headers).json()[
            "duplicate"
        ]
        assert (
            c.post("/api/billing/checkout", json={"plan": "starter"}).status_code == 409
        )
        assert (
            c.post("/api/billing/portal")
            .json()["url"]
            .startswith("https://billing.stripe.com/")
        )
        stripe.status = "past_due"
        event["id"] = "evt_2"
        event["type"] = "invoice.payment_failed"
        event["data"]["object"] = {"customer": "cus_acme", "subscription": "sub_acme"}
        raw, headers = signed(event)
        assert (
            c.post("/api/webhooks/stripe", content=raw, headers=headers).status_code
            == 200
        )
        assert c.get("/api/billing").json()["plan"]["status"] == "past_due"


def test_recovery_tokens_expire_are_single_use_and_revoke_sessions(
    platform, monkeypatch
):
    from supportops.accounts import send_token

    a, b, _ = platform
    info = register(a)
    config = a.app.state.auth.config.model_copy(
        update={"smtp_host": "smtp.example.test", "smtp_from": "hello@example.test"}
    )
    sent = []
    monkeypatch.setattr(
        "supportops.accounts.deliver",
        lambda cfg, to, subject, body: sent.append(body) or True,
    )
    send_token(config, a.app.state.sessions, info["user"]["id"], "reset")
    assert len(sent) == 1
    token = sent[0].split("/#reset/")[1].split()[0]
    assert (
        b.post(
            "/api/auth/reset-password",
            json={"token": token, "password": "A new secure password 456!"},
        ).status_code
        == 200
    )
    assert a.get("/api/tickets").status_code == 401
    assert (
        b.post(
            "/api/auth/reset-password",
            json={"token": token, "password": "A different password 789!"},
        ).status_code
        == 410
    )
    assert (
        b.post(
            "/api/auth/login",
            json={"email": "alex@acme.test", "password": "A new secure password 456!"},
        ).status_code
        == 200
    )
    send_token(config, a.app.state.sessions, info["user"]["id"], "verify")
    token = sent[-1].split("/#verify/")[1].split()[0]
    assert b.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    assert b.get("/api/auth/me").json()["user"]["email_verified"]
    r = b.post("/api/auth/forgot-password", json={"email": "unknown@example.test"})
    assert r.status_code == 202 and "token" not in r.text


def test_expired_recovery_token_and_disabled_checkout_are_rejected(platform):
    a, b, _ = platform
    info = register(a)
    with a.app.state.sessions() as db:
        db.add(
            RecoveryToken(
                token_hash=digest("x" * 32),
                user_id=info["user"]["id"],
                purpose="reset",
                expires_at=__import__("supportops.db", fromlist=["utcnow"]).utcnow()
                - timedelta(seconds=1),
                used=False,
            )
        )
        db.commit()
    assert (
        b.post(
            "/api/auth/reset-password", json={"token": "x" * 32, "password": PASSWORD}
        ).status_code
        == 410
    )
    assert a.post("/api/billing/checkout", json={"plan": "starter"}).status_code == 503
