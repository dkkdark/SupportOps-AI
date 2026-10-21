"""Workspace-owned policy ingestion and reply templates."""

import asyncio
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select
from supportops.auth import Identity, audit, identifier
from supportops.db import Document, KnowledgeSource, Organization, ReplyTemplate, utcnow
from supportops.retrieval import embed

MAX_FILE = 2 * 1024 * 1024
EXTRACTIONS = threading.BoundedSemaphore(2)
VARIABLES = {
    "facts",
    "customer_name",
    "business_name",
    "agent_name",
    "subject",
    "ticket_id",
}


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=3, max_length=180)
    text: str = Field(min_length=20, max_length=100000)
    category: str = Field(default="other", min_length=1, max_length=80)
    version: str = Field(default="1", min_length=1, max_length=30)
    key: str | None = Field(default=None, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    effective_from: date = Field(default_factory=date.today)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def bound_tags(cls, tags):
        if any(not t.strip() or len(t) > 50 for t in tags):
            raise ValueError("Tags must contain 1–50 characters.")
        return tags


class TemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=3, max_length=120)
    category: str = Field(default="other", min_length=1, max_length=80)
    body: str = Field(min_length=9, max_length=10000)
    active: bool = True

    @field_validator("body")
    @classmethod
    def validate_body(cls, body):
        names = re.findall(r"{{\s*([a-z_]+)\s*}}", body)
        if names.count("facts") != 1:
            raise ValueError(
                "Include {{facts}} exactly once so policy evidence cannot be omitted."
            )
        if set(names) - VARIABLES:
            raise ValueError(
                "Unsupported template variable. Use facts, customer_name, business_name, agent_name, subject or ticket_id."
            )
        if "{{" in re.sub(r"{{\s*[a-z_]+\s*}}", "", body) or "}}" in re.sub(
            r"{{\s*[a-z_]+\s*}}", "", body
        ):
            raise ValueError("Invalid template placeholder.")
        return body


class TemplateUpdate(TemplateInput):
    revision: int = Field(ge=1)


def chunks(text, size=1000):
    result = []
    current = ""
    for block in re.split(r"\n\s*\n|(?<=[.!?])\s+", text.strip()):
        if not block.strip():
            continue
        if len(current) + len(block) + 2 <= size:
            current = (current + "\n\n" + block).strip()
            continue
        if current:
            result.append(current)
            current = ""
        while len(block) > size:
            end = block.rfind(" ", 0, size)
            if end < size // 2:
                end = size
            result.append(block[:end].strip())
            block = block[end:].strip()
        current = block
    if current:
        result.append(current)
    if len(result) > 120:
        raise HTTPException(
            422,
            "This document creates too many chunks. Split it into smaller policies.",
        )
    return result


def source_dict(source, count=None):
    out = {
        k: getattr(source, k)
        for k in (
            "id",
            "key",
            "title",
            "category",
            "version",
            "effective_from",
            "effective_to",
            "text",
            "tags",
            "facts",
            "file_name",
            "content_hash",
            "archived",
        )
    }
    if count is not None:
        out["chunks"] = count
    return out


def template_dict(t):
    return {
        k: getattr(t, k)
        for k in ("id", "name", "category", "body", "active", "revision")
    }


def render_template(body, values):
    # Single-pass replacement: policy text containing {{...}} is never evaluated again.
    return re.sub(r"{{\s*([a-z_]+)\s*}}", lambda m: values.get(m[1], ""), body)


def publish(db, actor, body, file_name=None):
    db.scalar(
        select(Organization)
        .where(Organization.id == actor.organization_id)
        .with_for_update()
    )
    key = body.key or (
        re.sub(r"[^a-z0-9]+", "-", body.title.casefold()).strip("-")[:70]
        or identifier()
    )
    sha = hashlib.sha256(body.text.encode()).hexdigest()
    old = db.scalars(
        select(KnowledgeSource).where(
            KnowledgeSource.organization_id == actor.organization_id,
            KnowledgeSource.key == key,
        )
    ).all()
    same = next((d for d in old if d.version == body.version), None)
    if same:
        if same.content_hash == sha:
            return source_dict(same)
        raise HTTPException(
            409, "This policy version already exists. Choose a new version."
        )
    start = body.effective_from.isoformat()
    if old and start < max(d.effective_from for d in old):
        raise HTTPException(
            422, "A new policy version cannot start before an existing version."
        )
    for previous in old:
        if not previous.effective_to or previous.effective_to > start:
            previous.effective_to = start
            for chunk in db.scalars(
                select(Document).where(
                    Document.organization_id == actor.organization_id,
                    Document.source_id == previous.id,
                )
            ).all():
                chunk.effective_to = start
    id = identifier()
    source = KnowledgeSource(
        organization_id=actor.organization_id,
        id=id,
        key=key,
        title=body.title,
        category=body.category,
        version=body.version,
        effective_from=start,
        effective_to=None,
        text=body.text,
        tags=body.tags,
        facts={},
        file_name=file_name,
        content_hash=sha,
        archived=False,
    )
    db.add(source)
    db.flush()
    pieces = chunks(body.text)
    from sqlalchemy import func
    from supportops.billing import entitlement

    limits = entitlement(db, actor.organization_id)
    stored = (
        db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.organization_id == actor.organization_id)
        )
        or 0
    )
    if not limits["plan"] == "demo" and stored + len(pieces) > limits["chunks"]:
        raise HTTPException(
            402,
            "Knowledge storage limit reached. Upgrade your plan before publishing more documents.",
        )
    for i, text in enumerate(pieces):
        fact_id = f"policy_{id}_{i}"
        db.add(
            Document(
                organization_id=actor.organization_id,
                id=f"{id}-{i}",
                source_id=id,
                enabled=True,
                key=key,
                title=body.title,
                category=body.category,
                version=body.version,
                effective_from=start,
                effective_to=None,
                text=text,
                tags=body.tags,
                facts={fact_id: text},
                embedding=embed(body.title + " " + text + " " + " ".join(body.tags)),
            )
        )
    audit(
        db,
        actor,
        "policy.published",
        {
            "source_id": id,
            "version": body.version,
            "chunks": len(pieces),
            "file_name": file_name,
        },
    )
    return source_dict(source, len(pieces))


