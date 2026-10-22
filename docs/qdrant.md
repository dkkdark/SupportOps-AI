# Qdrant: vector search and migrating existing embeddings

Qdrant replaces pgvector for real business-workspace RAG. It stores 1536-dimensional OpenAI embeddings and technical chunk identifiers. Accounts, businesses, requests, source documents, permissions, costs and LangGraph checkpoints remain in SQL: SQLite locally and PostgreSQL in the existing server configurations. Qdrant does not replace the relational database for these records.

## Search workflow

1. The background job checks Qdrant availability **before** reserving money or calling the embedding model.
2. New text receives real OpenAI embeddings. Retries first check deterministic point IDs in Qdrant; matching legacy SQL embeddings can be transferred without model calls.
3. Qdrant acknowledges writes with `wait=true`. Only then does SQL save `vector_entries`: point ID, model profile, model and text hash. New vectors are not duplicated in SQL.
4. Queries include mandatory business, model-profile and current-document filters. The organization field has a tenant payload index.
5. Results are revalidated against SQL: organization, point ID, current version/dates, enabled state and text hash. Response evidence comes from SQL documents rather than untrusted Qdrant payload text.
6. Ranking combines cosine similarity, a small lexical bonus and MMR. The agent and independent verifier retain their budget limits.

When Qdrant is unavailable, the agent hands the request to a person. Manual operations and SQL readiness remain available. There is no hidden fallback to pgvector or synthetic embeddings. The synthetic sample workspace retains a separate hash/extractive baseline; historical pgvector columns remain for migration compatibility and transfer, not new semantic RAG.

## Local startup

Docker Desktop must be running. To use a standalone Qdrant alongside the Python application:

```bash
docker compose --env-file /dev/null -f compose.qdrant.yaml up -d
python -m scripts.dev
```

This standalone Compose command does not load `.env`; the file contains no settings interpolation. Data persists in a named Docker volume, and port 6333 binds only to `127.0.0.1`. This local service needs no API key and must not be exposed publicly.

Application defaults:

```dotenv
QDRANT_URL=http://127.0.0.1:6333
QDRANT_COLLECTION=supportops
QDRANT_TIMEOUT_SECONDS=10
```

The actual collection name combines the prefix with part of the profile. Profiles include the embedding endpoint, model, dimensions, format version and logical collection prefix. The physical Qdrant hostname is excluded so a consistent backup can move to another machine without repurchasing embeddings.

The main `compose.yaml` includes its own Qdrant **without publishing ports**, and supplies the internal `http://qdrant:6333` address to API/worker services. It is a development configuration with sample SQL credentials, not a production deployment.

## Transfer an existing index safely

Migration 006 creates `vector_entries` without deleting legacy SQL embeddings or business data. Stop the API and worker, then create a consistent SQL/checkpoint backup. Start Qdrant, apply migrations and inspect the transfer without writing:

```bash
python -m scripts.migrate
python -m scripts.transfer_vectors
```

After reviewing the counters:

```bash
python -m scripts.transfer_vectors --apply --confirm-stopped
```

This command never calls OpenAI or generates missing embeddings. It transfers only sources with a complete, matching legacy cache. `sources_without_complete_legacy_vectors` identifies sources requiring an explicit owner-triggered reindex through the UI; normal AI allowances apply. Restart the API and worker after transfer and verify a representative request.

For `vector_store_unavailable`, restore the service. For `vector_store_auth_failed`, check server credentials. For `vector_index_missing`, restore a consistent Qdrant backup or explicitly reindex. Keep the legacy cache until restoration has been verified.

## Oracle and other servers

On Oracle A1, Qdrant can run in a separate container with a persistent volume. `qdrant/qdrant:v1.19.0` has been tested locally on ARM64. No Oracle account or server has been provisioned by this project.

- API and worker must use the same Qdrant service and collection prefix.
- On a shared private Docker network, do not publish ports 6333/6334 externally or open them in OCI ingress.
- For a remote Qdrant connection, configure HTTPS and `QDRANT_API_KEY` in server secret storage. Keep the key out of the browser and repository.
- Use supported block storage and a POSIX filesystem for persistence. Fargate ephemeral storage is unsuitable for the persistent index.
- Back up Qdrant snapshots separately from SQL/checkpoints. SQL backups alone do not contain new vectors. Verify restoration in a separate collection/environment; changing the embedding model requires reindexing.
- AWS Terraform accepts an external Qdrant URL and secret ARN. It does not provision a persistent Qdrant server; account for that service's resources and cost separately.
- Helm uses external Qdrant settings from an existing Secret; see `.env.k8s.example`.

## Verification

`GET /health/vector` returns a sanitized Qdrant status. `/health/ready` checks SQL/schema/checkpoints; `/health/worker` checks the background worker heartbeat. Healthy endpoints do not prove index completeness after restoration.

Ordinary tests use a controlled Qdrant HTTP transport and synthetic OpenAI responses. To test a real server:

```bash
TEST_QDRANT_URL=http://127.0.0.1:6333 python -m pytest tests/test_qdrant.py
```

Official references: [multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/), [local startup](https://qdrant.tech/documentation/quickstart/), [security](https://qdrant.tech/documentation/security/), [snapshots](https://qdrant.tech/documentation/snapshots/).
