# Data Use and Hybrid Evaluation Plan

Review date: 7 September 2026 (Australia/Sydney).

Status: public licensing information reviewed; a pinned CUAD archive acquired and
eight training documents triaged locally. The owner subsequently authorized a
larger local experiment: [50 real contracts and 500 tasks](CUAD_REAL_DATA_RESULTS.md)
have now been processed by a local BM25 retriever. Individual sample clearance,
external model processing and source-text publication remain pending. See the
[completed intake record](CUAD_INTAKE_REVIEW.md) for the earlier, separate review.
Two candidates also received [complete-text assistant review](CUAD_PREPARATION.md):
one has a masked, private preparation bundle and one is held. This is distinct
from full-text human review or evaluation authorization.
This is a project risk-control plan, not a legal opinion, rights clearance or
guarantee of compliance in every jurisdiction. The checks below are manual
project gates, not a newly implemented authorization service.

## Current data register

| Material | Current use | Source/rights status | Next decision |
|---|---|---|---|
| Built-in `benchmarks/contract_qa/v1.json` | Six fictional documents, 36 test questions; offline regression fixtures | Created within this project with AI assistance, not supplied by a client. `self_authored_synthetic` is a provenance label, not a claim of exclusively human authorship or a rights warranty. | Review any new text and expected answers before use. |
| `benchmarks/evidence/v1.json` | Eight fictional documents, 12 evidence tasks and seven scripted scorer checks | Project-created with AI assistance; no external contract text or CUAD annotations copied into these fixtures. | Use for offline metric validation, not model-performance claims or legal advice. |
| CUAD v1 | Earlier eight-document intake and one masked preparation remain separate. A further 50 documents / 500 tasks have completed local lexical retrieval under a fixed protocol. | Publisher declares CC BY 4.0 but does not warrant underlying contract license status. New documents receive automated triage only, not individual source verification or clearance. | Review exact outgoing material and provider terms before any external model call; source publication remains unapproved. |
| Client, employer or restricted-access contracts | Excluded | No authorization established | Do not import, paste into prompts or use to generate fictional substitutes. |

The original synthetic `dbo` warehouse is unchanged. No CUAD data or CUAD model
results are currently part of the five-page report or the telemetry pilot.

## Verified licensing information

The publisher's [legal page](https://www.atticusprojectai.org/legal/) identifies
CUAD as CC BY 4.0. The [official dataset card](https://huggingface.co/datasets/theatticusproject/cuad-qa/blob/main/README.md)
identifies SEC EDGAR filings as the contract source and specifically declines to
warrant the underlying contracts' license status. Public availability is not
itself a public-domain declaration.

Under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode.en), licensed
material may be shared and adapted, including commercially. When sharing, retain
supplied creator/attribution information, copyright, license and warranty notices,
link to the material and license, and identify modifications. Do not imply
endorsement or impose restrictions inconsistent with the license on that material.
The grant covers only rights the licensor can license; privacy, personality,
trademark and other third-party rights may remain relevant. The license provides
no non-infringement warranty. A mixed synthetic/real dataset does not waive these
conditions. Renaming parties alone is not a rights-clearance method.

The publisher's [disclaimer](https://www.atticusprojectai.org/disclaimer/) also
disclaims warranties and says its materials are not legal advice. These findings
support considering CUAD for a portfolio, not asserting every underlying document
is cleared for every downstream use. Dataset licensing does not automatically
license a separate code repository, model, dependency or supplemental corpus.

## Three decisions, not one blanket approval

### A. Acquire a bounded sample for local inspection

- Identify the exact official distribution, version/revision and download URL.
  Inspect available terms before acquisition; inspect bundled notices afterwards.
  Stop if notices conflict with the permission relied upon.
- Record the purpose as independent portfolio research, the reviewer, review date,
  intended operations and unresolved questions. Keep a link or permitted record
  of the applicable terms, plus the downloaded artifact's SHA-256 hash.
- Keep the archive and extracted text under `private/datasets/cuad/`, already
  excluded by `.gitignore`. Do not add them to Git history or the tracked PBIX.
- Start with 5-10 candidate contracts for inspection, not an entire-dataset run.
  Check source identifiers, included notices, redactions, apparent personal data
  and any indications that a sample should be excluded or clarified.
- Log accepted/excluded IDs and reasons without copying unnecessary personal
  information into the decision log. An unresolved sample is excluded or held
  pending publisher clarification or qualified legal advice where appropriate.

Acquisition and automated triage of eight training documents are complete; selected
contexts were inspected by the coding assistant and two SEC source identities
corroborated. Full-text human review and individual sample clearance remain pending.
Two candidate texts subsequently received full assistant inspection. One was
prepared locally with reviewed name masking; the other is held pending its exact
source exhibit and task-scope review. Neither is cleared for external evaluation.

### A2. Owner-authorized local retrieval experiment

After the initial intake, the owner requested a meaningful real-data expansion
and agreed to 50 contracts with development/evaluation separation. This authorizes
local experimental computation, not external transmission or legal clearance.
The [fixed protocol](../benchmarks/cuad/protocol-v1.json) excludes all eight earlier
pilot documents, including held samples, and all exact normalized duplicate groups.
The new 50 documents are unmodified and processed locally with automated notice/
contact triage. They have not all received full-text human or individual source review.

