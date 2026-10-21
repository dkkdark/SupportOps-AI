import hmac
import json
import logging
import time
import uuid
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from opentelemetry import trace
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from supportops.config import Settings, settings
from supportops.db import Base, Document, Review, Ticket, make_database, utcnow
from supportops.retrieval import embed, serialize_document
from supportops.schemas import AnalyzeInput, ReviewInput, Rule, TicketInput
from supportops.telemetry import configure
from supportops.workflow import build_graph

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).parent / "web"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def ticket_dict(ticket):
    fields = ("id","customer","email","subject","message","order_id","channel","priority","status","category","result","revision")
    return {**{key:getattr(ticket,key) for key in fields},"created_at":ticket.created_at.isoformat(),"updated_at":ticket.updated_at.isoformat()}


def seed(sessions):
    with sessions() as db:
        if not db.scalar(select(func.count()).select_from(Document)):
            for raw in json.loads((ROOT/"data/knowledge/policies.json").read_text()):
                d = Rule.model_validate(raw)
                db.add(Document(**d.model_dump(),embedding=embed(d.text+" "+" ".join(d.tags))))
        if not db.scalar(select(func.count()).select_from(Ticket)):
            for raw in json.loads((ROOT/"data/tickets.json").read_text()):
                raw["created_at"] = datetime.fromisoformat(raw["created_at"])
                db.add(Ticket(**raw))
        db.commit()


