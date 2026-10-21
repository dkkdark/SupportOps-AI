# SupportOps AI

A multi-business AI platform for incoming requests: operations, sales, internal services, IT and customer teams. Businesses register a workspace, configure request types, connect incoming work, upload knowledge and invite employees. A real OpenAI agent investigates each request with scoped tools and semantic RAG, prepares a cited response, and passes it through a separate grounding check before human review.

![SupportOps platform](docs/preview.png)

## Start locally

Python 3.13 is recommended. Node is needed only for frontend formatting.

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
cp .env.example .env
# Set OPENAI_API_KEY in the server environment or ignored .env.
python -m scripts.dev
```

Open [the platform](http://127.0.0.1:8010) or [the product overview](http://127.0.0.1:8010/#home). API docs: [OpenAPI](http://127.0.0.1:8010/docs). Original local databases were backed up before migrations under ignored `var/backups/`. No secrets belong in Git.

The full setup guide and remaining founder actions are in [launch checklist — Russian](docs/launch-checklist.ru.md). Pricing and cost assumptions are in [unit economics](docs/unit-economics.md).

## Real agent and RAG

Default business configuration:

| Component | Implementation |
| --- | --- |
| Agent | `gpt-5.4-mini`, OpenAI Responses API, bounded tool-calling loop |
| Grounding reviewer | Separate fresh model call with a strict structured verdict |
| Embeddings | `text-embedding-3-large`, real 1536-dimensional vectors |
| Search | Tenant/profile-scoped pgvector cosine search, lexical ranking and maximal marginal relevance |
| Documents | Selectable-text PDF, DOCX, TXT, Markdown and JSON; immutable versions and effective dates |
| Indexing | Durable database jobs, immediate background processing, separate restartable worker and retry endpoint |
| Evidence | Paragraph-level citations, current-source rechecks and saved model/document/template versions |
| Human control | Persistent LangGraph interrupt/resume; approve, edit or escalate; no external action tools |

The agent can search again, read passages, inspect request types/templates and request a handoff. Tools cannot choose a tenant, execute arbitrary code, send replies, issue payments or change external records. Every factual answer block must cite retrieved evidence. A separate checker sees the complete rendered answer, including the template. Unsupported citations, failed grounding, model problems or exceeded budgets prevent an approvable draft. Model verification is a review aid, not a guarantee of truth; a person remains responsible for the decision.

Search does not require literal word overlap in semantic mode. Embedding model, endpoint and dimension profile are recorded so different indexes cannot be mixed. Changing the embedding model requires reindexing. Current/archived/future document rules and company filters are applied before evidence reaches the agent.

`MODEL_PROVIDER=baseline` and `EMBEDDING_PROVIDER=baseline` are explicit offline fixture modes. Sample retail workspaces remain synthetic and cannot own real businesses, create live intake connections or consume free external embedding calls. The separate 100-case retail benchmark does not measure the new general-purpose agent.

## Where requests come from

1. **Hosted form:** an admin creates a public request type and form in **Request sources**, then shares its link. A submission becomes a request in that company’s queue. No AI work is triggered automatically.
2. **Inbound API:** a website, CRM or workflow sends JSON with a write-only Bearer credential. Tokens are shown once, stored hashed and revocable. `external_id` deduplicates imports. The caller cannot read requests, pick another company, select privileged routing or trigger billable AI.
3. **Employees:** members create internal requests, choose a type and route work to an active teammate. Agents can assign work to themselves; admins manage other assignments.

Native email inbox and messenger synchronization are **not connected**. They require an adapter from the business’s selected provider to the inbound API. Request attachments and automatic outbound delivery are not implemented. Knowledge-document uploads are a separate feature.

Production public forms require configured Turnstile protection. Production signup uses Turnstile by default, and verified email is required before AI processing, indexing or external-source setup. Tokens, CSRF, request size limits and persistent throttles protect the entry points. A verified sender/SMTP account is required to actually deliver verification and password-recovery emails.

## Workspace setup

1. Register your business; you become its owner and start a 14-day trial.
2. Create **Request types**, such as vendor inquiry, travel expenses or IT access. Add instructions for investigation. Mark a type public only when it may appear on a form.
3. Upload documents in **Knowledge base**. Wait for **Semantic index ready**; retry failed indexing from the source dialog. Publish a new version instead of changing an existing rule in place.
4. Customize **Reply templates**. Include `{{facts}}` exactly once; optional variables include requester name (`{{customer_name}}`), business, agent, subject and request ID.
5. Invite employees through single-use, email-bound links. Owners manage admins and agents; admins manage agents; agents investigate and review requests.
6. Connect a source or create an internal request. Run the agent, inspect its sources/tool record, edit the draft, then approve or escalate.
7. Review **Plan & usage**. An owner chooses a paid plan through Stripe when the operator has configured payments.

## Subscriptions and cost control

| Plan | Monthly USD / workspace | Included AI credits | Seats | Requests / period | Stored knowledge chunks |
| --- | ---: | ---: | ---: | ---: | ---: |
| Trial | $0 for 14 days | 100 one-time | 3 | 300 | 300 |
| Starter | $49 | 1,000 | 5 | 10,000 | 1,000 |
| Growth | $149 | 5,000 | 20 | 50,000 | 5,000 |
| Scale | $399 | 15,000 | 50 | 100,000 | 15,000 |

Credits cover agent input/output tokens, query embeddings, verification and document indexing. Charges are rounded up from recorded provider cost using an internal $0.005 cost unit. A run reserves up to 90 available credits; unused reservation is released after known usage is recorded. The default provider cost budget is $0.40 per run. Long investigations can stop at this budget. Unknown provider charges retain their reservation until operator reconciliation.

No automatic overage charges or top-ups exist. AI and indexing pause when credits are exhausted; reading and manual review remain available. Trial credits are one-time, and paid usage resets from the verified subscription billing period. Credit quantities are **not a fixed number of replies**. Update configured token rates when changing models or provider prices.

Checkout verifies the server-side Stripe price against the published monthly USD plan. Only a signed, replay-checked webhook activates a paid entitlement; the return URL never proves payment. Subscription updates, cancellation and payment failures update access. Owners manage an existing subscription through Stripe Customer Portal. Stripe, SMTP, spam-protection keys and reviewed operator policies still need real account configuration before public sales. No live Stripe payment has been taken.

## Verification

```bash
make test
make eval                 # legacy synthetic retail benchmark only
python -m scripts.live_acceptance  # opt-in real OpenAI calls; synthetic data; $0.20 run budget
npm run check:format
```

A real OpenAI acceptance test passed for an internal travel-expense question: document embedding → semantic search tool → generated policy explanation → separate grounding check → saved human approval. Its agent/query/review calls cost approximately **$0.00354 in that one run**, excluding initial document indexing. This is not an average-cost or production-accuracy benchmark. Controlled tests additionally cover bad citations, model rejection, indexing failures/retry, tenant boundaries, inbound channels, quotas, Stripe signatures/lifecycle and recovery tokens.

```bash
# A full local pgvector stack; configure .env first.
docker compose up --build -d
# A separate indexing worker is included.
docker compose down
```

The current machine’s Docker daemon is unavailable; local PostgreSQL/container execution remains unverified and is included in CI. Terraform schema validation, Helm rendering/lint and Compose configuration checks are available. AWS and Kubernetes have not been deployed. Start real pilots and validate representative business requests before public expansion.

## Delivery and operation

- SQLite quick start; private PostgreSQL/pgvector and persisted checkpoints for deployment.
- Terraform: HTTPS ALB, Fargate application/indexer, RDS, ECR, Secrets Manager, CloudWatch, optional telemetry and scoped GitHub OIDC.
- Helm and Compose include a durable indexing worker. Failed/interrupted jobs can be retried safely.
- Production smoke uses a pre-verified account through `SMOKE_EMAIL` / `SMOKE_PASSWORD`; it never bypasses email verification.
- Owner JSON export includes business records without passwords, session cookies, invitation hashes or API credentials.
- Verification/recovery links are expiring, single-use and hashed; password reset revokes existing sessions.
- Operator policy drafts are available at `/terms` and `/privacy`. Production checkout requires operator details and reviewed HTTPS policy URLs. Configuration checks in Settings do not prove legal suitability or deployment success.

Migrations `002` and `003` preserve existing records. Back up both application/checkpoint data before upgrades. `002` is forward-only; returning to the former single-business binary requires its matching database/code backup.

Repository remote: [dkkdark/SupportOps-AI](https://github.com/dkkdark/SupportOps-AI). Work remains in local feature commits. **No push, publication or cloud apply has been performed.**

Details: [architecture](docs/architecture.md), [deployment](docs/deployment.md), [runbook](docs/runbook.md), [verification](docs/verification.md).
