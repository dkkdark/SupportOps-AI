# Local verification record

Verified October 21, 2026, for v0.3. These are local checks, not a production deployment.

| Check | Evidence |
| --- | --- |
| Python/API/workflow suite | 48 passed; 1 real PostgreSQL integration skipped locally because Docker is unavailable |
| Tenant and account boundaries | Independent companies, memberships, CSRF/origin, stale workspace headers, scoped routing/intake, invitations, verification/reset and revoked sessions covered |
| Real agent protocol | Controlled Responses/embedding transports cover tool loops, verifier rejection, invalid citations, index recovery, archive filtering, checkpoints and credit gates |
| Real OpenAI acceptance | `gpt-5.4-mini` agent + verifier, `text-embedding-3-large`, one synthetic policy/request, tool retrieval and human approval passed; sanitized report in `live-model-verification.json` |
| Cost of this one request | $0.00354394 including query embedding, agent and verifier, excluding initial source indexing; not an average or accuracy benchmark |
| Billing/intake | Signed Stripe fixture events, replay handling, configured-price validation, public/API ingestion, deduplication, credential revocation and quotas tested |
| Retail baseline | Dev 80/80 and holdout 20/20 passed; synthetic retail baseline only |
| Migration | Fresh/repeated 001→003 and preserved legacy records checked; local app migrated after consistent backup |
| Browser | Public Vendor inquiry submitted as Emma Wilson and received REQ-718081F57D; pricing and universal landing inspected; existing sample login preserved |
| Frontend | JavaScript syntax/format checked; standalone sample rebuilt |
| Containers | Compose configuration validated; Docker image/runtime not executed locally |
| Infrastructure | Terraform format/cached provider-schema validation and Helm lint/render; no plan/apply or cloud rollout |
| External services | No real Stripe purchase/webhook delivery, SMTP email, AWS or Kubernetes deployment |
| Git | Local feature commits only; no push or GitHub Actions run |

Ordinary tests disable live providers, email and payment credentials. `scripts.live_acceptance` is an explicit bounded opt-in using synthetic temporary data. Its successful example demonstrates the real API integration, not performance on arbitrary business rules. Evaluate representative requests before selling accuracy or time-saving claims. Detailed founder prerequisites are in `launch-checklist.ru.md`.
