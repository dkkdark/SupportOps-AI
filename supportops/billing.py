"""Subscriptions, usage reservations and verified Stripe lifecycle events."""

import hashlib
import hmac
import json
import math
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from supportops.auth import Identity, aware, identifier
from supportops.db import (
    Organization,
    Subscription,
    UsageCounter,
    WebhookReceipt,
    CreditReservation,
    utcnow,
)

CREDIT_USD = 0.005
PLANS = {
    "pilot": {
        "name": "Trial",
        "price_usd": 0,
        "credits": 100,
        "seats": 3,
        "requests": 300,
        "chunks": 300,
    },
    "starter": {
        "name": "Starter",
        "price_usd": 49,
        "credits": 1000,
        "seats": 5,
        "requests": 10000,
        "chunks": 1000,
    },
    "growth": {
        "name": "Growth",
        "price_usd": 149,
        "credits": 5000,
        "seats": 20,
        "requests": 50000,
        "chunks": 5000,
    },
    "scale": {
        "name": "Scale",
        "price_usd": 399,
        "credits": 15000,
        "seats": 50,
        "requests": 100000,
        "chunks": 15000,
    },
}


def entitlement(db, org_id):
    org = db.get(Organization, org_id)
    sub = db.get(Subscription, org_id)
    now = int(time.time())
    if org.is_demo:
        return {
            **PLANS["scale"],
            "name": "Demo workspace",
            "plan": "demo",
            "period": "demo",
            "reset_at": None,
            "ai_enabled": True,
            "status": "demo",
        }
    paid = (
        sub
        and sub.plan in PLANS
        and sub.status == "active"
        and sub.period_end
        and sub.period_end > now
    )
    if paid:
        return {
            **PLANS[sub.plan],
            "plan": sub.plan,
            "period": str(sub.period_start),
            "reset_at": datetime.fromtimestamp(
                sub.period_end, timezone.utc
            ).isoformat(),
            "ai_enabled": True,
            "status": sub.status,
        }
    expires = aware(org.created_at) + timedelta(days=14)
    return {
        **PLANS["pilot"],
        "plan": "pilot",
        "period": "trial",
        "reset_at": expires.isoformat(),
        "ai_enabled": utcnow() < expires,
        "status": sub.status if sub else "trial",
    }


def counter(db, org_id, period, metric):
    from sqlalchemy.dialects.sqlite import insert as si
    from sqlalchemy.dialects.postgresql import insert as pi

    insert = si if db.bind.dialect.name == "sqlite" else pi
    db.execute(
        insert(UsageCounter)
        .values(organization_id=org_id, period=period, metric=metric, value=0)
        .on_conflict_do_nothing()
    )


def consume(db, org_id, metric, amount=1, config=None):
    if config and config.demo_release and metric != "credits":
        return {}
    plan = entitlement(db, org_id)
    if plan["plan"] == "demo":
        return plan
    if metric == "credits" and not plan["ai_enabled"]:
        raise HTTPException(
            402,
            "Your trial or subscription has ended. Choose a plan to continue AI processing.",
        )
    counter(db, org_id, plan["period"], metric)
    if not db.execute(
        update(UsageCounter)
        .where(
            UsageCounter.organization_id == org_id,
            UsageCounter.period == plan["period"],
            UsageCounter.metric == metric,
            UsageCounter.value + amount <= plan[metric],
        )
        .values(value=UsageCounter.value + amount)
    ).rowcount:
        raise HTTPException(
            402,
            f"Workspace {metric} limit reached. Upgrade your plan or wait for renewal. Manual request review remains available.",
        )
    return plan


def reserve(db, config, org_id, amount=None, user_id=None):
    if config.demo_release:
        from supportops.demo_budget import reserve as demo_reserve

        return demo_reserve(db, config, org_id, user_id, amount)
    if amount is None:
        entitlement_data = entitlement(db, org_id)
        counter(db, org_id, entitlement_data["period"], "credits")
        used = db.get(
            UsageCounter, (org_id, entitlement_data["period"], "credits")
        ).value
        amount = max(
            1, min(config.agent_run_credit_reserve, entitlement_data["credits"] - used)
        )
    plan = consume(db, org_id, "credits", amount)
    if plan["plan"] == "demo":
        return None
    r = CreditReservation(
        id=identifier(),
        organization_id=org_id,
        period=plan["period"],
        reserved=amount,
        settled=False,
    )
    db.add(r)
    db.flush()
    return r.id


