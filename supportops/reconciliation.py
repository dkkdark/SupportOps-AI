"""Operator-only cost reconciliation, with transaction locking and an audit record."""

from datetime import timedelta, timezone
from sqlalchemy import select
from supportops.db import (
    AuditEvent,
    CreditReservation,
    DemoAccount,
    UsageCounter,
    utcnow,
)
from supportops.demo_budget import microusd, mark_limit
from supportops.auth import identifier


def reconcile(db, config, reservation_id, cost_usd, reason, apply=False):
    if len(reason.strip()) < 10 or len(reason) > 500:
        raise ValueError(
            "Record 10–500 characters describing authoritative provider evidence"
        )
    row = db.scalar(
        select(CreditReservation)
        .where(CreditReservation.id == reservation_id)
        .with_for_update()
    )
    if not row:
        raise ValueError("Reservation not found")
    stamp = (
        row.created_at.replace(tzinfo=timezone.utc)
        if row.created_at.tzinfo is None
        else row.created_at
    )
    if not row.settled and stamp > utcnow() - timedelta(
        seconds=max(600, config.agent_timeout_seconds + 120)
    ):
        raise ValueError("Reservation may still be running; wait before reconciling")
    if row.settled and not (row.usage or {}).get("reconciliation_required"):
        raise ValueError("Reservation is already reconciled")
    actual = microusd(cost_usd)
    result = {"reservation": row.id, "new_cost_usd": actual / 1_000_000, "apply": apply}
    if not apply:
        return result
    if config.demo_release and row.demo_reserved_microusd is None:
        raise ValueError(
            "Legacy company-level charges require reviewed owner attribution before changing a demo account balance"
        )
    if row.demo_reserved_microusd is not None:
        account = db.scalar(
            select(DemoAccount)
            .where(DemoAccount.user_id == row.user_id)
            .with_for_update()
        )
        if row.settled:
            account.spent_microusd += actual - (row.demo_charged_microusd or 0)
        else:
            account.reserved_microusd -= row.demo_reserved_microusd
            account.spent_microusd += actual
        if min(account.reserved_microusd, account.spent_microusd) < 0:
            raise ValueError("Account ledger is inconsistent; no correction applied")
        row.demo_charged_microusd = actual
        if account.spent_microusd < account.limit_microusd:
            account.limit_reached_at = None
        db.flush()
        mark_limit(db, row.user_id)
    else:
        used = db.get(UsageCounter, (row.organization_id, row.period, "credits"))
        if not used:
            raise ValueError("Usage counter is missing")
        charged = (actual + 4999) // 5000
        used.value += charged - (row.charged if row.settled else row.reserved)
        if used.value < 0:
            raise ValueError("Usage counter is inconsistent")
    row.charged, row.settled = (actual + 4999) // 5000, True
    row.usage = {
        **(row.usage or {}),
        "cost_usd": actual / 1_000_000,
        "reconciliation_required": False,
        "operator_reconciliation": {
            "at": utcnow().isoformat(),
            "reason": reason.strip(),
        },
    }
    db.add(
        AuditEvent(
            id=identifier(),
            organization_id=row.organization_id,
            user_id=None,
            action="usage.reconciled",
            details={**result, "reason": reason.strip()},
        )
    )
    db.flush()
    return result
