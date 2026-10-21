from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, create_engine
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


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
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
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(24))
    version: Mapped[str] = mapped_column(String(30))
    effective_from: Mapped[str] = mapped_column(String(10))
    effective_to: Mapped[str | None] = mapped_column(String(10))
    text: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON)
    facts: Mapped[dict] = mapped_column(JSON)
    embedding: Mapped[list] = mapped_column(Embedding())


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), index=True)
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
    return engine, sessionmaker(engine, expire_on_commit=False)
