# Testing and quality coverage

## Backend

The backend suite covers authenticated business workflows, persistence and integrations. Ordinary tests isolate their databases, disable dotenv loading and use controlled email, payment and model transports.

| Area | Coverage |
| --- | --- |
| Authentication | Password hashing, sessions, CSRF/origin checks, verification and password recovery |
| Tenant isolation | Workspace-scoped requests, knowledge, membership, routing, intake and audit records |
| Agent | Tool loops, citation validation, independent verification, provider failures and human-review checkpoints |
| Retrieval | Embedding profiles, effective dates, archive filtering, Qdrant tenant filters and SQL evidence revalidation |
| Cost control | Atomic reservations, settlement, concurrent usage, uncertain charges and the account-wide demo allowance |
| Intake | Public/API submissions, deduplication, token revocation and routing constraints |
| Background jobs | Leases, retry/backoff, delivery states, heartbeat and stale-worker recovery |
| Data | Schema migrations, privacy boundaries, account export, backup corruption detection and isolated restoration |
| Billing | Controlled Stripe signatures, event replay, price validation and subscription lifecycle |

Dedicated integration tests use PostgreSQL and Qdrant. Provider responses remain controlled so these tests do not require paid model calls. Real-model acceptance is a separate bounded workflow using synthetic data.

## Frontend

JSDOM regression tests execute the actual application modules with controlled API delays and errors. Coverage includes scroll restoration, modal focus, keyboard shortcuts, request selection, search/filter consistency, workspace switching, duplicate submissions, CAPTCHA lifecycle and public-form retries.

Browser verification complements DOM tests for responsive layout and scrolling at desktop and mobile sizes.

## Evaluation

The retail baseline has a separate synthetic development/holdout dataset and release guard. Its metrics describe that dataset rather than general business-domain accuracy. See [evaluation](evaluation.md).

Passing tests and infrastructure validation do not establish production deployment, SMTP delivery, real payment processing or customer productivity.
