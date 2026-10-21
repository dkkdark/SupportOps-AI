"""Isolated businesses, authentication, memberships and owned content.

Legacy demo records are preserved in an unassigned archive workspace.
"""

from datetime import datetime, timezone
import hashlib
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def schema(pg):
    m = sa.MetaData()

    def c(name, kind, *args, **kw):
        return sa.Column(name, kind, *args, **kw)

    def org():
        return c("organization_id", sa.String(40), sa.ForeignKey("organizations.id"))

    # Explicit schema snapshot: future ORM edits cannot alter this migration.
    sa.Table(
        "organizations",
        m,
        c("id", sa.String(40), primary_key=True),
        c("name", sa.String(120), nullable=False),
        c("slug", sa.String(100), unique=True, nullable=False),
        c("industry", sa.String(80), nullable=False),
        c("workflow_mode", sa.String(30), nullable=False),
        c("is_demo", sa.Boolean, nullable=False),
        c("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "users",
        m,
        c("id", sa.String(40), primary_key=True),
        c("email", sa.String(160), unique=True, nullable=False),
        c("name", sa.String(100), nullable=False),
        c("password_hash", sa.String(255), nullable=False),
        c("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "memberships",
        m,
        c(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            primary_key=True,
        ),
        c("user_id", sa.String(40), sa.ForeignKey("users.id"), primary_key=True),
        c("role", sa.String(20), nullable=False),
        c("active", sa.Boolean, nullable=False),
        c("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "login_sessions",
        m,
        c("token_hash", sa.String(64), primary_key=True),
        c(
            "user_id",
            sa.String(40),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        c("organization_id", sa.String(40), sa.ForeignKey("organizations.id")),
        c("csrf_token", sa.String(64), nullable=False),
        c("expires_at", sa.DateTime(timezone=True), nullable=False),
        c("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "invitations",
        m,
        c("id", sa.String(40), primary_key=True),
        c(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        c("email", sa.String(160), nullable=False),
        c("role", sa.String(20), nullable=False),
        c("token_hash", sa.String(64), unique=True, nullable=False),
        c("created_by", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        c("expires_at", sa.DateTime(timezone=True), nullable=False),
        c("accepted", sa.Boolean, nullable=False),
        c("revoked", sa.Boolean, nullable=False),
    )
    sa.Table(
        "auth_throttles",
        m,
        c("key", sa.String(64), primary_key=True),
        c("attempts", sa.Integer, nullable=False),
        c("window_start", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "reply_templates",
        m,
        c(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            primary_key=True,
        ),
        c("id", sa.String(40), primary_key=True),
        c("name", sa.String(120), nullable=False),
        c("category", sa.String(80), nullable=False),
        c("body", sa.Text, nullable=False),
        c("active", sa.Boolean, nullable=False),
        c("revision", sa.Integer, nullable=False),
        c("created_at", sa.DateTime(timezone=True), nullable=False),
        c("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "knowledge_sources",
        m,
        c(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            primary_key=True,
        ),
        c("id", sa.String(80), primary_key=True),
        c("key", sa.String(80), nullable=False),
        c("title", sa.String(180), nullable=False),
        c("category", sa.String(80), nullable=False),
        c("version", sa.String(30), nullable=False),
        c("effective_from", sa.String(10), nullable=False),
        c("effective_to", sa.String(10)),
        c("text", sa.Text, nullable=False),
        c("tags", sa.JSON, nullable=False),
        c("facts", sa.JSON, nullable=False),
        c("file_name", sa.String(180)),
        sa.UniqueConstraint(
            "organization_id", "key", "version", name="uq_policy_version"
        ),
        c("content_hash", sa.String(64), nullable=False),
        c("archived", sa.Boolean, nullable=False),
        c("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "audit_events",
        m,
        c("id", sa.String(40), primary_key=True),
        c(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        c("user_id", sa.String(40), sa.ForeignKey("users.id")),
        c("action", sa.String(80), nullable=False),
        c("details", sa.JSON, nullable=False),
        c("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "documents",
        m,
        c(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        c("id", sa.String(80), nullable=False),
        c("source_id", sa.String(80)),
        c("enabled", sa.Boolean, nullable=False),
        c("key", sa.String(80), index=True, nullable=False),
        c("title", sa.String(180), nullable=False),
        c("category", sa.String(80), nullable=False),
        c("version", sa.String(30), nullable=False),
        c("effective_from", sa.String(10), nullable=False),
        c("effective_to", sa.String(10)),
        c("text", sa.Text, nullable=False),
        c("tags", sa.JSON, nullable=False),
        c("facts", sa.JSON, nullable=False),
        c("embedding", Vector(128) if pg else sa.JSON, nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "id", name="pk_documents_tenant"),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["knowledge_sources.organization_id", "knowledge_sources.id"],
        ),
    )
    sa.Table(
        "tickets",
        m,
        c(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        c("id", sa.String(40), nullable=False),
        c("template_id", sa.String(40)),
        c("created_by", sa.String(40), sa.ForeignKey("users.id")),
        c("customer", sa.String(100), nullable=False),
        c("email", sa.String(160), nullable=False),
        c("subject", sa.String(180), nullable=False),
        c("message", sa.Text, nullable=False),
        c("order_id", sa.String(20)),
        c("channel", sa.String(20), nullable=False),
        c("priority", sa.String(20), nullable=False),
        c("status", sa.String(24), index=True, nullable=False),
        c("category", sa.String(24), nullable=False),
        c("result", sa.JSON),
        c("revision", sa.Integer, nullable=False),
        c("idempotency_key", sa.String(100)),
        c("created_at", sa.DateTime(timezone=True), nullable=False),
        c("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("organization_id", "id", name="pk_tickets_tenant"),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_ticket_org_idempotency"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "template_id"],
            ["reply_templates.organization_id", "reply_templates.id"],
        ),
    )
    sa.Table(
        "reviews",
        m,
        c("id", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reviews_tenant"),
        c(
            "organization_id",
            sa.String(40),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        c("ticket_id", sa.String(40), nullable=False, index=True),
        c("actor_id", sa.String(40), sa.ForeignKey("users.id")),
        c("decision", sa.String(20), nullable=False),
        c("text", sa.Text, nullable=False),
        c("note", sa.Text, nullable=False),
        c("actor", sa.String(80), nullable=False),
        c("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id", "ticket_id"], ["tickets.organization_id", "tickets.id"]
        ),
    )
    return m


def upgrade():
    bind = op.get_bind()
    pg = bind.dialect.name == "postgresql"
    for name, index in [
        ("documents", "ix_documents_key"),
        ("tickets", "ix_tickets_status"),
        ("reviews", "ix_reviews_ticket_id"),
    ]:
        op.drop_index(index, table_name=name)
    if pg:
        op.drop_index("ix_documents_embedding", table_name="documents")
    for name in ["reviews", "tickets", "documents"]:
        op.rename_table(name, "_legacy_" + name)
    m = schema(pg)
    m.create_all(bind)
    old = {
        name: sa.Table("_legacy_" + name, sa.MetaData(), autoload_with=bind)
        for name in ["documents", "tickets", "reviews"]
    }
    rows = {
        name: list(bind.execute(sa.select(table)).mappings())
        for name, table in old.items()
    }
    if any(rows.values()):
        tenant = "legacy-demo"
        now = datetime.now(timezone.utc)
        bind.execute(
            m.tables["organizations"].insert(),
            dict(
                id=tenant,
                name="Archived demo",
                slug="archived-demo",
                industry="Retail demo",
                workflow_mode="retail_demo",
                is_demo=True,
                created_at=now,
            ),
        )
        for row in rows["documents"]:
            d = dict(row)
            bind.execute(
                m.tables["knowledge_sources"].insert(),
                dict(
                    organization_id=tenant,
                    **{
                        k: d[k]
                        for k in [
                            "id",
                            "key",
                            "title",
                            "category",
                            "version",
                            "effective_from",
                            "effective_to",
                            "text",
                            "tags",
                            "facts",
                        ]
                    },
                    file_name=None,
                    content_hash=hashlib.sha256(d["text"].encode()).hexdigest(),
                    archived=False,
                    created_at=now,
                ),
            )
            bind.execute(
                m.tables["documents"].insert(),
                dict(d, organization_id=tenant, source_id=d["id"], enabled=True),
            )
        for row in rows["tickets"]:
            bind.execute(
                m.tables["tickets"].insert(),
                dict(row, organization_id=tenant, template_id=None, created_by=None),
            )
        for row in rows["reviews"]:
            bind.execute(
                m.tables["reviews"].insert(),
                dict(row, organization_id=tenant, actor_id=None),
            )
    for name in ["reviews", "tickets", "documents"]:
        op.drop_table("_legacy_" + name)
    if pg:
        op.execute(
            "CREATE INDEX ix_documents_embedding ON documents USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade():
    raise RuntimeError(
        "Workspace migration is intentionally forward-only. Restore a verified pre-migration backup to return to the single-business schema."
    )
