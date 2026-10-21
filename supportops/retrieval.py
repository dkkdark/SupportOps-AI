"""Version-aware hybrid retrieval. Hash embeddings are an offline baseline, not an LLM embedding model."""
import hashlib
import math
import re
from collections import Counter
from datetime import date
from sqlalchemy import select
from supportops.db import Document

EMBEDDING_VERSION = "hash-char-ngrams-128-v1"


def embed(text: str) -> list[float]:
    vector = [0.0] * 128
    clean = re.sub(r"\s+", " ", text.lower())
    for token in (clean[i:i+3] for i in range(max(0, len(clean)-2))):
        digest = hashlib.sha256(token.encode()).digest()
        vector[int.from_bytes(digest[:2], "big") % 128] += 1.0 if digest[2] % 2 else -1.0
    norm = math.sqrt(sum(x*x for x in vector)) or 1
    return [x/norm for x in vector]


def active_documents(session, on: date | None = None):
    day = (on or date.today()).isoformat()
    docs = session.scalars(select(Document).where(Document.effective_from <= day, (Document.effective_to.is_(None)) | (Document.effective_to > day))).all()
    counts = Counter(d.key for d in docs)
    if any(n > 1 for n in counts.values()):
        raise ValueError("conflicting_documents")
    return docs


def retrieve(session, query: str, categories: list[str], on: date | None = None):
    docs = active_documents(session, on)
    q = embed(query)
    # PostgreSQL uses pgvector cosine ordering; SQLite mirrors the same distance in Python.
    if session.bind.dialect.name == "postgresql":
        ordered = session.scalars(select(Document).where(Document.id.in_([d.id for d in docs])).order_by(Document.embedding.comparator.cosine_distance(q))).all()
        ranks = {d.id: i for i,d in enumerate(ordered)}
    else:
        ranks = {d.id:i for i,d in enumerate(sorted(docs, key=lambda d: -sum(a*b for a,b in zip(q,d.embedding))))}
    scored = []
    for doc in docs:
        hits = sum(tag in query.lower() for tag in doc.tags)
        score = (4 if doc.category in categories else 0) + hits * 3 + 1/(1+ranks[doc.id])
        if doc.category in categories or hits:
            scored.append((score, doc))
    return [d for _,d in sorted(scored,key=lambda x:-x[0])[:6]]


def serialize_document(d):
    return {k:getattr(d,k) for k in ("id","key","title","category","version","effective_from","effective_to","text","tags","facts")}
