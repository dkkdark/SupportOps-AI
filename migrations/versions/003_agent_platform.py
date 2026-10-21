"""Semantic embeddings, request intake, routing, usage and launch accounts.

Revision ID: 003
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade():
    pg = op.get_bind().dialect.name == "postgresql"
    op.add_column(
        "users",
        sa.Column(
            "email_verified", sa.Boolean, nullable=False, server_default=sa.false()
        ),
    )
    now = lambda: sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)
    org = lambda: sa.Column(
        "organization_id",
        sa.String(40),
        sa.ForeignKey("organizations.id"),
        primary_key=True,
    )
    op.create_table(
        "chunk_embeddings",
        org(),
        sa.Column("document_id", sa.String(80), primary_key=True),
        sa.Column("profile", sa.String(64), primary_key=True),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("vector", Vector(1536) if pg else sa.JSON, nullable=False),
        now(),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
        ),
    )
    op.create_table(
        "index_jobs",
        org(),
        sa.Column("source_id", sa.String(80), primary_key=True),
        sa.Column("profile", sa.String(64), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("error", sa.String(160)),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["knowledge_sources.organization_id", "knowledge_sources.id"],
        ),
    )
    op.create_table(
        "request_types",
        org(),
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("instructions", sa.Text, nullable=False),
        sa.Column("visibility", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        now(),
    )
    op.create_table(
        "intakes",
        org(),
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("slug", sa.String(60), nullable=False, unique=True),
        sa.Column("request_type_id", sa.String(40)),
        sa.Column("secret_hash", sa.String(64)),
        sa.Column("allowed_origin", sa.String(240)),
        sa.Column("active", sa.Boolean, nullable=False),
        now(),
        sa.ForeignKeyConstraint(
            ["organization_id", "request_type_id"],
            ["request_types.organization_id", "request_types.id"],
        ),
    )
    op.create_table(
        "request_contexts",
        sa.Column("organization_id", sa.String(40), primary_key=True),
        sa.Column("ticket_id", sa.String(40), primary_key=True),
        sa.Column("request_type_id", sa.String(40)),
        sa.Column("assigned_to", sa.String(40)),
        sa.Column("intake_id", sa.String(40)),
        sa.Column("external_id", sa.String(160)),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"], ["tickets.organization_id", "tickets.id"]
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "request_type_id"],
            ["request_types.organization_id", "request_types.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "assigned_to"],
            ["memberships.organization_id", "memberships.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "intake_id"], ["intakes.organization_id", "intakes.id"]
        ),
    )
    op.create_table(
        "usage_counters",
        org(),
        sa.Column("period", sa.String(40), primary_key=True),
        sa.Column("metric", sa.String(40), primary_key=True),
        sa.Column("value", sa.Integer, nullable=False),
    )
    op.create_table(
        "subscriptions",
        org(),
        sa.Column("stripe_customer", sa.String(120), unique=True),
        sa.Column("stripe_subscription", sa.String(120), unique=True),
        sa.Column("plan", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("period_start", sa.Integer),
        sa.Column("period_end", sa.Integer),
        sa.Column("checkout_url", sa.Text),
        sa.Column("checkout_expires", sa.Integer),
        sa.Column("latest_event_at", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "webhook_receipts", sa.Column("id", sa.String(120), primary_key=True), now()
    )
    op.create_table(
        "recovery_tokens",
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean, nullable=False),
    )
    op.create_table(
        "credit_reservations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("period", sa.String(40), nullable=False),
        sa.Column("reserved", sa.Integer, nullable=False),
        sa.Column("charged", sa.Integer),
        sa.Column("settled", sa.Boolean, nullable=False),
        sa.Column("usage", sa.JSON),
        now(),
    )
    if pg:
        op.execute(
            "CREATE INDEX ix_chunk_embeddings_vector ON chunk_embeddings USING hnsw (vector vector_cosine_ops)"
        )


def downgrade():
    for table in [
        "credit_reservations",
        "recovery_tokens",
        "webhook_receipts",
        "subscriptions",
        "usage_counters",
        "request_contexts",
        "intakes",
        "request_types",
        "index_jobs",
        "chunk_embeddings",
    ]:
        op.drop_table(table)
