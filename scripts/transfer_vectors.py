"""Copy existing SQL embeddings to Qdrant without any model calls. Dry run by default."""

import argparse
import json
from sqlalchemy import select
from supportops.config import Settings
from supportops.db import (
    ChunkEmbedding,
    Document,
    IndexJob,
    KnowledgeSource,
    Organization,
    utcnow,
    make_database,
)
from supportops.semantic import (
    enqueue,
    index_source,
    legacy_profile,
    profile,
    text_hash,
)
from supportops.vector_store import valid_vector


def transfer(config, sessions, apply=False, vector_transport=None):
    summary = {
        "mode": "apply" if apply else "dry_run",
        "eligible_sources": 0,
        "sources_without_complete_legacy_vectors": 0,
        "transferred_sources": 0,
        "failed_sources": 0,
        "model_calls": 0,
    }
    with sessions() as db:
        sources = db.scalars(
            select(KnowledgeSource)
            .join(Organization, Organization.id == KnowledgeSource.organization_id)
            .where(KnowledgeSource.archived.is_(False), Organization.is_demo.is_(False))
        ).all()
        eligible = []
        for source in sources:
            docs = db.scalars(
                select(Document).where(
                    Document.organization_id == source.organization_id,
                    Document.source_id == source.id,
                )
            ).all()
            cached = [
                db.get(
                    ChunkEmbedding,
                    (source.organization_id, d.id, legacy_profile(config)),
                )
                for d in docs
            ]
            if not docs or not all(
                c and c.text_hash == text_hash(d) and valid_vector(list(c.vector))
                for d, c in zip(docs, cached)
            ):
                summary["sources_without_complete_legacy_vectors"] += 1
                continue
            eligible.append((source.organization_id, source.id))
    summary["eligible_sources"] = len(eligible)
    if apply:
        for org_id, source_id in eligible:
            with sessions() as db:
                job = enqueue(db, config, org_id, source_id)
                if job.status == "indexing":
                    summary["failed_sources"] += 1
                    continue
                job.status, job.attempts, job.lease_token = "pending", 0, None
                job.next_attempt_at = utcnow()
                db.commit()
            # Never fall through to a paid provider, even if content changes mid-transfer.
            index_source(
                config,
                sessions,
                org_id,
                source_id,
                vector_transport=vector_transport,
                allow_model_calls=False,
            )
            with sessions() as db:
                job = db.get(IndexJob, (org_id, source_id, profile(config)))
                summary[
                    "transferred_sources"
                    if job and job.status == "ready"
                    else "failed_sources"
                ] += 1
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm-stopped",
        action="store_true",
        help="Confirm API and workers are stopped before applying the transfer",
    )
    args = parser.parse_args()
    if args.apply and not args.confirm_stopped:
        parser.error("Stop API and workers, then pass --confirm-stopped with --apply")
    config = Settings()
    engine, sessions = make_database(config.database_url)
    try:
        result = transfer(config, sessions, args.apply)
        print(json.dumps(result, indent=2))
        if result["failed_sources"]:
            raise SystemExit(2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
