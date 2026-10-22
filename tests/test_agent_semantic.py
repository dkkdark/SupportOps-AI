"""Model protocol, semantic indexing, tool isolation, grounding and usage contracts."""

import json
import httpx
from sqlalchemy import select
from supportops.db import VectorEntry, CreditReservation, UsageCounter
from tests.test_workspaces import make_ticket, POLICY


class Models:
    def __init__(self):
        self.calls = []
        self.bad_citation = False
        self.reject = False
        self.fail_embeddings = False

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append((request.url.path, body))
        if request.url.path.endswith("/embeddings"):
            if self.fail_embeddings:
                return httpx.Response(
                    503, json={"error": {"message": "private provider error"}}
                )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": i, "embedding": [1.0] + [0.0] * 1535}
                        for i, _ in enumerate(body["input"])
                    ],
                    "usage": {"total_tokens": 40},
                },
            )
        if "supported" in body["text"]["format"]["schema"]["properties"]:
            output = {
                "supported": not self.reject,
                "needs_live_data": False,
                "unsafe_action": False,
                "issues": ["Unsupported claim"] if self.reject else [],
            }
        else:
            results = [
                i for i in body["input"] if i.get("type") == "function_call_output"
            ]
            if not results:
                return httpx.Response(
                    200,
                    json={
                        "status": "completed",
                        "output": [
                            {
                                "type": "function_call",
                                "name": "search_knowledge",
                                "arguments": json.dumps(
                                    {"query": "appointment cancellation"}
                                ),
                                "call_id": "call_1",
                            }
                        ],
                        "usage": {"input_tokens": 100, "output_tokens": 20},
                    },
                )
            data = json.loads(results[-1]["output"])
            matches = data.get("matches", [])
            output = {
                "decision": "draft" if matches else "handoff",
                "category": "cancellation",
                "priority": "normal",
                "summary": "Checked the current appointment policy.",
                "answer_blocks": [
                    {
                        "kind": "fact",
                        "text": "You can cancel your appointment at least 48 hours in advance without a fee.",
                        "citations": [
                            "foreign-id"
                            if self.bad_citation
                            else matches[0]["evidence_id"]
                        ],
                    }
                ]
                if matches
                else [],
                "handoff_reason": "" if matches else "No current evidence.",
            }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": json.dumps(output)}
                        ],
                    }
                ],
                "usage": {"input_tokens": 200, "output_tokens": 50},
            },
        )


def publish(c):
    r = c.post("/api/knowledge", json=POLICY)
    assert r.status_code == 201, r.text
    status = c.get("/api/knowledge").json()[0]["indexing"]
    assert status["status"] == "ready", status
    return r.json()


def analyze(c):
    return c.post("/api/tickets/" + make_ticket(c)["id"] + "/analyze", json={})


def test_real_model_protocol_tools_embeddings_verification_and_checkpoint(ai_client):
    c, models = ai_client
    publish(c)
    r = analyze(c)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == "review", t
    result = t["result"]
    assert result["manifest"]["engine"] == "openai-agent-v1"
    assert result["tool_calls"][0]["name"] == "search_knowledge"
    assert result["verifier"]["supported"] and result["sources"]
    assert (
        result["usage"]["input_tokens"] > 0 and result["usage"]["embedding_tokens"] > 0
    )
    assert "48 hours" in result["draft"]
    for path, body in models.calls:
        if path.endswith("/responses"):
            assert body["store"] is False and body["text"]["format"]["strict"] is True
            assert "synthetic-key" not in str(body)
    assert c.app.state.agent_graph.get_state(
        {"configurable": {"thread_id": result["thread_id"]}}
    ).next == ("review",)
    assert (
        c.post(
            "/api/tickets/" + t["id"] + "/review",
            json={
                "decision": "approve",
                "text": result["draft"],
                "revision": t["revision"],
            },
        ).json()["status"]
        == "approved"
    )
    billing = c.get("/api/billing").json()
    assert 0 < billing["used"]["credits"] < 90
    with c.app.state.sessions() as db:
        assert len(db.scalars(select(VectorEntry)).all()) > 0
        assert all(r.settled for r in db.scalars(select(CreditReservation)).all())


def test_unknown_citations_and_verifier_rejections_block_drafts(ai_client):
    c, models = ai_client
    publish(c)
    models.bad_citation = True
    t = analyze(c).json()
    assert t["status"] == "manual" and t["result"]["reason"] == "unsupported_citations"
    assert t["result"]["draft"] is None
    models.bad_citation = False
    models.reject = True
    t = analyze(c).json()
    assert t["status"] == "manual" and t["result"]["reason"] == "grounding_failed"


def test_index_failure_is_visible_retryable_and_does_not_fake_embeddings(ai_client):
    c, models = ai_client
    models.fail_embeddings = True
    r = c.post("/api/knowledge", json=POLICY)
    assert r.status_code == 201
    status = c.get("/api/knowledge").json()[0]["indexing"]
    assert status["status"] == "failed"
    with c.app.state.sessions() as db:
        assert not db.scalars(select(VectorEntry)).all()
    models.fail_embeddings = False
    assert c.post("/api/knowledge/" + r.json()["id"] + "/reindex").status_code == 200
    assert c.get("/api/knowledge").json()[0]["indexing"]["status"] == "ready"


def test_profile_changes_do_not_mix_old_vectors_or_archived_policies(ai_client):
    c, models = ai_client
    source = publish(c)
    assert c.post("/api/knowledge/" + source["id"] + "/archive").status_code == 200
    t = analyze(c).json()
    assert t["status"] == "manual" and not t["result"]["sources"]


def test_credit_limit_is_enforced_before_model_calls(ai_client):
    c, models = ai_client
    publish(c)
    info = c.get("/api/auth/me").json()
    with c.app.state.sessions() as db:
        row = db.get(UsageCounter, (info["organization"]["id"], "trial", "credits"))
        row.value = 100
        db.commit()
    before = len(models.calls)
    r = analyze(c)
    assert r.status_code == 402 and len(models.calls) == before


def test_sample_workspaces_cannot_consume_billable_model_calls(ai_client):
    c, models = ai_client
    assert c.post("/api/auth/demo").status_code == 201
    info = c.get("/api/auth/me").json()
    c.headers.update(
        {
            "X-CSRF-Token": info["csrf_token"],
            "X-Workspace-ID": info["organization"]["id"],
        }
    )
    before = len(models.calls)
    assert c.post("/api/knowledge", json=POLICY).status_code == 201
    c.post("/api/tickets/SO-1284/analyze", json={})
    assert len(models.calls) == before
    assert (
        c.post("/api/organizations", json={"name": "A real business"}).status_code
        == 403
    )
