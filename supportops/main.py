import hashlib
import json
import logging
import time
import uuid
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from opentelemetry import trace
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from supportops.billing import billing_router, consume, reserve, settle
from supportops.intake import (
    intake_router,
    validate_routing,
    create_context,
    context_dict,
)
from supportops.launch import launch_router
from supportops.accounts import accounts_router
from supportops.agent import build_agent_graph
from supportops.auth import Auth, Identity, audit
from supportops.config import Settings, settings
from supportops.content import content_router
from supportops.db import (
    CreditReservation,
    Document,
    KnowledgeSource,
    Organization,
    ReplyTemplate,
    Review,
    Ticket,
    make_database,
    utcnow,
)
from supportops.migration import migrate
from supportops.retrieval import embed
from supportops.schemas import AnalyzeInput, ReviewInput, Rule, TicketInput
from supportops.telemetry import configure
from supportops.workflow import build_graph

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).parent / "web"
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)


def ticket_dict(ticket):
    fields = (
        "id",
        "customer",
        "email",
        "subject",
        "message",
        "order_id",
        "channel",
        "priority",
        "status",
        "category",
        "result",
        "revision",
        "template_id",
    )
    return {
        **{key: getattr(ticket, key) for key in fields},
        **context_dict(ticket),
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
    }


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
):
    config = config or settings()
    engine, sessions = make_database(config.database_url)
    auth = Auth(config, sessions)
    auth.embedding_transport = embedding_transport
    auth.config = config

    @asynccontextmanager
    async def lifespan(app):
        if config.app_env != "production":
            migrate(engine)
        configure(config)
        with ExitStack() as stack:
            if config.database_url.startswith("postgresql"):
                from langgraph.checkpoint.postgres import PostgresSaver

                cp = stack.enter_context(
                    PostgresSaver.from_conn_string(
                        config.database_url.replace(
                            "postgresql+psycopg://", "postgresql://"
                        )
                    )
                )
            else:
                from langgraph.checkpoint.sqlite import SqliteSaver

                cp = stack.enter_context(
                    SqliteSaver.from_conn_string(
                        str(engine.url.database) + ".checkpoints"
                    )
                )
            cp.setup()
            app.state.graph = build_graph(
                config, sessions, cp, orders_transport, llm_transport
            )
            app.state.agent_graph = build_agent_graph(
                config, sessions, cp, llm_transport
            )
            app.state.sessions = sessions
            app.state.auth = auth
            yield
        engine.dispose()

    app = FastAPI(
        title="SupportOps AI · Business support platform",
        version="0.2.0",
        lifespan=lifespan,
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
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/forms/") and config.turnstile_site_key:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; connect-src 'self' https://challenges.cloudflare.com; frame-ancestors 'none'; form-action 'self'"
            )
        return response

    app.include_router(auth.router(seed_demo))
    app.include_router(accounts_router(auth, sessions, config))
    app.include_router(content_router(auth, sessions))
    app.include_router(billing_router(auth, sessions, config, billing_transport))
    app.include_router(intake_router(auth, sessions, config, WEB))
    app.include_router(launch_router(auth, sessions, config))

    def find(db, actor, id):
        item = db.get(Ticket, (actor.organization_id, id))
        if not item:
            raise HTTPException(404, "Ticket not found.")
        return item

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            with sessions() as db:
                db.execute(text("SELECT 1"))
            return {"status": "ready", "database": "ok"}
        except Exception:
            raise HTTPException(503, "Database unavailable.")

    @app.get("/api/config")
    def public_config():
        return {
            "mode": "api",
            "provider": config.model_provider,
            "auth_required": True,
            "faults_enabled": config.fault_injection_enabled,
            "demo_available": config.app_env != "production"
            or config.allow_demo_signup,
            "agent_model": config.agent_model,
            "embedding_model": config.embedding_model,
            "email_delivery_enabled": bool(config.smtp_host and config.smtp_from),
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
            consume(db, actor.organization_id, "requests")
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
        if not actor.organization["is_demo"]:
            auth.ensure_verified(actor)
        if body.fault != "none" and (
            not config.fault_injection_enabled or not actor.organization["is_demo"]
        ):
            raise HTTPException(
                403, "Fault scenarios are available only in enabled demo workspaces."
            )
        with sessions() as db:
            t = find(db, actor, id)
            if t.status in ("approved", "escalated", "review"):
                return ticket_dict(t)
            if t.status == "processing" and t.updated_at.replace(
                tzinfo=timezone.utc
            ) > utcnow() - timedelta(
                seconds=max(90, config.agent_timeout_seconds + 30)
            ):
                raise HTTPException(409, "analysis_in_progress")
            reservation_id = (
                reserve(db, config, actor.organization_id)
                if actor.organization["workflow_mode"] == "knowledge"
                and config.model_provider == "openai"
                else None
            )
            if not db.execute(
                update(Ticket)
                .where(
                    Ticket.organization_id == actor.organization_id,
                    Ticket.id == id,
                    Ticket.revision == t.revision,
                )
                .values(
                    status="processing", revision=t.revision + 1, updated_at=utcnow()
                )
            ).rowcount:
                raise HTTPException(409, "revision_conflict")
            db.commit()
            db.refresh(t)
            thread = f"{actor.organization_id}:{id}:{t.revision}"
            initial = {
                "credit_budget_usd": db.get(CreditReservation, reservation_id).reserved
                * 0.005
                if reservation_id
                else config.agent_provider_cost_limit,
                "organization_id": actor.organization_id,
                "business_name": actor.organization["name"],
                "workflow_mode": actor.organization["workflow_mode"],
                "agent_name": actor.name,
                "subject": t.subject,
                "template_id": t.template_id,
                "ticket_id": id,
                "customer": t.customer,
                "message": t.message,
                "order_id": t.order_id,
                "fault": body.fault,
                "trace_id": uuid.uuid4().hex,
                "steps": [],
                "reason": None,
                "draft": None,
                "sources": [],
                "order": None,
                "usage": {},
            }
            started = time.perf_counter()
            try:
                with trace.get_tracer("supportops").start_as_current_span(
                    "ticket.analyze"
                ) as span:
                    span.set_attribute("ticket.id", id)
                    span.set_attribute("organization.id", actor.organization_id)
                    initial["trace_id"] = format(
                        span.get_span_context().trace_id, "032x"
                    )
                    graph = (
                        app.state.agent_graph
                        if actor.organization["workflow_mode"] == "knowledge"
                        and config.model_provider == "openai"
                        else app.state.graph
                    )
                    result = graph.invoke(
                        initial, config={"configurable": {"thread_id": thread}}
                    )
                for key in ("__interrupt__", "message", "customer"):
                    result.pop(key, None)
                result["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
                result["thread_id"] = thread
                t.result = result
                t.order_id = result.get("order_id", t.order_id)
                t.category = result.get("categories", ["other"])[0]
                t.status = "manual" if result.get("reason") else "review"
            except Exception as exc:
                logging.error(
                    "workflow_failed organization_id=%s ticket_id=%s error_type=%s",
                    actor.organization_id,
                    id,
                    type(exc).__name__,
                )
                t.result = {
                    "reason": "workflow_failed",
                    "trace_id": initial["trace_id"],
                    "steps": [],
                    "sources": [],
                    "draft": None,
                }
                t.status = "manual"
            t.updated_at = utcnow()
            audit(
                db,
                actor,
                "ticket.analyzed",
                {"ticket_id": id, "status": t.status, "reason": t.result.get("reason")},
            )
            db.commit()
            settle(
                sessions,
                reservation_id,
                (t.result or {}).get("usage", {}),
                uncertain=bool(
                    (t.result or {}).get("usage", {}).get("uncertain")
                    or (t.result or {}).get("reason") == "workflow_failed"
                ),
            )
            return ticket_dict(t)

    @app.post("/api/tickets/{id}/review")
    def review(id: str, body: ReviewInput, actor: Identity = Depends(auth.current)):
        with sessions() as db:
            t = find(db, actor, id)
            if t.revision != body.revision or t.status not in ("review", "manual"):
                raise HTTPException(409, "revision_conflict")
            if body.decision == "approve" and (
                t.status != "review" or len(body.text.strip()) < 10
            ):
                raise HTTPException(422, "draft_not_approvable")
            status = "approved" if body.decision == "approve" else "escalated"
            if not db.execute(
                update(Ticket)
                .where(
                    Ticket.organization_id == actor.organization_id,
                    Ticket.id == id,
                    Ticket.revision == body.revision,
                    Ticket.status.in_(["review", "manual"]),
                )
                .values(status=status, revision=body.revision + 1, updated_at=utcnow())
            ).rowcount:
                raise HTTPException(409, "revision_conflict")
            thread = (t.result or {}).get("thread_id")
            if thread:
                if (
                    not thread.startswith(actor.organization_id + ":")
                    and actor.organization_id != "legacy-demo"
                ):
                    raise HTTPException(409, "Workflow workspace mismatch.")
                graph_config = {"configurable": {"thread_id": thread}}
                graph = (
                    app.state.agent_graph
                    if (t.result or {}).get("manifest", {}).get("engine")
                    == "openai-agent-v1"
                    else app.state.graph
                )
                snapshot = graph.get_state(graph_config)
                if snapshot.next:
                    graph.invoke(Command(resume=body.model_dump()), config=graph_config)
                elif snapshot.values.get("review") != body.model_dump():
                    raise HTTPException(409, "checkpoint_review_conflict")
            db.add(
                Review(
                    id=uuid.uuid4().hex,
                    organization_id=actor.organization_id,
                    ticket_id=id,
                    decision=body.decision,
                    text=body.text,
                    note=body.note,
                    actor=actor.name[:80],
                    actor_id=actor.user_id,
                )
            )
            audit(db, actor, "ticket." + status, {"ticket_id": id})
            db.commit()
            db.refresh(t)
            return ticket_dict(t)

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
                "approved": sum(t.status == "approved" for t in items),
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
