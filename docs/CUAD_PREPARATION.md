# CUAD Candidate Review and Local Preparation

Review date: 7 September 2026. This follows the [eight-document intake](CUAD_INTAKE_REVIEW.md).

**Current outcome:** the coding assistant read the complete supplied CUAD text
of two candidates. One produced a private preparation bundle; one remains on
hold. No full-text human review, legal clearance, live model evaluation or
source-text publication approval is claimed. The original synthetic report and
the synthetic-only telemetry runner are unchanged.

## Candidate decisions

| Candidate | Assistant coverage | Source check | Local result |
|---|---|---|---|
| `cuad-0084b1ca188d` | All 8,257 characters, including operative text and signatures | Corresponding original SEC exhibit inspected | Prepare locally with reviewed name masking |
| `cuad-0556ac837501` | All 25,619 characters, including definitions and notice section | Filing cover corroborated; exact original exhibit not retrieved | Hold; no prepared input or reference file |

The [first original exhibit](https://www.sec.gov/Archives/edgar/data/1733186/000173318620000032/exhibit1011amendmentan.htm)
supports the recorded source identity. Formatting differs from CUAD, so this is
not a byte-equivalence claim. The second candidate's
[SEC filing cover](https://www.sec.gov/Archives/edgar/data/1062312/000094935305000294/f8k-071505.htm)
lists the affiliate agreement and software-license agreement separately. It does
not establish equivalence of the complete candidate text. The SEC directory
request returned HTTP 403; no access-control workaround was attempted.

Source provenance does not establish all reuse rights. The
[publisher-declared CC BY 4.0 license](https://www.atticusprojectai.org/legal/)
and the [underlying-contract rights caveat](https://huggingface.co/datasets/theatticusproject/cuad-qa/blob/main/README.md)
remain applicable. See [the data-use gates](DATA_GOVERNANCE.md).

## Preparation results

The separate `src/cuad_prepare.py` command consumes the pinned archive and a
private review record. It does not read a mutable extracted sample as the source
of truth. Dataset revision, canonical text hash and official train membership
must match; changed or held candidates cannot silently enter the prepared inputs.

| Check | Result |
|---|---:|
| Prepared training documents | 1 |
| Questions, with original wording retained | 41 |
| Answerable questions | 6 |
| Unanswerable questions | 35 |
| Original evidence spans retained | 16 |
| Reviewed signature-name masking intervals | 12 |
| Evidence mismatches after masking | 0 |

Six signatory names, repeated across 12 intervals, were replaced using reviewed
positions and segment hashes. Each non-whitespace character becomes `X` without
changing the number of Unicode code points. All 16 evidence spans remain exact
matches at their original positions. The prepared text was also checked for the
absence of the original names in those reviewed intervals. Entity names and
other identifying context remain: this is minimization, not anonymization.

The same source and review record produce bundle ID:
`65887ad1efbc03c27d1ea55a46b4abd30606443de1c87e2a0c81458d9f885ed5`.
It is a content-derived identifier, not a signature of legal or human approval.

The output directory under ignored `private/datasets/cuad/67faa0e6023b/prepared/`
contains three files, written with mode `0600`:

- `inputs.json`: masked full text, document-scope note, opaque case IDs and questions.
  No gold labels, expected answer spans or original category-bearing QA IDs.
- `references.json`: original QA IDs, unanswerable labels and unchanged gold spans.
  This is private evaluation material, not model input.
- `receipt.json`: source/review hashes, transformation record, held IDs, counts,
  input/reference file checksums and explicit use limits. Written last.

All files retain private-only status. The preparation code cannot call a provider
or write SQL. Source checks in the record are assistant assertions, not URL checks
performed by this offline tool. A local JSON record is editable and is not a
signed authorization mechanism. Keep the approval workflow separate.

## Evaluation implications

The reviewed material exposed several issues that a single-answer synthetic
grader does not model. Future evaluation must handle conditional dates, overlapping
party-name spans, reference-only agreements and rights limited to one part of an
arrangement. It must also preserve conflicting written/numeric values rather than
silently choosing one. The held CUAD text contains such a conflict, but the original
exhibit has not been checked to determine its origin.

No gold labels were corrected. An absence label means no annotated evidence in
the supplied task/document, not that the underlying transaction has no such term.
Original category definitions also matter; do not reinterpret a category based
only on its short title. Review notes stay separate from official gold labels.

The prepared candidate is particularly unbalanced: always abstaining would get
35/41 answerability decisions right (85.4%) without extracting any evidence. This
is a preparation fixture from official train, not a final test set or a model
success rate. Report answerable extraction, evidence coverage and unanswerable
abstention separately. Do not use gold locations to construct retrieval input.

## Reproduce and verify

The private candidate review record is intentionally not distributed. Do not
invent an approval record just to run the command. After source and text review,
the reviewed local record can be used from the repository root:

```bash
.venv/bin/python src/cuad_prepare.py --review private/datasets/cuad/67faa0e6023b/candidate_review.json
.venv/bin/python -m unittest discover -s tests -p 'test_cuad*.py' -v
```

The 33 CUAD tests (18 intake and 15 preparation) use invented fixtures and passed
on the review date. They cover held documents, changed hashes, train membership,
mask/evidence overlap, Unicode offsets, separate inputs/references, explicit use
limits, private paths, checksums and repeatability. No real contracts are in the
test fixtures. SQL and Windows report validation were not repeated for this stage.
The existing 17 observability unit tests were also rerun successfully: 50 offline
tests passed across the three suites in this stage.

An [offline multi-span evaluator](EVIDENCE_EVALUATION.md) has since been added and
tested using invented fixtures; it does not load this CUAD preparation bundle.
Source/use questions remain separate. Any real model run still
requires its own input-scope, provider-terms and budget decision. These preparation
files must not be passed to the existing synthetic-only runner as synthetic cases.
