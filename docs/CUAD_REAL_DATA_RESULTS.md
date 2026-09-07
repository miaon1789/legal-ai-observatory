# Real-Contract Retrieval Baseline

Run date: 7 September 2026. Status: completed local retrieval on **50 real public
contracts and 500 annotated tasks**, not an LLM evaluation or a firm's usage log.

## What is real

The texts and reference annotations come from [CUAD, curated by The Atticus
Project](https://www.atticusprojectai.org/cuad/). The publisher describes 510
commercial contracts with lawyer-supervised annotation across 41 clause types.
This project uses a pinned subset, not newly invented contracts or oracle output.
The [public result artifact](../results/cuad_retrieval_v1.json) contains aggregate
measurements only. Source texts, gold answers, selected offsets and query logs
stay under ignored `private/cuad_experiment/`.

| Partition | Official source | Contracts | Tasks | Answerable | Unanswerable |
|---|---|---:|---:|---:|---:|
| Development | Train | 10 | 100 | 66 | 34 |
| Evaluation | Test | 40 | 400 | 215 | 185 |
| Total | Disjoint official partitions | 50 | 500 | 281 | 219 |

There are 418 original gold spans across the selected tasks. Every selected
span matches its exact source position; zero offsets were repaired. All ten
categories are asked on every document, including cases with no annotated answer.
"Unanswerable" means the CUAD annotation for this supplied text and category,
not a legal finding that a provision is absent from every related agreement.
Selected texts range from 857 to 249,551 characters, totalling 2,612,174 characters.

## Fixed protocol

The [versioned protocol](../benchmarks/cuad/protocol-v1.json) was written before
the first real-data scoring run. Selection and retrieval do not use gold locations,
answerability or observed performance.

- Source revision: `67faa0e6023b04fcaae6cc09497ab00e5d63a2a2`.
- Archive SHA-256: `f8161d18bea4e9c05e78fa6dda61c19c846fb8087ea969c172753bc2f45b999a`.
- Canonical `CUADv1.json` supplies text and labels; the archive's train/test files
  supply document membership. Selected text copies must agree exactly.
- All members of whitespace-normalized, case-folded duplicate groups are excluded:
  two documents in one group. Eight previously reviewed pilot documents are also
  excluded, including the held candidates. Their separate review decisions remain intact.
- Within each official split, eligible documents are sorted by character length,
  divided into four equal-count bands, then ranked by a fixed seeded title hash.
  Development quotas are 2/3/2/3; evaluation quotas are 10/10/10/10.
- Contract-family, counterparty and near-duplicate screening are **not** implemented.
  Length stratification is not proof of a representative contract-type distribution.
- Source text is unchanged. Only a local algorithm reads it for this experiment;
  automated contact/notice markers are recorded privately, not treated as clearance.

Automated triage flags email patterns in 11 documents, phone-like patterns in 11,
bank-details language in 7 and confidential-treatment/redaction language in 17.
These overlapping counts are heuristics, not verified personal-data inventories,
bank-account detections or legal determinations. They reinforce the need to review
the actual outgoing excerpts before any external-model stage. No individual
source-exhibit verification or full-text human review of these 50 texts is claimed.

Within each contract, contiguous windows contain at most 250 whitespace-delimited
words, with 50-word overlap. No source truncation or gold-guided chunk placement is
used. Exact Unicode code-point offsets are retained. The fixed category queries
use the same Unicode word tokenization and case folding as the indexed text.

The retrieval engine is [rank-bm25 0.2.2](https://pypi.org/project/rank-bm25/0.2.2/),
using BM25Okapi with `k1=1.5`, `b=0.75`, `epsilon=0.25`. Both configurations use
the same ranking; only top-k differs. Ties use source order. Even zero or negative
scores remain rankings, not a calibrated prediction of answerability. This is
**within-document** retrieval, not search across an entire contract collection.

## Evaluation results

Positive-task quality metrics below use all **215 answerable evaluation tasks**.
Missing or failed positive tasks would score zero; there were none in this run.
Context size and latency summarize all 400 evaluation tasks per configuration.

| Metric | BM25 Top 3 | BM25 Top 5 |
|---|---:|---:|
| Mean gold-character recall | 74.09% | 80.83% |
| Tasks with any gold overlap | 77.67% | 84.19% |
| Tasks with all gold characters covered | 66.51% | 73.02% |
| Mean context precision | 6.10% | 4.00% |
| Mean retrieved characters, overlap deduplicated | 4,299 | 6,716 |
| Mean retrieved characters, overlapping chunks concatenated | 4,606 | 7,338 |
| Local query latency p50 | 0.057 ms | 0.057 ms |
| Local query latency p95 | 0.202 ms | 0.194 ms |

For each positive task, gold spans and retrieved intervals are independently
unioned. Recall is their intersecting character count divided by gold character
count; context precision divides the same intersection by retrieved character
count. Metrics are averaged across questions. Overlapping or duplicate spans
cannot inflate the intersection. "Any hit" can be a small partial overlap;
"all covered" is the stricter retrieval measure. Neither proves understanding.

Top 5 adds **6.75 percentage points** of mean evidence recall while increasing
deduplicated context by **56.2%**. Returning a superset mechanically cannot reduce
coverage; this is a context-budget tradeoff, not proof of a better reasoning system.
Low context precision is expected for small date/name spans inside long chunks,
but also quantifies how much irrelevant material a downstream model would receive.

The 185 negative evaluation tasks are retained in logs and workload denominators.
Top 5 still returns an average of 5,999 deduplicated characters for them. No
abstention classifier or answer generator exists in this baseline, so answer
accuracy, hallucination rate and abstention accuracy are **not measured**. They
are `null`, not zero or a success score.

### Where evidence is missed

| Clause category | Positive / negative tasks | Top 3 recall | Top 5 recall |
|---|---:|---:|---:|
| Agreement Date | 36 / 4 | 52.8% | 63.9% |
| Effective Date | 26 / 14 | 58.2% | 72.3% |
| Expiration Date | 30 / 10 | 58.6% | 68.6% |
| Renewal Term | 11 / 29 | 100.0% | 100.0% |
| Governing Law | 30 / 10 | 96.7% | 96.7% |
| Termination For Convenience | 10 / 30 | 86.4% | 86.4% |
| Anti-Assignment | 27 / 13 | 94.4% | 94.4% |
| Exclusivity | 13 / 27 | 55.7% | 68.7% |
| Revenue/Profit Sharing | 15 / 25 | 69.4% | 81.5% |
| Audit Rights | 17 / 23 | 93.0% | 95.1% |

The longest evaluation band contains ten contracts and 73 positive tasks.
Top 5 recall is **65.0%**, and all evidence is covered for only **49.3%** of its
positive tasks. In contrast, the second length band reaches 96.2% recall. This is
a useful failure signal, not a causal estimate of document length: category mix,
number of evidence spans and contract structure also differ between bands.

These are descriptive results on 40 evaluation documents, not 400 independent
contracts. No statistical-significance or population-generalization claim is made.
If intervals are added, resample paired documents, not individual questions.
Eleven successful renewal examples do not establish general renewal reliability.

## Execution evidence

- Run: `42d7020713f9453c9592e65a4dcd4a3d`.
- Bundle: `38bd0ec1faa3ccdee2a16af0ecee03c8c670059eeb1423c0f729f22e9cd39d78`.
- Protocol digest: `e338a634d12367ed1f393f9d63d38b09825df5403703eb7748a20022b3d0b209`.
- 1,000 actual local query attempts: 500 tasks x 2 settings; zero query failures.
- 50 document indexes built once and shared across settings; total indexing
  time 312.4 ms. Per-query times exclude indexing and are not provider latency.
  The repeated index duration in query logs is document-level metadata; do not sum it per query.
- Python 3.12.2, macOS arm64, NumPy 2.5.2, rank-bm25 0.2.2; code hashes recorded.
- No inference-provider calls, no API spend, and no model token measurements.
  Per-query token/cost fields are null; total API spend is zero because no API ran.

Execution opens only inputs first, writes predictions and per-query JSONL events,
then opens the separate reference file for scoring. Errors retain task denominators
and log exception types without message bodies. Content hashes detect changed
inputs/references; private files use owner-only file permissions and reject symlinks.
The bundle is reproducible; reruns get distinct IDs and do not replace earlier logs.
A verification rerun, `7ecef4cede234b5f9a5f9e342a4e58ca`, produced identical ranked
offsets, BM25 scores and quality metrics on another 1,000 queries. Timing naturally
varied. The public artifact retains the first run, not the fastest of repeated runs.

The official test labels have **now been used for evaluation**. Do not tune queries,
chunk settings or prompts on these results and then present the same sample as a
fresh untouched final test. Develop changes on the ten development contracts.
Reserve a separately frozen, previously unused subset for a future final comparison.
Public benchmark membership is also no guarantee of absence from an LLM's training.

## Reproduce locally

Run from the repository root on macOS; Windows and Power BI are not required.

```bash
.venv/bin/python -m pip install -r requirements-cuad.txt
.venv/bin/python src/cuad_intake.py --accept-local-review
.venv/bin/python src/cuad_experiment.py prepare
.venv/bin/python src/cuad_experiment.py run --bundle private/cuad_experiment/bundles/38bd0ec1faa3ccdee2a16af0ecee03c8c670059eeb1423c0f729f22e9cd39d78
env PYTHONPATH=tests .venv/bin/python -m unittest test_cuad_experiment test_evidence_eval test_cuad_intake test_cuad_prepare test_observability
```

The acquisition command reuses the pinned archive when already present. Read the
[data-use plan](DATA_GOVERNANCE.md) before first acquisition. Preparation reports
any offset mismatch and blocks the run instead of repairing or dropping a task.
For this version, 31 new fixture tests and 82 existing offline tests pass. These
software tests are separate from the real-data results above. SQL integration was
not rerun for this extension; no database or PBIX was changed by this experiment.

## Next experiment

1. Review a documented mix of development-set misses, multi-span tasks and negative
   cases. Keep corrections separate from the original annotations. No formal
   human adjudication has been completed in this run.
2. Use development data to design two evidence-grounded answer/abstention prompts.
   Keep the retriever fixed initially so retrieval failure can be separated from
   answer-generation failure; do not show gold answers to the model.
3. Before any external call, confirm provider, model, budget and service data terms,
   then review/minimize the exact outgoing contract excerpts. This 50-document
   local experiment is not automatic permission to send all texts to a provider.
4. Record actual usage, latency, errors, multi-span evidence and abstention outcomes.
   Add a separate real-evaluation SQL layer/Power BI page only with correct provenance;
   do not insert these rows into the synthetic-only telemetry validator or business facts.

See [third-party attribution and boundaries](THIRD_PARTY_DATA.md). These results
support a claim of a real-data **retrieval evaluation pipeline**, not deployed legal
AI, measured lawyer adoption, improved model answers or real revenue impact.
