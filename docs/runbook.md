# SupportOps recovery runbook

## Start with a ticket ID

Open **Monitoring**, select the run and inspect **Workflow**. Record the workspace ID, ticket ID, trace ID, manual reason and manifest. Use technical logs to find the failed step. Do not paste customer messages, API keys or full order payloads into shared incident logs.

```bash
docker compose ps
docker compose logs --since=10m app indexer
curl --fail http://127.0.0.1:8010/health/live
curl --fail http://127.0.0.1:8010/health/ready
```

Liveness means the process is serving requests. Readiness checks the database. The optional sample orders API is checked when a sample ticket uses it. Neither verifies AI answer quality. A configured LLM can be unavailable while technical readiness remains green; the workflow handles it explicitly and live evaluation provides separate content checks.

## 1. Orders API timeout

**Expected:** no approvable draft, `manual`, reason `orders_unavailable`, order step visible in the trace.

In an isolated sample workspace during local development, **Monitoring → Orders API timeout → Run** creates a ticket with a controlled injected timeout. Fault injection must be enabled; the production default disables it. This tests the exception route deterministically rather than adding an actual sleep to an HTTP server. A separate network timeout test should be used for infrastructure timing.

For a real local dependency outage:

```bash
docker compose stop orders
# Analyze a new ticket in the UI. It should require manual review.
docker compose start orders
docker compose ps
```

Check `ORDERS_API_URL`, service health, DNS/network rules and the 2-second timeout. Restore the API and select **Retry analysis**. Recovery creates a new revision and trace; it must not claim the old run succeeded. Do not increase the timeout indefinitely. Confirm order details, current sources and all checks before approval.

## 2. Invalid model output

Real-agent failures produce manual handoff with no approvable draft. Inspect the failing tool/verifier step, citation IDs, source state and model manifest. The bounded agent loop differs from the deterministic retail fixture. Unknown citations, unsupported claims and invalid schemas fail closed. The sample workflow below retains its two-attempt fact-selection contract.

Use **Monitoring → Invalid model output → Run**. Inspect the version manifest and `usage.attempts`. Restore the provider/prompt configuration and retry. The app never displays provider secrets. An external provider outage produces `model_unavailable` or `model_not_configured` as appropriate.

Reproduce regression checks:

```bash
pytest tests/test_workflow.py -k 'faults or sensitive'
python -m eval.run --split dev --output reports/dev.json
```

## 3. Missing or conflicting policies

**Expected:** no approvable draft, explicit `missing_sources` or `conflicting_documents` reason. An archived policy must never be used just because it has similar wording.

Check source version, effective date and the requested fact ID. Only one version of a policy key may be active at a time. In the affected workspace, open Knowledge base, check archival/effective dates, then publish a corrected version. New business knowledge is never replaced by the sample corpus. Retry analysis after publishing. The sample archive/date boundary can be checked with:

```bash
pytest tests/test_workflow.py -k 'policy or conflicting'
```

## 4. Regression or unhealthy release

**Before deployment:** a failed evaluation returns a nonzero exit code, so CI must stop before image publication/deployment. Review individual case failures and the exact code/prompt/model/document manifest. Do not lower a factual gate to force the release through.

**During deployment:** ECS circuit breaker rolls back failed health/startup deployments when a previous completed deployment exists. Inspect stopped-task reasons, CloudWatch logs, ALB target health, database connectivity and migration status. The staging workflow waits for stability and verifies the actual task definition so a silent ECS rollback is not mistaken for success.

**After deployment:** a failed smoke triggers workflow rollback to the saved previous task definition. If a content regression appears later, use the last verified immutable task definition/image digest. From the authorized staging workflow environment:

```bash
python -m scripts.ecs_deploy rollback
```

That helper requires the preserved `var/deployment.json` for the release and `ECS_CLUSTER`/`ECS_SERVICE`. If it is not available, retrieve the release evidence from S3 through your normal access workflow; do not guess a task ARN. Rolling back an image does not reverse database migrations. Use backward-compatible expand/contract migrations; review data migrations separately.

Validate recovery with health/readiness, smoke and the correct evaluation provider. A green baseline does not validate a live external model that changed.

## 5. Process crashes or stuck processing

