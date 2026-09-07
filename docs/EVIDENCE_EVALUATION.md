# Offline Multi-Evidence Evaluation

Validated: 7 September 2026. Metric version: `evidence-union-v1`.

**Implemented:** an independent evidence scorer, eight project-created fictional
documents with 12 tasks, seven scripted scenarios, a repeatable CLI and 32 tests.
This is a software-validation stage, not model evaluation or an official CUAD
benchmark result. No CUAD text or paid API was used in the demonstration.

The existing single-answer scorer in `src/observability/benchmark.py` and all
existing telemetry/SQL/PBIX behavior remain unchanged. The new module is
`src/observability/evidence.py`; its CLI is `src/evidence_eval.py`.

## What the scorer measures

Predictions contain an explicit abstention decision and zero or more cited spans.
Each span must identify an exact quote in the supplied document at `[start, end)`.
Offsets count Unicode code points, not UTF-8 bytes or JavaScript UTF-16 units.
The scorer does not search for a quote elsewhere when its supplied position is
wrong, normalize whitespace, change capitalization or repair malformed evidence.

All supplied gold spans are **required evidence**, not alternative acceptable
answers. Convert any future alternative-answer dataset explicitly before use.
Multiple conditions, split entities and overlapping annotations remain intact.
For scoring, duplicate, overlapping and touching intervals are merged into a union.
The original benchmark annotations are not modified.

For each answerable case, let `P` be the set of predicted character positions and
`G` the set of gold character positions:

```text
precision = size(P intersect G) / size(P)
recall    = size(P intersect G) / size(G)
F1        = 2 * precision * recall / (precision + recall)
exact evidence union = (P == G)
all gold covered    = (G is a subset of P)
```

Whitespace inside a span counts. Evidence precision penalizes unrelated extra
text, even when recall is perfect. Exact union ignores segmentation: adjacent
fragments and an equivalent single span have the same union. This is not the
same as exact span-list equality or the official CUAD evaluation procedure.

## Denominators and failures

- Evidence precision, recall and F1 are per-case macro means across **all
  answerable cases**. F1 is the mean of case F1 values, not F1 of the mean
  precision/recall. Missing, failed, invalid and incorrectly abstained responses
  contribute zero to these metrics. If no case is answerable, they are null.
- Correct-abstention rate uses **all unanswerable cases**. Missing responses,
  errors and invalid output never receive abstention credit. If no case is
  unanswerable, the rate is null. These cases do not inflate evidence F1.
- Coverage reports submitted, valid, missing, invalid and error counts. A valid
  response is structurally valid with source-matching quotes, not necessarily
  correct evidence. A wrong occurrence of a repeated quote can be valid but wrong.
- A bad quote or offset invalidates that case's entire response. A response with
  both abstention and evidence, or a successful non-abstention with no evidence,
  is invalid. Explicit error responses must have null abstention and empty spans.
- Missing expected IDs remain in the report. Duplicate, unknown or unidentified
  prediction IDs stop the run, as do invalid gold annotations or stale benchmark
  hashes. Rows tied to the wrong document receive no credit.
- An incorrect-abstention count describes explicit valid refusals, not missing
  or failed responses. Inspect it with coverage rather than treating a low
  refusal rate as sufficient evidence of success. The same caution applies to
  false-answer rate on unanswerable tasks.

There is deliberately no blended headline quality score. Per-category and
per-case results are retained alongside the overall separated metrics.

## Fictional demonstration

`benchmarks/evidence/v1.json` was created for this project with AI assistance.
It contains eight invented short documents, eight answerable tasks and four
unanswerable tasks. No client document, external contract quotation or original
CUAD annotation was copied into these fixtures. The provenance label is not a
claim of exclusively human authorship or a rights warranty.

Cases cover conditional dates, overlapping entity mentions, rights restricted
to part of an arrangement, unresolved numeric conflicts, repeated text, Unicode
offsets, missing referenced material, exceptions and an untrusted instruction
embedded in source text. The last case tests the scoring fixture only; no model
has been shown to resist that instruction by this demonstration.

