# Legal AI Operations Observatory: Case Study

Portfolio snapshot updated on 9 September 2026. The saved report now has six
pages. See the [release checklist](RELEASE_CHECKLIST.md) for checks and scope.

## Problem

An organisation adopting AI needs more than a count of active users. It needs
to understand who uses the tools, what they cost, where outputs need review,
and whether missing data is distorting the report.

This personal project explores those questions in a simulated law firm. It
combines a Power BI report with a SQL warehouse and Python analysis. A separate
CUAD evaluation tests evidence retrieval on public contracts.

It is not a project delivered for a law firm. The report does not contain
live Harvey or Copilot usage data, and the retrieval tests do not generate answers.

## Deliverables

| Workstream | Completed work | Boundary |
|---|---|---|
| Data preparation | Scripted CSV generation, analysis and SQL loading | Local batch workflow, not a scheduled production integration |
| Operations reporting | Five-page Power BI report, dimensional model and DAX measures | Synthetic operations data |
| Analysis | Adoption, licence utilisation, review coverage and billable-impact scenarios | Findings describe the generated scenario |
| Retrieval evaluation | Local BM25 comparisons, exact evidence scoring and separate development/evaluation samples | No model answers or legal-accuracy assessment |
| Evaluation reporting | Separate SQL schema, replay checks, 1,700 reconciled rows and a sixth Power BI page | Numerical benchmark results, not contract text or model answers |
| Observability | Request/attempt records and scripted fault/recovery drills | Fixture execution, not live provider monitoring |

## Three Decisions Worth Explaining

### Count the Eligible Population

An adoption rate needs to include people who never used the tool. A denominator
derived only from session records can exclude them and overstate adoption.

The report uses the lawyer dimension for its population and retains the relevant
group filters. It presents adoption by seniority and practice group. This lets a
reader distinguish low participation from low activity among existing users.

The [DAX definitions](../powerbi/measures.dax) and
[SQL checks](../sql/05_validation/01_dax_expected_values.sql) provide the
implementation and control calculations. A high early retention measure alone
does not prove that later churn is absent. Causes such as training or incentives
would need user research on real data.

### Separate Capacity from Revenue

Saved time has different implications under hourly, fixed-fee and capped
arrangements. The project therefore separates all-hours capacity estimates from
hourly revenue exposure based on billable hours.

The [Python estimator](../src/billable_impact.py) fits a fixed-effects model and
reports lawyer-clustered uncertainty. Its comparison group includes lawyers
with fewer than 20 sessions over the full window, not exclusively zero-use
lawyers. The fitted analysis contains 170,589 timesheet entries.

The corrected hourly scenario is approximately AUD 10.14 million at list rates.
This is estimated exposure within synthetic data, not money saved or lost by
a client. It should not appear on a resume as a realised financial impact.
See the [reporting checks](../sql/05_validation/02_reporting_fixes.sql) and
[stored estimates](../results/billable_impact_estimate.csv).

### Hold the Retrieval Budget Constant

The first retrieval experiment compared Top 3 with Top 5. Top 5 found more
evidence but also returned more text, so it did not isolate an improvement at
the same context length.

Version 2 used an equal per-task budget. It selected a candidate on ten
development contracts before evaluating it on twenty additional contracts.
The candidate used smaller windows and reserved part of the same budget for
the preamble on two date-related question categories.

On 113 answerable tasks in the new sample, mean gold-character recall increased
from 85.13% to 91.86%. Complete evidence coverage increased from 90 to 93 tasks.
There were also nine tasks with lower recall. The result supports a descriptive
comparison on this sample, not a claim of universal improvement.

Later sentence-boundary and stemming experiments reused development data.
Neither passed the predefined acceptance criteria. Keeping their negative
results explains why a more complex approach did not replace the current one.

Evidence: [v2 protocol and results](CUAD_BUDGET_V2.md),
[boundary experiment](CUAD_BOUNDARY_ABLATION.md),
[stemming experiment](CUAD_STEMMING_ABLATION.md).

## Findings and Proposed Actions

These are analytical recommendations, not actions implemented by a client.

| Finding | Data source | Proposed action | Important limit |
|---|---|---|---|
| Unused seat-months account for about 51% of licence spend | Synthetic seat and usage records | Review licence allocation before renewal | No actual licence savings were achieved |
| A three-day ingestion outage removes sessions from the downstream table | Planted synthetic failure | Check data completeness before explaining a usage decline | The outage is deliberately generated |
| The review-priority Top 20 contains 13 members of the generated risk cohort | Synthetic ground truth withheld from the report | Use the list for follow-up, not an automatic misconduct decision | Rules were compared on the same scenario, not an independent test sample |
| Mean evidence recall improves at equal context length, but some cases regress | Twenty additional CUAD contracts | Retain per-case comparisons and regression checks | Retrieval coverage is not answer accuracy |

