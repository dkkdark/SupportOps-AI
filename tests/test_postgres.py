"""Real PostgreSQL/pgvector integration. CI provides an isolated service database."""

import os
import httpx
import pytest
from fastapi.testclient import TestClient
from supportops.config import Settings
from supportops.main import create_app
from tests.conftest import orders_handler, sign_in_demo


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires isolated PostgreSQL with pgvector; exercised in CI",
)
def test_postgres_vector_search_checkpoint_and_review(orders):
    config = Settings(database_url=os.environ["TEST_DATABASE_URL"], app_env="test")
    with TestClient(
        create_app(config, orders_transport=httpx.MockTransport(orders_handler(orders)))
    ) as c:
        sign_in_demo(c)
        body = {
            "customer": "Emma Wilson",
            "subject": "PostgreSQL integration",
            "message": "Где заказ 1042 и можно отменить покупку?",
            "order_id": "1042",
        }
        t = c.post("/api/tickets", json=body).json()
        r = c.post(f"/api/tickets/{t['id']}/analyze", json={}).json()
        assert r["status"] == "review" and r["result"]["sources"]
        assert c.app.state.graph.get_state(
            {"configurable": {"thread_id": r["result"]["thread_id"]}}
        ).next == ("review",)
        assert (
            c.post(
                f"/api/tickets/{t['id']}/review",
                json={
                    "decision": "approve",
                    "text": r["result"]["draft"],
                    "revision": r["revision"],
                },
            ).json()["status"]
            == "approved"
        )
