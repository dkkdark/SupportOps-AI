# SupportOps recovery runbook

## Start with a ticket ID

Open **Monitoring**, select the run and inspect **Workflow**. Record the ticket ID, trace ID, manual reason and manifest. Use technical logs to find the failed step. Do not paste customer messages, API keys or full order payloads into shared incident logs.

```bash
docker compose ps
docker compose logs --since=10m app orders
curl --fail http://127.0.0.1:8010/health/live
curl --fail http://127.0.0.1:8010/health/ready
```

Liveness means the process is serving requests. Readiness checks the database and orders API. Neither verifies AI answer quality. A configured LLM can be unavailable while technical readiness remains green; the workflow handles it explicitly and live evaluation provides separate content checks.

## 1. Orders API timeout

**Expected:** no approvable draft, `manual`, reason `orders_unavailable`, order step visible in the trace.

In local development, **Monitoring → Orders API timeout → Run** creates a ticket with a controlled injected timeout. Fault injection must be enabled; the production default disables it. This tests the exception route deterministically rather than adding an actual sleep to an HTTP server. A separate network timeout test should be used for infrastructure timing.

For a real local dependency outage:

```bash
docker compose stop orders
# Analyze a new ticket in the UI. It should require manual review.
docker compose start orders
docker compose ps
```

Check `ORDERS_API_URL`, service health, DNS/network rules and the 2-second timeout. Restore the API and select **Retry analysis**. Recovery creates a new revision and trace; it must not claim the old run succeeded. Do not increase the timeout indefinitely. Confirm order details, current sources and all checks before approval.

## 2. Invalid model output

**Expected:** at most two attempts, no draft, `manual`, reason `invalid_model_output`. Unknown fact IDs, missing required facts, extra schema fields and invalid tones must fail closed.

Use **Monitoring → Invalid model output → Run**. Inspect the version manifest and `usage.attempts`. Restore the provider/prompt configuration and retry. The app never displays provider secrets. An external provider outage produces `model_unavailable` or `model_not_configured` as appropriate.

Reproduce regression checks:

```bash
pytest tests/test_workflow.py -k 'faults or sensitive'
python -m eval.run --split dev --output reports/dev.json
```

## 3. Missing or conflicting policies

**Expected:** no approvable draft, explicit `missing_sources` or `conflicting_documents` reason. An archived policy must never be used just because it has similar wording.

Check source version, effective date and the requested fact ID. Only one version of a policy key may be active at a time. Fix the corpus with a reviewed version/data migration; startup deliberately does not overwrite an existing corpus. Restore the current rules, then retry analysis. Test the effective-date boundary and archived 14-day policy:

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

Restart the application and inspect the ticket. A `review` ticket should resume its saved human-review checkpoint after restart. An interrupted `processing` claim can be retried after the 90-second lease expires. A new claim gets a new revision/thread; no cancellation/refund side effect exists to repeat. If the provider is still processing an ambiguous timed-out call, its billing is authoritative.

```bash
pytest tests/test_api.py -k persistent
pytest tests/test_workflow.py -k idempotency
```

If a review returns 409, refresh the ticket; another decision or revision has already changed it. Do not repeatedly submit a stale approval. The checkpoint/review reconciliation allows a crash after graph resume but before audit commit to recover only with the same decision payload.

## 6. Database recovery

Local SQLite: stop the process before making a consistent backup of both the application DB and its `.checkpoints` file. If WAL mode is used, use SQLite’s backup API or checkpoint/close first; do not copy only an active main file. Keep backups outside source control.

Compose: preserve the `postgres` volume. Use PostgreSQL backup tools for both application tables and LangGraph checkpoint tables. Restoring only tickets loses resumable workflow state.

AWS: use the seven-day automated RDS backups and point-in-time restore to a **new** instance. Verify pgvector, Alembic revision and checkpoint tables; update the DB endpoint/managed secret references and redeploy tasks. Run readiness, smoke and review-resume checks before moving traffic. This restore procedure has not been executed against an AWS account in this project.
