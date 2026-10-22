# Evaluation and release guard

This suite evaluates the isolated synthetic retail workflow only. Real business workspaces do not display this benchmark as their own score; they require a representative business-specific dataset. Tenant isolation and uploaded-content workflows are covered by `tests/test_workspaces.py`.

## Dataset

`eval/dataset.json` contains 100 English synthetic requests in 20 hand-labeled scenario families. Each family has four development phrasings and one reserved holdout phrasing. `eval/build_dataset.py` reproduces the checked-in dataset; labels are explicit, not calculated from the classifier. Russian input remains covered by API regression tests.

The families cover delivery, delayed delivery + cancellation, cancellations before/after dispatch, current return policy, damage, payment duplication, refunds, address changes, missing/unknown orders, ownership mismatch, API timeout, malformed model output, missing documents, unsupported intent, delivered-order cancellation, return before delivery and instruction override attempts.

The dataset is intentionally small. Shared scenario families make the holdout a regression signal, not evidence of generalization to unseen customer populations. The corpus does not include independently collected production requests.

## Measures

| Metric | Definition |
| --- | --- |
| Classification accuracy | Exact match of the multi-label category set |
| Macro-F1 | Mean per-category F1 across the six categories |
| Factual accuracy | Correct no-draft behavior for manual cases; otherwise required fact IDs, canonical fact text, passing checks and no archived return source |
| Escalation recall | Expected-manual cases that produce a manual reason |
| Latency | End-to-end analyze duration at sequential load, p50 and p95 |
| LLM usage | Sum of reported input/output tokens and configured price estimate |

Each case must also match its exact expected manual reason. Every case must pass for the CLI exit status to be zero; the report records the stricter case-pass gate as well as component thresholds. A normal HTTP health check cannot replace these checks.

This is a contract/fact-selection evaluator. It does not claim an independent LLM-as-judge evaluation, human preference study, source-entailment study for arbitrary prose or measured reduction in agent handling time. Human review edits are audited but are not automatically revalidated as model output.

## Isolation

Each run creates an isolated temporary database, controlled order API responses and fresh checkpoints. The order fixtures’ delivered dates are set to two days before the evaluation date so a moving calendar does not invalidate the ordinary-return scenario. The normal application uses the dates in the actual synthetic API fixture. Both date assumptions are intentional; the policies themselves are effective from October 1, 2026.

Development and reserved holdout data have separate roles; a holdout used for further tuning no longer represents untouched release evidence. Multilingual classifier behavior is covered separately by regression tests.

## Live benchmark

The live run uses the same controlled order fixtures and the real external selector. Fault-injected cases intentionally skip or corrupt the model response. There are at most two model attempts per generated case, and the input size is bounded by the small dataset. Results depend on provider compatibility, configured prices and endpoint behavior.

## Evidence

Reports record dataset version/hash, split, seed, prompt version, code version, model, per-case document/embedding manifest, reasons, assertions, durations and token usage. CI uploads reports as artifacts named by code commit. The staging deployment additionally saves the image digest and previous/next task definitions. Technical readiness and evaluation evidence are separate signals. Passing baseline tests alone says nothing about an untested external model.
