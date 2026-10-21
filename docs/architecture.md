# Architecture and contracts — v0.3

## Workspaces and access

A global user can belong to multiple businesses through owner/admin/agent memberships. Business APIs derive the workspace from an authenticated server-side session and require the matching `X-Workspace-ID`. Object IDs never authorize access. Requests, source chunks, templates, routing, intake connections, usage and audit records are tenant-scoped; composite foreign keys prevent cross-business relationships. Shared PostgreSQL is used, without row-level security or per-company databases.

Passwords use Argon2id. Opaque session tokens are hashed in storage; cookies are HttpOnly/SameSite and Secure in production. Unsafe authenticated requests require CSRF. Cross-origin unsafe requests are rejected except the separately authenticated inbound API. Membership revocation takes effect on subsequent requests. Invitations are email-bound, single-use, hashed, expiring links shared manually by administrators. SMTP sends verification and recovery links; reset tokens are hashed, single-use and expiring. Password reset revokes sessions. Production AI, external intake setup, uploads and checkout require verified email. Turnstile protects production signup and public forms. MFA/SSO are not implemented.

## Universal requests and intake

Employees create requests internally. Administrators define request types and publish public forms or scoped bearer-token API connections. Each connection can route to a type; assignment validates active workspace membership. Public forms expose only their public type and submission receipt, never internal records. Revoked connections reject new requests. API credentials are shown once and stored hashed. Connection-scoped `external_id` deduplicates ingestion and rejects conflicting retries. Incoming messages do not automatically run AI or send responses.

A CRM, email service or automation can POST to the inbound API. Native mailbox, Slack, WhatsApp and helpdesk synchronization are not included. Inbound attachments and outbound delivery are not implemented.

## Knowledge and semantic retrieval

Workspace administrators write policies or import TXT, Markdown, JSON, selectable PDF and DOCX. Uploads have byte/text/page/expanded-archive budgets; parsing runs in a bounded subprocess. Original uploads are not retained. Stable source keys, immutable versions, hashes and effective intervals preserve provenance. Publishing closes prior versions under a workspace lock; archival disables retrieval. Templates are revisioned, use an allowlist of variables and interpolate only once.

Real businesses use OpenAI `text-embedding-3-large` with 1,536 dimensions. Durable index jobs track pending/indexing/ready/failed state and recover through a separate worker. Embeddings are bound to endpoint/model/dimension profile and text hash. PostgreSQL uses pgvector cosine ranking; SQLite ranks the same real vectors in Python. Retrieval filters workspace, enabled/effective date and model profile before returning evidence, then uses semantic similarity, a small lexical bonus and maximal marginal relevance. Model changes require reindexing. No hash-vector fallback is silently substituted for live semantic retrieval.

The explicitly synthetic retail sample and offline baseline retain the older 128-dimensional hash/extractive workflow for reproducibility. Sample workspaces cannot spend unmetered live-model calls and are not connected to a real merchant.

## Tool-using agent and review

The production workflow uses the OpenAI Responses API with `gpt-5.4-mini` for the agent and independent verifier. Tools search knowledge, read a cited policy, list request types/templates and request handoff. Tool inputs use strict schemas; execution supplies the authenticated workspace. First retrieval is required. The loop bounds model turns, tool calls, output tokens, time and provider cost. Tools are read-only; the agent cannot charge, refund, email or modify external systems.

The agent produces typed answer blocks and evidence IDs. Every factual block must cite retrieved workspace evidence. Sources are revalidated for current text/version/status, and the chosen company template is rendered. A separate model call checks the entire finished reply against the evidence and rejects unsupported claims or unavailable live-data/actions. Missing evidence, invalid citations, verifier rejection or provider failure produce manual handoff without an approvable draft. These checks reduce errors; they do not establish business-domain accuracy without representative evaluation.

LangGraph persists a human-review interrupt under `organization:request:revision`. SQL compare-and-swap prevents duplicate analysis and stale decisions. Processing leases cover the configured agent timeout plus recovery margin. Approval resumes the exact saved checkpoint and records the signed-in employee, final text and decision. Copied source/template versions and a model/prompt manifest remain with the result. Approval records a decision; no reply is sent automatically.

## Paid usage and billing

Trial lasts 14 days with 100 one-time credits. Starter/Growth/Scale use verified Stripe subscription periods and quotas defined in `billing.py`. Token accounting includes indexing, retrieval queries, agent turns and verifier calls. One credit covers up to $0.005 of configured provider cost; charges round up per reservation. An atomic reservation prevents concurrent overspend, limits provider calls and refunds unused credits after settlement. Unknown provider charges retain their reserve and are flagged for operator reconciliation. Process crashes can also leave an unsettled reservation; never refund before checking provider usage. There are no automatic overage charges.

Seats, monthly incoming requests and stored knowledge chunks have separate server-enforced limits. Trial credits do not reset monthly. Paid access follows signed webhook events plus authoritative Stripe subscription retrieval, with replay protection. Checkout validates configured USD monthly prices; success redirects do not activate a plan. Owners use Stripe Checkout/Customer Portal. Production checkout requires configured operator details and reviewed legal document URLs. Model prices must be kept current; credit margins exclude hosting, payment fees, tax and staffing. See `unit-economics.md`.

## Runtime and data

Development uses SQLite; production uses PostgreSQL/pgvector plus LangGraph checkpoint tables. Schema migration 003 adds real-vector profiles, request context, intake, subscriptions, counters and recovery records; 002 preserved former single-company records in an inaccessible legacy archive. Back up application and checkpoint data together. A binary rollback cannot undo a data conversion.

API and index-worker share the database and server secrets. The AWS blueprint supplies HTTPS ALB, Fargate, private RDS, Secrets Manager, CloudWatch and optional telemetry; it has not been applied. The production task excludes the synthetic order service. Readiness tests database access, not model quality, SMTP or payment delivery. Technical logs avoid messages, credentials and full model payloads. Operator exports omit passwords and tokens. Retention/deletion execution, restricted runtime DB roles, restore drills, secret rotation and domain-specific quality checks remain deployment responsibilities.
