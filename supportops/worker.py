"""Durable email/index worker, heartbeat and bounded technical-data maintenance."""

from datetime import timedelta
from sqlalchemy import select, update, delete
from supportops.db import (
    AccountEmailJob,
    AuthThrottle,
    IndexJob,
    LoginSession,
    RecoveryToken,
    WorkerHeartbeat,
    utcnow,
)
from supportops.semantic import index_source, profile
from supportops.email_jobs import run_pending


def heartbeat(sessions, worker_id, details):
    with sessions() as db:
        db.merge(WorkerHeartbeat(id=worker_id, updated_at=utcnow(), details=details))
        db.commit()


def maintenance(sessions):
    """Only expired technical records; never delete business requests or policies."""
    now = utcnow()
    with sessions() as db:
        counts = {}
        for model, condition in [
            (LoginSession, LoginSession.expires_at < now - timedelta(days=1)),
            (RecoveryToken, RecoveryToken.expires_at < now - timedelta(days=7)),
            (AuthThrottle, AuthThrottle.window_start < now - timedelta(days=1)),
            (
                AccountEmailJob,
                AccountEmailJob.status.in_(["sent", "cancelled", "failed"])
                & (AccountEmailJob.updated_at < now - timedelta(days=30)),
            ),
            (WorkerHeartbeat, WorkerHeartbeat.updated_at < now - timedelta(days=7)),
        ]:
            # Bound transaction size even after long downtime.
            primary = list(model.__table__.primary_key.columns)[0]
            ids = select(primary).where(condition).limit(1000)
            counts[model.__tablename__] = db.execute(
                delete(model).where(primary.in_(ids))
            ).rowcount
        db.commit()
        return counts


def index_pending(config, sessions, on_progress=lambda: None, limit=10):
    if config.embedding_provider != "openai":
        return 0
    cutoff = utcnow() - timedelta(seconds=max(180, config.agent_timeout_seconds + 30))
    with sessions() as db:
        db.execute(
            update(IndexJob)
            .where(
                IndexJob.status == "indexing",
                IndexJob.updated_at < cutoff,
                IndexJob.attempts >= 3,
            )
            .values(
                status="failed",
                error="indexing_interrupted",
                lease_token=None,
                next_attempt_at=None,
                updated_at=utcnow(),
            )
        )
        db.commit()
        jobs = list(
            db.scalars(
                select(IndexJob)
                .where(
                    IndexJob.profile == profile(config),
                    IndexJob.attempts < 3,
                    (
                        IndexJob.status.in_(["pending", "failed"])
                        & (IndexJob.next_attempt_at <= utcnow())
                    )
                    | (
                        (IndexJob.status == "indexing") & (IndexJob.updated_at < cutoff)
                    ),
                )
                .order_by(IndexJob.updated_at)
                .limit(limit)
            )
        )
    for job in jobs:
        on_progress()
        index_source(config, sessions, job.organization_id, job.source_id)
    return len(jobs)


def cycle(config, sessions, worker_id, housekeeping=False):
    details = {"status": "running"}
    progress = lambda: heartbeat(sessions, worker_id, details)
    progress()
    details["emails_sent"] = run_pending(config, sessions)
    details["index_jobs_checked"] = index_pending(config, sessions, progress)
    if housekeeping:
        details["maintenance"] = maintenance(sessions)
    progress()
    return details
