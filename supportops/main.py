import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from supportops.billing import billing_router, consume
from supportops.intake import (
    intake_router,
    validate_routing,
    create_context,
)
from supportops.launch import launch_router
from supportops.gmail import gmail_router
from supportops.preparation import reply_router
from supportops.gmail_api import ready as gmail_ready
from supportops.accounts import accounts_router
from supportops.operations import operations_router
from supportops.auth import Auth, Identity, audit
from supportops.config import Settings, settings
from supportops.content import content_router
from supportops.db import (
    Document,
    KnowledgeSource,
    ReplyTemplate,
    Review,
    Ticket,
    make_database,
)
from supportops.migration import migrate, verify_schema
from supportops.retrieval import embed
from supportops.schemas import AnalyzeInput, ReviewInput, Rule, TicketInput
from supportops.telemetry import configure
from supportops.runtime import graph_runtime
from supportops.tickets import ticket_dict, analyze_ticket, review_ticket

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).parent / "web"
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)


def seed_demo(db, organization_id):
    for raw in json.loads((ROOT / "data/knowledge/policies.json").read_text()):
        d = Rule.model_validate(raw)
        db.add(
            KnowledgeSource(
                organization_id=organization_id,
                **d.model_dump(),
                file_name="demo-policies.json",
                content_hash=hashlib.sha256(d.text.encode()).hexdigest(),
                archived=False,
            )
        )
        db.flush()
        db.add(
            Document(
                organization_id=organization_id,
                **d.model_dump(),
                source_id=d.id,
                enabled=True,
                embedding=embed(d.text + " " + " ".join(d.tags)),
            )
        )
    for raw in json.loads((ROOT / "data/tickets.json").read_text()):
        raw["created_at"] = datetime.fromisoformat(raw["created_at"])
        db.add(Ticket(organization_id=organization_id, **raw))
    db.flush()


class RequestBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        seen = 0
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 0
        if length > 3 * 1024 * 1024:
            return await JSONResponse(
                {"detail": "Request exceeds 3 MB."}, status_code=413
            )(scope, receive, send)

        async def limited():
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > 3 * 1024 * 1024:
                raise HTTPException(413, "Request exceeds 3 MB.")
            return message

        await self.app(scope, limited, send)


