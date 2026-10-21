"""Build a browser-only companion demo. No secrets, infrastructure or production claims."""

import json
import logging
import shutil
import tempfile
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from supportops.config import Settings
from supportops.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def build():
    out = ROOT / "demo/dist"
    (out / "assets").mkdir(parents=True, exist_ok=True)
    for file in (ROOT / "supportops/web").iterdir():
        if file.is_file():
            if file.name == "index.html":
                (out / "index.html").write_text(
                    file.read_text().replace(
                        'name="supportops-mode" content="api"',
                        'name="supportops-mode" content="demo"',
                    )
                )
            else:
                shutil.copy2(file, out / "assets" / file.name)
    orders = json.loads((ROOT / "data/orders.json").read_text())

    def handle(request):
        if request.url.path == "/health/live":
            return httpx.Response(200, json={"status": "ok"})
        order = next(
            (o for o in orders if request.url.path == "/orders/" + o["id"]), None
        )
        return httpx.Response(200 if order else 404, json=order or {})

    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory() as tmp:
        with TestClient(
            create_app(
                Settings(database_url=f"sqlite:///{tmp}/demo.db"),
                orders_transport=httpx.MockTransport(handle),
            )
        ) as c:
            for id in ("SO-1284", "SO-1283", "SO-1282", "SO-1281"):
                c.post(f"/api/tickets/{id}/analyze", json={})
            tickets = c.get("/api/tickets").json()
            docs = c.get("/api/knowledge").json()
    for t in tickets:
        t["reviews"] = []
        if t["result"]:
            t["result"]["trace_id"] = "demo-" + t["id"]
            t["result"]["duration_ms"] = None
            for step in t["result"]["steps"]:
                step["duration_ms"] = None
            t["result"]["manifest"]["code"] = "browser-demo-v1"
    report = ROOT / "reports/evaluation.json"
    data = {
        "tickets": tickets,
        "docs": docs,
        "orders": orders,
        "evaluation": json.loads(report.read_text())
        if report.exists()
        else {"status": "not_run"},
    }
    (out / "assets/seed.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2)
    )
    print(f"Demo built at {out}")


if __name__ == "__main__":
    build()
