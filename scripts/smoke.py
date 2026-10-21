"""Create an isolated synthetic business and exercise the actual SaaS HTTP flow."""

import argparse
import secrets
import os
import time
import uuid
import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8010")
    parser.add_argument(
        "--health-only",
        action="store_true",
        help="First-deployment bootstrap only; does not verify authenticated workflows",
    )
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=150) as c:
        c.get("/health/ready").raise_for_status()
        if args.health_only:
            c.get("/").raise_for_status()
            print(
                "Bootstrap health passed; authenticated acceptance is still required."
            )
            return
        suffix = uuid.uuid4().hex
        if os.getenv("SMOKE_EMAIL") and os.getenv("SMOKE_PASSWORD"):
            r = c.post(
                "/api/auth/login",
                json={
                    "email": os.environ["SMOKE_EMAIL"],
                    "password": os.environ["SMOKE_PASSWORD"],
                },
            )
        else:
            r = c.post(
                "/api/auth/register",
                json={
                    "name": "Alex Morgan",
                    "email": f"smoke-{suffix}@example.test",
                    "password": secrets.token_urlsafe(24),
                    "business_name": "Smoke test " + suffix[:8],
                },
            )
        r.raise_for_status()
        a = c.get("/api/auth/me").json()
        c.headers.update(
            {"X-CSRF-Token": a["csrf_token"], "X-Workspace-ID": a["organization"]["id"]}
        )
        if os.getenv("SMOKE_EMAIL"):
            r = c.post("/api/organizations", json={"name": "Smoke test " + suffix[:8]})
            r.raise_for_status()
            a = c.get("/api/auth/me").json()
            c.headers.update({"X-Workspace-ID": a["organization"]["id"]})
        assert c.get("/api/tickets").json() == []
        r = c.post(
            "/api/knowledge",
            json={
                "title": "Appointment cancellation",
                "text": "Appointment cancellation is free at least 48 hours before the scheduled time.",
                "category": "cancellation",
            },
        )
        r.raise_for_status()
        for _ in range(30):
            docs = c.get("/api/knowledge").json()
            if docs and docs[0].get("indexing", {}).get("status") in (
                "ready",
                "failed",
                "not_indexed",
            ):
                break
            time.sleep(1)
        t = c.post(
            "/api/tickets",
            headers={"Idempotency-Key": suffix},
            json={
                "customer": "Emma Wilson",
                "subject": "Appointment cancellation",
                "message": "What is the appointment cancellation policy?",
            },
        ).json()
        r = c.post(f"/api/tickets/{t['id']}/analyze", json={})
        r.raise_for_status()
        t = r.json()
        assert t["status"] == "review" and "48 hours" in t["result"]["draft"], t
        assert (
            t["result"]["sources"]
            and t["result"]["organization_id"] == a["organization"]["id"]
        )
        assert (
            c.post(f"/api/tickets/{t['id']}/analyze", json={}).json()["result"][
                "trace_id"
            ]
            == t["result"]["trace_id"]
        )
        r = c.post(
            f"/api/tickets/{t['id']}/review",
            json={
                "decision": "approve",
                "text": t["result"]["draft"],
                "revision": t["revision"],
            },
        )
        r.raise_for_status()
        assert r.json()["status"] == "approved"
        c.post("/api/auth/logout").raise_for_status()
        assert c.get("/api/tickets").status_code == 401
        print(
            "PASS: health → business registration → owned policy → grounded draft → sources → idempotency → human approval → logout"
        )
        print("Synthetic smoke workspace retained: " + a["organization"]["id"])


if __name__ == "__main__":
    main()
