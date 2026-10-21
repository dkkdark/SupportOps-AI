"""Durable account-mail outbox. Raw recovery tokens are never stored in the queue."""

from datetime import timedelta
from sqlalchemy import select, update, or_, and_
from supportops.auth import identifier, aware
from supportops.db import AccountEmailJob, User, utcnow
from supportops.mailer import email_status

ACTIVE = ("pending", "retry", "blocked", "sending")


def enqueue(db, user_id, purpose):
    if purpose not in ("verify", "reset"):
        raise ValueError("Unknown account email purpose")
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not user or (purpose == "verify" and user.email_verified):
        return None
    key = f"{user_id}:{purpose}"
    old = db.scalar(select(AccountEmailJob).where(AccountEmailJob.dedupe_key == key))
    if (
        old
        and old.status != "sending"
        and aware(old.created_at)
        < utcnow() - timedelta(minutes=30 if purpose == "reset" else 1440)
    ):
        old.status, old.dedupe_key, old.last_error = (
            "cancelled",
            None,
            "request_expired",
        )
        db.flush()
        old = None
    if old:
        if old.status in ("blocked", "retry"):
            old.status, old.next_attempt_at = "pending", utcnow()
        return old.id
    job = AccountEmailJob(
        id=identifier(), user_id=user_id, purpose=purpose, dedupe_key=key
    )
    db.add(job)
    db.flush()
    return job.id


def eligible(now):
    return or_(
        and_(
            AccountEmailJob.status.in_(("pending", "retry", "blocked")),
            AccountEmailJob.next_attempt_at <= now,
        ),
        and_(AccountEmailJob.status == "sending", AccountEmailJob.lease_until < now),
    )


def process(config, sessions, job_id):
    now, claim = utcnow(), identifier()
    with sessions() as db:
        current = db.get(AccountEmailJob, job_id)
        if not current or current.status not in ACTIVE:
            return False
        if (
            current.status == "sending"
            and current.lease_until
            and aware(current.lease_until) > now
        ):
            return False
        if aware(current.created_at) < now - timedelta(
            minutes=30 if current.purpose == "reset" else 1440
        ):
            current.status, current.dedupe_key, current.last_error = (
                "cancelled",
                None,
                "request_expired",
            )
            current.lease_token, current.lease_until = None, None
            db.commit()
            return False
        # Missing credentials do not consume retry attempts or invalidate tokens.
        if not email_status(config)["ready"]:
            db.execute(
                update(AccountEmailJob)
                .execution_options(synchronize_session=False)
                .where(
                    AccountEmailJob.id == job_id,
                    AccountEmailJob.status.in_(("pending", "retry", "blocked")),
                )
                .values(
                    status="blocked",
                    last_error="smtp_not_configured",
                    next_attempt_at=now + timedelta(minutes=1),
                    updated_at=now,
                )
            )
            db.commit()
            return False
        if not db.execute(
            update(AccountEmailJob)
            .execution_options(synchronize_session=False)
            .where(
                AccountEmailJob.id == job_id,
                eligible(now),
                AccountEmailJob.attempts < config.email_max_attempts,
            )
            .values(
                status="sending",
                lease_token=claim,
                lease_until=now + timedelta(minutes=5),
                attempts=AccountEmailJob.attempts + 1,
                updated_at=now,
            )
        ).rowcount:
            return False
        db.commit()
        job = db.get(AccountEmailJob, job_id, populate_existing=True)
        user = db.get(User, job.user_id)
        if not user or (job.purpose == "verify" and user.email_verified):
            db.execute(
                update(AccountEmailJob)
                .execution_options(synchronize_session=False)
                .where(
                    AccountEmailJob.id == job_id, AccountEmailJob.lease_token == claim
                )
                .values(
                    status="cancelled",
                    dedupe_key=None,
                    lease_token=None,
                    lease_until=None,
                    last_error=None,
                    updated_at=utcnow(),
                )
            )
            db.commit()
            return False
        user_id, purpose, attempt = job.user_id, job.purpose, job.attempts
    try:
        from supportops.accounts import send_token

        sent = send_token(config, sessions, user_id, purpose)
    except Exception:
        # No message content, token or provider response is persisted as an error.
        sent = False
    with sessions() as db:
        values = dict(lease_token=None, lease_until=None, updated_at=utcnow())
        if sent:
            values.update(
                status="sent", dedupe_key=None, sent_at=utcnow(), last_error=None
            )
        elif attempt >= config.email_max_attempts:
            values.update(
                status="failed", dedupe_key=None, last_error="smtp_delivery_failed"
            )
        else:
            values.update(
                status="retry",
                next_attempt_at=utcnow()
                + timedelta(seconds=min(3600, 60 * 2 ** (attempt - 1))),
                last_error="smtp_delivery_failed",
            )
        db.execute(
            update(AccountEmailJob)
            .execution_options(synchronize_session=False)
            .where(AccountEmailJob.id == job_id, AccountEmailJob.lease_token == claim)
            .values(**values)
        )
        db.commit()
    return bool(sent)


def run_pending(config, sessions, limit=10):
    with sessions() as db:
        db.execute(
            update(AccountEmailJob)
            .execution_options(synchronize_session=False)
            .where(
                AccountEmailJob.status == "sending",
                AccountEmailJob.lease_until < utcnow(),
                AccountEmailJob.attempts >= config.email_max_attempts,
            )
            .values(
                status="failed",
                dedupe_key=None,
                lease_token=None,
                lease_until=None,
                last_error="smtp_delivery_uncertain",
                updated_at=utcnow(),
            )
        )
        db.commit()
        ids = list(
            db.scalars(
                select(AccountEmailJob.id)
                .where(eligible(utcnow()))
                .order_by(AccountEmailJob.next_attempt_at)
                .limit(limit)
            )
        )
    return sum(process(config, sessions, job_id) for job_id in ids)


def cancel(db, user_id, purpose):
    db.execute(
        update(AccountEmailJob)
        .execution_options(synchronize_session=False)
        .where(
            AccountEmailJob.user_id == user_id,
            AccountEmailJob.purpose == purpose,
            AccountEmailJob.status.in_(ACTIVE),
        )
        .values(
            status="cancelled",
            dedupe_key=None,
            lease_token=None,
            lease_until=None,
            updated_at=utcnow(),
        )
    )
