# Third-Party Data Notice

Checked: 9 September 2026.

## CUAD

Contract Understanding Atticus Dataset (CUAD) is curated and maintained by
**The Atticus Project, Inc.**, with the contributors credited on its
[dataset page](https://www.atticusprojectai.org/cuad/).
The associated research is [CUAD: An Expert-Annotated NLP Dataset for Legal Contract
Review](https://arxiv.org/abs/2103.06268).

- Source: [official CUAD repository, pinned revision](https://github.com/The-Atticus-Project/cuad/tree/67faa0e6023b04fcaae6cc09497ab00e5d63a2a2).
- Publisher's declaration: [CC BY 4.0](https://www.atticusprojectai.org/legal/).
- License terms: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/legalcode.en).
- Warranty notice: [publisher disclaimer](https://www.atticusprojectai.org/disclaimer/).
- Source/rights caveat: [official dataset card](https://huggingface.co/datasets/theatticusproject/cuad-qa/blob/main/README.md)
  identifies SEC EDGAR as the contract source and declines to warrant underlying
  contract license status. The dataset license is not a blanket rights clearance.

Version 1 selects 50 documents and ten categories. Version 2 reuses the ten
development contracts and adds twenty evaluation contracts, for 70 distinct
contracts across the two experiments. Later ablations reuse development data.
The project preserves canonical texts and gold spans privately, constructs
retrieval chunks and category queries, and computes its own character-overlap
metrics. Text and annotation values are not corrected or masked in these local
experiments. The earlier masked preparation exercise remains separate.

This repository's public artifacts contain protocol settings, code, hashes and
aggregate results, not redistributed contract bodies, gold quotations, signatory
details or individual retrieval predictions. The reviewed
[PBIX](../powerbi/legal-ai-observatory.pbix) additionally embeds 1,700 numerical
per-task evaluation rows. These include document/task IDs, category, answerability
labels, coverage metrics, context lengths and timing. They do not include source
titles, source text, annotated answer text/spans or retrieved passages. IDs should
not be treated as a guarantee of anonymisation.

This is a project publication choice, not a claim that CC BY categorically forbids
redistribution. Downloading the PBIX gives access to its imported data, not only
its charts. Any added excerpts, columns or other data need a new review of notices,
attribution and rights. The dataset and source archive remain private.

No endorsement by The Atticus Project, its contributors, contract parties or
government agencies is claimed. Project-specific retrieval results are not the
official CUAD benchmark score and are not legal advice or a compliance guarantee.
These data conditions do not determine the license of this project's original code.

## Retrieval dependency

[rank-bm25 0.2.2](https://pypi.org/project/rank-bm25/0.2.2/) by D. Brown is an
installed dependency, not vendored source. Its [Apache 2.0 license](https://github.com/dorianbrown/rank_bm25/blob/master/LICENSE)
is separate from CUAD's data license. Runtime versions are recorded with the results.
