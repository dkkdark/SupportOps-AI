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
docker compose --env-file /dev/null -f compose.qdrant.yaml up -d
python -m scripts.dev
```

Open [the platform](http://127.0.0.1:8010) or [the product overview](http://127.0.0.1:8010/#home). API docs: [OpenAPI](http://127.0.0.1:8010/docs). Original local databases were backed up before migrations under ignored `var/backups/`. No secrets belong in Git.

The full setup guide and remaining founder actions are in [launch checklist](docs/launch-checklist.md). The current demo API setup is in [API connection steps](docs/api-setup.md). Future pricing assumptions remain in [unit economics](docs/unit-economics.md).

## Real agent and RAG

Default business configuration:

| Component | Implementation |
| --- | --- |
| Agent | `gpt-5.4-mini`, OpenAI Responses API, bounded tool-calling loop |
| Grounding reviewer | Separate fresh model call with a strict structured verdict |
| Embeddings | `text-embedding-3-large`, real 1536-dimensional vectors |
| Search | Tenant/profile-scoped Qdrant cosine search with SQL evidence checks, lexical ranking and maximal marginal relevance |
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

1. Register your business; you become its owner. Each account has a one-time $5 AI demo allowance across all workspaces.
2. Create **Request types**, such as vendor inquiry, travel expenses or IT access. Add instructions for investigation. Mark a type public only when it may appear on a form.
3. Upload documents in **Knowledge base**. Wait for **Semantic index ready**; retry failed indexing from the source dialog. Publish a new version instead of changing an existing rule in place.
4. Customize **Reply templates**. Include `{{facts}}` exactly once; optional variables include requester name (`{{customer_name}}`), business, agent, subject and request ID.
5. Invite employees through single-use, email-bound links. Owners manage admins and agents; admins manage agents; agents investigate and review requests.
6. Connect a source or create an internal request. Run the agent, inspect its sources/tool record, edit the draft, then approve or escalate.
7. Review **AI usage**. Purchasing is coming soon; request a launch email or continue manual work after the AI allowance is used.

## Demo release and cost control

`DEMO_RELEASE=true` is the default. Prices and paid plans are hidden. Checkout, Customer Portal and payment webhooks are disabled even if Stripe keys exist. Previous subscription entitlements do not bypass the demo budget.

Each user account has a one-time **$5 AI usage allowance**, shared across all its businesses, with no monthly reset or 14-day expiry. Paid agent calls, query embeddings, verification and document indexing are attributed to the initiating user. The server atomically reserves estimated dollars before calling a model, then settles usage to millionths of a dollar. Known unused reserves are released; unknown provider charges stay conservatively accounted until reconciliation. The per-run provider limit remains $0.40. Update token prices if models change.

At the limit, further paid calls pause. Creating/reading/reviewing requests, inviting colleagues, editing templates and saving knowledge remain available. Knowledge can be stored while its paid index waits. Former plan-based seat/request/storage caps do not apply in the demo; security throttles and upload size limits remain.

The exhausted account is marked in `demo_accounts` with its user ID, limit timestamp, notification request, reason and pending email state. Users can also choose **Email me at launch** earlier. A local operator command exports verified, pending recipients without sending anything:

```bash
python -m scripts.demo_waitlist --output var/launch-notifications.csv
```

A future launch mailing requires a configured provider, reviewed content and actual sending; `launch_email_sent_at` must only be recorded after delivery handling. See [API setup](docs/api-setup.md). Earlier company-level model spending is conservatively migrated to the owner’s demo balance in migration 004. Future subscription code is retained behind the disabled release mode; changing that mode is a separate product launch.

## Verification

```bash
make test
npm ci --ignore-scripts --no-audit --no-fund
npm test                  # DOM regression tests: navigation, races, forms, CAPTCHA lifecycle
make eval                 # legacy synthetic retail benchmark only
python -m scripts.live_acceptance  # opt-in real OpenAI calls; synthetic data; $0.20 run budget
npm run check:format
```

The [local UI verification report](docs/ui-verification.md) records browser checks, responsive layouts and fixed edge cases. CI runs the frontend regression suite alongside backend tests.

A real OpenAI acceptance test passed for an internal travel-expense question: document embedding → semantic search tool → generated policy explanation → separate grounding check → saved human approval. Its agent/query/review calls cost approximately **$0.00354 in that one run**, excluding initial document indexing. This is not an average-cost or production-accuracy benchmark. Controlled tests additionally cover bad citations, model rejection, indexing failures/retry, tenant boundaries, inbound channels, quotas, Stripe signatures/lifecycle and recovery tokens.

```bash
# A full local PostgreSQL + Qdrant stack; configure .env first.
docker compose up --build -d
# A separate indexing worker is included.
docker compose down
```

Real local Qdrant and PostgreSQL integrations have passed, including the agent workflow with controlled model responses. See [Qdrant setup and migration](docs/qdrant.md). Terraform schema validation, Helm rendering/lint and Compose configuration checks are available. AWS and Kubernetes have not been deployed. Start real pilots and validate representative business requests before public expansion.

## Gmail sender

The configured sender/contact is `info.xenonimus@gmail.com`. Server-side STARTTLS/SSL transport, credential-aware readiness and non-sending SMTP diagnostics are implemented. Add the Google App Password only to local `.env` or server secret storage; see [Gmail setup](docs/gmail-setup.md). `python -m scripts.check_email` performs no network requests; add `--connect` to check TLS/authentication without sending a message. Incoming Gmail synchronization is not implemented.

## Delivery and operation

Durable account-mail delivery, worker heartbeat/retries, technical retention, private account export/deletion requests, AI-charge reconciliation and isolated SQLite restore drills are implemented. Commands and external launch prerequisites: [backend operations](docs/backend-operations.md). Production startup checks the schema; `python -m scripts.migrate` initializes both application and checkpoint tables.

- SQLite quick start; private PostgreSQL for business records/checkpoints and separate Qdrant for semantic vectors in deployment. Historical migrations still require the vector extension; new semantic indexing does not write SQL vectors.
- Terraform: HTTPS ALB, Fargate application/indexer, RDS, ECR, Secrets Manager, CloudWatch, optional telemetry and scoped GitHub OIDC.
- Helm and Compose include a durable indexing worker. Failed/interrupted jobs can be retried safely.
- Production smoke uses a pre-verified account through `SMOKE_EMAIL` / `SMOKE_PASSWORD`; it never bypasses email verification.
- Owner JSON export includes business records without passwords, session cookies, invitation hashes or API credentials.
- Verification/recovery links are expiring, single-use and hashed; password reset revokes existing sessions.
- Operator policy drafts are available at `/terms` and `/privacy`. Future production checkout requires operator details and reviewed HTTPS policy URLs; demo checkout is disabled. Configuration checks in Settings do not prove legal suitability or deployment success.

Migrations `002`–`005` preserve existing records and prior model usage. Back up both application/checkpoint data before upgrades. `002` is forward-only; returning to the former single-business binary requires its matching database/code backup.

Repository remote: [dkkdark/SupportOps-AI](https://github.com/dkkdark/SupportOps-AI). Work remains in local feature commits. **No push, publication or cloud apply has been performed.**

Details: [architecture](docs/architecture.md), [deployment](docs/deployment.md), [runbook](docs/runbook.md), [verification](docs/verification.md).
