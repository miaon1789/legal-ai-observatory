# CUAD Local Intake Review

Checked: 7 September 2026 (Australia/Sydney).

**Outcome:** the pinned archive was acquired and eight training documents were
triaged locally. No document has received full-text human review, legal clearance,
external evaluation approval or source-text publication approval. This is an
engineering intake record, not a legal opinion or a model-performance report.

**Follow-up:** two candidates have since received complete-text assistant review;
one private, masked preparation bundle was generated and the other held. See
[the preparation record](CUAD_PREPARATION.md). Human review and rights clearance
remain pending; the eight-document automated results below are unchanged.

## Source and attribution

CUAD (Contract Understanding Atticus Dataset) is curated by The Atticus Project.
The associated paper is *CUAD: An Expert-Annotated NLP Dataset for Legal Contract
Review*, Dan Hendrycks, Collin Burns, Anya Chen and Spencer Ball, 2021. See the
[dataset homepage](https://www.atticusprojectai.org/cuad/) and
[paper](https://arxiv.org/abs/2103.06268).

The publisher declares [CC BY 4.0](https://www.atticusprojectai.org/legal/).
Importantly, its [dataset card](https://huggingface.co/datasets/theatticusproject/cuad-qa/blob/main/README.md)
expressly disclaims a warranty about the underlying contracts' license status.
Public filing status is not a substitute for that missing assurance. Retain the
[license terms](https://creativecommons.org/licenses/by/4.0/legalcode.en),
[publisher disclaimer](https://www.atticusprojectai.org/disclaimer/) and
[privacy-policy reference](https://www.atticusprojectai.org/privacy-policy/)
in any subsequent use review. This independent project is not endorsed by Atticus.

| Item | Recorded value |
|---|---|
| Distribution | Official GitHub `data.zip`, not a third-party mirror |
| Revision | `67faa0e6023b04fcaae6cc09497ab00e5d63a2a2` |
| Archive bytes | 18,309,308 |
| Git blob SHA-1 | `1ae94ff0a9b70b2e3b9b8d215737c8bfae460ddc` |
| Archive SHA-256 | `f8161d18bea4e9c05e78fa6dda61c19c846fb8087ea969c172753bc2f45b999a` |
| Members | `CUADv1.json`, `train_separate_questions.json`, `test.json` |
| Bundled license/notice files | None in this archive; online publisher declaration recorded separately |
| Document partitions | 510 canonical documents; 408 train, 102 test; disjoint with matching union |

[Pinned source archive](https://raw.githubusercontent.com/The-Atticus-Project/cuad/67faa0e6023b04fcaae6cc09497ab00e5d63a2a2/data.zip).
The checksum establishes the identity of the inspected artifact, not its rights.
The project has not copied upstream training/evaluation code or acquired the
supplemental PDF corpus. Dataset licensing is not a code-license determination.

## What was inspected

The script selects the first eight training titles sorted by SHA-256 of the title,
before looking at flags or performance. It takes annotations from canonical
`CUADv1.json`, using the official train file only for partition membership and
text consistency. This is reproducible convenience sampling, not representative
sampling. The loader parses all partition files, but only selected training
documents are emitted for review. No test-set model evaluation was performed.

| Local check | Result |
|---|---:|
| Documents selected | 8 |
| Canonical questions | 328 |
| Annotated answer spans | 270 |
| Questions labelled unanswerable | 226 (68.9%) |
| Exact source-offset mismatches in those 270 spans | 0 |
| Documents with email or phone-like markers | 4 |
| Documents with confidential-treatment language | 2 |
| Documents with signature markers | 8 |
| Document character range | 8,257-209,067 |

These counts are reproducible script outputs, not a semantic validation of every
annotation. Contact and confidentiality markers require contextual interpretation;
zero matches do not establish that identifying information or restrictions are
absent. A confidentiality clause does not itself prove unlawful disclosure.

Assistant inspection of selected openings, endings and flagged contexts found
that blanks sometimes represent unfilled form fields, and a bank-related hit
describes a future payment instruction rather than an exposed account number.
Those findings must not be generalized to all occurrences or all 510 documents.

The archive supplies no per-document filing URL. Separate web inspection
corroborated the identity of two selected documents against original SEC exhibits:
[sample `cuad-0084b1ca188d`](https://www.sec.gov/Archives/edgar/data/1733186/000173318620000032/exhibit1011amendmentan.htm)
and [sample `cuad-03a1e0f461f5`](https://www.sec.gov/Archives/edgar/data/1689657/000119312520181007/d911336dex1012.htm).
Exhibit identity, opening parties and dates were compared; this was not a
byte-for-byte comparison or a rights clearance. Six source chains remain pending.
The generated local report therefore retains `source_urls_verified: 0`: it
describes checks performed by the offline script, not this separate web review.

## Current controls

- The complete source archive and the eight extracted samples stay under ignored
  `private/datasets/cuad/67faa0e6023b/`, with generated files mode `0600`.
  `.gitignore` is an accidental-commit control, not encryption or legal protection.
- The script uses a pinned source and verifies size and Git blob identity. It
  rejects unexpected redirects, unsafe archive members and symlinked output paths.
  It does not execute contract contents or use unrestricted archive extraction.
- Raw sample text is unchanged; triage previews mask some obvious contact patterns.
  This is not complete anonymization. Gold annotations have not been rewritten.
- No SQL import, telemetry provenance change, PBIX edit or additional inference
  API call was made for this intake. Selected excerpts were inspected within the
  current coding-assistant session; this was not a wholly offline human review.
- The generated reports always retain pending review and unapproved external-use
  and publication states. Separate review notes must not be stored in generated
  files, which are replaced on rerun. These records are not an authorization service.
- Source contracts, original annotations and full outputs are not included in the
  public repository changes. This document contains aggregate observations only.

## Reproduce the local checks

Run from the repository root after reading [the data-use plan](DATA_GOVERNANCE.md):

```bash
.venv/bin/python src/cuad_intake.py --accept-local-review
.venv/bin/python src/cuad_intake.py --inspect --sample-count 8
.venv/bin/python -m unittest discover -s tests -p 'test_cuad_intake.py' -v
```

The first command downloads the pinned archive only when absent; it revalidates
an existing cache and preserves its original acquisition record. The second
command performs offline triage of 5-10 training documents. It neither calls a
model nor approves a sample. The 18 tests use invented fixtures and mock network
responses; they do not need the real archive, credentials, a database or Windows.
All 18 passed on the review date.

## Decision before integration

Continue the synthetic regression set as the distributable demonstration.
For real material, first finish source, full-text and intended-use review on a
small local candidate subset. Redacted and appendix-heavy documents are held
out of the proposed initial small pilot for review complexity, not declared
unlawful. Keep exclusions and reasons visible to avoid presenting a curated pilot
as full-CUAD performance. The private review notes identify these candidates.

Before any approved evaluation, implement explicit external provenance and split
fields; the current runner still accepts only `self_authored_synthetic`. Do not
relax that validator or relabel CUAD as fictional. Bound long-document handling
and preserve evidence offsets without consulting gold locations for retrieval.
Report answerable extraction and unanswerable abstention separately: an all-abstain
baseline would already get 226 of these 328 answerability decisions right without
extracting a single clause. That is not a useful model-quality result.

External provider processing/budget and public artifacts remain separate decisions
under gates B and C. Any unresolved rights concern should be clarified with the
publisher or qualified counsel before the affected use proceeds. Adding real
contract text cannot turn synthetic firm-adoption, billing or revenue metrics
into observed business outcomes.
