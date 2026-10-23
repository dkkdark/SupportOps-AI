"""Workspace capacity includes every creation path, never invited employee accounts."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from supportops.auth import DUMMY_HASH, identifier
from supportops.db import User, Organization
from supportops.registration import REGISTRATION_CLOSED
from scripts.users import roster
from tests.test_workspaces import register, invitation, accept, account, PASSWORD


def fill_workspaces(c, count):
    with c.app.state.sessions() as db:
        for n in range(count):
            db.add(
                Organization(
                    id=identifier(), name=f"Existing business {n}", slug=identifier()
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


def test_tenth_workspace_closes_creation_but_existing_users_keep_access(platform):
    c, other, _ = platform
    fill_workspaces(c, 9)
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
    account(c)
    assert (
        c.post("/api/organizations", json={"name": "Overflow workspace"}).status_code
        == 503
    )
    report = roster(c.app.state.sessions, 10)
    assert report["total"] == 1 and report["workspace_count"] == 10
    assert not report["registration_available"] and report["users"][0]["workspaces"]
    assert "password" not in json.dumps(report) and "token" not in json.dumps(report)


def test_invited_employees_can_join_full_service_and_accounts_have_no_global_cap(
    platform,
):
    owner, existing, employee = platform
    config = owner.app.state.auth.config
    config.workspace_limit = 2
    config.demo_release = True
    register(owner)
    register(existing, "Second business", "james@acme.test")
    for n in range(11):
        email = f"employee{n}@acme.test"
        assert accept(employee, invitation(owner, email), email).status_code == 200
    assert (
        accept(
            existing, invitation(owner, "james@acme.test"), "james@acme.test"
        ).status_code
        == 200
    )
    with owner.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 13
        assert db.scalar(select(func.count()).select_from(Organization)) == 2
    assert (
        owner.post("/api/organizations", json={"name": "Another workspace"}).status_code
        == 503
    )


def test_existing_user_can_take_last_workspace_slot(platform):
    c, _, _ = platform
    register(c)
    fill_workspaces(c, 8)
    assert (
        c.post("/api/organizations", json={"name": "Last workspace"}).status_code == 201
    )
    account(c)
    assert (
        c.post("/api/organizations", json={"name": "Overflow workspace"}).status_code
        == 503
    )
    report = roster(c.app.state.sessions, 10)
    assert report["workspace_count"] == 10 and report["total"] == 1


def test_many_accounts_do_not_close_workspace_creation(platform):
    c, _, _ = platform
    with c.app.state.sessions() as db:
        for n in range(12):
            db.add(
                User(
                    id=identifier(),
                    name=f"Employee {n}",
                    email=f"employee{n}@example.test",
                    password_hash=DUMMY_HASH,
                )
            )
        db.commit()
    assert c.get("/api/config").json()["registration_available"]
    assert c.post("/api/auth/register", json=payload(10)).status_code == 201


def test_parallel_signup_and_existing_user_creation_share_the_last_slot(platform):
    c, _, _ = platform
    register(c)
    fill_workspaces(c, 8)
    start = threading.Barrier(4)

    def create(n):
        candidate = TestClient(c.app)
        try:
            if n == 0:
                candidate.cookies.update(c.cookies)
                candidate.headers.update(c.headers)
            start.wait(timeout=10)
            if n == 0:
                return candidate.post(
                    "/api/organizations", json={"name": "Existing owner's workspace"}
                ).status_code
            return candidate.post("/api/auth/register", json=payload(n)).status_code
        finally:
            candidate.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(4)))
    assert sorted(results) == [201, 503, 503, 503]
    with c.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Organization)) == 10