async def uploaded_text(file):
    name = Path(file.filename or "document.txt").name[:180]
    ext = Path(name).suffix.lower()
    if ext not in (".txt", ".md", ".json", ".pdf", ".docx"):
        raise HTTPException(415, "Use TXT, Markdown, PDF, DOCX or JSON.")
    data = await file.read(MAX_FILE + 1)
    await file.close()
    if not data or len(data) > MAX_FILE:
        raise HTTPException(413, "Upload a non-empty file up to 2 MB.")
    if not EXTRACTIONS.acquire(blocking=False):
        raise HTTPException(429, "Document processing is busy. Try again shortly.")
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "supportops.extract", ext],
            input=data,
            capture_output=True,
            timeout=10,
        )
        if result.returncode:
            raise HTTPException(
                422,
                result.stdout.decode("utf-8", errors="replace")[:200]
                or "Could not read this document.",
            )
        text = result.stdout.decode("utf-8")
    except subprocess.TimeoutExpired:
        raise HTTPException(
            422,
            "Document parsing took too long. Upload a smaller TXT or Markdown file.",
        )
    finally:
        EXTRACTIONS.release()
    if not text.strip() or len(text) > 100000:
        raise HTTPException(
            422, "Use a document with selectable text, up to 100,000 characters."
        )
    return name, text


