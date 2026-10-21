"""Durable outbox, worker recovery, operator reconciliation, privacy and restore drills."""

from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from sqlalchemy import select
from supportops.db import (
    AccountEmailJob,
    IndexJob,
    AuditEvent,
    DemoAccount,
    LoginSession,
    RecoveryToken,
    utcnow,
)
from supportops.email_jobs import enqueue, process, run_pending
from supportops.billing import reserve, settle
from supportops.reconciliation import reconcile
from supportops.worker import heartbeat, maintenance
from supportops.backups import create, verify, restore_check
from tests.test_workspaces import register, PASSWORD


def mail_config(c):
    return c.app.state.auth.config.model_copy(
        update={
            "smtp_host": "smtp.gmail.com",
            "smtp_from": "SupportOps AI <info.xenonimus@gmail.com>",
            "smtp_user": "info.xenonimus@gmail.com",
            "smtp_password": "synthetic-only",
            "email_max_attempts": 2,
        }
    )


def test_outbox_survives_missing_credentials_and_recovers_without_duplicate_send(
    platform, monkeypatch
):
    c, _, _ = platform
    info = register(c)
    uid = info["user"]["id"]
    with c.app.state.sessions() as db:
        job = db.scalar(select(AccountEmailJob).where(AccountEmailJob.user_id == uid))
        assert job.status == "blocked" and job.attempts == 0
        original = job.id
        assert enqueue(db, uid, "verify") == original
        db.commit()
    sent = []
    monkeypatch.setattr(
        "supportops.accounts.send_token", lambda *args: sent.append(args[2:]) or True
    )
    assert run_pending(mail_config(c), c.app.state.sessions) == 1
    assert run_pending(mail_config(c), c.app.state.sessions) == 0
    assert len(sent) == 1
    with c.app.state.sessions() as db:
        job = db.get(AccountEmailJob, original)
        assert job.status == "sent" and job.sent_at and job.dedupe_key is None
    status = c.get("/api/auth/email-deliveries").json()
    assert status[0]["status"] == "sent"
    assert "token" not in str(status).lower()


def test_outbox_backoff_final_failure_and_stale_lease_recovery(platform, monkeypatch):
    c, _, _ = platform
    info = register(c)
    cfg = mail_config(c)
    monkeypatch.setattr("supportops.accounts.send_token", lambda *args: False)
    with c.app.state.sessions() as db:
        jid = enqueue(db, info["user"]["id"], "reset")
        db.commit()
    assert not process(cfg, c.app.state.sessions, jid)
    with c.app.state.sessions() as db:
        job = db.get(AccountEmailJob, jid)
        assert job.status == "retry" and job.attempts == 1
        assert job.next_attempt_at.replace(tzinfo=utcnow().tzinfo) > utcnow()
        job.status, job.lease_token = "sending", "lost-process"
        job.lease_until = utcnow() - timedelta(seconds=1)
        db.commit()
    assert not process(cfg, c.app.state.sessions, jid)
    with c.app.state.sessions() as db:
        job = db.get(AccountEmailJob, jid)
        assert job.status == "failed" and job.attempts == 2
        assert job.dedupe_key is None