Inputs, references, manifests, predictions and query logs stay under ignored
`private/cuad_experiment/`. Code and reviewed aggregate results contain no source
quotations. File hashes, dependencies, actual query timing and experiment identity
are recorded. No model API or external storage service is used by this runner.
Retention of local source artifacts remains an owner decision; keep only what is
needed for reproducibility and revisit the use if new rights concerns emerge.

### B. Use selected material in an external model

- Complete A and choose only accepted samples; local inspection does not itself
  authorize external transmission.
- Review the selected provider's input-data terms, retention, training use,
  processing location and available controls for the intended account/service.
  Record the decision rather than assuming all API providers behave alike.
- Minimize unnecessary identifying text. If text is transformed, preserve a
  private mapping, revalidate evidence offsets and record the transformation.
  Exclude samples where necessary changes undermine the evaluation.
- Confirm provider, model, request limits and budget separately. No credentials
  in prompts, Git, screenshots or decision documents.
- Use the synthetic set first for a connectivity check; then run a small accepted
  real-data sample. Keep request provenance and content private by default.

No additional evaluation-provider call or paid evaluation run was authorized or
performed. Selected excerpts were inspected in the current coding-assistant
session, so this is not a claim of wholly offline human processing. A proposed
evaluation provider's terms still need to be checked when selected.

### C. Publish portfolio artifacts

- Prefer code, source/download instructions, evaluation definitions, aggregate
  metrics and reviewed screenshots. This is a conservative project choice, not
  a statement that CC BY prohibits redistribution.
- Review every published excerpt, model quotation and embedded PBIX dataset.
  Exclude signatures, unnecessary contact details and unreviewed source text.
  A model repeating the input does not automatically create independent material.
- Add source attribution, the exact version and modifications to the README and
  a third-party data notice. Preserve supplied notices when distributing material.
- Clearly separate original code licensing from third-party data conditions.
  Do not assert an affiliation with the publisher or a professional legal opinion.
- Check the staged files and any existing Git history for accidental inclusion;
  `.gitignore` does not remove tracked content or cleanse an embedded report.
- Record reviewer, date, approved artifact scope and exclusions. New files or
  a changed use require a new check. Local-only files should have an owner-chosen
  retention/review date and a documented response if rights concerns arise.

Publication of external text or a real-data PBIX has not been approved here.

## Hybrid evaluation design

Use two complementary sets, not a blended headline score:

| Set | Purpose | Claims it can support |
|---|---|---|
| Project-created fictional clauses | Controlled omissions, exceptions, conflicting terms, misleading instructions, parser checks and repeatable fault drills | Behavior on designed cases and pipeline regression checks. Oracle replay is not model evaluation. |
| Accepted public real-contract sample | Long text, repeated provisions, multiple evidence spans and natural drafting variation | Performance on the specifically documented benchmark sample, not a firm's operations or general legal competence. |

Keep the existing synthetic regression sets. The first substantive local real-data
benchmark now contains 50 contracts and 500 tasks, including unanswerable labels.
Ten development contracts come from official train and forty evaluation contracts
from official test. This size is not a statistical adequacy claim or permission to
start calls. Any external-model stage is still subject to B.

Use original licensed annotations to define real-data tasks where suitable;
log corrections separately instead of silently rewriting the answer key. Newly
constructed fictional documents should not copy client text or unreviewed paid
templates. If a scenario is adapted from a real source, record that derivation
and its rights conditions rather than treating it as independently authored.

Respect official data splits. Separate prompt-development and final-test
contracts, keep duplicates and related derived versions in the same partition,
and never use gold evidence locations to select input context while claiming
to evaluate retrieval. Report extraction, evidence, abstention, cost, latency and
review coverage separately by set, task, configuration and experiment. Do not
mix oracle output with actual model calls or assert that a public benchmark was
necessarily unseen during model training. Keep fault drills out of quality scores.

The current telemetry fields already distinguish two independent concepts:

- `case_origin` describes task material; currently only `self_authored_synthetic`
  is implemented. External dataset identity, split, source/document IDs, version,
  transformations and review-decision reference must be added before ingestion.
- `data_origin` describes execution: `simulated` for replay, `observed` for actual
  API attempts. A real API call on fictional text is still an observed run on
  synthetic material. Public real text processed by a fixture is not live evidence.

The new, separate retrieval format records `case_origin=public_real_contract` and
`execution_origin=local_algorithm`. It does not change the existing telemetry
schema or its synthetic-only validation. External-model ingestion remains future
work; do not relabel CUAD as synthetic just to bypass the current validator.

## Decision record to complete before integration

| Field | Required record |
|---|---|
| Source | Dataset name, version/revision, official URL, archive hash |
| Terms | License and terms URLs, checked date, bundled notices, attribution parties |
| Scope | Local inspection, external processing, public artifacts: separate decisions |
| Samples | Candidate/accepted/excluded document IDs and non-sensitive reasons |
| Transformations | Parent source, method, new version/hash, evidence revalidation |
| Processing | Provider/service, privacy settings, intended payload, model/budget decision |
| Evaluation | Task/rubric version, development/test split, expected limitations |
| Accountability | Reviewer, date, unresolved questions, retention/review date |

Keep detailed sample decisions under `private/`. Publish a short factual
provenance summary only after the checks have actually been completed.