def create_app(
    config: Settings | None = None,
    orders_transport=None,
    llm_transport=None,
    embedding_transport=None,
    billing_transport=None,
    vector_transport=None,
    gmail_transport=None,
):
    config = config or settings()
    engine, sessions = make_database(config.database_url)
    auth = Auth(config, sessions)
    auth.embedding_transport = embedding_transport
    auth.vector_transport = vector_transport
    auth.config = config
    auth.gmail_transport = gmail_transport

    @asynccontextmanager
    async def lifespan(app):
        if config.app_env != "production":
            migrate(engine)
        verify_schema(engine)
        configure(config)
        with graph_runtime(
            config, sessions, orders_transport, llm_transport, vector_transport
        ) as graphs:
            app.state.graph = graphs.graph
            app.state.agent_graph = graphs.agent_graph
            app.state.sessions = sessions
            app.state.auth = auth
            yield
        engine.dispose()

    app = FastAPI(
        title="SupportOps AI · Business support platform",
        version="0.3.0",
        lifespan=lifespan,
        docs_url=None if config.app_env == "production" else "/docs",
        redoc_url=None if config.app_env == "production" else "/redoc",
        openapi_url=None if config.app_env == "production" else "/openapi.json",
    )
    app.add_middleware(RequestBodyLimit)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo passwords or customer content inside validation responses.
        return JSONResponse(
            {
                "detail": [
                    {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                    for e in exc.errors()
                ]
            },
            status_code=422,
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        return JSONResponse(
            {
                "detail": "This record already exists or changed concurrently. Refresh and try again."
            },
            status_code=409,
        )

    @app.middleware("http")
    async def security_headers(request, call_next):
        origin = request.headers.get("origin")
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and origin
            and not request.url.path.startswith("/api/inbound/")
        ):
            parsed = urlparse(origin)
            if (
                parsed.netloc != request.headers.get("host")
                or parsed.scheme not in ("http", "https")
                or (config.app_env == "production" and parsed.scheme != "https")
            ):
                return JSONResponse(
                    {"detail": "Cross-origin requests are not allowed."},
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = uuid.uuid4().hex
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = (
            "no-referrer"
            if request.url.path == "/api/integrations/gmail/callback"
            else "same-origin"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith(
            "/assets/"
        ) or "text/html" in response.headers.get("content-type", ""):
            # Assets have stable filenames, so deployments must revalidate them.
            response.headers["Cache-Control"] = "no-cache"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
        )
        if config.turnstile_site_key:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; connect-src 'self' https://challenges.cloudflare.com; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
            )
        return response

    app.include_router(auth.router(seed_demo))
    app.include_router(accounts_router(auth, sessions, config))
    app.include_router(operations_router(auth, sessions, config))
    app.include_router(content_router(auth, sessions))
    app.include_router(billing_router(auth, sessions, config, billing_transport))
    app.include_router(intake_router(auth, sessions, config, WEB))
    app.include_router(launch_router(auth, sessions, config))
    app.include_router(gmail_router(auth, sessions, config))
    app.include_router(reply_router(auth, sessions, config))

    def find(db, actor, id):
        item = db.get(Ticket, (actor.organization_id, id))
        if not item:
            raise HTTPException(404, "Ticket not found.")
        return item

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/worker")
    def worker_health():
        from supportops.operations import worker_status

        with sessions() as db:
            result = worker_status(db)
        if not result["healthy"]:
            raise HTTPException(
                503, "Background worker is not reporting a recent heartbeat."
            )
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            with sessions() as db:
                db.execute(text("SELECT 1"))
            verify_schema(engine)
            return {"status": "ready", "database": "ok"}
        except Exception:
            raise HTTPException(503, "Database unavailable.")

    @app.get("/health/vector")
    def vector_health():
        from supportops.semantic import profile
        from supportops.vector_store import QdrantStore

        store = None
        try:
            store = QdrantStore(config, profile(config), vector_transport)
            if not store.health():
                raise ValueError()
            return {"status": "ok", "store": "qdrant"}
        except Exception:
            raise HTTPException(503, "Vector search unavailable.")
        finally:
            if store:
                store.close()

    @app.get("/api/config")
    def public_config():
        from supportops.mailer import email_status
        from supportops.registration import available

        with sessions() as db:
            registration_available = available(db, config.workspace_limit)

        return {
            "mode": "api",
            "registration_available": registration_available,
            "workspace_limit": config.workspace_limit,
            "gmail_available": gmail_ready(config),
            "public_forms_available": config.public_intake_enabled
            and (
                config.app_env != "production"
                or bool(config.turnstile_site_key and config.turnstile_secret_key)
            ),
            "demo_release": config.demo_release,
            "demo_ai_budget_usd": config.demo_ai_budget_usd,
            "provider": config.model_provider,
            "auth_required": True,
            "faults_enabled": config.fault_injection_enabled,
            "demo_available": config.app_env != "production"
            or config.allow_demo_signup,
            "agent_model": config.agent_model,
            "embedding_model": config.embedding_model,
            "vector_store": "qdrant",
            "captcha_site_key": config.turnstile_site_key
            if config.require_signup_captcha
            else "",
            "email_delivery_enabled": email_status(config)["ready"],
            "support_email": config.support_email,
            "ai_configured": bool(config.openai_api_key or config.llm_api_key),
            "embedding_provider": config.embedding_provider,
            "version": "0.3.0",
            "platform": "SupportOps AI",
        }

    @app.get("/api/tickets")
    def tickets(actor: Identity = Depends(auth.current)):
        with sessions() as db:
            return [
                ticket_dict(t)
                for t in db.scalars(
                    select(Ticket)
                    .where(Ticket.organization_id == actor.organization_id)
                    .order_by(Ticket.created_at.desc())
                ).all()
            ]

    @app.post("/api/tickets", status_code=201)
    def create_ticket(
        body: TicketInput,
        idempotency_key: str | None = Header(default=None, max_length=100),
        actor: Identity = Depends(auth.current),
    ):
        with sessions() as db:
            validate_routing(
                db, actor.organization_id, body.request_type_id, body.assigned_to
            )
            if actor.role == "agent" and body.assigned_to not in (None, actor.user_id):
                raise HTTPException(
                    403, "Agents may assign requests only to themselves."
                )
            if body.template_id:
                template = db.get(
                    ReplyTemplate, (actor.organization_id, body.template_id)
                )
                if not template or not template.active:
                    raise HTTPException(
                        422, "Choose an active template from this workspace."
                    )
            if idempotency_key:
                old = db.scalar(
                    select(Ticket).where(
                        Ticket.organization_id == actor.organization_id,
                        Ticket.idempotency_key == idempotency_key,
                    )
                )
                if old:
                    if any(
                        (
                            getattr(old.context, k, None)
                            if k in ("request_type_id", "assigned_to")
                            else getattr(old, k)
                        )
                        != v
                        for k, v in body.model_dump().items()
                    ):
                        raise HTTPException(409, "idempotency_key_reused")
                    return ticket_dict(old)
            consume(db, actor.organization_id, "requests", config=config)
            item = Ticket(
                organization_id=actor.organization_id,
                id="SO-" + uuid.uuid4().hex[:8].upper(),
                created_by=actor.user_id,
                **body.model_dump(exclude={"request_type_id", "assigned_to"}),
                idempotency_key=idempotency_key,
            )
            db.add(item)
            try:
                db.flush()
                create_context(
                    db,
                    actor.organization_id,
                    item.id,
                    body.request_type_id,
                    body.assigned_to,
                )
                audit(db, actor, "ticket.created", {"ticket_id": item.id})
                db.commit()
            except IntegrityError:
                db.rollback()
                old = (
                    db.scalar(
                        select(Ticket).where(
                            Ticket.organization_id == actor.organization_id,
                            Ticket.idempotency_key == idempotency_key,
                        )
                    )
                    if idempotency_key
                    else None
                )
                if old and all(
                    (
                        getattr(old.context, k, None)
                        if k in ("request_type_id", "assigned_to")
                        else getattr(old, k)
                    )
                    == v
                    for k, v in body.model_dump().items()
                ):
                    return ticket_dict(old)
                raise HTTPException(409, "idempotency_conflict")
            return ticket_dict(item)

    @app.get("/api/tickets/{id}")
    def get_ticket(id: str, actor: Identity = Depends(auth.current)):
        with sessions() as db:
            t = find(db, actor, id)
            reviews = db.scalars(
                select(Review)
                .where(
                    Review.organization_id == actor.organization_id,
                    Review.ticket_id == id,
                )
                .order_by(Review.created_at)
            ).all()
            return {
                **ticket_dict(t),
                "reviews": [
                    {
                        "id": r.id,
                        "decision": r.decision,
                        "text": r.text,
                        "note": r.note,
                        "actor": r.actor,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in reviews
                ],
            }

    @app.post("/api/tickets/{id}/analyze")
    def analyze(id: str, body: AnalyzeInput, actor: Identity = Depends(auth.current)):
        return analyze_ticket(config, sessions, app.state, auth, id, body, actor)

    @app.post("/api/tickets/{id}/review")
    def review(id: str, body: ReviewInput, actor: Identity = Depends(auth.current)):
        return review_ticket(config, sessions, app.state, id, body, actor)

    @app.get("/api/metrics")
    def metrics_view(actor: Identity = Depends(auth.current)):
        with sessions() as db:
            items = db.scalars(
                select(Ticket).where(Ticket.organization_id == actor.organization_id)
            ).all()
            results = [t.result for t in items if t.result]
            durations = sorted(r["duration_ms"] for r in results if "duration_ms" in r)
            return {
                "total": len(items),
                "new": sum(t.status == "new" for t in items),
                "review": sum(t.status == "review" for t in items),
                "approved": sum(t.status in {"approved", "sent"} for t in items),
                "manual": sum(t.status in ("manual", "escalated") for t in items),
                "analyzed": len(results),
                "p50_ms": durations[len(durations) // 2] if durations else None,
                "p95_ms": durations[min(len(durations) - 1, int(len(durations) * 0.95))]
                if durations
                else None,
                "cost_usd": sum(r.get("usage", {}).get("cost_usd", 0) for r in results),
                "tokens": sum(
                    r.get("usage", {}).get("input_tokens", 0)
                    + r.get("usage", {}).get("output_tokens", 0)
                    for r in results
                ),
                "runs": [
                    {
                        "ticket_id": t.id,
                        "status": t.status,
                        "reason": t.result.get("reason"),
                        "trace_id": t.result.get("trace_id"),
                        "steps": t.result.get("steps", []),
                        "duration_ms": t.result.get("duration_ms"),
                        "manifest": t.result.get("manifest", {}),
                    }
                    for t in items
                    if t.result
                ],
            }

    @app.get("/api/evaluation")
    def evaluation(actor: Identity = Depends(auth.current)):
        report = ROOT / "reports/evaluation.json"
        if actor.organization["is_demo"] and report.exists():
            return json.loads(report.read_text())
        return {
            "status": "not_run",
            "message": "No evaluation has been configured for this workspace. Demo benchmarks do not measure your uploaded policies.",
        }

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    return app


app = create_app()