def test_outbox_allows_only_one_concurrent_claim(platform, monkeypatch):
    c, _, _ = platform
    info = register(c)
    cfg = mail_config(c)
    with c.app.state.sessions() as db:
        jid = enqueue(db, info["user"]["id"], "reset")
        db.commit()
    sending, release = threading.Event(), threading.Event()
    calls = []

    def send(*args):
        calls.append(1)
        sending.set()
        assert release.wait(5)
        return True

    monkeypatch.setattr("supportops.accounts.send_token", send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(process, cfg, c.app.state.sessions, jid)
        assert sending.wait(5)
        assert not process(cfg, c.app.state.sessions, jid)
        release.set()
        assert first.result(5)
    assert len(calls) == 1


def test_worker_status_and_maintenance_keep_business_and_live_records(platform):
    c, other, _ = platform
    register(c)
    assert c.get("/health/worker").status_code == 503
    heartbeat(c.app.state.sessions, "active-worker", {"status": "running"})
    heartbeat(c.app.state.sessions, "other-stopped", {"status": "stopped"})
    assert c.get("/health/worker").status_code == 200
    assert c.get("/api/operations/status").json()["worker"]["healthy"]
    assert other.get("/api/operations/status").status_code == 401
    with c.app.state.sessions() as db:
        uid = c.get("/api/auth/me").json()["user"]["id"]
        db.add(
            LoginSession(
                token_hash="expired",
                user_id=uid,
                organization_id=None,
                csrf_token="expired",
                expires_at=utcnow() - timedelta(days=2),
            )
        )
        db.add(
            RecoveryToken(
                token_hash="expired-token",
                user_id=uid,
                purpose="reset",
                expires_at=utcnow() - timedelta(days=8),
                used=True,
            )
        )
        db.commit()
    counts = maintenance(c.app.state.sessions)
    assert counts["login_sessions"] == 1 and counts["recovery_tokens"] == 1
    assert c.get("/api/auth/me").status_code == 200


def test_operator_reconciliation_is_dry_by_default_audited_and_idempotent(platform):
    c, _, _ = platform
    info = register(c)
    cfg = c.app.state.auth.config.model_copy(update={"demo_release": True})
    uid, oid = info["user"]["id"], info["organization"]["id"]
    with c.app.state.sessions() as db:
        rid = reserve(db, cfg, oid, user_id=uid)
        db.commit()
    settle(c.app.state.sessions, rid, {}, uncertain=True)
    with c.app.state.sessions() as db:
        before = db.get(DemoAccount, uid).spent_microusd
        reconcile(
            db, cfg, rid, "0.012345", "Provider usage report verified", apply=False
        )
        assert db.get(DemoAccount, uid).spent_microusd == before
    with c.app.state.sessions() as db:
        reconcile(
            db, cfg, rid, "0.012345", "Provider usage report verified", apply=True
        )
        db.commit()
        assert db.get(DemoAccount, uid).spent_microusd == 12345
        assert db.scalar(
            select(AuditEvent).where(AuditEvent.action == "usage.reconciled")
        )
    with c.app.state.sessions() as db:
        with pytest.raises(ValueError, match="already reconciled"):
            reconcile(db, cfg, rid, "0", "Provider usage report verified", apply=True)
    with c.app.state.sessions() as db:
        pending = reserve(db, cfg, oid, user_id=uid)
        db.commit()
        with pytest.raises(ValueError, match="still be running"):
            reconcile(
                db, cfg, pending, "0", "Provider usage report verified", apply=True
            )


def test_account_export_privacy_and_notification_opt_out_are_private(platform):
    c, other, _ = platform
    info = register(c)
    assert (
        c.get("/api/account/export").json()["profile"]["email"] == info["user"]["email"]
    )
    assert "password" not in c.get("/api/account/export").text
    assert other.get("/api/account/export").status_code == 401
    assert (
        c.post("/api/account/deletion-request", json={"password": "wrong"}).status_code
        == 403
    )
    request = c.post("/api/account/deletion-request", json={"password": PASSWORD})
    assert request.status_code == 202 and request.json()["status"] == "pending"
    assert (
        c.post("/api/account/deletion-request", json={"password": PASSWORD}).json()[
            "id"
        ]
        == request.json()["id"]
    )
    assert c.get("/api/auth/me").status_code == 200  # A request is not silent erasure.
    assert c.delete("/api/demo/launch-notification").status_code == 200
    with c.app.state.sessions() as db:
        row = db.get(DemoAccount, info["user"]["id"])
        row.spent_microusd = row.limit_microusd
        db.commit()
        from supportops.demo_budget import state

        current = state(db, c.app.state.auth.config, info["user"]["id"])
        assert current["limit_reached"] and current["launch_email_opt_out"]
        assert not current["launch_email_requested"]
        assert "we'll email" not in current["message"]


def test_backup_verification_restore_drill_and_corruption_detection(platform, tmp_path):
    c, _, _ = platform
    register(c)
    config = c.app.state.auth.config
    with pytest.raises(ValueError, match="Stop API"):
        create(config.database_url, tmp_path / "unsafe")
    # No concurrent writers in this isolated test fixture.
    saved = tmp_path / "snapshot"
    manifest = create(config.database_url, saved, confirm_stopped=True)
    assert manifest["schema_revision"] == "005"
    assert verify(saved)["files"]
    result = restore_check(saved, tmp_path / "restored")
    assert result["verified"] and c.get("/api/auth/me").status_code == 200
    with pytest.raises(FileExistsError):
        restore_check(saved, tmp_path / "restored")
    (saved / "application.db").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        verify(saved)


def test_index_worker_waits_for_backoff_and_never_retries_blocked_jobs(
    platform, monkeypatch
):
    from tests.test_workspaces import POLICY
    from supportops.semantic import profile
    from supportops.worker import index_pending

    c, _, _ = platform
    info = register(c)
    source = c.post("/api/knowledge", json=POLICY).json()
    cfg = c.app.state.auth.config.model_copy(update={"embedding_provider": "openai"})
    key = (info["organization"]["id"], source["id"], profile(cfg))
    with c.app.state.sessions() as db:
        db.add(
            IndexJob(
                organization_id=key[0],
                source_id=key[1],
                profile=key[2],
                status="blocked",
                requested_by=info["user"]["id"],
                attempts=1,
            )
        )
        db.commit()
    calls = []
    monkeypatch.setattr(
        "supportops.worker.index_source", lambda *args: calls.append(args)
    )
    assert index_pending(cfg, c.app.state.sessions) == 0
    with c.app.state.sessions() as db:
        job = db.get(IndexJob, key)
        job.status, job.next_attempt_at = "failed", utcnow() + timedelta(minutes=1)
        db.commit()
    assert index_pending(cfg, c.app.state.sessions) == 0
    with c.app.state.sessions() as db:
        job = db.get(IndexJob, key)
        job.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert index_pending(cfg, c.app.state.sessions) == 1 and len(calls) == 1
    with c.app.state.sessions() as db:
        job = db.get(IndexJob, key)
        job.status, job.attempts = "indexing", 3
        job.updated_at = utcnow() - timedelta(minutes=10)
        db.commit()
    assert index_pending(cfg, c.app.state.sessions) == 0
    with c.app.state.sessions() as db:
        assert db.get(IndexJob, key).status == "failed"


def test_account_data_and_privacy_requests_do_not_cross_accounts(platform):
    c, other, _ = platform
    first = register(c)
    second = register(other, "Second business", "olivia@second.test")
    c.post("/api/account/deletion-request", json={"password": PASSWORD})
    assert other.get("/api/account/privacy-requests").json() == []
    data = other.get("/api/account/export").json()
    assert data["profile"]["id"] == second["user"]["id"]
    assert first["user"]["id"] not in str(data)
    own_jobs = other.get("/api/auth/email-deliveries").json()
    first_jobs = c.get("/api/auth/email-deliveries").json()
    assert {j["id"] for j in own_jobs}.isdisjoint({j["id"] for j in first_jobs})


def test_privacy_password_confirmation_is_rate_limited(platform):
    c, _, _ = platform
    register(c)
    for _ in range(3):
        assert (
            c.post(
                "/api/account/deletion-request", json={"password": "wrong"}
            ).status_code
            == 403
        )
    assert (
        c.post("/api/account/deletion-request", json={"password": "wrong"}).status_code
        == 429
    )


def test_stale_password_email_requests_expire_instead_of_sending_later(
    platform, monkeypatch
):
    c, _, _ = platform
    info = register(c)
    with c.app.state.sessions() as db:
        jid = enqueue(db, info["user"]["id"], "reset")
        job = db.get(AccountEmailJob, jid)
        job.created_at = utcnow() - timedelta(hours=1)
        db.commit()
    sent = []
    monkeypatch.setattr(
        "supportops.accounts.send_token", lambda *args: sent.append(args) or True
    )
    assert not process(mail_config(c), c.app.state.sessions, jid)
    assert not sent
    with c.app.state.sessions() as db:
        assert db.get(AccountEmailJob, jid).last_error == "request_expired"
        new = enqueue(db, info["user"]["id"], "reset")
        assert new != jid
