import os
import uuid
import httpx
import pytest
from sqlalchemy import select
from supportops.config import Settings
from supportops.db import ChunkEmbedding, Document, IndexJob, VectorEntry
from supportops.openai_client import ModelError
from supportops.semantic import index_source, legacy_profile, profile, text_hash
from supportops.vector_store import QdrantStore, point_id
from tests.test_agent_semantic import publish, analyze


def test_vectors_live_in_qdrant_and_sql_has_only_registry(ai_client, vector_service):
    c, _ = ai_client
    publish(c)
    with c.app.state.sessions() as db:
        assert not db.scalars(select(ChunkEmbedding)).all()
        entries = db.scalars(select(VectorEntry)).all()
        assert entries and not hasattr(entries[0], "vector")
        assert sum(
            len(x["points"]) for x in vector_service.collections.values()
        ) == len(entries)
    assert analyze(c).json()["status"] == "review"
    query = next(
        body for _, path, body in vector_service.calls if path.endswith("/points/query")
    )
    assert {x["key"] for x in query["filter"]["must"]} == {
        "organization_id",
        "profile",
        "document_id",
    }


def test_qdrant_failure_blocks_indexing_before_any_model_cost(
    ai_client, vector_service
):
    c, models = ai_client
    vector_service.unavailable = True
    from tests.test_workspaces import POLICY

    assert c.post("/api/knowledge", json=POLICY).status_code == 201
    status = c.get("/api/knowledge").json()[0]["indexing"]
    assert (
        status["status"] == "failed" and status["error"] == "vector_store_unavailable"
    )
    assert models.calls == []


def test_qdrant_outage_stops_agent_before_paid_model_calls(ai_client, vector_service):
    c, models = ai_client
    publish(c)
    before = len(models.calls)
    vector_service.unavailable = True
    result = analyze(c).json()
    assert (
        result["status"] == "manual"
        and result["result"]["reason"] == "vector_store_unavailable"
    )
    assert len(models.calls) == before


def test_unconfirmed_writes_never_publish_ready_and_retry_reuses_points(
    ai_client, vector_service
):
    c, models = ai_client
    vector_service.unconfirmed = True
    from tests.test_workspaces import POLICY

    source = c.post("/api/knowledge", json=POLICY).json()
    assert c.get("/api/knowledge").json()[0]["indexing"]["status"] == "failed"
    before = len(models.calls)
    vector_service.unconfirmed = False
    assert c.post(f"/api/knowledge/{source['id']}/reindex").status_code == 200
    assert c.get("/api/knowledge").json()[0]["indexing"]["status"] == "ready"
    assert len(models.calls) == before


def test_search_rechecks_foreign_and_stale_remote_payloads(ai_client, vector_service):
    c, _ = ai_client
    publish(c)
    original = next(
        iter(next(iter(vector_service.collections.values()))["points"].values())
    )
    vector_service.inject = [
        {
            **original,
            "payload": {**original["payload"], "organization_id": "another-company"},
        }
    ]
    assert analyze(c).json()["status"] == "review"
    for collection in vector_service.collections.values():
        for p in collection["points"].values():
            p["payload"]["text_hash"] = "old-text"
    vector_service.inject = []
    assert analyze(c).json()["status"] == "manual"


def test_legacy_vectors_transfer_without_paid_calls(ai_client, vector_service):
    c, models = ai_client
    source = publish(c)
    cfg = c.app.state.auth.config
    org = c.get("/api/auth/me").json()["organization"]["id"]
    with c.app.state.sessions() as db:
        for d in db.scalars(select(Document).where(Document.organization_id == org)):
            db.add(
                ChunkEmbedding(
                    organization_id=org,
                    document_id=d.id,
                    profile=legacy_profile(cfg),
                    model=cfg.embedding_model,
                    text_hash=text_hash(d),
                    vector=[1.0] + [0.0] * 1535,
                )
            )
        db.commit()
    target = cfg.model_copy(update={"qdrant_collection": "transferred"})
    before = len(models.calls)
    index_source(
        target, c.app.state.sessions, org, source["id"], allow_model_calls=False
    )
    with c.app.state.sessions() as db:
        assert db.get(IndexJob, (org, source["id"], profile(target))).status == "ready"
    assert len(models.calls) == before

    # Operator transfer is dry-run by default and repairs a restored-empty store.
    from scripts.transfer_vectors import transfer

    assert transfer(cfg, c.app.state.sessions)["transferred_sources"] == 0
    vector_service.collections.clear()
    summary = transfer(cfg, c.app.state.sessions, apply=True)
    assert summary["transferred_sources"] == 1 and summary["model_calls"] == 0
    assert len(models.calls) == before


