"""Encrypted workspace Gmail grants, OAuth states and durable import cursors.

Revision ID: 007
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gmail_oauth_states",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("verifier_encrypted", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_gmail_oauth_states_organization_id",
        "gmail_oauth_states",
        ["organization_id"],
    )
    op.create_index(
        "ix_gmail_oauth_states_expires_at", "gmail_oauth_states", ["expires_at"]
    )
    op.create_table(
        "gmail_connections",
        sa.Column(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            primary_key=True,
        ),
        sa.Column("id", sa.String(40), nullable=False, unique=True),
        sa.Column("mailbox", sa.String(160), nullable=False),
        sa.Column("label_id", sa.String(120), nullable=False),
        sa.Column("label_name", sa.String(100), nullable=False),
        sa.Column("request_type_id", sa.String(40)),
        sa.Column(
            "connected_by", sa.String(40), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("refresh_token_encrypted", sa.Text()),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("import_since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scan_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("history_id", sa.String(40), nullable=False),
        sa.Column("target_history_id", sa.String(40)),
        sa.Column("page_token", sa.Text()),
        sa.Column("batch_final", sa.Boolean(), nullable=False),
        sa.Column("pending_ids", sa.JSON(), nullable=False),
        sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(40)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(80)),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "request_type_id"],
            ["request_types.organization_id", "request_types.id"],
        ),
    )
    op.create_index(
        "ix_gmail_connections_next_sync_at", "gmail_connections", ["next_sync_at"]
    )


def downgrade():
    op.drop_table("gmail_connections")
    op.drop_table("gmail_oauth_states")
