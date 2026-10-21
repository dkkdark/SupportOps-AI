"""Opt-in, bounded live OpenAI acceptance test using only synthetic business data."""

import json
import logging
import secrets
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from supportops.config import Settings
from supportops.main import create_app


def main():
    logging.disable(logging.CRITICAL)
    cfg = Settings(
        smtp_host="",
        model_provider="openai",
        embedding_provider="openai",
        agent_max_turns=4,
        agent_max_output_tokens=2000,
        agent_provider_cost_limit=0.20,
    )
    if not (cfg.openai_api_key or cfg.llm_api_key):
        raise SystemExit("Set OPENAI_API_KEY on the server before live acceptance.")
    with tempfile.TemporaryDirectory() as tmp:
        cfg = cfg.model_copy(
            update={"database_url": f"sqlite:///{tmp}/live.db", "app_env": "test"}
        )
        with TestClient(create_app(cfg)) as c:
            r = c.post(
                "/api/auth/register",
                json={
                    "name": "Alex Morgan",
                    "email": "alex@acceptance.example.test",
                    "password": secrets.token_urlsafe(24),
                    "business_name": "Bright Operations",
                },
            )
            r.raise_for_status()
            a = c.get("/api/auth/me").json()
            c.headers.update(
                {
                    "X-CSRF-Token": a["csrf_token"],
                    "X-Workspace-ID": a["organization"]["id"],
                }
            )
            r = c.post(
                "/api/knowledge",
                json={
                    "title": "Business travel expense reimbursement",
                    "category": "expenses",
                    "text": "Employees may request reimbursement for business travel expenses, including hotel accommodation. Submit an itemized receipt within 30 calendar days after the trip. The finance team must review and approve each reimbursement request. Submitting a request does not mean a payment has been approved or issued.",
                },
            )
            r.raise_for_status()
            status = c.get("/api/knowledge").json()[0]["indexing"]
            if status["status"] != "ready":
                raise SystemExit("Live indexing failed: " + str(status.get("error")))
            t = c.post(
                "/api/tickets",
                json={
                    "customer": "Emma Wilson",
                    "channel": "internal",
                    "subject": "Hotel receipt from business trip",
                    "message": "May I submit a hotel receipt after returning from a business trip, and how long do I have to do it?",
                },
            ).json()
            r = c.post("/api/tickets/" + t["id"] + "/analyze", json={})
            r.raise_for_status()
            t = r.json()
            result = t["result"]
            report = {
                "status": t["status"],
                "reason": result.get("reason"),
                "models": result.get("manifest"),
                "tool_calls": result.get("tool_calls"),
                "usage": result.get("usage"),
                "sources": [
                    {"title": d["title"], "score": d.get("score")}
                    for d in result.get("sources", [])
                ],
                "verifier": result.get("verifier"),
                "draft": result.get("draft"),
            }
            Path("reports").mkdir(exist_ok=True)
            Path("reports/live-acceptance.json").write_text(
                json.dumps(report, indent=2)
            )
            if t["status"] != "review":
                raise SystemExit(
                    "Live agent did not produce a verified reply: "
                    + str(result.get("reason"))
                )
            assert (
                "30" in result["draft"]
                and result["sources"]
                and result["verifier"]["supported"]
            )
            r = c.post(
                "/api/tickets/" + t["id"] + "/review",
                json={
                    "decision": "approve",
                    "text": result["draft"],
                    "revision": t["revision"],
                },
            )
            r.raise_for_status()
            print(
                json.dumps(
                    {
                        "result": "PASS",
                        "agent_model": cfg.agent_model,
                        "embedding_model": cfg.embedding_model,
                        "tool_calls": len(result["tool_calls"]),
                        "usage": result["usage"],
                        "human_review": "approved",
                        "report": "reports/live-acceptance.json",
                    }
                )
            )


if __name__ == "__main__":
    main()
