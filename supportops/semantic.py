"""Tenant-scoped semantic RAG with versioned, resumable document indexing."""

from datetime import date, timedelta
import hashlib
import math
from sqlalchemy import select, update, cast
from pgvector.sqlalchemy import Vector
from fastapi import HTTPException
from supportops.db import (
    Organization,
    ChunkEmbedding,
    CreditReservation,
    Document,
    IndexJob,
    KnowledgeSource,
    utcnow,
)
from supportops.openai_client import ModelError, OpenAIClient
from supportops.retrieval import terms, serialize_document
from supportops.billing import reserve, settle, reservation_budget, CREDIT_USD


def profile(config):
    return hashlib.sha256(
        f"{config.openai_base_url.rstrip('/')}:{config.embedding_model}:1536:v1".encode()
    ).hexdigest()


def embedding_text(d):
    return f"Title: {d.title}\nCategory: {d.category}\n{d.text}"


def text_hash(d):
    return hashlib.sha256(embedding_text(d).encode()).hexdigest()


def enqueue(db, config, org_id, source_id, user_id=None):
    if config.embedding_provider != "openai":
        return None
    key = (org_id, source_id, profile(config))
    job = db.get(IndexJob, key)
    if not job:
        job = IndexJob(
            organization_id=org_id,
            source_id=source_id,
            profile=key[2],
            status="pending",
        )
        db.add(job)
    if user_id and job.status != "indexing":
        job.requested_by = user_id
    return job


def index_source(config, sessions, org_id, source_id, transport=None):
    key = (org_id, source_id, profile(config))
    client = None
    reservation_id = None
    with sessions() as db:
        org = db.get(Organization, org_id)
        if not org or org.is_demo:
            return
        source = db.get(KnowledgeSource, (org_id, source_id))
        if not source or source.archived:
            return
        enqueue(db, config, org_id, source_id)
        db.commit()
        claimed = db.execute(
            update(IndexJob)
            .execution_options(synchronize_session=False)
            .where(
                IndexJob.organization_id == org_id,
                IndexJob.source_id == source_id,
                IndexJob.profile == key[2],
                (IndexJob.status != "indexing")
                | (IndexJob.updated_at < utcnow() - timedelta(minutes=3)),
            )
            .values(
                status="indexing",
                error=None,
                attempts=IndexJob.attempts + 1,
                updated_at=utcnow(),
            )
        ).rowcount
        db.commit()
        if not claimed:
            return
        user_id = db.get(IndexJob, key, populate_existing=True).requested_by
        docs = db.scalars(
            select(Document).where(
                Document.organization_id == org_id, Document.source_id == source_id
            )
        ).all()
        pending = [
            d
            for d in docs
            if (cached := db.get(ChunkEmbedding, (org_id, d.id, key[2]))) is None
            or cached.text_hash != text_hash(d)
        ]
    try:
        if pending:
            with sessions() as db:
                upper_bytes = sum(len(embedding_text(d).encode()) for d in pending)
                amount = max(
                    1,
                    math.ceil(
                        upper_bytes
                        * config.embedding_input_usd_per_million
                        / 1e6
                        / CREDIT_USD
                    )
                    + 1,
                )
                reservation_id = reserve(db, config, org_id, amount, user_id=user_id)
                budget = (
                    reservation_budget(db.get(CreditReservation, reservation_id))
                    if reservation_id
                    else config.agent_provider_cost_limit
                )
                db.commit()
            client = OpenAIClient(
                config.model_copy(update={"agent_provider_cost_limit": budget}),
                transport,
            )
            vectors = client.embeddings([embedding_text(d) for d in pending])
            with sessions() as db:
                for d, vector in zip(pending, vectors):
                    current = db.get(Document, (org_id, d.id))
                    if not current or text_hash(current) != text_hash(d):
                        raise ModelError("knowledge_changed")
                    db.merge(
                        ChunkEmbedding(
                            organization_id=org_id,
                            document_id=d.id,
                            profile=key[2],
                            model=config.embedding_model,
                            text_hash=text_hash(d),
                            vector=vector,
                        )
                    )
                job = db.get(IndexJob, key)
                job.status = "ready"
                job.error = None
                job.input_tokens += client.usage["embedding_tokens"]
                job.updated_at = utcnow()
                db.commit()
        else:
            with sessions() as db:
                job = db.get(IndexJob, key)
                job.status = "ready"
                job.error = None
                job.updated_at = utcnow()
                db.commit()
    except Exception as exc:
        with sessions() as db:
            job = db.get(IndexJob, key)
            if job:
                job.status = "failed"
                job.error = (
                    exc.code
                    if isinstance(exc, ModelError)
                    else "demo_budget_exhausted"
                    if isinstance(exc, HTTPException) and exc.status_code == 429
                    else "indexing_failed"
                )
                job.updated_at = utcnow()
                db.commit()
    finally:
        if reservation_id:
            settle(
                sessions,
                reservation_id,
                client.usage if client else {},
                uncertain=bool(client and any([getattr(client, "uncertain", False)])),
            )
        if client:
            client.close()


def index_status(db, config, source):
    job = db.get(IndexJob, (source.organization_id, source.id, profile(config)))
    return {
        "status": job.status if job else "not_indexed",
        "error": job.error if job else None,
        "model": config.embedding_model,
        "updated_at": job.updated_at.isoformat() if job else None,
    }


def search(db, config, client, org_id, query, limit=6):
    if not query.strip() or len(query) > 1000:
        raise ModelError("invalid_tool_arguments")
    day = date.today().isoformat()
    pr = profile(config)
    current = [
        Document.organization_id == org_id,
        Document.enabled.is_(True),
        Document.effective_from <= day,
        (Document.effective_to.is_(None)) | (Document.effective_to > day),
    ]
    docs = db.scalars(select(Document).where(*current)).all()
    if not docs:
        return []
    vector = client.embeddings([query])[0]
    byid = {d.id: d for d in docs}
    # Scope and model-profile filters are applied before candidates reach the agent.
    stmt = (
        select(ChunkEmbedding)
        .join(
            Document,
            (Document.organization_id == ChunkEmbedding.organization_id)
            & (Document.id == ChunkEmbedding.document_id),
        )
        .where(
            *current,
            ChunkEmbedding.organization_id == org_id,
            ChunkEmbedding.profile == pr,
        )
    )
    if db.bind.dialect.name == "postgresql":
        stmt = stmt.order_by(
            cast(ChunkEmbedding.vector, Vector(1536)).cosine_distance(vector)
        ).limit(60)
    rows = db.scalars(stmt).all()
    scored = []
    for row in rows:
        d = byid[row.document_id]
        if row.text_hash != text_hash(d):
            continue
        cosine = sum(a * b for a, b in zip(vector, row.vector))
        if cosine < 0.20:
            continue
        lexical = len(terms(query) & terms(d.title + " " + d.text))
        scored.append((cosine + min(lexical, 5) * 0.015, row, d, cosine))
    # Maximal marginal relevance avoids filling the context with near-identical chunks.
    selected = []
    while scored and len(selected) < limit:
        best = max(
            scored,
            key=lambda x: 0.8 * x[0]
            - 0.2
            * max(
                (
                    sum(a * b for a, b in zip(x[1].vector, y[1].vector))
                    for y in selected
                ),
                default=0,
            ),
        )
        selected.append(best)
        scored.remove(best)
    return [
        {
            **serialize_document(d),
            "score": round(cosine, 4),
            "embedding_model": config.embedding_model,
        }
        for _, _, d, cosine in selected
    ]
