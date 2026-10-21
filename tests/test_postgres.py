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


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="requires isolated PostgreSQL/pgvector"
)
def test_postgres_semantic_agent_demo_ledger_and_durable_email():
    import uuid
    from sqlalchemy import select
    from supportops.db import AccountEmailJob, DemoAccount
    from tests.test_agent_semantic import Models
    from tests.test_workspaces import register, POLICY, make_ticket

    models = Models()
    transport = httpx.MockTransport(models)
    config = Settings(
        database_url=os.environ["TEST_DATABASE_URL"],
        app_env="test",
        demo_release=True,
        model_provider="openai",
        embedding_provider="openai",
        openai_api_key="synthetic-only",
    )
    app = create_app(config, llm_transport=transport, embedding_transport=transport)
    with TestClient(app) as c:
        identity = register(
            c, "Semantic PostgreSQL", f"alex-{uuid.uuid4().hex}@example.test"
        )
        assert c.post("/api/knowledge", json=POLICY).status_code == 201
        ticket = make_ticket(c)
        response = c.post(f"/api/tickets/{ticket['id']}/analyze", json={})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "review"
        assert response.json()["result"]["manifest"]["engine"] == "openai-agent-v1"
        assert c.get("/api/billing").json()["spent_usd"] > 0
        with app.state.sessions() as db:
            assert db.get(DemoAccount, identity["user"]["id"]).reserved_microusd == 0
            assert (
                db.scalar(
                    select(AccountEmailJob).where(
                        AccountEmailJob.user_id == identity["user"]["id"]
                    )
                ).status
                == "blocked"
            )
