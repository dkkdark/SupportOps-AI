from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator
from pgvector.sqlalchemy import Vector


def utcnow():
    return datetime.now(timezone.utc)


class Embedding(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(
            Vector(128) if dialect.name == "postgresql" else JSON()
        )


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    industry: Mapped[str] = mapped_column(String(80), default="General business")
    workflow_mode: Mapped[str] = mapped_column(String(30), default="knowledge")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(160), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Membership(Base):
    __tablename__ = "memberships"
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="agent")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    email: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AuthThrottle(Base):
    __tablename__ = "auth_throttles"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ReplyTemplate(Base):
    __tablename__ = "reply_templates"
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(80), default="other")
    body: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        UniqueConstraint("organization_id", "key", "version", name="uq_policy_version"),
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(80), default="other")
    version: Mapped[str] = mapped_column(String(30))
    effective_from: Mapped[str] = mapped_column(String(10))
    effective_to: Mapped[str | None] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    file_name: Mapped[str | None] = mapped_column(String(180))
    content_hash: Mapped[str] = mapped_column(String(64))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_ticket_org_idempotency"
        ),
        ForeignKeyConstraint(
            ["organization_id", "template_id"],
            ["reply_templates.organization_id", "reply_templates.id"],
        ),
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    template_id: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    customer: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(160), default="")
    subject: Mapped[str] = mapped_column(String(180))
    message: Mapped[str] = mapped_column(Text)
    order_id: Mapped[str | None] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(20), default="email")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    category: Mapped[str] = mapped_column(String(24), default="other")
    result: Mapped[dict | None] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["knowledge_sources.organization_id", "knowledge_sources.id"],
        ),
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), primary_key=True
    )
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_id: Mapped[str | None] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(80))
    version: Mapped[str] = mapped_column(String(30))
    effective_from: Mapped[str] = mapped_column(String(10))
    effective_to: Mapped[str | None] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON)
    facts: Mapped[dict] = mapped_column(JSON)
    embedding: Mapped[list] = mapped_column(Embedding())


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "ticket_id"], ["tickets.organization_id", "tickets.id"]
        ),
    )
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id"), index=True
    )
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(40), index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(80), default="operator")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


def make_database(url):
    if url.startswith("sqlite"):
        Path("var").mkdir(exist_ok=True)
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    return engine, sessionmaker(engine, expire_on_commit=False)
