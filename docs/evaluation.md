# Evaluation and release guard

## Dataset

`eval/dataset.json` contains 100 English synthetic requests in 20 hand-labeled scenario families. Each family has four development phrasings and one reserved holdout phrasing. `eval/build_dataset.py` reproduces the checked-in dataset; labels are explicit, not calculated from the classifier. Russian input remains covered by API regression tests.

The families cover delivery, delayed delivery + cancellation, cancellations before/after dispatch, current return policy, damage, payment duplication, refunds, address changes, missing/unknown orders, ownership mismatch, API timeout, malformed model output, missing documents, unsupported intent, delivered-order cancellation, return before delivery and instruction override attempts.

The dataset is intentionally small. Shared scenario families make the holdout a regression signal, not evidence of generalization to unseen customer populations. Add independently collected and privacy-reviewed requests for a production benchmark.

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

## Reproduction

```bash
source .venv/bin/activate
python -m eval.run --split dev --output reports/dev.json
python -m eval.run --split holdout
```

Each run creates an isolated temporary database, controlled order API responses and fresh checkpoints. The order fixtures’ delivered dates are set to two days before the evaluation date so a moving calendar does not invalidate the ordinary-return scenario. The normal application uses the dates in the actual synthetic API fixture. Both date assumptions are intentional; the policies themselves are effective from October 1, 2026.

The development corpus identified a Russian inflection bug (`денег` versus `деньг`) before the English conversion. The regression remains in tests. Evaluate development changes against development data, then run the reserved set once for release assessment. When a reserved result drives further tuning, retire/version that set and create a new final sample rather than reporting it as untouched.

## Live benchmark

Set `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY` and actual token rates, then run:

```bash
python -m eval.run --split holdout --live --output reports/live.json
```

The live run uses the same controlled order fixtures and the real external selector. Fault-injected cases intentionally skip or corrupt the model response. There are at most two model attempts per generated case, and the input size is bounded by the small dataset. Provider compatibility, pricing and actual behavior must be checked with your selected endpoint. No real-model run has been performed in this environment.

## Evidence

Reports record dataset version/hash, split, seed, prompt version, code version, model, per-case document/embedding manifest, reasons, assertions, durations and token usage. CI uploads reports as artifacts named by code commit. The staging deployment additionally saves the image digest and previous/next task definitions. Compare both technical readiness and evaluation evidence before a release. Passing baseline tests alone says nothing about an untested external model.
