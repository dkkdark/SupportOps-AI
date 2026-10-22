"""Tenant-scoped semantic RAG with versioned, resumable document indexing."""

from datetime import date, timedelta
import hashlib
import math
from sqlalchemy import select, update
from fastapi import HTTPException
from supportops.db import (
    Organization,
    ChunkEmbedding,
    CreditReservation,
    Document,
    IndexJob,
    KnowledgeSource,
    VectorEntry,
    utcnow,
)
from supportops.openai_client import ModelError, OpenAIClient
from supportops.retrieval import terms, serialize_document
from supportops.billing import reserve, settle, reservation_budget, CREDIT_USD
from supportops.auth import identifier
from supportops.vector_store import QdrantStore, point_id, valid_vector


def legacy_profile(config):
    return hashlib.sha256(
        f"{config.openai_base_url.rstrip('/')}:{config.embedding_model}:1536:v1".encode()
    ).hexdigest()


def profile(config):
    # Keep snapshots portable across hostnames. Missing remote data fails closed
    # at retrieval; a physical server address is not an embedding-model profile.
    return hashlib.sha256(
        f"{legacy_profile(config)}:qdrant-v1:{config.qdrant_collection}".encode()
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
        job.status, job.attempts = "pending", 0
        job.next_attempt_at, job.lease_token = utcnow(), None
    return job


def index_source(
    config,
    sessions,
    org_id,
    source_id,
    transport=None,
    vector_transport=None,
    *,
    allow_model_calls=True,
):
    if config.embedding_provider != "openai":
        return
    key = (org_id, source_id, profile(config))
    claim = identifier()
    client = None
    store = None
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
                IndexJob.attempts < 3,
                (
                    (IndexJob.status.in_(["pending", "failed"]))
                    & (IndexJob.next_attempt_at <= utcnow())
                )
                | (
                    (IndexJob.status == "indexing")
                    & (
                        IndexJob.updated_at
                        < utcnow()
                        - timedelta(seconds=max(180, config.agent_timeout_seconds + 30))
                    )
                ),
            )
            .values(
                status="indexing",
                lease_token=claim,
                error=None,
                attempts=IndexJob.attempts + 1,
                updated_at=utcnow(),
            )
        ).rowcount
        db.commit()
        if not claimed:
            return
        job = db.get(IndexJob, key, populate_existing=True)
        user_id, attempt = job.requested_by, job.attempts
        docs = db.scalars(
            select(Document).where(
                Document.organization_id == org_id, Document.source_id == source_id
            )
        ).all()
    try:
        # Check Qdrant before reserving allowance or contacting a paid model.
        store = QdrantStore(config, key[2], vector_transport)
        store.ensure()
        points = {
            d.id: {
                "id": point_id(org_id, d.id, key[2], text_hash(d)),
                "payload": {
                    "organization_id": org_id,
                    "document_id": d.id,
                    "source_id": source_id,
                    "profile": key[2],
                    "text_hash": text_hash(d),
                },
            }
            for d in docs
        }
        # Deterministic IDs allow a retry after Qdrant succeeded but SQL commit failed.
        remote = {
            str(p.get("id")): p
            for p in store.retrieve([p["id"] for p in points.values()])
        }
        with sessions() as db:
            for d in docs:
                point = points[d.id]
                existing = remote.get(point["id"], {})
                if existing.get("payload") == point["payload"] and valid_vector(
                    existing.get("vector")
                ):
                    point["vector"] = existing["vector"]
                    continue
                # Read-only legacy cache: never pay to re-embed an unchanged document.
                cached = db.get(ChunkEmbedding, (org_id, d.id, legacy_profile(config)))
                if (
                    cached
                    and cached.text_hash == text_hash(d)
                    and valid_vector(list(cached.vector))
                ):
                    point["vector"] = list(cached.vector)
        pending = [d for d in docs if "vector" not in points[d.id]]
        if pending and not allow_model_calls:
            raise ModelError("legacy_vectors_missing")
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
            for d, vector in zip(pending, vectors):
                points[d.id]["vector"] = vector
        with sessions() as db:
            job = db.scalar(
                select(IndexJob)
                .where(
                    IndexJob.organization_id == org_id,
                    IndexJob.source_id == source_id,
                    IndexJob.profile == key[2],
                    IndexJob.lease_token == claim,
                )
                .with_for_update()
            )
            if not job:
                raise ModelError("index_claim_expired")
            source = db.get(KnowledgeSource, (org_id, source_id))
            if not source or source.archived:
                raise ModelError("knowledge_changed")
            for d in docs:
                current = db.get(Document, (org_id, d.id))
                if not current or text_hash(current) != text_hash(d):
                    raise ModelError("knowledge_changed")
            # An acknowledged Qdrant write precedes SQL evidence publication.
            store.upsert(list(points.values()))
            for d in docs:
                db.merge(
                    VectorEntry(
                        organization_id=org_id,
                        document_id=d.id,
                        profile=key[2],
                        point_id=points[d.id]["id"],
                        model=config.embedding_model,
                        text_hash=text_hash(d),
                    )
                )
            job.status, job.lease_token, job.next_attempt_at, job.error = (
                "ready",
                None,
                None,
                None,
            )
            job.input_tokens += client.usage["embedding_tokens"] if client else 0
            job.updated_at = utcnow()
            db.commit()
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, ModelError)
            else "demo_budget_exhausted"
            if isinstance(exc, HTTPException) and exc.status_code in (402, 429)
            else "membership_revoked"
            if isinstance(exc, HTTPException) and exc.status_code == 403
            else "indexing_failed"
        )
        blocked = code in (
            "model_not_configured",
            "demo_budget_exhausted",
            "membership_revoked",
            "agent_budget_exceeded",
            "legacy_vectors_missing",
            "vector_store_not_configured",
            "vector_store_auth_failed",
            "vector_store_tls_required",
            "vector_store_profile_mismatch",
        )
        with sessions() as db:
            db.execute(
                update(IndexJob)
                .where(
                    IndexJob.organization_id == org_id,
                    IndexJob.source_id == source_id,
                    IndexJob.profile == key[2],
                    IndexJob.lease_token == claim,
                )
                .values(
                    status="blocked" if blocked else "failed",
                    error=code,
                    lease_token=None,
                    next_attempt_at=None
                    if blocked or attempt >= 3
                    else utcnow() + timedelta(seconds=30 * 2 ** (attempt - 1)),
                    updated_at=utcnow(),
                )
            )
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
        if store:
            store.close()


