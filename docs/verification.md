# Local verification record

Verified on October 21, 2026. These are local results, not a claim of a production deployment.

| Check | Result |
| --- | --- |
| Python / API / workflow suite | 24 passed |
| Real PostgreSQL/pgvector test | 1 skipped locally; Docker daemon unavailable; configured in CI |
| Development evaluation | 80/80 passed |
| Holdout evaluation | 20/20 passed; deterministic baseline |
| Live local HTTP smoke | Passed: readiness, creation, analysis, sources, duplicate-run protection, saved approval |
| SQLite migration cycle | Upgrade → downgrade → upgrade passed on a temporary database |
| Browser UI | Ticket selection, creation, analysis, evidence/version modal, editing, approval history, English reply and English quality report checked |
| Frontend | JavaScript syntax and Prettier checks passed |
| Docker Compose | Configuration validated; image build/runtime not executed locally |
| Terraform | Init, format check and provider-schema validation passed; no plan/apply |
| Helm | Check/render passed; no Kubernetes rollout |
| External model | Adapter tested with controlled HTTP responses, including retry and token accounting; no real provider call |
| AWS | Not provisioned or deployed |
| GitHub | Origin configured; no push and no Actions run |

Reports are generated under ignored `reports/`. The standalone demo contains an explicitly labeled evaluation snapshot. Use `make test`, `make eval`, `make smoke` and the commands in the deployment guide to repeat the relevant checks in the intended environment. Do not treat passing local baseline evaluation as validation of a different live model or a load benchmark.
