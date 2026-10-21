"""Lifetime demo AI budgets per account and launch-notification tracking.

Revision ID: 004
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "demo_accounts",
        sa.Column(
            "user_id", sa.String(40), sa.ForeignKey("users.id"), primary_key=True
        ),
        sa.Column("spent_microusd", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reserved_microusd", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "limit_microusd", sa.Integer, nullable=False, server_default="5000000"
        ),
        sa.Column("limit_reached_at", sa.DateTime(timezone=True)),
        sa.Column("launch_email_requested_at", sa.DateTime(timezone=True)),
        sa.Column("launch_email_sent_at", sa.DateTime(timezone=True)),
        sa.Column("notification_reason", sa.String(60)),
    )
    # SQLite cannot ALTER a foreign-key constraint in place; use batch mode.
    with op.batch_alter_table("credit_reservations") as batch:
        batch.add_column(sa.Column("user_id", sa.String(40)))
        batch.add_column(sa.Column("demo_reserved_microusd", sa.Integer))
        batch.add_column(sa.Column("demo_charged_microusd", sa.Integer))
        batch.create_foreign_key("fk_reservation_user", "users", ["user_id"], ["id"])
        batch.create_index("ix_credit_reservations_user_id", ["user_id"])
    with op.batch_alter_table("index_jobs") as batch:
        batch.add_column(sa.Column("requested_by", sa.String(40)))
        batch.create_foreign_key("fk_index_job_user", "users", ["requested_by"], ["id"])
    # Preserve earlier model spend. Prior releases had company-level attribution;
    # charge it to the company's owner, conservatively retaining unknown reserves.
    conn = op.get_bind()
    owners = dict(
        conn.execute(
            sa.text(
                "SELECT organization_id, min(user_id) FROM memberships WHERE role='owner' GROUP BY organization_id"
            )
        ).all()
    )
    totals = {}
    for org_id, user_id in owners.items():
        conn.execute(
            sa.text(
                "UPDATE index_jobs SET requested_by=:uid WHERE organization_id=:oid"
            ),
            {"uid": user_id, "oid": org_id},
        )
    for row in conn.execute(sa.text("SELECT * FROM credit_reservations")).mappings():
        owner = owners.get(row["organization_id"])
        if not owner:
            continue
        usage = row["usage"] or {}
        if isinstance(usage, str):
            usage = json.loads(usage)
        unknown = not row["settled"] or usage.get("reconciliation_required", False)
        cost = (
            (row["charged"] if row["charged"] is not None else row["reserved"]) * 5000
            if unknown
            else int(
                (Decimal(str(usage.get("cost_usd", 0))) * 1_000_000).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
        )
        totals[owner] = totals.get(owner, 0) + max(0, cost)
    for user_id, cost in totals.items():
        reached = cost >= 5_000_000
        conn.execute(
            sa.text(
                "INSERT INTO demo_accounts (user_id, spent_microusd, reserved_microusd, limit_microusd, limit_reached_at, launch_email_requested_at, notification_reason) "
                "VALUES (:uid, :cost, 0, 5000000, :reached, :reached, :reason)"
            ),
            {
                "uid": user_id,
                "cost": cost,
                "reached": datetime.now(timezone.utc) if reached else None,
                "reason": "demo_budget_exhausted" if reached else None,
            },
        )


def downgrade():
    with op.batch_alter_table("index_jobs") as batch:
        batch.drop_constraint("fk_index_job_user", type_="foreignkey")
        batch.drop_column("requested_by")
    with op.batch_alter_table("credit_reservations") as batch:
        batch.drop_constraint("fk_reservation_user", type_="foreignkey")
        batch.drop_index("ix_credit_reservations_user_id")
        for name in ("user_id", "demo_reserved_microusd", "demo_charged_microusd"):
            batch.drop_column(name)
    op.drop_table("demo_accounts")