def index_status(db, config, source):
    job = db.get(IndexJob, (source.organization_id, source.id, profile(config)))
    return {
        "status": job.status if job else "not_indexed",
        "error": job.error if job else None,
        "model": config.embedding_model,
        "store": "qdrant",
        "updated_at": job.updated_at.isoformat() if job else None,
    }


def search(db, config, client, org_id, query, limit=6, vector_transport=None):
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
    # Scope and model-profile filters are applied before candidates reach the agent.
    stmt = (
        select(VectorEntry, Document)
        .select_from(VectorEntry)
        .join(
            Document,
            (Document.organization_id == VectorEntry.organization_id)
            & (Document.id == VectorEntry.document_id),
        )
        .where(
            *current,
            VectorEntry.organization_id == org_id,
            VectorEntry.profile == pr,
        )
    )
    eligible = list(db.scalars(stmt.with_only_columns(Document.id)))
    if not eligible:
        return []
    store = QdrantStore(config, pr, vector_transport)
    try:
        scope = store.scoped_filter(org_id, pr, eligible)
        if not store.count(scope):
            raise ModelError("vector_index_missing")
        vector = client.embeddings([query])[0]
        matches = store.query(vector, scope)
    finally:
        store.close()
    # Treat remote payloads as untrusted: authorize every candidate again in SQL.
    candidates = {}
    for point in matches:
        payload = point.get("payload", {})
        if (
            payload.get("organization_id") == org_id
            and payload.get("profile") == pr
            and payload.get("document_id") in eligible
            and valid_vector(point.get("vector"))
        ):
            candidates[str(point.get("id"))] = point
    if not candidates:
        return []
    rows = db.execute(stmt.where(VectorEntry.point_id.in_(candidates))).all()
    scored = []
    for row, d in rows:
        point = candidates.get(row.point_id)
        if (
            not point
            or row.text_hash != text_hash(d)
            or point["payload"].get("document_id") != d.id
            or point["payload"].get("text_hash") != row.text_hash
        ):
            continue
        retrieved_vector = point["vector"]
        norm = math.sqrt(
            sum(a * a for a in vector) * sum(b * b for b in retrieved_vector)
        )
        cosine = sum(a * b for a, b in zip(vector, retrieved_vector)) / norm
        if cosine < 0.20:
            continue
        lexical = len(terms(query) & terms(d.title + " " + d.text))
        normalized = [
            v / math.sqrt(sum(a * a for a in retrieved_vector))
            for v in retrieved_vector
        ]
        scored.append((cosine + min(lexical, 5) * 0.015, normalized, d, cosine))
    # Maximal marginal relevance avoids filling the context with near-identical chunks.
    selected = []
    while scored and len(selected) < limit:
        best = max(
            scored,
            key=lambda x: 0.8 * x[0]
            - 0.2
            * max(
                (sum(a * b for a, b in zip(x[1], y[1])) for y in selected),
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