def create_app(config: Settings | None = None, orders_transport=None, llm_transport=None):
    config = config or settings()
    engine, sessions = make_database(config.database_url)

    @asynccontextmanager
    async def lifespan(app):
        if config.app_env == "production" and not config.app_api_key:
            raise RuntimeError("APP_API_KEY is required in production")
        if config.app_env != "production":
            Base.metadata.create_all(engine)
        seed(sessions)
        configure(config)
        with ExitStack() as stack:
            if config.database_url.startswith("postgresql"):
                from langgraph.checkpoint.postgres import PostgresSaver
                cp = stack.enter_context(PostgresSaver.from_conn_string(config.database_url.replace("postgresql+psycopg://","postgresql://")))
            else:
                from langgraph.checkpoint.sqlite import SqliteSaver
                cp_path = str(engine.url.database)+".checkpoints"
                cp = stack.enter_context(SqliteSaver.from_conn_string(cp_path))
            cp.setup()
            app.state.graph = build_graph(config,sessions,cp,orders_transport,llm_transport)
            app.state.sessions = sessions
            yield
        engine.dispose()

    app = FastAPI(title="SupportOps AI",version="0.1.0",lifespan=lifespan)

    @app.middleware("http")
    async def headers(request,call_next):
        request_id = uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    def authorize(x_api_key: str = Header(default="")):
        if config.app_api_key and not hmac.compare_digest(x_api_key,config.app_api_key):
            raise HTTPException(401,"invalid_api_key")

    def find(db,id):
        item = db.get(Ticket,id)
        if not item:
            raise HTTPException(404,"ticket_not_found")
        return item

    @app.get("/health/live")
    def live():
        return {"status":"ok"}

    @app.get("/health/ready")
    def ready():
        try:
            with sessions() as db:
                db.execute(text("SELECT 1"))
            with httpx.Client(transport=orders_transport,timeout=config.orders_timeout_seconds) as client:
                client.get(config.orders_api_url+"/health/live").raise_for_status()
            return {"status":"ready"}
        except Exception:
            raise HTTPException(503,"dependency_unavailable")

    @app.get("/api/config")
    def public_config():
        return {"mode":"api", "provider":config.model_provider,"auth_required":bool(config.app_api_key),"faults_enabled":config.fault_injection_enabled,"version":"0.1.0","store":"Листва"}

    deps = [Depends(authorize)]

    @app.get("/api/tickets",dependencies=deps)
    def tickets():
        with sessions() as db:
            return [ticket_dict(t) for t in db.scalars(select(Ticket).order_by(Ticket.created_at.desc())).all()]

    @app.post("/api/tickets",status_code=201,dependencies=deps)
    def create_ticket(body: TicketInput, idempotency_key: str | None = Header(default=None, max_length=100)):
        with sessions() as db:
            if idempotency_key:
                old = db.scalar(select(Ticket).where(Ticket.idempotency_key==idempotency_key))
                if old:
                    if any(getattr(old,k)!=v for k,v in body.model_dump().items()):
                        raise HTTPException(409,"idempotency_key_reused")
                    return ticket_dict(old)
            item = Ticket(id="SO-"+uuid.uuid4().hex[:8].upper(),**body.model_dump(),idempotency_key=idempotency_key)
            db.add(item)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                old = db.scalar(select(Ticket).where(Ticket.idempotency_key==idempotency_key))
                if old and all(getattr(old,k)==v for k,v in body.model_dump().items()):
                    return ticket_dict(old)
                raise HTTPException(409,"idempotency_conflict")
            return ticket_dict(item)

    @app.get("/api/tickets/{id}",dependencies=deps)
    def get_ticket(id: str):
        with sessions() as db:
            t = find(db,id)
            reviews = db.scalars(select(Review).where(Review.ticket_id==id).order_by(Review.created_at)).all()
            return {**ticket_dict(t),"reviews":[{"id":r.id,"decision":r.decision,"text":r.text,"note":r.note,"actor":r.actor,"created_at":r.created_at.isoformat()} for r in reviews]}

    @app.post("/api/tickets/{id}/analyze",dependencies=deps)
    def analyze(id: str,body: AnalyzeInput):
        if body.fault != "none" and not config.fault_injection_enabled:
            raise HTTPException(403,"fault_injection_disabled")
        with sessions() as db:
            t = find(db,id)
            if t.status in ("approved","escalated","review"):
                return ticket_dict(t)
            if t.status=="processing" and t.updated_at.replace(tzinfo=timezone.utc)>utcnow()-timedelta(seconds=90):
                raise HTTPException(409,"analysis_in_progress")
            previous_revision = t.revision
            if not db.execute(update(Ticket).where(Ticket.id==id,Ticket.revision==previous_revision).values(status="processing",revision=previous_revision+1,updated_at=utcnow())).rowcount:
                raise HTTPException(409,"revision_conflict")
            db.commit()
            db.refresh(t)
            thread = f"{id}:{t.revision}"
            initial = {"ticket_id":id,"customer":t.customer,"message":t.message,"order_id":t.order_id,"fault":body.fault,"trace_id":uuid.uuid4().hex,"steps":[],"reason":None,"draft":None,"sources":[],"order":None,"usage":{}}
            started = time.perf_counter()
            try:
                with trace.get_tracer("supportops").start_as_current_span("ticket.analyze") as span:
                    span.set_attribute("ticket.id",id)
                    initial["trace_id"] = format(span.get_span_context().trace_id,"032x")
                    result = app.state.graph.invoke(initial,config={"configurable":{"thread_id":thread}})
                result.pop("__interrupt__",None)
                result.pop("message",None)
                result.pop("customer",None)
                result["duration_ms"] = round((time.perf_counter()-started)*1000,2)
                result["thread_id"] = thread
                t.result = result
                t.order_id = result.get("order_id",t.order_id)
                t.category = result.get("categories",["other"])[0]
                t.status = "manual" if result.get("reason") else "review"
            except Exception as exc:
                logging.error("workflow_failed ticket_id=%s error_type=%s",id,type(exc).__name__)
                t.result = {"reason":"workflow_failed","trace_id":initial["trace_id"],"steps":[],"sources":[],"draft":None}
                t.status = "manual"
            t.updated_at = utcnow()
            db.commit()
            return ticket_dict(t)

    @app.post("/api/tickets/{id}/review",dependencies=deps)
    def review(id: str,body: ReviewInput):
        with sessions() as db:
            t = find(db,id)
            if t.revision != body.revision or t.status not in ("review","manual"):
                raise HTTPException(409,"revision_conflict")
            if body.decision == "approve" and (t.status!="review" or len(body.text.strip())<10):
                raise HTTPException(422,"draft_not_approvable")
            status = "approved" if body.decision=="approve" else "escalated"
            if not db.execute(update(Ticket).where(Ticket.id==id,Ticket.revision==body.revision,Ticket.status.in_(["review","manual"])).values(status=status,revision=body.revision+1,updated_at=utcnow())).rowcount:
                raise HTTPException(409,"revision_conflict")
            thread = (t.result or {}).get("thread_id")
            if thread:
                graph_config = {"configurable":{"thread_id":thread}}
                snapshot = app.state.graph.get_state(graph_config)
                if snapshot.next:
                    app.state.graph.invoke(Command(resume=body.model_dump()),config=graph_config)
                elif snapshot.values.get("review") != body.model_dump():
                    raise HTTPException(409,"checkpoint_review_conflict")
            db.add(Review(id=uuid.uuid4().hex,ticket_id=id,decision=body.decision,text=body.text,note=body.note))
            db.commit()
            db.refresh(t)
            return ticket_dict(t)

    @app.get("/api/knowledge",dependencies=deps)
    def knowledge():
        with sessions() as db:
            return [serialize_document(d) for d in db.scalars(select(Document).order_by(Document.category,Document.title)).all()]

    @app.get("/api/metrics",dependencies=deps)
    def metrics_view():
        with sessions() as db:
            items = db.scalars(select(Ticket)).all()
            results = [t.result for t in items if t.result]
            durations = sorted(r["duration_ms"] for r in results if "duration_ms" in r)
            return {"total":len(items),"new":sum(t.status=="new" for t in items),"review":sum(t.status=="review" for t in items),"approved":sum(t.status=="approved" for t in items),"manual":sum(t.status in ("manual","escalated") for t in items),"analyzed":len(results),"p50_ms":durations[len(durations)//2] if durations else None,"p95_ms":durations[min(len(durations)-1,int(len(durations)*0.95))] if durations else None,"cost_usd":sum(r.get("usage",{}).get("cost_usd",0) for r in results),"tokens":sum(r.get("usage",{}).get("input_tokens",0)+r.get("usage",{}).get("output_tokens",0) for r in results),"runs":[{"ticket_id":t.id,"status":t.status,"reason":t.result.get("reason"),"trace_id":t.result.get("trace_id"),"steps":t.result.get("steps",[]),"duration_ms":t.result.get("duration_ms"),"manifest":t.result.get("manifest",{})} for t in items if t.result]}

    @app.get("/api/evaluation",dependencies=deps)
    def evaluation():
        report = ROOT/"reports/evaluation.json"
        return json.loads(report.read_text()) if report.exists() else {"status":"not_run","message":"Запустите python -m eval.run --split holdout"}

    app.mount("/assets",StaticFiles(directory=WEB),name="assets")

    @app.get("/")
    def index():
        return FileResponse(WEB/"index.html")

    return app


app = create_app()
