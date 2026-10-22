# Local verification record

Verified October 22, 2026, for the Qdrant migration release. These are local checks, not a production deployment.

| Check | Evidence |
| --- | --- |
| Python/API/workflow suite | 83 passed, including real local PostgreSQL and Qdrant integrations; no skipped tests in the final run |
| Tenant and account boundaries | Independent companies, memberships, CSRF/origin, stale workspace headers, scoped routing/intake, invitations, verification/reset and revoked sessions covered |
| Real agent protocol | Controlled Responses/embedding transports cover tool loops, verifier rejection, invalid citations, index recovery, archive filtering, checkpoints and credit gates |
| Real OpenAI acceptance | `gpt-5.4-mini` agent + verifier, `text-embedding-3-large`, one synthetic policy/request, tool retrieval and human approval passed; sanitized report in `live-model-verification.json` |
| Cost of this one request | $0.00354394 including query embedding, agent and verifier, excluding initial source indexing; not an average or accuracy benchmark |
| Demo allowance | Dollar-accurate settlement, cross-workspace account scope, concurrent reservations, retained unknown usage, disabled payments, notifications and manual access tested |
| Gmail transport | STARTTLS/SSL ordering, credential-aware readiness, no-send connection check, header rejection, sanitized errors and recovery resend/revocation tested with mocked SMTP |
| Billing/intake | Signed Stripe fixture events, replay handling, configured-price validation, public/API ingestion, deduplication, credential revocation and quotas tested |
| Retail baseline | Dev 80/80 and holdout 20/20 passed; synthetic retail baseline only |
| Migration | Fresh/repeated 001→006 checked; local 005→006 migration followed a stopped-service backup and isolated restore drill with unchanged business-record counts |
| Browser | Public Vendor inquiry submitted as Emma Wilson and received REQ-718081F57D; pricing and universal landing inspected; existing sample login preserved |
| Frontend | JavaScript syntax/format checked; standalone sample rebuilt |
| Containers | Qdrant v1.19.0 and PostgreSQL 17 containers ran locally; real server tests passed. Compose configuration validated; a full app container deployment was not run |
| Infrastructure | Terraform format/cached provider-schema validation and Helm lint/render; no plan/apply or cloud rollout |
| External services | No real Stripe purchase/webhook delivery, SMTP email, AWS or Kubernetes deployment |
| Git | Local feature commits only; no push or GitHub Actions run |

Ordinary tests disable live providers, email and payment credentials. `scripts.live_acceptance` is an explicit bounded opt-in using synthetic temporary data. Its successful example demonstrates the real API integration, not performance on arbitrary business rules. Evaluate representative requests before selling accuracy or time-saving claims. Detailed founder prerequisites are in `launch-checklist.md`.

The operations release additionally verifies a durable account-email outbox (retry/lease/deduplication/request expiry), worker probes, index backoff, account export/privacy boundaries, notification opt-out, audited reconciliation and backup corruption detection. A stopped local app/checkpoint backup was restored into an isolated directory and upgraded from schema 004 to 005 with unchanged business-record counts. No real launch email or new billable model acceptance was run for these operational changes.

The Qdrant release stores new vectors only in Qdrant and SQL evidence metadata in `vector_entries`. Actual server checks cover collection/index creation, upsert acknowledgement, tenant filters, query/retrieve and reconnect; the end-to-end agent test combines real PostgreSQL/Qdrant with controlled model responses. Failure tests cover unavailable storage before model cost, stale/foreign payloads, missing collections, profile/namespace separation and no-cost cached-vector transfer. One local legacy source was transferred without model calls; two sources lacked complete cached embeddings and were left for explicit reindexing. Qdrant/worker/API health checks passed. No new real-model call, SMTP test, cloud resource or Git push was performed for this change. See `qdrant-verification.json` and `qdrant.md`.