Restart the application and inspect the ticket. A `review` ticket should resume its saved human-review checkpoint after restart. An interrupted `processing` claim can be retried after the lease expires (at least the agent timeout plus 30 seconds; 90 seconds for shorter workflows). A new claim gets a new revision/thread; no cancellation/refund side effect exists to repeat. If the provider is still processing an ambiguous timed-out call, its billing is authoritative.

```bash
pytest tests/test_api.py -k persistent
pytest tests/test_workflow.py -k idempotency
```

If a review returns 409, refresh the ticket; another decision or revision has already changed it. Do not repeatedly submit a stale approval. The checkpoint/review reconciliation allows a crash after graph resume but before audit commit to recover only with the same decision payload.

## 6. Database recovery

Local SQLite: stop the process before making a consistent backup of both the application DB and its `.checkpoints` file. If WAL mode is used, use SQLite’s backup API or checkpoint/close first; do not copy only an active main file. Keep backups outside source control.

Compose: preserve the `postgres` volume. Use PostgreSQL backup tools for both application tables and LangGraph checkpoint tables. Restoring only tickets loses resumable workflow state.

AWS: use the seven-day automated RDS backups and point-in-time restore to a **new** instance. Verify pgvector, Alembic revision and checkpoint tables; update the DB endpoint/managed secret references and redeploy tasks. Run readiness, smoke and review-resume checks before moving traffic. This restore procedure has not been executed against an AWS account in this project.


## 7. Business access and schema conversion

For access errors, sign in again and check the selected workspace. Refresh stale tabs after switching workspaces. An owner/admin can inspect membership and restore an agent’s removed access. Admins cannot change owner/admin memberships; owner/self demotion is blocked. Never grant access by editing a ticket ID or copying another company’s rows.

Invitation links are single-use, email-bound and expire after 7 days. Revoke/recreate them from Team if lost or expired; no email is sent automatically. Existing users must enter their existing password. Verification and password recovery use SMTP with hashed expiring tokens. Check sender/domain configuration and delivery before retrying; do not expose raw recovery tokens in incident logs. A password reset revokes all user sessions.

Migration 002 is a forward-only schema conversion. Previous single-business records are retained under `legacy-demo` with no members, not assigned to the next registrant. The pre-conversion local backup lives under `var/backups/before-saas-20261021-163754/`. To restore it, stop the app and restore both application/checkpoint files together with the pre-conversion code. Do not downgrade by dropping tenant keys or roll back just the application image across this boundary. Keep production migration/restore credentials separate from ordinary employees.


## 8. Indexing, credit reservations and payment lifecycle

For pending knowledge, inspect `IndexJob` status, model profile, attempts and sanitized error. Ensure `python -m scripts.index_worker` is running with the same database and model configuration as the API. It retries pending/failed jobs up to three attempts and reclaims stale indexing leases. Correct configuration before selecting retry in Knowledge. A changed embedding model requires a new index profile; old vectors are not reused for a different profile.

When AI returns 402, check the workspace plan, billing period and remaining credits. An active paid entitlement requires a verified current Stripe subscription period. Inspect signed webhook deliveries and authoritative subscription state; never grant access from a checkout success URL. Duplicate events are safe to replay. Configure prices, webhook secret and Customer Portal in Stripe test mode before switching to live mode.

Token calls reserve credits first. Known usage settles and releases unused credits. An ambiguous timeout retains the reservation; `CreditReservation.usage.reconciliation_required` marks it. A process crash can leave `settled=false`. Review affected reservation IDs, trace IDs, stored token usage and the provider's authoritative usage before an operator adjusts credits. Do not automatically refund unknown calls or repeatedly retry chargeable requests. Use a reviewed database transaction to correct the reservation and its same-period usage counter together, record evidence/reason, and keep an incident audit outside customer messages. There is no self-service automatic reconciliation for ambiguous provider charges.

If SMTP fails, registration does not pretend delivery succeeded. Check SMTP credentials/TLS and sender validation, then use the account's verification action. If Turnstile is not configured in production, signup/public forms fail closed; configure keys for the actual hostname rather than disabling protection for a public launch.

Back up schema 003 and checkpoints before upgrades. Local pre-agent backup is under `var/backups/before-agent-20261021-181236/`; keep production backups outside the source checkout.
