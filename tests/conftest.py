import copy
import json
import os
from datetime import date, timedelta
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from supportops.config import Settings

# Tests must never load the operator's .env, including during module import.
Settings.model_config["env_file"] = None
from supportops.main import create_app


@pytest.fixture(autouse=True)
def offline_defaults(monkeypatch):
    # Never make billable calls from the ordinary test suite.
    monkeypatch.setenv("MODEL_PROVIDER", "baseline")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "baseline")
    # Keep previous paid-release contracts explicit; demo-release tests enable it.
    monkeypatch.setenv("DEMO_RELEASE", "false")
    for key in (
        "SMTP_HOST",
        "SMTP_FROM",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "TURNSTILE_SITE_KEY",
        "TURNSTILE_SECRET_KEY",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("SMTP_SSL", "false")
    monkeypatch.setenv("SMTP_STARTTLS", "true")


@pytest.fixture(autouse=True)
def vector_service(monkeypatch):
    from supportops.vector_store import QdrantStore
    from tests.qdrant_service import QdrantService

    service = QdrantService()
    original = QdrantStore.__init__

    # Ordinary tests are fully offline. Only dedicated integration tests opt in.
    def initialize(self, config, profile, transport=None):
        original(self, config, profile, transport or httpx.MockTransport(service))

    monkeypatch.setattr(QdrantStore, "__init__", initialize)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test:6333")
    monkeypatch.setenv("QDRANT_API_KEY", "")
    yield service


@pytest.fixture
def orders():
    data = json.loads(Path("data/orders.json").read_text())
    for order in data:
        if order.get("delivered_at"):
            order["delivered_at"] = (date.today() - timedelta(days=2)).isoformat()
    return data


def orders_handler(orders):
    def handler(request):
        if request.url.path == "/health/live":
            return httpx.Response(200, json={"status": "ok"})
        order = next(
            (o for o in orders if request.url.path == "/orders/" + o["id"]), None
        )
        return httpx.Response(
            200 if order else 404,
            json=copy.deepcopy(order) if order else {"detail": "not_found"},
        )

    return handler


def sign_in_demo(c):
    response = c.post("/api/auth/demo")
    assert response.status_code == 201, response.text
    account = c.get("/api/auth/me").json()
    c.headers.update(
        {
            "X-CSRF-Token": account["csrf_token"],
            "X-Workspace-ID": account["organization"]["id"],
        }
    )
    c.organization_id = account["organization"]["id"]
    return account


@pytest.fixture
def client(tmp_path, orders):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path}/test.db", fault_injection_enabled=True
        ),
        orders_transport=httpx.MockTransport(orders_handler(orders)),
    )
    with TestClient(app) as c:
        sign_in_demo(c)
        yield c


@pytest.fixture
def payload():
    return {
        "customer": "Emma Wilson",
        "email": "customer1@example.test",
        "subject": "Вопрос по заказу",
        "message": "Где заказ 1042 и можно ли отменить покупку?",
        "order_id": "1042",
        "channel": "web",
    }


@pytest.fixture
def platform(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/saas.db"))
    with TestClient(app) as a, TestClient(app) as b, TestClient(app) as employee:
        yield a, b, employee


@pytest.fixture
def ai_client(tmp_path):
    from tests.test_agent_semantic import Models
    from tests.test_workspaces import register

    models = Models()
    transport = httpx.MockTransport(models)
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path}/ai.db",
            model_provider="openai",
            embedding_provider="openai",
            openai_api_key="synthetic-key",
            openai_base_url="https://models.example.test/v1",
        ),
        llm_transport=transport,
        embedding_transport=transport,
    )
    with TestClient(app) as c:
        register(c)
        yield c, models
