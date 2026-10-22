# SupportOps AI: launch readiness and remaining steps

> The current release is a demo: pricing and payments are hidden, with a one-time $5 AI allowance per account. The paid-launch steps below apply to a future release. For current setup, use [API setup](api-setup.md).

SupportOps AI handles business requests across operations, sales, internal services, IT and customer-facing teams. Each business has its own workspace, knowledge, employees and request types. The current demo AI allowance belongs to the user account across all businesses; future subscription credits are workspace-based.

## Implemented capabilities

- Business registration, roles, invitations, email verification and password recovery.
- A real OpenAI agent with tools, semantic RAG using `text-embedding-3-large`, response generation, citations and independent model verification.
- Public forms, inbound API, internal requests, request types and employee assignments.
- Model-cost accounting, credit reservations and server-enforced limits. Trial and three paid-plan implementations remain behind the disabled paid-release mode.
- Stripe Checkout, Customer Portal and signed payment-event verification, disabled in the current demo.
- A landing page explaining workflows, channels, use cases and AI; pricing is hidden during demo mode.
- Docker/Helm/Terraform configurations, background indexing, tests, audit history and data export.

OpenAI integration has been checked with a real call on a synthetic internal request. Live Stripe payments and cloud deployment have not been performed.

## 1. Define the first market and offer

Choose one clear initial workflow, such as incoming requests for a small service business or internal operations. Define the outcome: a shared queue, less manual information lookup and a draft employees can verify. Start with 3–5 pilot businesses.

Prepare anonymized procedures and 20–30 representative requests with expected actions. Employees should assess correctness, completeness, review time and cases requiring manual handling. A single technical acceptance test does not establish accuracy or time savings.

## 2. Prepare operator accounts and business details

You will need:

- An OpenAI API project/key with access to the configured models. Keep it server-side, configure spending notifications and review pricing regularly.
- For a future paid launch, a Stripe account available to the business, billing/tax details and a payout account.
- A domain and DNS access, plus AWS or another hosting provider with PostgreSQL for business records, Qdrant for vectors and HTTPS.
- An SMTP provider with a verified sender for verification and recovery emails.
- Cloudflare Turnstile site/secret keys for the domain.
- Legal operator identity, contact email and reviewed documents for the target jurisdiction.

The operator must supply entity, tax, banking, domain and legal decisions and the necessary account access.

## 3. Configure models and indexing

Copy `.env.example` to an ignored `.env` if no environment file exists, or configure equivalent server secrets:

```text
MODEL_PROVIDER=openai
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=...
AGENT_MODEL=gpt-5.4-mini
VERIFIER_MODEL=gpt-5.4-mini
EMBEDDING_MODEL=text-embedding-3-large
```

When changing models, update token rates and verify cost limits. `python -m scripts.live_acceptance` performs a bounded real-model check on synthetic data. Then evaluate a representative business dataset. The offline `baseline` mode does not demonstrate real AI integration.

Run `python -m scripts.worker` on the server. `scripts.index_worker` is a compatibility entry point. Compose/Helm/AWS configurations include worker handling. Reindex knowledge after changing embedding models; model and index versions remain isolated.

## 4. Configure Stripe subscriptions for a future paid launch

These prices are implemented planning assumptions, hidden in the current demo. Create monthly USD prices:

| Plan | Price | AI credits | Seats |
| --- | ---: | ---: | ---: |
| Starter | $49 | 1,000 | 5 |
| Growth | $149 | 5,000 | 20 |
| Scale | $399 | 15,000 | 50 |

Set `STRIPE_SECRET_KEY`, `STRIPE_STARTER_PRICE_ID`, `STRIPE_GROWTH_PRICE_ID` and `STRIPE_SCALE_PRICE_ID`. Start in test mode. The server rejects prices with a different currency, amount or billing period.

Add `https://your-domain.example/api/webhooks/stripe` and store `STRIPE_WEBHOOK_SECRET`. Required events:

```text
checkout.session.completed
customer.subscription.created
customer.subscription.updated
customer.subscription.deleted
invoice.paid
invoice.payment_failed
```

Configure Customer Portal with allowed plans, subscription changes and a clear cancellation policy. Configure Stripe Tax in the account before enabling `STRIPE_AUTOMATIC_TAX=true`.

Test successful subscriptions, payment failures, webhook retries, plan changes, cancellation and period expiry. A success-URL redirect alone must not update credits. After acceptance, configure live keys and price IDs. Real charges remain an action for the Stripe account owner; automated live purchases have not been performed.

## 5. Configure email, form protection and legal documents

Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` and `SMTP_FROM`. Use an inbox you control to check delivery, spam handling, email verification and password recovery. Production users with unverified email cannot run AI or connect external request sources.

Set `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` for the deployed domain before public registration and form intake.

`/terms` and `/privacy` contain drafts. Review service terms, processing, retention/deletion, subprocessors, cancellation/refunds and operator contact details. Configure:

```text
LEGAL_BUSINESS_NAME=...
LEGAL_CONTACT_EMAIL=...
LEGAL_TERMS_URL=https://.../reviewed-terms
LEGAL_PRIVACY_URL=https://.../reviewed-privacy
LEGAL_DOCUMENTS_APPROVED=true
```

The approval flag does not replace document review. Production paid checkout stays blocked until these settings are complete. Publishing legal documents and determining applicable requirements remain operator decisions.

## 6. Deploy staging with HTTPS and PostgreSQL

Follow [deployment](deployment.md). AWS requires ACM, DNS, private RDS, a separate Qdrant service, Secrets Manager and a GitHub staging environment. Set the actual HTTPS `PUBLIC_BASE_URL`.

For the first GitHub deployment, `bootstrap=true` checks only page/database availability. Register an account over HTTPS, verify its email, add `SMOKE_EMAIL`/`SMOKE_PASSWORD` to staging secrets and deploy with `bootstrap=false` for full acceptance. Bootstrap does not establish payment readiness.

Before migration, create a consistent application/checkpoint backup. Run `python -m scripts.migrate`, then start the API and worker. Verify availability, restart recovery and an isolated backup restoration.

Deployment requires a separate review of resources and costs and explicit authorization to push/apply/deploy. Preparing these files does not provision AWS resources.

## 7. Complete acceptance before collecting payments

- Register a business and verify the owner's email.
- Register a second business and verify tenant isolation for requests and knowledge.
- Upload policies, wait for indexing, and test version updates and archive handling.
- Create request types, a public form and an API connection; submit requests externally.
- Test `external_id` deduplication, API-token revocation and employee-access removal.
- Complete the agent workflow: tools → evidence → verification → employee review → saved decision.
- Verify that exhausted credits block new paid calls while existing records remain readable.
- Test account email/recovery and Stripe payment/cancellation flows in test mode.
- Configure monitoring, support contact, backups and deletion-request handling.
- Create a verified staging test account for `SMOKE_EMAIL`/`SMOKE_PASSWORD`.

## 8. Run a limited paid pilot

For a separately approved paid release, onboard 3–5 businesses, help upload their knowledge and choose their intake channel. Email and messenger integrations need adapters to the inbound API; native inbox reading is not implemented. Incoming-request attachments and automatic delivery of results are also outside the current release.

Track per-business AI costs, credit usage, manual handoffs, response quality and review time during the first weeks. Adjust limits using actual evidence before wider sales.

A complete launch requires real hosting, verified credentials/delivery/payments, operator documentation and a successful pilot. Prepared code and local tests do not replace these steps.
