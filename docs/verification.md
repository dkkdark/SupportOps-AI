# Local verification record

Verified October 21, 2026, for the business-workspace version. These are local results, not a production deployment.

| Check | Result |
| --- | --- |
| Python / API / workflow suite | 37 passed |
| Real PostgreSQL/pgvector integration | 1 skipped locally; Docker daemon unavailable; configured in CI |
| Business isolation | Separate registrations, lists, object access, mutations, retrieval, templates, metrics, workspace headers, idempotency keys and workflow threads checked |
| Team access | Email-bound, single-use, expiring/revocable invitations; owner/admin/agent permissions; immediate membership revocation checked |
| Authentication | Argon2/session hashing, sign-in/out, CSRF/origin checks, stale workspace tabs, expiry and throttling checked |
| Owned documents/templates | Markdown and DOCX import; selectable PDF import through grounded draft; invalid/oversized uploads; atomic template imports; revision conflicts; source versions/archive/snapshots checked |
| Development evaluation | 80/80 synthetic retail cases passed |
| Holdout evaluation | 20/20 synthetic retail cases passed; deterministic baseline |
| Live local HTTP smoke | Registration → isolated business → own policy → grounded reply → source inspection/idempotency → human approval → logout passed |
| SQLite migration | Fresh 001→002, repeated upgrade, and preserved legacy records/foreign keys checked; actual pre-SaaS backup copy migrated successfully |
| Browser workflow | Registered Bright Studio; published own policy and brand template; created/analyzed/approved an appointment ticket; generated invitation and joined as James Miller with agent access |
| Responsive UI | Desktop (1440 px) and mobile (390 px) registration layout inspected; screenshots retained |
| Frontend | JavaScript syntax and Prettier passed; standalone sample rebuilt |
| Docker Compose | Configuration validated; image build/runtime not executed locally |
| Terraform | Format and cached provider-schema validation passed; registry re-init was unavailable; no plan/apply |
| Helm | Lint and template rendering passed, including local development environment option |
| External model | Adapter covered by controlled HTTP responses; no real provider calls |
| AWS / Kubernetes | No deployment |
| GitHub | Origin configured; feature commits stay local; no push or Actions run |

Reports are generated under ignored `reports/`. The retail benchmark is shown only in explicitly synthetic sample workspaces. A new business has no configured accuracy benchmark; these results do not measure quality on arbitrary uploaded business policies.

Migration 002 is forward-only. Rolling back the former single-business schema requires the matching database/checkpoint/code backup. Password recovery, email verification/delivery, billing and real account/helpdesk integrations remain separate work before public operation.
