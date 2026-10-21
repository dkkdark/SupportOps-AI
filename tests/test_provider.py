import httpx
from fastapi.testclient import TestClient
from supportops.config import Settings
from supportops.main import create_app
from tests.conftest import orders_handler, sign_in_demo


def test_live_adapter_validates_and_counts_both_attempts(tmp_path, orders):
    calls = []

    def model(request):
        import json

        body = json.loads(request.content)
        evidence = json.loads(body["messages"][1]["content"])
        calls.append(body)
        # First response violates the contract. The second selects only allowed facts.
        selection = (
            {"fact_ids": ["invented_refund"], "tone": "neutral"}
            if len(calls) == 1
            else {"fact_ids": evidence["required_fact_ids"], "tone": "empathetic"}
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(selection)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    config = Settings(
        database_url=f"sqlite:///{tmp_path}/provider.db",
        db_host="",
        model_provider="openai_compatible",
        llm_api_key="synthetic-test-key",
        llm_model="contract-test-model",
        model_input_usd_per_million=2,
        model_output_usd_per_million=8,
    )
    with TestClient(
        create_app(
            config,
            orders_transport=httpx.MockTransport(orders_handler(orders)),
            llm_transport=httpx.MockTransport(model),
        )
    ) as client:
        sign_in_demo(client)
        r = client.post("/api/tickets/SO-1284/analyze", json={}).json()
        assert r["status"] == "review" and len(calls) == 2
        assert r["result"]["usage"] == {
            "input_tokens": 200,
            "output_tokens": 40,
            "cost_usd": 0.00072,
            "attempts": 2,
        }
        assert r["result"]["manifest"]["model"] == "contract-test-model"
        assert "invented_refund" not in r["result"]["draft"]
        assert "synthetic-test-key" not in str(calls)


def test_invalid_order_date_is_not_used(tmp_path, orders):
    orders[0]["expected_at"] = "yesterday-ish"
    config = Settings(database_url=f"sqlite:///{tmp_path}/date.db", db_host="")
    with TestClient(
        create_app(config, orders_transport=httpx.MockTransport(orders_handler(orders)))
    ) as client:
        sign_in_demo(client)
        r = client.post("/api/tickets/SO-1284/analyze", json={}).json()
        assert r["result"]["reason"] == "invalid_order_data"
        assert not r["result"]["draft"]