def settle(sessions, reservation_id, usage, uncertain=False):
    if not reservation_id:
        return
    with sessions() as db:
        r = db.get(CreditReservation, reservation_id)
        if not r or r.settled:
            return
        if r.demo_reserved_microusd is not None:
            from supportops.demo_budget import settle as demo_settle

            demo_settle(db, r, usage, uncertain)
            db.commit()
            return
        actual = (
            r.reserved
            if uncertain
            else min(
                r.reserved,
                max(0, math.ceil(float(usage.get("cost_usd", 0)) / CREDIT_USD)),
            )
        )
        claimed = db.execute(
            update(CreditReservation)
            .where(CreditReservation.id == r.id, CreditReservation.settled.is_(False))
            .values(
                settled=True,
                charged=actual,
                usage={**usage, "reconciliation_required": uncertain},
            )
        ).rowcount
        if claimed:
            db.execute(
                update(UsageCounter)
                .where(
                    UsageCounter.organization_id == r.organization_id,
                    UsageCounter.period == r.period,
                    UsageCounter.metric == "credits",
                )
                .values(value=UsageCounter.value - (r.reserved - actual))
            )
        db.commit()


def reservation_budget(row):
    return (
        row.demo_reserved_microusd / 1_000_000
        if row.demo_reserved_microusd is not None
        else row.reserved * CREDIT_USD
    )


def billing_state(db, config, org_id):
    plan = entitlement(db, org_id)
    rows = db.scalars(
        select(UsageCounter).where(
            UsageCounter.organization_id == org_id,
            UsageCounter.period == plan["period"],
        )
    ).all()
    used = {r.metric: r.value for r in rows}
    sub = db.get(Subscription, org_id)
    return {
        "plan": plan,
        "used": used,
        "credits_remaining": max(0, plan["credits"] - used.get("credits", 0)),
        "checkout_enabled": bool(
            config.stripe_secret_key and config.stripe_webhook_secret
        ),
        "portal_enabled": bool(
            sub and sub.stripe_customer and config.stripe_secret_key
        ),
        "reservation_credits": config.agent_run_credit_reserve,
        "credit_policy": "AI use is charged from actual model tokens, including embeddings and verification. A temporary credit reserve is reconciled after each run. Requests with an unknown provider charge retain the reserve for reconciliation. Credits renew with a paid billing period; trial credits are one-time. No automatic overage charges.",
    }


def price_ids(config):
    return {
        "starter": config.stripe_starter_price_id,
        "growth": config.stripe_growth_price_id,
        "scale": config.stripe_scale_price_id,
    }


def stripe_call(config, method, path, data=None, transport=None, key=None):
    if not config.stripe_secret_key:
        raise HTTPException(
            503, "Billing is not configured yet. Contact the service operator."
        )
    try:
        with httpx.Client(
            base_url="https://api.stripe.com/v1/",
            headers={
                "Authorization": "Bearer " + config.stripe_secret_key,
                **({"Idempotency-Key": key} if key else {}),
            },
            transport=transport,
            timeout=15,
        ) as c:
            r = c.request(method, path, data=data)
            if not r.is_success:
                raise HTTPException(
                    502,
                    "The payment provider could not complete this request. No subscription has been changed here.",
                )
            return r.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            502, "The payment provider is temporarily unavailable."
        ) from None


class CheckoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: str


