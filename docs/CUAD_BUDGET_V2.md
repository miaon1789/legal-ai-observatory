# CUAD Equal-Budget Retrieval and SQL Handoff

Experiment completed locally on 7 September 2026.

Reporting update, 9 September: the sixth page of the
[saved PBIX](../powerbi/legal-ai-observatory.pbix) now displays these results.
Its imported rows, 14 evaluation measures and saved visual bindings were checked.
[Screenshot 08](../powerbi/screenshots/08-real-contract-evaluation.png) is this
sixth page, not an eighth report page. See the [release checklist](RELEASE_CHECKLIST.md)
for remaining presentation checks. The experiment record below retains its
original validation counts.

## Experiment

The v1 comparison changed only top-k and consequently returned more context.
Version 2 tests a more controlled retrieval change: each successful query returns
exactly `min(6000, source_length)` unique source characters. Overlap is removed;
the last selected interval is clipped if necessary. This may cut a word or clause
and does not constitute a polished model-input formatter. Character budgets are
not provider token budgets, semantic completeness guarantees or API prices.

The [protocol](../benchmarks/cuad/protocol-budget-v2.json) fixed three configurations
before development scoring, reusing v1's queries and BM25Okapi parameters:

| Configuration | Window / overlap, words | Additional rule |
|---|---|---|
| `bm25-250-budget` | 250 / 50 | Baseline |
| `bm25-120-budget` | 120 / 24 | Smaller windows |
| `bm25-120-preamble` | 120 / 24 | Reserve the first 800 characters for Agreement Date and Effective Date |

The preamble consumes the same budget, not an extra allowance. Remaining content
is selected by BM25 ranking, subtracting already selected source intervals. There
is no gold-guided selection, model API, embedding model or abstention classifier.
The smaller windows and preamble rule are deliberately simple hypotheses, not a
claim to understand document structure or a general legal-reasoning improvement.

## Development and fresh evaluation

All three configurations ran on the same ten v1 development contracts: 100 tasks,
66 with evidence and 34 without. The nonbaseline candidate was selected by highest
mean gold-character recall, then all-evidence coverage, then configuration ID.

| Development configuration | Recall | All gold covered |
|---|---:|---:|
| 250-word baseline | 78.94% | 69.70% |
| 120-word windows | 86.77% | 75.76% |
| 120-word windows + date preamble | 89.80% | 78.79% |

After that choice was saved, twenty further documents were selected from the
official test partition, excluding all forty v1 evaluation documents, the eight
earlier review candidates and normalized duplicate groups. Five were selected
from each length quartile of the remaining eligible pool using the fixed v1
title-hash ordering. The ten development documents remain unchanged.

The new evaluation has **200 tasks: 113 answerable and 87 unanswerable**.
Both the baseline and the selected candidate were run once. These results now
count as accessed evaluation data; no further tuning was done against them.
Near duplicates, related transactions and individual source-exhibit identity have
not been independently screened. Original gold offsets are validated, not corrected.

## Fresh results

| Metric | 250-word baseline | 120-word + date preamble |
|---|---:|---:|
| Mean gold-character recall, 113 positives | 85.13% | 91.86% |
| All gold covered, 113 positives | 79.65% (90) | 82.30% (93) |
| Any gold overlap, 113 positives | 88.50% (100) | 94.69% (107) |
| Mean context precision, positives | 4.22% | 4.49% |
| Mean unique context characters, all 200 tasks | 5,591.3 | 5,591.3 |
| Query failures / missing queries | 0 / 0 | 0 / 0 |

The paired mean recall difference is **+6.73 percentage points** at exactly matched
per-task character counts. This is not an improvement from v1's 80.83%: v1 used a
different evaluation sample and a different context policy.

There are **15 positive tasks with better recall, 9 worse, and 89 unchanged**.
Ten tasks gain complete coverage while seven lose it, explaining the smaller net
gain of three fully covered tasks. Thus the candidate is not uniformly better.

| Category | Positive tasks | Baseline recall | Candidate recall |
|---|---:|---:|---:|
| Agreement Date | 18 | 83.33% | 94.44% |
| Effective Date | 15 | 66.22% | 92.46% |
| Revenue/Profit Sharing | 7 | 62.67% | 95.25% |
| Exclusivity | 7 | 72.52% | 65.96% |
| Anti-Assignment | 13 | 90.43% | 89.95% |
| Expiration Date | 16 | 90.78% | 90.21% |

