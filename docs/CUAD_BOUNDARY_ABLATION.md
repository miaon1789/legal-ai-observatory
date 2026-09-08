# Sentence-Boundary Development Ablation

Completed locally on 7 September 2026. **Not promoted:** the candidate fails the
predeclared recall and regression guardrails. Existing SQL and Power BI data remain
unchanged. All values below refer to development data, not the v2 fresh evaluation.

## Question and fixed design

The [v2 experiment](CUAD_BUDGET_V2.md) selected 120-word windows with a date-preamble
rule. Subsequent development diagnostics found 14 incompletely covered positive
tasks, including seven with partial evidence crossing a visited window boundary.
These were nonexclusive diagnostic features, not verified causal labels.

This [protocol](../benchmarks/cuad/protocol-boundaries-v3.json) was fixed before
scoring the new configurations. It was explicitly informed by those already seen
development failures. The same ten contracts, 100 tasks, 66 positives and 34
negatives are reused. No evaluation document is read by the new runner.

The experiment uses [pySBD 0.3.4](https://pypi.org/project/pysbd/0.3.4/), a local
rule-based sentence segmenter, with unchanged source text and character spans.
See [the upstream implementation](https://github.com/nipunsadvilkar/pySBD).
Detected sentence starts partition the source; whitespace is retained and offsets
are checked. These units are not guaranteed to be complete legal clauses.

| Arm | Context policy |
|---|---|
| `v2-window-6000` | Exact replay of the selected v2 policy, up to 6,000 unique characters |
| `sentence-units-6000` | Same BM25 window ranking; visit intersecting sentence units in source order; accept whole units that fit, otherwise skip; never clip or pad |
| `v2-window-matched` | Original v2 selector with each query's actual candidate character count as its budget |

Overlapping units are deduplicated. Date queries retain the 800-character seed's
priority, but the candidate expands that seed to intersecting whole units within
the shared total cap. That can consume more than 800 characters. Query terms,
BM25 parameters, original indexing windows, labels and offsets do not change.

The matched arm is a diagnostic control, not a deployable independent policy:
its budget depends on the candidate's output length, never on gold evidence.
Candidate failures produce failed controls, not successful empty controls.
All new predictions are saved before this runner loads the reference answers.

## Results

| Metric | Original v2 | Whole sentence units | Matched-length v2 |
|---|---:|---:|---:|
| Mean gold-character recall, 66 positives | 89.80% | 87.92% | 89.80% |
| Complete evidence coverage | 78.79% (52/66) | 80.30% (53/66) | 78.79% (52/66) |
| Any evidence overlap | 98.48% | 95.45% | 98.48% |
| Mean unique context characters, 100 tasks | 5,753.2 | 5,751.8 | 5,751.8 |
| Failed / missing queries | 0 / 0 | 0 / 0 | 0 / 0 |

The candidate improves recall on six positive tasks, regresses on five, and leaves
55 unchanged. Three tasks gain complete evidence coverage, while two lose it:
the net improvement in complete coverage is only one task. Mean recall falls by
**1.88 percentage points**. The matched-length comparison has the same quality
results, so the decline is not explained solely by returning fewer characters.
This does not isolate every interaction of segmentation, header expansion and
greedy unit packing.

The two newly incomplete tasks are dates: one Effective Date and one Agreement
Date, both falling from full coverage to no gold overlap. Their gold text contains
29 and 34 characters, respectively. The containing units need 338 and 174
characters, but only 31 and 107 remain when first visited. The fixed whole-unit
rule skips them. Preserving a larger unit can therefore displace a short answer.

Three previously incomplete tasks become complete: two Anti-Assignment tasks and
one Audit Rights task. This is a real benefit, but not a uniform improvement.
No query/label was amended after inspecting these results.

The segmenter processed all ten contracts into 4,032 units; none exceeded 6,000
characters. All 100 candidate queries use whole source units and exactly match
their controls' unique character counts. Neither arm has empty contexts. The 34
negative tasks retain NULL quality metrics; answers and abstentions are not scored.

## Decision

The primary metric, complete evidence coverage, improves. However, two predeclared
guardrails fail: mean recall must not decline, and no previously complete task may
be lost. Therefore this policy **does not qualify for a new frozen evaluation**.
No broad claim about all sentence-aware retrieval methods follows from this one
heuristic. The current v2 configuration remains the reporting baseline.

Next work should remain on development data: test morphology normalization as a
separate variable, and consider a bounded expansion policy that protects original
retrieved evidence instead of replacing it indiscriminately. Any future change
needs its own frozen protocol. Do not repeatedly tune against the accessed v2
evaluation sample or present development scores as new generalization evidence.

## Reproducibility and validation

- Primary run: `820863b1d64e44599caf5cbb565eb3c4` (300 queries).
- Verification rerun: `55cfd5f2391c4b7f8c941cfc5f986092` (same data, not another sample).
- Parent development run: `5799a89c1d6544b7b90ed1ac88f56245`.
- Protocol SHA-256: `3b8aa96c968c3144661a9ef44fc9f8ee47e1c863986687486a7f3cebaa619044`.
- Runner SHA-256: `2657dbf89406ca73071a0789924bf9869fef3938a1ab61a6f5c2253d6c696266`.
- All 300 predictions and non-timing score records match between runs. Timing is
  descriptive only: segmentation is separate, and matched controls execute last.
- 149 offline tests pass, including 13 new boundary tests. SQL was not modified
  or retested in this step. No model API, human adjudication or Windows validation
  was performed in this experiment.
- [Public aggregate artifact](../results/cuad_boundaries_v3.json). Raw text,
  evidence spans, predictions, logs and per-case changes remain private.

```bash
.venv/bin/python -m pip install -r requirements-cuad.txt
.venv/bin/python -m pip install --no-deps --require-hashes -r requirements-cuad-boundaries.txt
.venv/bin/python src/cuad_boundary_experiment.py
env PYTHONPATH=tests .venv/bin/python -m unittest test_cuad_boundaries
```

Local replay requires the private frozen parent artifacts. They are not included
in Git. A fresh clone must first recreate v2 development and explicitly version
a protocol referencing its newly generated parent run ID. Repeated execution
here is a reproducibility check, not another unseen evaluation.

CUAD attribution and data-use boundaries remain in [THIRD_PARTY_DATA.md](THIRD_PARTY_DATA.md).
This new development run does not authorize source publication or external model
processing. The 1,700-row SQL reporting layer and tonight's Power BI acceptance
values are unchanged.
