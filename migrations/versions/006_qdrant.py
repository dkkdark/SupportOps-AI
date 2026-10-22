"""Qdrant evidence registry; retain existing SQL embeddings for a free transfer.

Revision ID: 006
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "vector_entries",
        sa.Column(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            primary_key=True,
        ),
        sa.Column("document_id", sa.String(80), primary_key=True),
        sa.Column("profile", sa.String(64), primary_key=True),
        sa.Column("point_id", sa.String(36), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_id"],
            ["documents.organization_id", "documents.id"],
        ),
    )


def downgrade():
    op.drop_table("vector_entries")
