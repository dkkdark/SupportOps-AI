# Architecture and contracts

## Business isolation

Users are global identities. An organization is a business workspace, with memberships carrying `owner`, `admin` or `agent` roles. A user can belong to several businesses. Registering creates an empty business and owner membership; accepting a personal invitation adds a membership to an existing business.

Every business API derives the organization from an authenticated database session, validates active membership, and requires `X-Workspace-ID` to match it. The latter prevents a stale browser tab from writing into another business after a workspace switch. Client-supplied object IDs never establish authorization. Queries, metrics, source retrieval and audit history are organization-scoped. Tickets, policy chunks, source documents and templates have composite tenant/object keys, with composite foreign keys for their relationships. Ticket creation idempotency is unique within a business.

LangGraph threads use `organization_id:ticket_id:revision`. Review validates both tenant ownership and the saved thread prefix. Archived legacy records receive no membership automatically. A shared database is used; PostgreSQL row-level security and database-per-business isolation are not implemented. The application authorization boundary is covered by multi-identity tests.

## Authentication and team access

Passwords use Argon2id. A random opaque session token is stored in an HttpOnly, SameSite=Lax cookie; only its SHA-256 hash is stored in the database. Sessions expire after `SESSION_DAYS` (default 7); production adds Secure. Login rotates the current browser session, logout deletes it. Unsafe authenticated requests require the session’s CSRF value through `X-CSRF-Token`. Cross-origin unsafe requests are rejected. Auth responses and other business API responses use `Cache-Control: no-store`; passwords are removed from validation error bodies.

Persistent throttles bound registration, login, demo signup, invitation acceptance, workspace creation and uploads. Reverse-proxy forwarded IP headers must only be accepted from trusted ingress. There is no email verification, password-reset flow, MFA, SSO or automatic expiration cleanup job yet.

Invitation tokens are random and stored only as hashes. They are bound to an email, expire in 7 days, and are atomically claimed once. Existing users must authenticate with their existing password. Invitation links are returned once for manual sharing; the service does not send email. An owner can manage admins and agents; admins manage only agents. Owner/self membership edits are blocked. Access checks read membership on each request, so removing a member blocks existing sessions immediately.

## Owned content

Policies can be written manually or imported from TXT, Markdown, JSON, PDF or DOCX. Upload size is 2 MB; extracted text is limited to 100,000 characters. PDF parsing allows unencrypted documents with selectable text and at most 50 pages. DOCX expansion has an entry/size budget and XML declaration checks. A disposable subprocess bounds parser time/CPU and Linux memory; a semaphore bounds concurrent parsers. Extraction is moved off the async request loop. The request body has a separate 3 MB limit. Original files are not retained.

A source has a business ID, stable policy key, immutable version, content hash, effective interval `[from,to)`, text and metadata. Paragraph/sentence chunks are bounded to roughly 1,000 characters and carry exact excerpt fact maps. Publishing a new version closes older intervals and serializes against the organization row on PostgreSQL. A unique business/key/version constraint rejects conflicting duplicate versions. Archiving disables its chunks. Earlier ticket results retain copied source text and versions.

Only enabled, current chunks belonging to the active business are eligible for retrieval. Conflicting active versions fail closed. Ranking combines categories, lexical overlap and a versioned 128-dimensional character-hash embedding. PostgreSQL uses pgvector cosine ordering; SQLite computes equivalent vector ranking in Python. General businesses additionally require lexical overlap. This baseline is not a trained semantic model and can miss paraphrases; review realistic examples before relying on it.

Templates are business-owned and revisioned. `{{facts}}` is required exactly once, with a fixed allowlist of other variables. Interpolation is a single pass so policy/customer content containing braces cannot be interpreted as another variable. JSON template imports validate all items before a transaction commits. Auto-selection prefers a matching classification category, then `other`; a ticket may select an active template explicitly. Disabled explicit templates fail closed. Source and selected-template revision/body snapshots survive later edits.

## Two workflows

New businesses use `knowledge` mode. Order lookup is skipped. Retrieval selects up to three relevant policy excerpts, the provider selects allowed fact IDs, and trusted code inserts exact excerpts into the company template. Requests with insufficient context have no draft and an explicit manual reason. Requests requiring live order/account facts cannot claim such data is connected.

The explicitly separate `retail_demo` mode uses synthetic Northstar Goods records and curated policy facts. Its read-only order API demonstrates status/tracking and return/cancellation checks. Customer-name matching is a demonstration, not real identity verification. No real merchant integration is configured for new companies. Only this mode can use configured fault scenarios or show synthetic retail evaluation metrics.

`MODEL_PROVIDER=baseline` selects required fact IDs without an external call. The optional live provider receives the customer message, evidence map and required IDs and returns `{fact_ids,tone}`. Pydantic rejects invalid selections, extra fields and unsupported tones. At most two attempts are allowed, with bounded 20-second HTTP timeouts. Token usage accumulates across responses; missing responses may contain unrecoverable provider-billed usage. Uploaded policies and templates are authoritative workspace-admin content; the software cannot independently prove the truth of those rules. Human review covers the finished wording, including template claims.

## Persistence and review

```text
new → processing → review → approved
                 ↘ manual → escalated
                    ↓
                 processing (retry)
```

An SQL compare-and-swap claims each analysis revision. Stuck processing can be retried after a 90-second lease. Completed review/approved/escalated tickets return existing analysis results. The graph persists an interrupt awaiting human approval or escalation. The review endpoint resumes that exact checkpoint and records the actual signed-in employee, decision, text and note. Revision/status conditions reject duplicate or stale decisions. A crash after graph resume and before the application commit can reconcile only the same decision payload.

SQLite stores checkpoints separately; PostgreSQL uses PostgresSaver. Both contain customer messages and require the same data protections as business records. No reply sending, cancellation or refund side effect exists. A future outbound integration needs its own tenant credentials, identity mapping, transactional outbox and idempotency contract.

## Operations and migrations

Per-run OpenTelemetry trace IDs connect workflow steps. Logs contain technical IDs/durations/error types, not support messages, draft text or credentials. Metrics summarize the active workspace’s latest ticket runs; they are not a Prometheus endpoint or capacity benchmark. Readiness checks the database. Optional demo orders and LLM availability are workflow concerns.

Development runs Alembic; production requires migrations before startup and HTTPS for session cookies. Migration `002` preserves single-business data in an inaccessible `legacy-demo` archive and is forward-only. Back up application/checkpoint data before conversion. Returning to the former version requires restoring the former database and code together; an image rollback alone is insufficient.

The AWS blueprint provides private RDS, Fargate behind HTTPS ALB, Secrets Manager, optional telemetry and scoped OIDC deployment. It has not been applied. Runtime/migration database roles, email/account recovery, retention/deletion, workload quotas and business-specific evaluation remain public-service operational work.
