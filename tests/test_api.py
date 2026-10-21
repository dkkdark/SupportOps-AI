import httpx
from fastapi.testclient import TestClient
from supportops.config import Settings
from supportops.main import create_app
from tests.conftest import orders_handler, sign_in_demo


def test_create_validation_and_idempotency(client, payload):
    headers = {"Idempotency-Key": "same-request"}
    a = client.post("/api/tickets", json=payload, headers=headers)
    b = client.post("/api/tickets", json=payload, headers=headers)
    assert a.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert (
        client.post(
            "/api/tickets", json={**payload, "subject": "Иная тема"}, headers=headers
        ).status_code
        == 409
    )
    for field, value in [
        ("message", "x"),
        ("order_id", "../secret"),
        ("email", "invalid"),
        ("customer", ""),
    ]:
        assert (
            client.post("/api/tickets", json={**payload, field: value}).status_code
            == 422
        )
    assert client.get("/api/tickets/nonexistent").status_code == 404


def test_auth_and_faults_disabled(tmp_path, orders):
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path}/auth.db"),
        orders_transport=httpx.MockTransport(orders_handler(orders)),
    )
    with TestClient(app) as c:
        assert c.get("/api/tickets").status_code == 401
        assert (
            c.get("/api/tickets", headers={"X-API-Key": "test-key"}).status_code == 401
        )
        sign_in_demo(c)
        assert (
            c.post(
                "/api/tickets/SO-1284/analyze",
                json={"fault": "orders_timeout"},
                headers={"X-API-Key": "test-key"},
            ).status_code
            == 403
        )
        assert c.get("/health/ready").status_code == 200
        assert c.get("/").headers["X-Content-Type-Options"] == "nosniff"


def test_persistent_checkpoint_survives_restart(tmp_path, orders):
    config = Settings(database_url=f"sqlite:///{tmp_path}/persistent.db")

    def make():
        return create_app(
            config, orders_transport=httpx.MockTransport(orders_handler(orders))
        )

    with TestClient(make()) as c:
        sign_in_demo(c)
        cookies, headers = dict(c.cookies), dict(c.headers)
        t = c.post("/api/tickets/SO-1284/analyze", json={}).json()
    with TestClient(make()) as c:
        c.cookies.update(cookies)
        c.headers.update(headers)
        state = c.app.state.graph.get_state(
            {"configurable": {"thread_id": t["result"]["thread_id"]}}
        )
        assert state.next == ("review",)
        assert (
            c.post(
                "/api/tickets/SO-1284/review",
                json={
                    "decision": "approve",
                    "text": t["result"]["draft"],
                    "revision": t["revision"],
                },
            ).json()["status"]
            == "approved"
        )


def test_unhealthy_dependency_returns_503(tmp_path):
    def broken(r):
        raise httpx.ConnectError("offline")

    with TestClient(
        create_app(
            Settings(database_url=f"sqlite:///{tmp_path}/offline.db"),
            orders_transport=httpx.MockTransport(broken),
        )
    ) as c:
        assert c.get("/health/live").status_code == 200
        assert c.get("/health/ready").status_code == 200
        sign_in_demo(c)
        assert (
            c.post("/api/tickets/SO-1284/analyze", json={}).json()["result"]["reason"]
            == "orders_unavailable"
        )
