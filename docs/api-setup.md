# Connecting real APIs: the current demo release

The application supports the OpenAI Responses API, embeddings, SMTP and Turnstile. A real OpenAI call has been verified using synthetic data. A public launch requires operator-owned accounts and server configuration. Stripe is unnecessary for the current demo: pricing is hidden, and checkout, portal and payment webhooks are disabled when `DEMO_RELEASE=true`.

## 1. Prepare the environment and keep demo mode enabled

Open a terminal in the project root. If `.env` does not exist, copy `.env.example` to `.env`; do not overwrite an existing file. Configure:

```dotenv
DEMO_RELEASE=true
DEMO_AI_BUDGET_USD=5
MODEL_PROVIDER=openai
EMBEDDING_PROVIDER=openai
```

The allowance is granted once per account, across all businesses. Switching workspaces, entering a new month or reaching a registration anniversary does not reset it. Indexing, query embeddings, agent steps and independent verification all count toward the allowance. Manual features have no former plan-based seat, request or knowledge quotas; upload size, permissions and request-rate limits still apply.

## 2. Connect OpenAI

1. Sign in to [OpenAI Platform](https://platform.openai.com/), create a SupportOps project and configure API billing.
2. Create a server-side project API key. Keep it out of chat, frontend code and Git. Store it in the ignored local `.env`, or server secret storage such as Secrets Manager. Use separate projects and keys for staging and production.
3. Configure the application:

```dotenv
OPENAI_API_KEY=your_secret_key
OPENAI_BASE_URL=https://api.openai.com/v1
AGENT_MODEL=gpt-5.4-mini
VERIFIER_MODEL=gpt-5.4-mini
EMBEDDING_MODEL=text-embedding-3-large
MODEL_INPUT_USD_PER_MILLION=0.75
MODEL_OUTPUT_USD_PER_MILLION=4.50
VERIFIER_INPUT_USD_PER_MILLION=0.75
VERIFIER_OUTPUT_USD_PER_MILLION=4.50
EMBEDDING_INPUT_USD_PER_MILLION=0.13
```

These are project configuration values, not a guarantee of current provider prices. Verify model access and pricing before launch. Update token rates when changing models, and reindex documents when changing the embedding model.

4. Configure OpenAI spending notifications and monitor actual billing. The application estimates costs using token usage and configured rates; provider invoices remain authoritative. A provider notification should not be treated as an automatic spending cutoff.

Official references: [OpenAI quickstart](https://developers.openai.com/api/docs/quickstart), [production practices](https://developers.openai.com/api/docs/guides/production-best-practices), [pricing](https://developers.openai.com/api/docs/pricing).

## 3. Apply migrations and verify models locally

Create a consistent backup of the application and checkpoint databases. Then, with `.venv` activated:

```bash
docker compose --env-file /dev/null -f compose.qdrant.yaml up -d
python -m scripts.migrate
python -m scripts.live_acceptance
```

`scripts.live_acceptance` makes real paid calls on synthetic data with a $0.20 budget. Do not run it repeatedly in a loop. The report should show indexing → search tool → response → independent verification → human approval. Ordinary `pytest` runs use controlled responses and incur no OpenAI charges.

Start the application:

```bash
python -m scripts.dev
```

The dev command starts the API, sample orders service and background worker. If running the API separately, start the worker in another terminal:

```bash
python -m scripts.worker
```

`scripts.index_worker` is a compatibility entry point for the same worker; do not start an unnecessary second worker alongside the dev command.

Open `http://127.0.0.1:8010`. Create a business workspace, upload a short policy and wait for `Semantic index ready`. The sample workspace uses synthetic data and does not call paid models.

## 4. Connect email

The project's sender and support contact is **info.xenonimus@gmail.com**. See [Gmail setup](gmail-setup.md) for App Password configuration and diagnostics.

For another SMTP service, verify a sender and obtain the host, port, username and password from its dashboard. The application supports STARTTLS, typically on port 587 when supported by the provider:

```dotenv
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your_smtp_username
SMTP_PASSWORD=your_smtp_password
SMTP_FROM=SupportOps AI <hello@example.com>
SMTP_STARTTLS=true
REQUIRE_VERIFIED_EMAIL=true
PUBLIC_BASE_URL=https://your-domain.example
```

Amazon SES SMTP credentials differ from AWS access keys. Use the region's SMTP endpoint, TLS and a verified sender, following the [SES SMTP documentation](https://docs.aws.amazon.com/ses/latest/dg/send-email-smtp.html). Check the account's sending restrictions.

Restart the application and verify registration, email confirmation and password recovery using an inbox you control. In production, an unverified email cannot activate paid AI operations.

Purchasing-launch emails are **not sent automatically**. The application records notification requests; once purchasing is ready, the operator must prepare and send the announcement through the configured service.

## 5. Protect registration and public forms

Create a Cloudflare Turnstile widget for the domain. The browser widget and server-side token verification are implemented; configure:

```dotenv
TURNSTILE_SITE_KEY=your_public_site_key
TURNSTILE_SECRET_KEY=your_secret_key
```

The site key is public; the secret key stays on the server. Allow the actual staging and production hostnames, then verify registration and public forms on the deployed site. See [Turnstile setup](https://developers.cloudflare.com/turnstile/get-started/).

## 6. Configure the server, PostgreSQL and Qdrant

For a public demo, set `APP_ENV=production`, an HTTPS `PUBLIC_BASE_URL`, PostgreSQL for business data, a separate Qdrant for vectors and persistent checkpoint storage. The API and worker must share database and model configuration.

The AWS Terraform blueprint covers ACM, ALB, Fargate, RDS, Secrets Manager and deployment workflows; see [deployment](deployment.md). Set the server-side OpenAI key ARN in `llm_secret_arn`, SMTP/Turnstile secret ARNs and public settings. The AWS configuration keeps demo mode and the $5 allowance enabled. Keep Stripe disabled and retain `DEMO_RELEASE=true` for this release.

Start with staging: review resource costs, migrations and backup restoration. The first deployment can use `bootstrap=true` to check page/database availability. After registering and verifying a test account, configure `SMOKE_EMAIL`/`SMOKE_PASSWORD` and run full acceptance with `bootstrap=false`. Repository preparation does not itself perform a push, infrastructure apply or deployment.

## 7. Connect real request sources

1. Create a request type under `Request types`.
2. For a hosted form, choose public visibility, create the form under `Request sources` and share its link on the business website.
3. For a CRM or website integration, create an API connection. Save the Bearer token, shown once, in the integration's secret storage.
4. Configure the source system to POST to the connection URL. `Request sources` provides a curl example. Send `customer`, `subject`, `message`, optional `email` and a stable `external_id` for deduplication.
5. Repeat a submission with the same `external_id`; it must not create a second request. Revoke a test token and verify that access is denied.

Inbound API calls create requests without automatically running paid AI. Native Gmail, Outlook and messenger connectors are not implemented; connect these services to the inbound API through an adapter. Replies are not sent automatically.

## 8. Verify the allowance and launch-notification records

`AI usage` shows spending, current reservations and the remaining allowance. At $5, paid operations stop and the UI explains demo status and future purchasing notifications. Creating another workspace does not grant another $5. `tests/test_demo_budget.py` verifies this without spending a real allowance.

The `demo_accounts` table records:

- `user_id`: links to the email in `users`;
- `spent_microusd`, `reserved_microusd`, `limit_microusd`: amounts in millionths of a dollar;
- `limit_reached_at`: the time the limit was reached;
- `launch_email_requested_at`: the notification request timestamp;
- `notification_reason`: `demo_budget_exhausted` or `launch_requested`;
- `launch_email_sent_at`: unset until a future delivery is recorded.

Users can choose `Email me at launch` before exhausting the allowance. Export verified addresses with pending notification requests using:

```bash
python -m scripts.demo_waitlist --output var/launch-notifications.csv
```

This creates a new local CSV and **does not send email**. Store personal data in a restricted operator environment outside Git. Record `launch_email_sent_at` only after future delivery handling, never in advance.

For Qdrant setup and transferring cached embeddings without new OpenAI calls, see [Qdrant operations](qdrant.md).
