"""Account-wide lifetime demo allowance, atomically reserved before paid calls."""

from decimal import Decimal, ROUND_CEILING
from fastapi import HTTPException
from sqlalchemy import update
from supportops.auth import identifier
from supportops.db import CreditReservation, DemoAccount, Membership, utcnow

MICRO = 1_000_000
DEMO_MESSAGE = (
    "SupportOps AI is in demo. You've reached your demo AI spending limit. "
    "Paid AI processing is paused. Purchasing will be available soon — we'll email you when it launches. "
    "You can still use your workspace and handle requests manually."
)


def microusd(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid model usage cost")
    return int((amount * MICRO).to_integral_value(rounding=ROUND_CEILING))


def account(db, config, user_id):
    from sqlalchemy.dialects.sqlite import insert as si
    from sqlalchemy.dialects.postgresql import insert as pi

    insert = si if db.bind.dialect.name == "sqlite" else pi
    db.execute(
        insert(DemoAccount)
        .values(
            user_id=user_id,
            spent_microusd=0,
            reserved_microusd=0,
            limit_microusd=microusd(config.demo_ai_budget_usd),
        )
        .on_conflict_do_nothing()
    )
    return db.get(DemoAccount, user_id, populate_existing=True)


def mark_limit(db, user_id):
    db.execute(
        update(DemoAccount)
        .where(
            DemoAccount.user_id == user_id,
            DemoAccount.spent_microusd >= DemoAccount.limit_microusd,
            DemoAccount.limit_reached_at.is_(None),
        )
        .values(
            limit_reached_at=utcnow(),
            launch_email_requested_at=utcnow(),
            notification_reason="demo_budget_exhausted",
        )
    )


def reserve(db, config, org_id, user_id, amount=None):
    # Fail closed for a missing or revoked initiator, including background jobs.
    member = db.get(Membership, (org_id, user_id)) if user_id else None
    if not member or not member.active:
        raise HTTPException(
            403, "A current workspace member must initiate paid AI processing."
        )
    row = account(db, config, user_id)
    available = max(0, row.limit_microusd - row.spent_microusd - row.reserved_microusd)
    desired = microusd(
        config.agent_provider_cost_limit if amount is None else amount * 0.005
    )
    reserved = min(desired, available)
    if not reserved:
        mark_limit(db, user_id)
        db.commit()  # Retain the notification marker even when the HTTP request fails.
        raise HTTPException(
            429,
            DEMO_MESSAGE
            if row.spent_microusd >= row.limit_microusd
            else "Your available demo AI budget is reserved by running work. Please wait for it to finish.",
        )
    if not db.execute(
        update(DemoAccount)
        .where(
            DemoAccount.user_id == user_id,
            DemoAccount.spent_microusd + DemoAccount.reserved_microusd + reserved
            <= DemoAccount.limit_microusd,
        )
        .values(reserved_microusd=DemoAccount.reserved_microusd + reserved)
    ).rowcount:
        raise HTTPException(
            429,
            "Your demo AI budget changed during another request. Refresh and try again.",
        )
    reservation = CreditReservation(
        id=identifier(),
        organization_id=org_id,
        user_id=user_id,
        period="demo-account",
        reserved=(reserved + 4999) // 5000,
        demo_reserved_microusd=reserved,
        settled=False,
    )
    db.add(reservation)
    db.flush()
    return reservation.id


def settle(db, reservation, usage, uncertain):
    amount = reservation.demo_reserved_microusd
    # Unknown billing retains the reservation for operator reconciliation.
    charged = amount if uncertain else microusd(usage.get("cost_usd", 0))
    if db.execute(
        update(CreditReservation)
        .where(
            CreditReservation.id == reservation.id,
            CreditReservation.settled.is_(False),
        )
        .values(
            settled=True,
            demo_charged_microusd=charged,
            charged=(charged + 4999) // 5000,
            usage={**usage, "reconciliation_required": uncertain},
        )
    ).rowcount:
        db.execute(
            update(DemoAccount)
            .where(DemoAccount.user_id == reservation.user_id)
            .values(
                reserved_microusd=DemoAccount.reserved_microusd - amount,
                spent_microusd=DemoAccount.spent_microusd + charged,
            )
        )
        mark_limit(db, reservation.user_id)


def state(db, config, user_id):
    row = account(db, config, user_id)
    mark_limit(db, user_id)
    db.flush()
    db.refresh(row)
    result = {
        "demo_release": True,
        "limit_usd": row.limit_microusd / MICRO,
        "spent_usd": row.spent_microusd / MICRO,
        "reserved_usd": row.reserved_microusd / MICRO,
        "remaining_usd": max(
            0, row.limit_microusd - row.spent_microusd - row.reserved_microusd
        )
        / MICRO,
        "limit_reached": row.spent_microusd >= row.limit_microusd,
        "launch_email_requested": bool(row.launch_email_requested_at),
        "message": DEMO_MESSAGE
        if row.limit_reached_at
        else (
            "SupportOps AI is in demo. Purchasing will be available soon. "
            "Your account includes a one-time AI allowance across all workspaces. "
            "Only operations that call paid models use this allowance."
        ),
        "checkout_enabled": False,
        "portal_enabled": False,
    }
    db.commit()
    return result
