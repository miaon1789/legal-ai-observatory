# English Stemming Development Ablation

Completed locally on 7 September 2026. **Not promoted:** both primary quality
metrics decline, and five previously complete tasks lose evidence. The existing
v2 SQL and Power BI reporting layer is unchanged.

## Fixed question and method

The development failure analysis found lexical mismatches, such as an unreturned
annotated span containing an inflected word rather than an exact query term.
This [protocol](../benchmarks/cuad/protocol-stemming-v4.json) tests English stemming
as a separate change to v2, not in combination with the rejected
[sentence-unit policy](CUAD_BOUNDARY_ABLATION.md).

It reuses ten development contracts: 100 tasks, 66 with annotated evidence and
34 without. The policy was fixed before its new scores were computed, but its
design was informed by already accessed development cases. These are not unseen
evaluation or independent validation data.

The normalizer is [Snowball's English/Porter2 algorithm](https://snowballstem.org/algorithms/english/stemmer.html),
using the pinned pure-Python `EnglishStemmer` from
[snowballstemmer 3.1.1](https://pypi.org/project/snowballstemmer/3.1.1/).
Stemming groups word forms for search; it is not semantic understanding or a
guarantee that related meanings share a stem. No language model is downloaded.

Both arms use the same raw text, 120-word windows, 24-word overlap, BM25 settings,
query strings, 800-character date preamble, ranking tie-break, clipping rule and
6,000-character budget. Source text and gold offsets are not rewritten.

The candidate stems both corpus tokens and query tokens after the existing word
tokenization and case folding. Order and multiplicity are preserved. There is
no stopword removal, synonym expansion, deduplication or special per-case rule.
For example, `assignment`, `assign`, and `assigned` all become `assign`, and all
three query occurrences remain. Normalization consequently changes document
frequencies, matching term frequencies and repeated-stem query contributions.
This run does not separately isolate those effects.

Both successful arms return exactly `min(6000, document_length)` unique source
characters per question, so no additional length-matching control is needed.
The original v2 selector is reused without modification. All predictions are
persisted before the new runner reads reference answers.

## Results

| Metric | Original v2 | English stemming |
|---|---:|---:|
| Mean gold-character recall, 66 positives | 89.80% | 86.22% |
| Complete evidence coverage | 78.79% (52/66) | 74.24% (49/66) |
| Any evidence overlap | 98.48% (65/66) | 93.94% (62/66) |
| Mean context precision, positives | 4.55% | 4.74% |
| Mean unique context characters, 100 tasks | 5,753.2 | 5,753.2 |
| Failed / missing queries | 0 / 0 | 0 / 0 |

Five positive tasks improve, seven regress and 54 are unchanged. Two previously
incomplete tasks become complete, but five previously complete tasks lose
coverage. Mean recall falls by **3.58 percentage points**; complete coverage loses
three tasks net. The small precision increase does not override the primary
metric and guardrail failures.

Some category means improve: Audit Rights, Anti-Assignment and Revenue/Profit
Sharing. Effective Date, Expiration Date, Renewal Term and Termination For
Convenience decline. Category sample sizes are small; these observations do not
justify routing particular categories to the stemmer without a new protocol.

Posthoc ranking checks illustrate the tradeoff. For one Audit Rights task, the
first window intersecting a previously missed 110-character gold region moves
from rank 15 to rank 3, and the region is fully retrieved. For another task,
a 251-character Termination For Convenience region moves from a rank-1
intersecting window to rank 12 and is entirely missed. A 29-character Effective
Date region similarly moves from rank 5 to rank 10 and is missed.
These ranks refer to the first overlapping window, not necessarily a window
containing the entire gold region. Exact source references stay private.

The evidence establishes a ranking/context tradeoff, not that duplicate query
stems alone caused every regression. Gold labels remain unchanged, and no
deduplication or parameter adjustment was tried after seeing these scores.

All 34 negative questions were run in both arms, with NULL evidence-quality
metrics. No answers, abstentions, hallucination rates, token usage or API costs
were measured. Timing logs include Python stemming work and are descriptive,
not a production throughput benchmark.

## Decision and next work

The predeclared requirements were: strictly better complete evidence coverage,
nondecreasing mean recall, no loss of a previously complete task, and no failed
or missing queries. Only the execution-completeness requirement passes.
**This candidate does not qualify for a new frozen evaluation.** No SQL import
or Power BI promotion is performed.

Two development ideas have now failed the guardrails: replacing contexts with
whole sentence units and replacing the lexical index with stemmed tokens.
This is evidence against these specific implementations on these ten contracts,
not a general rejection of either technique. Before further optimization,
complete the pending case review. A future bounded experiment could retain an
original-token retrieval channel alongside normalization, with fixed fusion and
budget rules. It must report regressions, not assume fusion is safe or better.
Do not indefinitely tune these ten contracts and call the result generalization.

## Reproducibility

- Primary run: `5d4d862d590d4c56a1b092d3caeab427`, 200 queries.
- Verification run: `f97a64d4e57b4d6e939d42d82a7657e0`, the same data again.
- Parent development run: `5799a89c1d6544b7b90ed1ac88f56245`.
- Protocol SHA-256: `5b1df02cba33276e0368eadac32718cdbb5f16f89dc73a8743d6dd9e6f7ea5ce`.
- Runner SHA-256: `30acdc93eea4e53da8f58566919b4102fbe2953a4e1df236289d8006a6f3969d`.
- All 200 predictions and non-timing score records match across runs. All 100
  baseline contexts reproduce v2, and all 100 pairs have equal unique character
  counts. Date preambles, parent inputs, labels and frozen source hashes match.
- 161 offline tests pass, including 12 new stemming tests. SQL and Windows were
  not modified or retested in this step. Human adjudication remains pending.
- [Public aggregate artifact](../results/cuad_stemming_v4.json). Raw source,
  per-case scores and logs remain under `private/` and are not published.

```bash
.venv/bin/python -m pip install -r requirements-cuad.txt
.venv/bin/python -m pip install --no-deps --require-hashes -r requirements-cuad-stemming.txt
.venv/bin/python src/cuad_stemming_experiment.py
env PYTHONPATH=tests .venv/bin/python -m unittest test_cuad_stemming
```

The runner reuses frozen parent-loading and paired-comparison helpers from the
boundary module, but does not invoke segmentation or require pySBD to run this
experiment. The private parent artifacts are required for local replay; they are
not included in Git. A fresh clone must recreate v2 development and explicitly
version a protocol referencing its new parent run ID. Repeating a run does not
increase the number of independent contracts or questions.

CUAD attribution and data-use boundaries remain in [THIRD_PARTY_DATA.md](THIRD_PARTY_DATA.md).
No external model processing or source publication is authorized by this local
experiment. The 1,700-row reporting layer and Power BI acceptance values stay as-is.