def billing_router(auth, sessions, config, transport=None):
    router = APIRouter(prefix="/api")

    @router.get("/plans")
    def plans():
        if config.demo_release:
            return []
        return [
            {
                "id": id,
                **p,
                "currency": "USD",
                "interval": "month",
                "checkout_available": bool(
                    config.stripe_secret_key
                    and config.stripe_webhook_secret
                    and price_ids(config).get(id)
                ),
            }
            for id, p in PLANS.items()
        ]

    @router.get("/billing")
    def state(actor: Identity = Depends(auth.current)):
        with sessions() as db:
            if config.demo_release:
                from supportops.demo_budget import state as demo_state

                return demo_state(db, config, actor.user_id)
            return billing_state(db, config, actor.organization_id)

    @router.post("/billing/checkout")
    def checkout(body: CheckoutInput, actor: Identity = Depends(auth.admin)):
        if config.demo_release:
            raise HTTPException(
                403, "Purchasing is not available during the demo release."
            )
        auth.ensure_verified(actor)
        from supportops.launch import legal_ready

        if config.app_env == "production" and not legal_ready(config):
            raise HTTPException(
                503,
                "The service operator must complete and approve the legal policies before enabling paid checkout.",
            )
        if actor.role != "owner":
            raise HTTPException(
                403, "Only the workspace owner can manage a subscription."
            )
        if actor.organization["is_demo"]:
            raise HTTPException(
                403, "Register your business before choosing a paid plan."
            )
        price = price_ids(config).get(body.plan)
        if not price or not config.stripe_webhook_secret:
            raise HTTPException(
                503, "This plan is awaiting payment setup. No payment can be taken yet."
            )
        detail = stripe_call(config, "GET", "prices/" + price, transport=transport)
        if (
            detail.get("currency") != "usd"
            or detail.get("unit_amount") != PLANS[body.plan]["price_usd"] * 100
            or detail.get("recurring", {}).get("interval") != "month"
            or detail.get("recurring", {}).get("interval_count", 1) != 1
            or not detail.get("active")
        ):
            raise HTTPException(
                503, "The configured payment price does not match this published plan."
            )
        with sessions() as db:
            db.scalar(
                select(Organization)
                .where(Organization.id == actor.organization_id)
                .with_for_update()
            )
            sub = db.get(Subscription, actor.organization_id)
            if not sub:
                sub = Subscription(organization_id=actor.organization_id)
                db.add(sub)
                db.flush()
            if sub.status == "active":
                raise HTTPException(
                    409, "Use Manage subscription to change an existing paid plan."
                )
            if (
                sub.checkout_url
                and sub.checkout_expires
                and sub.checkout_expires > int(time.time())
            ):
                return {"url": sub.checkout_url}
            if not sub.stripe_customer:
                customer = stripe_call(
                    config,
                    "POST",
                    "customers",
                    {
                        "email": actor.email,
                        "name": actor.organization["name"],
                        "metadata[organization_id]": actor.organization_id,
                    },
                    transport,
                    key="customer-" + actor.organization_id,
                )
                sub.stripe_customer = customer["id"]
            session = stripe_call(
                config,
                "POST",
                "checkout/sessions",
                {
                    "mode": "subscription",
                    "automatic_tax[enabled]": str(config.stripe_automatic_tax).lower(),
                    "customer": sub.stripe_customer,
                    "line_items[0][price]": price,
                    "line_items[0][quantity]": "1",
                    "success_url": config.public_base_url.rstrip("/") + "/#billing",
                    "cancel_url": config.public_base_url.rstrip("/") + "/#billing",
                    "client_reference_id": actor.organization_id,
                    "subscription_data[metadata][organization_id]": actor.organization_id,
                },
                transport,
                key=f"checkout-{actor.organization_id}-{int(time.time()) // 3600}",
            )
            url = session.get("url", "")
            if (
                urlparse(url).scheme != "https"
                or urlparse(url).hostname != "checkout.stripe.com"
            ):
                raise HTTPException(
                    502, "Payment provider returned an invalid checkout URL."
                )
            sub.checkout_url = url
            sub.checkout_expires = session.get("expires_at", int(time.time()) + 1800)
            db.commit()
            return {"url": url}

    @router.post("/demo/launch-notification")
    def launch_notification(actor: Identity = Depends(auth.current)):
        if not config.demo_release:
            raise HTTPException(404, "Demo notification signup is unavailable.")
        from supportops.demo_budget import account

        with sessions() as db:
            row = account(db, config, actor.user_id)
            row.launch_email_opt_out = False
            if not row.launch_email_requested_at:
                row.launch_email_requested_at = utcnow()
                row.notification_reason = "launch_requested"
            db.commit()
        return {"launch_email_requested": True}

    @router.delete("/demo/launch-notification")
    def cancel_launch_notification(actor: Identity = Depends(auth.current)):
        from supportops.demo_budget import account

        with sessions() as db:
            row = account(db, config, actor.user_id)
            row.launch_email_opt_out = True
            row.launch_email_requested_at = None
            row.notification_reason = None
            db.commit()
        return {"launch_email_requested": False}

    @router.post("/billing/portal")
    def portal(actor: Identity = Depends(auth.admin)):
        if config.demo_release:
            raise HTTPException(
                403, "Purchasing is not available during the demo release."
            )
        if actor.role != "owner":
            raise HTTPException(403, "Only the workspace owner can manage billing.")
        with sessions() as db:
            sub = db.get(Subscription, actor.organization_id)
            if not sub or not sub.stripe_customer:
                raise HTTPException(409, "No payment customer is connected yet.")
            result = stripe_call(
                config,
                "POST",
                "billing_portal/sessions",
                {
                    "customer": sub.stripe_customer,
                    "return_url": config.public_base_url.rstrip("/") + "/#billing",
                },
                transport,
            )
            if urlparse(result.get("url", "")).hostname != "billing.stripe.com":
                raise HTTPException(502, "Invalid billing portal URL.")
            return {"url": result["url"]}

    @router.post("/webhooks/stripe")
    async def webhook(request: Request):
        if config.demo_release:
            raise HTTPException(503, "Payments are disabled during the demo release.")
        if not config.stripe_webhook_secret:
            raise HTTPException(503, "Payment webhooks are not configured.")
        raw = await request.body()
        signature = request.headers.get("Stripe-Signature", "")
        try:
            parts = [v.split("=", 1) for v in signature.split(",")]
            timestamp = next(v for k, v in parts if k == "t")
            provided = [v for k, v in parts if k == "v1"]
            if abs(time.time() - int(timestamp)) > 300:
                raise ValueError()
            expected = hmac.new(
                config.stripe_webhook_secret.encode(),
                timestamp.encode() + b"." + raw,
                hashlib.sha256,
            ).hexdigest()
            if not any(hmac.compare_digest(expected, v) for v in provided):
                raise ValueError()
            event = json.loads(raw)
            event_id = event["id"]
            kind = event["type"]
            obj = event["data"]["object"]
        except (ValueError, KeyError, StopIteration, TypeError):
            raise HTTPException(
                400, "Invalid payment webhook signature or payload."
            ) from None
        with sessions() as db:
            if db.get(WebhookReceipt, event_id):
                return {"received": True, "duplicate": True}
            if kind in (
                "customer.subscription.created",
                "customer.subscription.updated",
                "customer.subscription.deleted",
                "invoice.paid",
                "invoice.payment_failed",
                "checkout.session.completed",
            ):
                customer = obj.get("customer")
                sub = db.scalar(
                    select(Subscription)
                    .where(Subscription.stripe_customer == customer)
                    .with_for_update()
                )
                if sub:
                    sid = (
                        obj.get("id")
                        if kind.startswith("customer.subscription.")
                        else obj.get("subscription")
                        or obj.get("parent", {})
                        .get("subscription_details", {})
                        .get("subscription")
                    )
                    if sid:
                        current = (
                            obj
                            if kind == "customer.subscription.deleted"
                            else stripe_call(
                                config,
                                "GET",
                                "subscriptions/" + sid,
                                transport=transport,
                            )
                        )
                        items = current.get("items", {}).get("data", [])
                        item = items[0] if items else {}
                        pid = item.get("price", {}).get("id")
                        plan = next(
                            (
                                id
                                for id, value in price_ids(config).items()
                                if value == pid
                            ),
                            None,
                        )
                        if current.get("customer") != sub.stripe_customer:
                            raise HTTPException(400, "Subscription customer mismatch.")
                        if event.get("created", 0) >= sub.latest_event_at:
                            sub.stripe_subscription = sid
                            sub.status = (
                                "canceled"
                                if kind == "customer.subscription.deleted"
                                else current.get("status", "incomplete")
                            )
                            sub.plan = plan or "pilot"
                            sub.period_start = item.get(
                                "current_period_start",
                                current.get("current_period_start"),
                            )
                            sub.period_end = item.get(
                                "current_period_end", current.get("current_period_end")
                            )
                            sub.latest_event_at = event.get("created", 0)
                            sub.updated_at = utcnow()
                            sub.checkout_url = None
                            sub.checkout_expires = None
            db.add(WebhookReceipt(id=event_id))
            db.commit()
        return {"received": True}

    return router


def seat_available(db, org_id, email=None, config=None):
    if config and config.demo_release:
        return
    from sqlalchemy import func
    from supportops.db import Membership, Invitation

    db.scalar(select(Organization).where(Organization.id == org_id).with_for_update())
    plan = entitlement(db, org_id)
    if plan["plan"] == "demo":
        return
    active = (
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.organization_id == org_id, Membership.active.is_(True))
        )
        or 0
    )
    stmt = (
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.organization_id == org_id,
            Invitation.accepted.is_(False),
            Invitation.revoked.is_(False),
            Invitation.expires_at > utcnow(),
        )
    )
    if email:
        stmt = stmt.where(Invitation.email != email)
    pending = db.scalar(stmt) or 0
    if active + pending >= plan["seats"]:
        raise HTTPException(
            402,
            "Your plan’s team-seat limit is reached. Revoke an unused invitation or upgrade your plan.",
        )
