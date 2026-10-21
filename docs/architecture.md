# Architecture and contracts

## Authoritative data

The order status, tracking number, carrier and items come from the orders API. Return/cancellation rules come from effective policy documents. A customer message is untrusted input. Neither the model nor the message can introduce a new fact or perform an order action.

`TicketInput` validates lengths, channel, priority, email and explicit order ID. A single order reference can be extracted from English/Russian text; multiple or mismatched references require review. A name mismatch is conservatively routed to an agent. **Name matching is only a synthetic demonstration, not proof of customer identity.** A real integration must authorize access against the merchant’s customer/account identifiers before retrieving an order.

## Persistence and transitions

```text
new → processing → review → approved
                 ↘ manual → escalated
                    ↓
                 processing (retry)
```

`processing` increments the revision with an SQL compare-and-swap. Concurrent analyses cannot both claim the same revision. A processing lease older than 90 seconds can be retried. All external calls are bounded; a run makes at most two LLM calls with a 20-second timeout each and one order request with a 2-second timeout by default.

Successful and manual runs reach a LangGraph `interrupt` with a persistent thread ID of `ticket_id:revision`. SQLite uses a separate checkpoint database; PostgreSQL uses PostgresSaver. The review endpoint resumes with `Command(resume=...)`, then records the human text, note and decision. The database review update uses revision and status conditions; stale/repeated submissions return 409. An already completed checkpoint is reconciled against the same review decision after a crash between checkpoint and application commit. Future real-world cancellation/refund operations need a transactional outbox and downstream idempotency keys; no such outbound actions exist here.

`Idempotency-Key` deduplicates ticket creation. Reusing the same key with different content returns 409. Reanalysis of `review`, `approved` and `escalated` returns the existing result. A recoverable manual run creates a new revision/trace and leaves the old checkpoint available for diagnosis. The UI exposes only the latest run; historical execution checkpoints remain in the database.

## Policy ingestion and retrieval

`data/knowledge/policies.json` is a curated corpus of 12 short rule chunks plus an archived return policy. `Rule` validates each entry, and startup ingestion stores the chunk, source identity, version, half-open effective date interval `[from, to)`, tags, trusted fact map and baseline embedding. Because each document is already a short, self-contained rule, no lossy character slicing is applied.

Do not edit the text of a published policy under the same version. Add a new version with a new ID, close the previous interval and re-ingest in a controlled migration. Startup only seeds an empty corpus; it never silently overwrites existing policy data. The archive/current boundary is tested explicitly. Overlapping active versions fail closed. The retriever ranks current rules by category, lexical matches and versioned hash-vector similarity. Only policies that contribute selected facts are shown as used sources for a successful draft.

The source fact map is curated, trusted content. The system checks selection against it; it does not independently prove that an author’s fact map accurately summarizes its policy. Changes to that map require code review and evaluation. The return-eligibility parser reads the explicit `within N calendar days` window from the current trusted fact. Missing/unparseable windows fail closed; a policy change must preserve this supported format or ship a parser/schema change with tests.

## Generation

The deterministic provider includes all required fact IDs. The live provider receives the customer message, evidence map and required IDs. It returns JSON `{fact_ids, tone}` only. Pydantic rejects extra fields and invalid tones. The validator checks that every selected ID exists and every required ID is included. Trusted code renders fixed order facts and approved policy facts, with a bounded greeting/closing.

Malformed output or missing/unsupported IDs gets at most one retry. Network failures also stay within this two-attempt limit. A model failure produces no approvable draft. Token usage across valid API responses is accumulated; cost is calculated from explicitly configured input/output rates. An ambiguous billed network timeout may incur provider usage that cannot be recovered from the missing response; provider billing remains authoritative.

The model never sees full card data and receives no API credentials in its prompt. The minimal example still sends the customer message to the configured provider in live mode; real data requires a retention and data-processing policy.

## Observability

A root `ticket.analyze` span contains spans for each workflow node. The actual OTel trace ID is saved on the ticket result and included in technical step logs. Logs contain IDs, node names, durations and error types, not messages, draft text or credentials. Technical exceptions are intentionally logged without SQL/model payloads. Result records and persistent checkpoints do contain the support message and must be protected as application data.

Configure `OTEL_EXPORTER_OTLP_ENDPOINT` to send traces and step-duration histograms to a collector. The Terraform collector option uses ADOT → X-Ray traces and CloudWatch EMF metrics. Without a collector, CloudWatch still receives ECS step/access logs and ALB health/error metrics. `/api/metrics` is a workspace summary, not a Prometheus endpoint; its latency values summarize the latest run of each ticket, not all historical runs or an SLO.

## Deployment boundaries

Development creates the schema for quick start. Production requires Alembic before startup and a configured API key. PostgreSQL persists application records and checkpoints across container restarts. SQLite quick start is a single-host option, not a distributed production database.

ECS runs the application and synthetic orders API as separate containers in one task. The ALB only reaches the application; the order service is reached through task-local loopback. RDS is private. An optional collector sends telemetry. The mock order service is read-only, so there is no claim of a real merchant integration.