In the longest remaining-pool quartile (five contracts, 32 positive tasks), recall
is 79.72% versus 94.05%, but complete evidence coverage is still only 78.13% for
the candidate. Group thresholds differ from v1; do not present this as a direct
improvement over the old longest-contract group's result.

These are descriptive, paired findings on only twenty contracts. No production
readiness, broad significance, legal correctness or answer accuracy is claimed.
The 87 negative tasks are executed and retained but still have no answer/abstention
score. Human adjudication and external-processing review remain pending.

## Reproducibility

- Development run: `5799a89c1d6544b7b90ed1ac88f56245` (300 queries).
- Fresh evaluation run: `b2e440c1a7c14a67a8530723570a6fc7` (400 queries).
- Protocol SHA-256: `4840c67f99fb4c41eb022b38c65597fca041bd6994f823d455d6d517f93edf5c`.
- Code hashes, separate inputs/references, per-query logs, predictions, scores and
  index timings are saved privately. The selection references the development
  result hash and code hashes; changed code/selection prevents evaluation.
- [Public aggregate artifact](../results/cuad_budget_v2.json); no raw source text.

```bash
.venv/bin/python src/cuad_budget_experiment.py development
.venv/bin/python src/cuad_budget_experiment.py evaluate --development-run private/cuad_budget_v2/runs/5799a89c1d6544b7b90ed1ac88f56245
```

A rerun creates a new development ID; use that ID to evaluate the new run.
Replaying this protocol selects the same evaluation sample, not another unseen set.
The new experiment reuses ten contracts, adds twenty, and does not replace v1:
there are now 70 distinct contracts across the two substantive experiments, not 80.

CUAD is curated by [The Atticus Project](https://www.atticusprojectai.org/cuad/).
See [attribution and data-use limits](THIRD_PARTY_DATA.md). Source text is unchanged
and private; new local experiments do not authorize external model processing.

## SQL and Power BI

The additive [migration](../sql/07_evaluation/01_retrieval_results.sql) creates
`evaluation.experiment_run`, `evaluation.configuration`, and `evaluation.case_result`.
It does not relax the synthetic-only telemetry validator or alter the original
`dbo` objects. Contract text, quotes, gold answer text/spans and model responses are
not stored in this schema. Numeric answerability labels and gold-derived evidence
metrics are stored for reporting.

Three runs have been deployed to `LegalAIObservatory`: v1's original 1,000 rows,
v2's 300 development rows and 400 fresh-evaluation rows. All **1,700 rows** reconcile
field-by-field with Python. Repeating each import inserts zero rows. The row counts
of all fifteen original `dbo` and four `telemetry` tables are unchanged.

`evaluation.vw_case_results` is the detail view for the new Power BI table
`eval_results`; `evaluation.vw_config_summary` provides independent control totals.
Quality metrics include failed/missing positive tasks as zero and leave negative
task quality NULL. The DAX quality measures require a single run, configuration and
dataset partition, so development and evaluation results are not silently blended.

The bridge validates a strict field allowlist, task pairing, metadata consistency,
context budgets and numeric/null semantics before sending source-free JSON. Imports
are transactional, run IDs are immutable, and conflicting replay is rejected.
Runtime SQL access still uses the existing local administrator bridge, not a
production least-privilege service. The integrity hash is not a cryptographic
signature or authentication of experiment authorship.

```bash
.venv/bin/python src/cuad_warehouse.py prepare --run private/cuad_budget_v2/runs/b2e440c1a7c14a67a8530723570a6fc7
.venv/bin/python src/cuad_warehouse.py deploy
.venv/bin/python src/cuad_warehouse.py ingest --packet private/cuad_warehouse/b2e440c1a7c14a67a8530723570a6fc7.json
.venv/bin/python src/cuad_warehouse.py verify --packet private/cuad_warehouse/b2e440c1a7c14a67a8530723570a6fc7.json
```

Validation: 128 offline tests pass and six new SQL integration tests pass in an
automatically created/removed database. The new SQL tests cover long JSON transport,
replay, conflict protection, failure/NULL denominators, rollback and run isolation.
At that experiment checkpoint, Power BI DAX and layout awaited Windows work.
The subsequent saved-file review is recorded at the top of this document.

Tonight's assets are [the Power Query connection](../powerbi/cuad_query.m) and
[fourteen DAX measures](../powerbi/cuad_measures.dax). The Chinese step-by-step
handoff is private. The original experiment ended at a working SQL reporting
layer. The later sixth page adds the numerical reporting view without changing
the experiment, publishing source contracts or evaluating model answers.
