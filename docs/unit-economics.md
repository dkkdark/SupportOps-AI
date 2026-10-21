# Subscription unit economics

> Future paid-launch planning only. The current demo hides pricing and disables payments, with a one-time $5 AI allowance per user account. See [current API setup](api-setup.ru.md).

The published plans charge per business workspace. They include bounded AI usage rather than unlimited model calls. Prices/limits are implemented in `supportops/billing.py`; plan IDs and payment amounts are validated server-side.

| Plan | Revenue / month | AI credits | Maximum configured direct model-cost allowance | Revenue remaining before other costs |
| --- | ---: | ---: | ---: | ---: |
| Starter | $49 | 1,000 | $5 | $44 |
| Growth | $149 | 5,000 | $25 | $124 |
| Scale | $399 | 15,000 | $75 | $324 |

The internal cost unit is $0.005 per credit. Actual recorded provider cost is rounded up to credits, including document indexing, query embeddings, agent turns and verification. This puts a bound on included model expense at the **configured** token rates. The default per-run provider budget is $0.40; reservations prevent concurrent work from exceeding a business’s credit allowance. Unknown charges are retained for reconciliation. No automatic overages, purchases or top-ups occur.

These are contribution amounts before hosting, payment fees, tax treatment, support, acquisition, refunds and other operating costs. They are not profit or a profitability guarantee. Rates must be updated when models or provider prices change.

## Current model-rate assumptions

- GPT-5.4 mini: $0.75 input and $4.50 output per million tokens.
- The separate verifier uses the same model by default, with its own configurable rates.
- text-embedding-3-large: $0.13 per million input tokens.
- Cached-input discounts are not assumed, so normal successful calls may cost less than this accounting estimate.

Sources: [OpenAI model pricing](https://developers.openai.com/api/docs/models/gpt-5.4-mini) and [OpenAI API pricing](https://developers.openai.com/api/docs/pricing), checked October 21, 2026. Provider billing remains authoritative, particularly for ambiguous network failures.

A single synthetic live acceptance run used approximately $0.00354 for agent/query/review calls, excluding initial document embedding. It is a technical example, not a representative cost estimate. Long documents, repeated tool searches and large outputs consume more; credits cannot honestly be advertised as a fixed number of responses.

## Example break-even scenario

Assume, purely for planning, $150/month of shared fixed infrastructure/operations cost. This is an editable scenario, **not an AWS quote**. Before payment fees, support and acquisition:

```text
Contribution = subscription revenue − actual AI cost − variable costs
Break-even businesses = ceiling(fixed costs / average contribution per business)
```

At the full Starter AI allowance, the simplified contribution is $44, so $150 / $44 rounds up to 4 businesses. That calculation excludes every other variable cost and must not be used as a final launch budget. Price the actual cloud configuration in the [AWS calculator](https://calculator.aws/), inspect your Stripe account’s applicable fees, and measure support/acquisition costs in the first pilot.

## Limits that protect the business

- A 14-day trial has 100 one-time credits: at most $0.50 in configured direct model cost per genuine trial, before other costs.
- Verified production email, registration throttles, Turnstile and request/knowledge/seat limits help control abuse; they do not make abuse impossible.
- Publishing a document checks the stored-chunk limit before indexing.
- Inbound credentials create requests only. They cannot trigger paid AI automatically.
- The provider budget is checked before each model request; an over-budget investigation hands off.
- Seat limits count active memberships and valid pending invitations.
- Subscription entitlements come from verified Stripe events and paid-period dates.
- Changing a plan or renewing a period cannot silently create unlimited credits.

Review weekly during pilot: revenue, AI cost, credit reservations awaiting reconciliation, hosting, active companies/seats, retained customers, support time and acquisition cost. Adjust published plans only after reviewing the effect on existing subscriptions and operator terms.
