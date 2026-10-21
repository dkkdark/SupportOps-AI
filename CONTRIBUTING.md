# Working on SupportOps AI

- Keep the interface, sample store content and documentation in English. Retain Russian input regression coverage.
- Make small feature-specific commits. The owner has requested **no push until explicitly authorized**.
- Use Python 3.13 and the checked-in dependency lock. Frontend runtime is buildless; `npm ci` is only needed for formatting.
- Run `make test` for workflow/API changes. Run development evaluation before the reserved holdout for a release. Do not tune prompts to an already inspected final holdout and continue presenting it as untouched.
- Use `npm run format` for frontend formatting and `ruff format supportops scripts eval tests migrations` if Ruff is installed. Ruff is optional authoring tooling, not a runtime dependency.
- Treat policy fact maps as trusted code: review every factual change, publish a new version, close the prior effective interval and add a meaningful regression case.
- Never add real customer records, provider keys, `.env`, database files, Terraform state or AWS credentials to Git. Synthetic fixture data is intentionally publishable.
- Keep actual verification separate from prepared configuration. Container/DB integration, live-model runs and cloud deployments require their own passing evidence.
- Do not send replies or mutate orders as a side effect of analysis. A future action integration needs explicit approval, authorization and transactional idempotency.
