"""Durable account email, worker leases, privacy requests and notification opt-out.

Revision ID: 005
"""

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "index_jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True))
    )
    op.add_column("index_jobs", sa.Column("lease_token", sa.String(40)))
    op.execute(sa.text("UPDATE index_jobs SET next_attempt_at=updated_at"))
    op.add_column(
        "demo_accounts",
        sa.Column(
            "launch_email_opt_out",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "account_email_jobs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("dedupe_key", sa.String(80), unique=True),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(40)),
        sa.Column("last_error", sa.String(80)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
    )
    op.create_table(
        "privacy_requests",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    for name in ("privacy_requests", "worker_heartbeats", "account_email_jobs"):
        op.drop_table(name)
    op.drop_column("demo_accounts", "launch_email_opt_out")
    op.drop_column("index_jobs", "lease_token")
    op.drop_column("index_jobs", "next_attempt_at")
