"""Global workspace capacity, serialized across API processes and signup methods."""

from fastapi import HTTPException
from sqlalchemy import func, select, text
from supportops.db import Organization

REGISTRATION_CLOSED = "Registration is currently unavailable. Please check back later."


def available(db, limit):
    return db.scalar(select(func.count()).select_from(Organization)) < limit


def lock(db):
    # This must be the first statement in the workspace-creation transaction.
    # The lock lasts through the workspace insert and session commit, not CAPTCHA I/O.
    if db.bind.dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    elif db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(731530447005)"))
    else:
        raise RuntimeError("Workspace creation requires SQLite or PostgreSQL")


def require_capacity(db, limit):
    if not available(db, limit):
        raise HTTPException(503, REGISTRATION_CLOSED, headers={"Retry-After": "3600"})
