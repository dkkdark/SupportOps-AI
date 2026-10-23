"""Durable bulk preparation, explicit reply policies and Gmail delivery.

Revision ID: 008
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "gmail_connections",
        sa.Column(
            "send_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.create_table(
        "reply_policies",
        sa.Column(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            primary_key=True,
        ),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column(
            "updated_by", sa.String(40), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "preparation_jobs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("ticket_id", sa.String(40), nullable=False),
        sa.Column("actor_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("lease_token", sa.String(40)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column(
            "reservation_id", sa.String(40), sa.ForeignKey("credit_reservations.id")
        ),
        sa.Column("last_error", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"], ["tickets.organization_id", "tickets.id"]
        ),
        sa.UniqueConstraint(
            "organization_id", "ticket_id", name="uq_preparation_ticket"
        ),
    )
    op.create_table(
        "email_origins",
        sa.Column(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            primary_key=True,
        ),
        sa.Column("ticket_id", sa.String(40), primary_key=True),
        sa.Column("mailbox", sa.String(160), nullable=False),
        sa.Column("message_id", sa.String(120), nullable=False),
        sa.Column("thread_id", sa.String(120)),
        sa.Column("rfc_message_id", sa.String(520)),
        sa.Column("reply_to", sa.String(160), nullable=False),
        sa.Column("auto_reply_blocked", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"], ["tickets.organization_id", "tickets.id"]
        ),
    )
    op.create_table(
        "outbound_replies",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("ticket_id", sa.String(40), nullable=False),
        sa.Column("actor_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("ticket_revision", sa.Integer(), nullable=False),
        sa.Column("mailbox", sa.String(160), nullable=False),
        sa.Column("recipient", sa.String(160), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("lease_token", sa.String(40)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("gmail_id", sa.String(120)),
        sa.Column("last_error", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"], ["tickets.organization_id", "tickets.id"]
        ),
        sa.UniqueConstraint("organization_id", "ticket_id", name="uq_outbound_ticket"),
    )
    for table in ("preparation_jobs", "outbound_replies"):
        for column in ("organization_id", "status"):
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in (
        "outbound_replies",
        "email_origins",
        "preparation_jobs",
        "reply_policies",
    ):
        op.drop_table(table)
    op.drop_column("gmail_connections", "send_enabled")