def test_collection_namespace_is_scoped_snapshots_are_portable_and_missing_store_fails_closed(
    ai_client, vector_service
):
    c, _ = ai_client
    publish(c)
    cfg = c.app.state.auth.config
    assert profile(cfg) == profile(
        cfg.model_copy(update={"qdrant_url": "http://other:6333"})
    )
    assert profile(cfg) != profile(
        cfg.model_copy(update={"qdrant_collection": "other"})
    )
    vector_service.collections.clear()
    result = analyze(c).json()
    assert result["status"] == "manual" and not result["result"]["draft"]


def test_wrong_vector_dimensions_and_unsafe_secret_transport_are_rejected():
    with pytest.raises(ModelError, match="vector_store_tls_required"):
        QdrantStore(
            Settings(qdrant_url="http://external.test:6333", qdrant_api_key="private"),
            "p",
        )
    store = QdrantStore(Settings(), "p")
    try:
        with pytest.raises(ModelError, match="invalid_embeddings"):
            store.upsert([{"id": str(uuid.uuid4()), "vector": [1.0, 0.0]}])
    finally:
        store.close()


@pytest.mark.skipif(
    not os.getenv("TEST_QDRANT_URL"),
    reason="requires isolated real Qdrant; exercised in CI",
)
def test_real_qdrant_roundtrip_tenant_filtering_and_persistence():
    cfg = Settings(
        qdrant_url=os.environ.get("TEST_QDRANT_URL", ""),
        qdrant_collection="ci_" + uuid.uuid4().hex,
    )
    # An explicit real transport bypasses the offline fixture, without an API key.
    store = QdrantStore(cfg, "profile", httpx.HTTPTransport())
    try:
        store.ensure()
        points = [
            {
                "id": point_id(org, "doc", "profile", "hash"),
                "vector": [1.0] + [0.0] * 1535,
                "payload": {
                    "organization_id": org,
                    "document_id": "doc",
                    "profile": "profile",
                    "text_hash": "hash",
                },
            }
            for org in ("a", "b")
        ]
        store.upsert(points)
        scope = store.scoped_filter("a", "profile", ["doc"])
        assert store.count(scope) == 1
        assert {
            p["payload"]["organization_id"]
            for p in store.query(points[0]["vector"], scope)
        } == {"a"}
        assert len(store.retrieve([p["id"] for p in points])) == 2
        reopened = QdrantStore(cfg, "profile", httpx.HTTPTransport())
        try:
            assert reopened.count(scope) == 1
        finally:
            reopened.close()
    finally:
        store._request("DELETE", store.path)
        store.close()


@pytest.mark.skipif(
    not os.getenv("TEST_QDRANT_URL"),
    reason="requires isolated real Qdrant; exercised in CI",
)
def test_real_qdrant_agent_index_review_and_archive(tmp_path):
    from fastapi.testclient import TestClient
    from supportops.main import create_app
    from tests.test_agent_semantic import Models
    from tests.test_workspaces import register

    models = Models()
    cfg = Settings(
        database_url=os.getenv("TEST_DATABASE_URL", f"sqlite:///{tmp_path}/qdrant.db"),
        model_provider="openai",
        embedding_provider="openai",
        openai_api_key="synthetic-only",
        qdrant_url=os.environ.get("TEST_QDRANT_URL", ""),
        qdrant_collection="ci_" + uuid.uuid4().hex,
    )
    transport = httpx.MockTransport(models)
    app = create_app(
        cfg,
        llm_transport=transport,
        embedding_transport=transport,
        vector_transport=httpx.HTTPTransport(),
    )
    try:
        with TestClient(app) as c:
            register(c, "Qdrant integration", f"qa-{uuid.uuid4().hex}@example.test")
            source = publish(c)
            result = analyze(c).json()
            assert (
                result["status"] == "review"
                and result["result"]["manifest"]["vector_store"] == "qdrant"
            )
            assert c.post(f"/api/knowledge/{source['id']}/archive").status_code == 200
            assert analyze(c).json()["status"] == "manual"
    finally:
        store = QdrantStore(cfg, profile(cfg), httpx.HTTPTransport())
        try:
            store._request("DELETE", store.path, allow_missing=True)
        finally:
            store.close()