The governance screen has a minimum of 25 mandatory outputs. Low-volume users
are outside that rule's scope. Its reported 65% precision does not automatically
apply after changing filters, thresholds or the underlying population.

## Evidence Register

| Claim | Evidence | Interpretation |
|---|---|---|
| More than 460,000 synthetic records | [Generator](../src/generate.py), [configuration](../src/config.py) | Current 13 dimension/fact CSV extracts contain 464,379 data rows, including three fitted result rows. Operational logs and snapshots are not added to this count |
| 400 lawyers over 18 months | [Configuration](../src/config.py), [data model](DATA_MODEL.md) | Scenario size, not actual users served |
| Six report pages | [PBIX](../powerbi/legal-ai-observatory.pbix), [CUAD page](../powerbi/screenshots/08-real-contract-evaluation.png) | Five synthetic operations pages plus one real-contract evaluation page |
| 16 configurable alert rules | [Catalogue](ALERT_RULES.md), [SQL view](../sql/03_views/04_vw_alert_status.sql) | Includes operational and governance rules with documented windows |
| 1,700 evaluation rows reconciled | [SQL handoff record](CUAD_BUDGET_V2.md), [warehouse code](../src/cuad_warehouse.py) | Configuration-level result rows across three runs, not 1,700 unique questions |
| 85.13% to 91.86% recall | [Public aggregate JSON](../results/cuad_budget_v2.json) | Mean gold-character recall on 113 answerable tasks from 20 new contracts |
| Exact context matching | [Budget experiment](../src/cuad_budget_experiment.py), [protocol](../benchmarks/cuad/protocol-budget-v2.json) | Each task returns min(6,000, source length) unique characters. This is not a token or API-cost comparison |
| 169 passing offline tests | [Test suites](../tests/), command in [README](../README.md#offline-tests) | Rerun on 9 September. It excludes SQL and Windows tests |
| Successful fault/recovery exercise | [Validation record](OBSERVABILITY_VALIDATION.md) | Scripted local fixtures, with no live-provider or genuine human-review claim |

Across v1 and v2 there are 70 distinct contracts and 700 unique questions.
The ten development contracts are reused. Repeated configurations and later
development experiments do not increase the unique sample size.

## Status and Limitations

The current presentation separates three kinds of evidence:

1. Synthetic operations data demonstrates modelling, metric design and reporting.
2. Public contracts provide real source documents for a local retrieval experiment.
3. Scripted fixtures test software behaviour and observability plumbing.

SQL verification and original report checks were recorded on 7 September 2026.
The 9 September review checked six saved page definitions, the 14 evaluation
measures, model isolation and all 1,700 cached evaluation rows. Thirty aggregate
groups match the public experiment results. The offline suite passed 169 tests.
This review did not rerun SQL integration tests or execute a Windows DAX engine.

The prototype still has engineering work to do. The original bulk-load script
does not explicitly request constraint checking. Historical pipeline freshness
and some cross-layer metric definitions need further review. The local SQL
bridge uses administrator access. These are reasons to avoid production-readiness
claims, not reasons to hide the implemented analysis.

The retrieval experiment is small. Human adjudication, broader generalisation
and live-model answer evaluation remain open. The evaluation page is built and
its saved definitions and cached data have been checked. Power BI Service
deployment and scheduled refresh remain outside the validated scope.

## Data Boundaries

CUAD source text, annotations and individual logs stay under ignored `private/`.
Public artifacts contain implementation details, aggregate metrics and numerical
per-task results embedded in the PBIX. Document/task IDs and answerability labels
are included, but source text, answer spans and query logs are excluded. No
contract text is sent to an external model provider in these experiments.

The [data-use record](THIRD_PARTY_DATA.md) documents attribution and limits.
It is not a guarantee of legal clearance for every contract or every future use.
There has been no client engagement or independent legal review of this prototype.

## Suggested Walkthrough

1. Start with the [executive screenshot](../powerbi/screenshots/01-executive-overview.png) and explain the operational questions.
2. Open the [operations model view](../powerbi/screenshots/07-model-view.png) and trace one KPI from source data to DAX.
3. Explain one metric decision, such as the adoption denominator or billable-only exposure.
4. Show the [sixth report page](../powerbi/screenshots/08-real-contract-evaluation.png) and explain the budget, denominator and regressions using the [experiment record](CUAD_BUDGET_V2.md).
5. Close with the validation scope and the next deployment steps.

For a BI discussion, focus on modelling, DAX and reconciliation. For an AI
operations discussion, give more time to evaluation, logs and failure handling.
Both are views of the same personal project, not separate employment experience.
