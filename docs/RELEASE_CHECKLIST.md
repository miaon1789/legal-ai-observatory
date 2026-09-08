# Portfolio Release Checklist

Checked on 9 September 2026. The six-page report is saved locally. Publication
checks cover the current files and locally reachable Git history. Final visual
checks and manual Git publication remain pending.

## Implemented

| Workstream | Status | Evidence |
|---|---|---|
| Synthetic data and SQL warehouse | Implemented locally for over 460,000 records | [Data model](DATA_MODEL.md) |
| Six-page Power BI report | Five operations pages and a separate CUAD evaluation page | [Report](../powerbi/legal-ai-observatory.pbix) |
| Operational and governance metrics | 16 rules, adoption, cost and review-priority analysis | [KPI catalogue](KPI_CATALOGUE.md), [rules](ALERT_RULES.md) |
| Real-contract retrieval evaluation | Local experiments completed with recorded regressions | [CUAD v2](CUAD_BUDGET_V2.md) |
| Evaluation reporting | 1,700 cached result rows and 14 evaluation measures checked | [Evaluation page](../powerbi/screenshots/08-real-contract-evaluation.png) |
| Observability prototype | Local fixture-based logging and failure/recovery checks | [Validation record](OBSERVABILITY_VALIDATION.md) |
| Portfolio presentation | Updated README, source notice and evidence-based case study | [Case study](PORTFOLIO_CASE_STUDY.md) |

Screenshot numbers are not page numbers. Screenshot 06 is a governance list
detail. Screenshot 07 shows the original operations model. Screenshot 08 is
report page 6, Real Contract Evaluation.

## Validation Record

- Recomputed the screening results on 8 September. Top 20 precision is 65% for
  review completion and 10% for exposure rate. The public catalogue and saved
  PBIX now use the current explanations, not the old 55% and 15% text.
- Stated the full-window scope and the minimum of 25 mandatory outputs. These
  results do not evaluate quarterly alerts or arbitrary report filters.
- The combined offline suite passed all 169 tests on 9 September. Synthetic
  consistency checks passed on 8 September.
- The generated [SQL text sync](../sql/05_validation/03_rule_catalogue_sync.sql)
  updated two local rule explanations on 8 September. A repeat run updated zero.
- Decoded the current PBIX's 18 tables and 58 measures, including 14 evaluation
  measures. Its evaluation definitions match the checked-in DAX file.
- Matched all 1,700 cached evaluation rows to the previously validated import
  packets. There are no duplicate run/configuration/task keys. These are repeated
  experiment results, not 1,700 distinct questions.
- Reconciled 30 overall, clause-category and length-group aggregates against the
  public v2 results. The new evaluation sample has 20 contracts and 200 questions,
  including 113 answerable and 87 unanswerable tasks. Recall is 85.13% for the
  baseline and 91.86% for the candidate, a gain of 6.73 percentage points.
- The evaluation table has no relationships to the synthetic operations model.
  Page 6 selects the fresh v2 evaluation sample and has no active configuration
  filter that would exclude one arm of the comparison.

These are saved-data, definition and structural checks. They do not execute the
Power BI DAX engine or certify every Windows interaction. The earlier SQL import
validation is recorded in the experiment documents. SQL integration tests were
not rerun for this publication update.

## Public File Scope

| Include | Exclude |
|---|---|
| Source code, tests, SQL definitions and dependency specifications | Database backups, local database files and connection credentials |
| Experiment protocols and aggregate result files | Downloaded CUAD archive, contract bodies, gold quotations and answer spans |
| English documentation and the third-party data notice | Private working notes, audit dumps and personal application materials |
| Reviewed report screenshots | Screenshots of passwords, account dialogs or private contract text |
| The single reviewed `powerbi/legal-ai-observatory.pbix` | Other PBIX copies, backups and individual request/retrieval logs |
| Placeholder-only `.env.example` | `.env`, environment-specific secrets and private keys |

The public PBIX contains synthetic operations data and 1,700 numerical per-task
evaluation rows. Evaluation fields include document/task IDs, answerability
labels, categories, coverage metrics, lengths and timing. It does not contain
contract text, source titles, annotated answer text/spans or retrieved passages.
Downloading a PBIX exposes imported data, not just visible charts. IDs are not a
guarantee of anonymisation.

The source notice retains the publisher's CUAD license declaration, attribution
and underlying-rights caveat. This review supports this specific publication
scope. It is not a legal clearance for arbitrary contract redistribution.

## Security Check Scope

The check scans candidate public files, locally reachable historical blobs,
PBIX archive members, decoded model metadata and decoded table contents. It
compares known local secret values and common credential patterns. No matches
or excluded private-data paths were found in that scope.

The Power Query definitions retain a local demonstration SQL Server address
and database name. These are visible connection metadata, not authentication
credentials. They do not grant a repository reader access to the local database.
Synthetic lawyer IDs and names match the generated source data.

This is a targeted check, not a guarantee that every possible secret can be
detected. Opaque security-binding bytes are scanned but not semantically decoded.
Windows data-source permission settings require a user-side check. Remote `main`
matched the locally audited commit when checked. Remote-only branches, deleted
GitHub objects, issues and other account content are outside this review.

The ignore rules exclude private working data, secrets, backups and additional
PBIX copies. `.gitignore` does not remove files already tracked or erase history.
Any file changes after this check require renewed review before publication.

## Final Presentation Checks

- Sort page 6's length-quartile chart by `length_quartile` ascending. The current
  saved visual still sorts by baseline recall descending. Its values are correct,
  and an explicit length sort will keep the intended order clear after filtering.
- Replace screenshot 08 after the final save so it matches the current canvas
  and corrected sort. Check that no labels or method notes are clipped.
- Confirm data-source permissions in Windows. Do not add credentials to M code
  or publish a database backup to make refresh work for another person.
- Review the selected Git changes, then commit and push manually. Recheck any
  replacement PBIX or screenshot before adding it to the reviewed snapshot.

## Deferred

- Genuine human case review and independent legal adjudication.
- Live-provider API evaluation or live Harvey/Copilot integrations.
- Dual-channel retrieval, further experiments or a larger evaluation sample.
- Power BI Service deployment, scheduled refresh and production hardening.

The CUAD Power BI page is built and is no longer deferred. The remaining work
above is not required to describe the completed local evaluation as a personal
portfolio project. Earlier dated records keep their original validation scope.