def content_router(auth, sessions):
    from supportops.semantic import enqueue, index_source, index_status
    from supportops.billing import entitlement

    config = auth.config
    transport = getattr(auth, "embedding_transport", None)

    def queue(db, actor, source_id, tasks):
        if actor.organization and actor.organization.get("is_demo"):
            return
        job = enqueue(db, config, actor.organization_id, source_id)
        db.commit()
        if job:
            tasks.add_task(
                index_source,
                config,
                sessions,
                actor.organization_id,
                source_id,
                transport,
            )

    router = APIRouter(prefix="/api")

    @router.get("/knowledge")
    def list_sources(actor: Identity = Depends(auth.current)):
        with sessions() as db:
            docs = db.scalars(
                select(KnowledgeSource)
                .where(KnowledgeSource.organization_id == actor.organization_id)
                .order_by(KnowledgeSource.created_at.desc())
            ).all()
            return [
                {
                    **source_dict(
                        d,
                        len(
                            db.scalars(
                                select(Document.id).where(
                                    Document.organization_id == actor.organization_id,
                                    Document.source_id == d.id,
                                )
                            ).all()
                        ),
                    ),
                    "indexing": index_status(db, config, d),
                }
                for d in docs
            ]

    @router.post("/knowledge", status_code=201)
    def create_source(
        body: PolicyInput, tasks: BackgroundTasks, actor: Identity = Depends(auth.admin)
    ):
        with sessions() as db:
            auth.ensure_verified(actor)
            result = publish(db, actor, body)
            db.commit()
            queue(db, actor, result["id"], tasks)
            return result

    @router.post("/knowledge/upload", status_code=201)
    async def upload_policy(
        request: Request,
        tasks: BackgroundTasks,
        file: UploadFile = File(...),
        title: str = Form(...),
        category: str = Form("other"),
        version: str = Form("1"),
        effective_from: str = Form(""),
        key: str = Form(""),
        actor: Identity = Depends(auth.admin),
    ):
        auth.throttle(request, "upload", actor.organization_id, limit=30)
        name, text = await uploaded_text(file)
        try:
            body = PolicyInput(
                title=title,
                text=text,
                category=category,
                version=version,
                effective_from=effective_from or date.today(),
                key=key or None,
            )
        except ValidationError as exc:
            raise HTTPException(422, str(exc.errors()[0]["msg"]))
        with sessions() as db:
            auth.ensure_verified(actor)
            result = publish(db, actor, body, name)
            db.commit()
            queue(db, actor, result["id"], tasks)
            return result

    @router.post("/knowledge/{id}/reindex")
    def reindex(id: str, tasks: BackgroundTasks, actor: Identity = Depends(auth.admin)):
        with sessions() as db:
            source = db.get(KnowledgeSource, (actor.organization_id, id))
            if not source or source.archived:
                raise HTTPException(404, "Active document not found.")
            queue(db, actor, id, tasks)
            return {"status": "queued"}

    @router.post("/knowledge/{id}/archive")
    def archive(id: str, actor: Identity = Depends(auth.admin)):
        with sessions() as db:
            source = db.get(KnowledgeSource, (actor.organization_id, id))
            if not source:
                raise HTTPException(404, "Policy not found.")
            source.archived = True
            for chunk in db.scalars(
                select(Document).where(
                    Document.organization_id == actor.organization_id,
                    Document.source_id == id,
                )
            ).all():
                chunk.enabled = False
            audit(db, actor, "policy.archived", {"source_id": id})
            db.commit()
        return {"ok": True}

    @router.get("/templates")
    def list_templates(actor: Identity = Depends(auth.current)):
        with sessions() as db:
            return [
                template_dict(t)
                for t in db.scalars(
                    select(ReplyTemplate)
                    .where(ReplyTemplate.organization_id == actor.organization_id)
                    .order_by(ReplyTemplate.created_at)
                ).all()
            ]

    @router.post("/templates", status_code=201)
    def create_template(body: TemplateInput, actor: Identity = Depends(auth.admin)):
        with sessions() as db:
            t = ReplyTemplate(
                organization_id=actor.organization_id,
                id=identifier(),
                **body.model_dump(),
            )
            db.add(t)
            db.flush()
            audit(db, actor, "template.created", {"template_id": t.id})
            db.commit()
            return template_dict(t)

    @router.patch("/templates/{id}")
    def update_template(
        id: str, body: TemplateUpdate, actor: Identity = Depends(auth.admin)
    ):
        from sqlalchemy import update

        with sessions() as db:
            t = db.get(ReplyTemplate, (actor.organization_id, id))
            if not t:
                raise HTTPException(404, "Template not found.")
            changed = db.execute(
                update(ReplyTemplate)
                .where(
                    ReplyTemplate.organization_id == actor.organization_id,
                    ReplyTemplate.id == id,
                    ReplyTemplate.revision == body.revision,
                )
                .values(
                    **body.model_dump(exclude={"revision"}),
                    revision=body.revision + 1,
                    updated_at=utcnow(),
                )
            ).rowcount
            if not changed:
                raise HTTPException(
                    409,
                    "This template was changed by another administrator. Refresh it.",
                )
            audit(
                db,
                actor,
                "template.updated",
                {"template_id": id, "revision": body.revision + 1},
            )
            db.commit()
            db.refresh(t)
            return template_dict(t)

    @router.post("/templates/upload", status_code=201)
    async def upload_templates(
        request: Request,
        file: UploadFile = File(...),
        name: str = Form("Imported reply template"),
        category: str = Form("other"),
        actor: Identity = Depends(auth.admin),
    ):
        auth.throttle(request, "upload", actor.organization_id, limit=30)
        filename, text = await uploaded_text(file)
        try:
            if filename.lower().endswith(".json"):
                raw = json.loads(text)
                items = raw.get("templates", [raw]) if isinstance(raw, dict) else raw
                if not isinstance(items, list) or not 1 <= len(items) <= 50:
                    raise ValueError("Use a JSON object or a list of 1–50 templates.")
                parsed = [TemplateInput.model_validate(item) for item in items]
            else:
                parsed = [TemplateInput(name=name, category=category, body=text)]
        except (ValueError, ValidationError) as exc:
            raise HTTPException(
                422,
                str(exc.errors()[0]["msg"])
                if isinstance(exc, ValidationError)
                else str(exc),
            )
        with sessions() as db:
            result = []
            for body in parsed:
                t = ReplyTemplate(
                    organization_id=actor.organization_id,
                    id=identifier(),
                    **body.model_dump(),
                )
                db.add(t)
                db.flush()
                result.append(template_dict(t))
            audit(
                db,
                actor,
                "templates.imported",
                {"count": len(result), "file_name": filename},
            )
            db.commit()
            return result

    @router.get("/audit")
    def audits(actor: Identity = Depends(auth.admin)):
        from supportops.db import AuditEvent

        with sessions() as db:
            return [
                {
                    "id": a.id,
                    "action": a.action,
                    "details": a.details,
                    "created_at": a.created_at.isoformat(),
                }
                for a in db.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.organization_id == actor.organization_id)
                    .order_by(AuditEvent.created_at.desc())
                    .limit(100)
                ).all()
            ]

    return router
