"""The global account cap includes every signup path and concurrent requests."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from supportops.auth import DUMMY_HASH, identifier
from supportops.db import User, Invitation
from supportops.registration import REGISTRATION_CLOSED
from scripts.users import roster
from tests.test_workspaces import register, invitation, accept, PASSWORD


def fill(c, count):
    with c.app.state.sessions() as db:
        for n in range(count):
            db.add(
                User(
                    id=identifier(),
                    name=f"Existing user {n}",
                    email=f"existing{n}@example.test",
                    password_hash=DUMMY_HASH,
                )
            )
        db.commit()


def payload(n):
    return {
        "name": "Alex Morgan",
        "business_name": f"Business {n}",
        "email": f"signup{n}@example.test",
        "password": PASSWORD,
    }


def test_tenth_account_closes_registration_but_existing_users_keep_access(platform):
    c, other, _ = platform
    fill(c, 9)
    assert c.get("/api/config").json()["registration_available"]
    c.post("/api/auth/register", json=payload(10)).raise_for_status()
    assert c.get("/api/config").json()["registration_available"] is False
    for n in range(11, 24):
        response = other.post("/api/auth/register", json=payload(n))
        assert (
            response.status_code == 503
            and response.json()["detail"] == REGISTRATION_CLOSED
        )
    assert other.post("/api/auth/demo").status_code == 503
    response = other.post(
        "/api/auth/login", json={"email": "signup10@example.test", "password": PASSWORD}
    )
    assert response.status_code == 200
    with c.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 10
    report = roster(c.app.state.sessions, 10)
    assert report["total"] == 10 and not report["registration_available"]
    assert report["users"][-1]["workspaces"]
    assert "password" not in json.dumps(report) and "token" not in json.dumps(report)


def test_invites_cannot_bypass_cap_and_existing_members_can_join(platform):
    owner, existing, newcomer = platform
    owner.app.state.auth.config.registration_limit = 2
    register(owner)
    register(existing, "Second business", "james@acme.test")
    pending = invitation(owner, "new@example.test")
    response = accept(newcomer, pending, "new@example.test")
    assert (
        response.status_code == 503 and response.json()["detail"] == REGISTRATION_CLOSED
    )
    with owner.app.state.sessions() as db:
        assert not db.get(Invitation, pending["id"]).accepted
        assert db.scalar(select(func.count()).select_from(User)) == 2
    known = invitation(owner, "james@acme.test")
    assert accept(existing, known, "james@acme.test").status_code == 200
    assert (
        owner.post("/api/organizations", json={"name": "Another workspace"}).status_code
        == 201
    )


def test_parallel_signups_share_the_last_slot(platform):
    c, _, _ = platform
    fill(c, 9)
    start = threading.Barrier(4)

    # Reuse the running lifespan: startup must not race schema migrations.
    def signup(n):
        candidate = TestClient(c.app)
        try:
            start.wait(timeout=10)
            return candidate.post("/api/auth/register", json=payload(n)).status_code
        finally:
            candidate.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(signup, range(20, 24)))
    assert sorted(results) == [201, 503, 503, 503]
    with c.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 10