Run from the repository root:

```bash
.venv/bin/python src/evidence_eval.py demo
.venv/bin/python -m unittest discover -s tests -p 'test_evidence_eval.py' -v
```

Measured scripted-check results, rounded to one decimal place:

| Scripted output | Evidence precision | Evidence recall | Evidence F1 | Correct abstention |
|---|---:|---:|---:|---:|
| Gold replay | 100.0% | 100.0% | 100.0% | 100.0% |
| Always abstain | 0.0% | 0.0% | 0.0% | 100.0% |
| First gold span only | 100.0% | 56.9% | 68.5% | 100.0% |
| Quote entire document | 44.0% | 100.0% | 56.0% | 0.0% |
| Invalid answer offsets | 0.0% | 0.0% | 0.0% | 100.0% |
| Half the predictions missing | 50.0% | 50.0% | 50.0% | 50.0% |
| All requests fail | 0.0% | 0.0% | 0.0% | 0.0% |

Gold replay and the first-span scenario intentionally consult the gold labels
to exercise the scorer. They are neither retrieval methods nor model outputs.
The invalid-offset scenario has eight invalid answerable responses and four
valid scripted abstentions; their separate metrics expose that difference.

## Artifacts and input contract

Outputs are written under `private/evidence_evaluation/<content-derived-id>/`
with mode `0600`; output paths outside `private/` or through symlinks are rejected.
The demonstration produces `summary.md`, a benchmark copy, `inputs.json`, and
numbered prediction/result JSON pairs. Rerunning the same inputs and scorer
results writes the same directory. Keep manual notes outside generated files.

`inputs.json` contains documents, scope notes and questions, with no gold label
or answer-span fields. Gold remains in the benchmark and the deliberately
gold-derived scripted prediction files. Those are not model inputs. Per-case
result JSON records contain metrics and reason codes, not quoted source text.

`score` accepts a saved synthetic scripted prediction packet:

```bash
.venv/bin/python src/evidence_eval.py score --benchmark path/to/benchmark.json --predictions path/to/predictions.json
```

The packet has `format: evidence-predictions-v1`, a `prediction_origin` of
`scripted_fixture`, `config_id`, `benchmark_sha256` and a `predictions` list.
Each row has `case_id`, `document_id`, `status`, `abstain` and `spans`.
Each span has `start`, `end` and `quote`. The benchmark hash is `events.digest`
of canonical JSON content, **not** a raw-file checksum. Use a generated packet
as the format example. Duplicate JSON fields and non-finite numbers are rejected.

The CLI recognizes only its synthetic benchmark format and scripted provenance.
It rejects observed/model labels and CUAD preparation files. These labels are
declarations, not a mechanism that proves authorship; never relabel unreviewed
real material as synthetic to bypass a scope restriction.

## Verification and remaining work

All 32 new tests passed. This includes an independent set-of-positions reference
check against the interval algorithm on 500 deterministic random examples, plus
CLI repeatability, private paths, Unicode, failure denominators and exact quotes.
The previous 50 offline tests were rerun: **82 passed in total**. No SQL integration
or Windows report tests were rerun, since those components were not changed.

This scorer evaluates extraction, not whether a natural-language answer correctly
interprets a legal provision. A perfect quotation can accompany an incorrect
explanation; this schema does not accept or score explanations. It also does not
establish robustness of any model, resolve disputed gold annotations, support
alternative evidence sets, or prove a dataset was unseen in model training.

Next integration should add a separately versioned multi-span response adapter,
human-review fields and real-versus-scripted provenance, with unequal coverage
kept visible. Any actual provider call and real-contract use still require their
own scope, terms and budget decisions under [data governance](DATA_GOVERNANCE.md).
The [CUAD preparation status](CUAD_PREPARATION.md) has not been upgraded by these tests.
