# Public demo backend: operations and remaining external setup

## Implemented capabilities

- Business workspaces, roles, CSRF protection, single-use invitations, owned knowledge and templates.
- Forms and inbound API routing with deduplication; incoming messages do not automatically trigger paid AI.
- An OpenAI agent with tools, real embeddings, citations, independent verification and mandatory employee approval.
- A one-time $5 allowance per account, atomic reservations, indexing/generation accounting, purchasing-launch notification records and explicit email opt-out.
- A durable email queue: jobs persist before delivery, retry with backoff, recover expired leases, enforce attempt limits and expose safe user-facing statuses.
- A shared worker for email and indexing, heartbeat health checks and retention of technical records.
- Bounded indexing retries, protection against stale workers and a separate blocked state for missing keys, access or budget. Qdrant semantic search retrieves a bounded candidate set rather than loading the entire corpus into memory.
- Audited reconciliation of uncertain AI charges. Clients cannot refund their own AI allowance through the API.
- Account export, deletion requests for operator review and owner-only workspace export. A deletion request does not automatically erase shared business records.
- Separate application/checkpoint migrations; the production API checks schema readiness without running CREATE/ALTER during startup.
- Consistent SQLite backups after API/worker shutdown, checksums, integrity/foreign-key checks and isolated restoration drills.
- Compose, Helm, AWS task configuration, CI and local dev startup include worker and migration handling.

This is an implemented demo backend. External-service delivery, legal-document suitability and quality on customer data require separate acceptance.

## Startup

```bash
python -m scripts.migrate
python -m scripts.dev
```

The dev command waits for API readiness before starting the worker; shutdown stops its child processes. Server deployments use separate API and `python -m scripts.worker` processes. `scripts.index_worker` remains a compatibility entry point for the same worker. Compose/Helm/AWS configurations include it.

`python -m scripts.migrate` creates and updates both business and checkpoint tables. `alembic upgrade head` alone does not initialize checkpoints on first setup. The production API refuses to start with an unprepared schema. Migration requires separate DDL permissions; API/worker processes should use restricted runtime access. See `infra/sql/runtime-role.sql` for a PostgreSQL role template. Set the runtime password through a secure database workflow and store it in secrets, outside SQL files and Git. The cloud blueprint initially uses the provisioning role; configure separate runtime credentials before deployment.

## Readiness checks

```bash
python -m scripts.check_backend
python -m scripts.check_backend --production
python -m scripts.check_email
python -m scripts.check_email --connect
```

The first three commands do not contact external services. Exit code 2 means configuration is incomplete, not necessarily broken code. The last command checks SMTP TLS/LOGIN/NOOP without sending email. `/health/ready` checks database/schema/checkpoints; `/health/worker` checks a recent heartbeat. Settings → Background jobs shows the current workspace's jobs and the user's own account-email statuses. Other users' email details and balances remain private.

`ready=true` or a successful SMTP LOGIN does not prove delivery: the recipient must verify an actual message and link. Set a public domain in `PUBLIC_BASE_URL`; localhost links are inaccessible from a customer's other device.

## Email queue

`account_email_jobs` stores the user ID, verify/reset purpose, state, attempt count and next-attempt time. It does not store message text, passwords or raw recovery tokens. Single-use link hashes are stored in `recovery_tokens`. Supported states are pending/blocked/sending/retry/sent/failed/cancelled.

Without SMTP credentials, jobs remain blocked without consuming attempts. After configuration and worker restart, delivery resumes for unexpired requests. Pending password recovery expires after 30 minutes; email verification expires after 24 hours. Users request a new email after expiration. Repeated requests are coalesced into an active job. Repeated delivery failures eventually mark a job failed; the user may then request a new message. Using a link revokes other links and unnecessary jobs for the same purpose.

SMTP is not exactly-once: a crash after server acceptance but before recording sent can produce a duplicate. Leases prevent ordinary concurrent duplicates, but cannot eliminate this network ambiguity. Links remain single-use and time-limited. Bulk purchasing-launch emails are not sent automatically.

## Reconcile AI reservations

```bash
python -m scripts.reconcile_usage
python -m scripts.reconcile_usage --reservation ID --cost-usd 0.012345 --reason 'Verified provider usage report for this reservation'
```

Corrections are not written without `--apply`. Verify the provider's actual usage report before recording a cost. Repeat the confirmed command with `--apply`. Recent unfinished operations are protected against premature reconciliation. Adjustments are transactional, audited and cannot refund the same reservation twice. Uncertain old reservations are not automatically released because they may represent paid calls. Legacy company-level records migrated before per-user attribution require separate owner-attribution review; the command does not infer a refund.

## Backup and restore

Stop API and worker so application and checkpoint stores represent the same stopped system:

```bash
python -m scripts.backup create --output var/backups/RELEASE-TIMESTAMP --confirm-stopped
python -m scripts.backup verify --path var/backups/RELEASE-TIMESTAMP
python -m scripts.backup restore-check --path var/backups/RELEASE-TIMESTAMP --output var/restore-drills/RELEASE-TIMESTAMP
```

These commands do not overwrite existing directories or replace the live database. `restore-check` verifies an isolated copy. Store backups off the source server and outside Git; a local directory is not an off-site backup. PostgreSQL requires configured managed backups/pg_dump and a separate restoration test. A cloud restore has not been performed.

## Privacy and data

Settings provides account export and a deletion request confirmed with the user's current password. Operators export the review queue with:

```bash
python -m scripts.privacy_requests --output var/privacy-review.json
```

The command does not send emails, delete accounts or mark requests complete. The operator must resolve shared-business ownership and retention requirements before reviewed deletion/anonymization. The JSON contains personal data and is created with restricted permissions.

The worker removes only long-expired sessions, old recovery tokens, expired throttle counters, completed email jobs older than 30 days and stale heartbeats. Requests, knowledge, decision audits and financial records are not automatically deleted.

## External launch prerequisites

1. Configure the Gmail App Password for `info.xenonimus@gmail.com` and verify delivery using an inbox you control.
2. Configure an operator-owned OpenAI project key and verify access/billing. Token rates must match the selected models.
3. Create a Turnstile widget for the actual domain and configure site/secret keys.
4. Confirm a domain and hosting provider, provision resources, configure separate database credentials and off-site backups. Local repository preparation does not perform a push, deployment or infrastructure apply.
5. Supply legal operator details, review and publish terms/privacy documents, and define deletion handling and retention.
6. Pilot real business policies. Native Gmail/Outlook/CRM connectors require source-specific OAuth/API access and an adapter; forms and inbound API already work. Employees save and use reviewed text; replies are not sent automatically.

Keep payments disabled for the current demo. A paid release requires separate Stripe acceptance and owner approval.

## Qdrant vector index

Real semantic embeddings have moved from SQL/pgvector to Qdrant. SQL retains accounts, requests, source policies, financial records and the `vector_entries` registry. Migration 006 preserves legacy embeddings. Standalone startup, no-cost cache transfer, monitoring and coordinated backups are documented in [Qdrant operations](qdrant.md). SQL backups do not replace Qdrant backups.
