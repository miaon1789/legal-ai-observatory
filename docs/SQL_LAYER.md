# SQL Server layer

Business logic lives in views; Power BI presents. `./sql/run_all.sh` rebuilds
everything from the CSV extracts in about eight seconds.

```
sql/01_schema/   tables, keys, CHECK constraints, indexes
sql/02_load/     BULK INSERT from /data/raw (mounted into the container)
sql/03_views/    the seven business views, plus the policy rule
sql/04_procs/    usp_refresh_adoption_snapshot
```

15 tables · 11 views · 1 stored procedure · 465,795 rows.

## Views

| View | Serves | Note |
|---|---|---|
| `vw_effective_review_policy` | all | The rule: task type, escalated by confidentiality tier and client-facing status. Returns the driver as well as the verdict, so a reader can see *why* an output needed review. |
| `vw_ai_output_enriched` | all | What Power BI imports in place of `fact_ai_output`, so no measure can reach acceptance and review while bypassing the rule. |
| `vw_retention_cohort` | Page 2 | Week-4 retention by cohort week x level. Cohorts whose W+4 week falls past the end of the extract are returned as `is_complete = 0` with a NULL numerator, so an incomplete cohort cannot be read as a failed one. |
| `vw_adoption_by_group` | Page 2 | Denominator built from `dim_lawyer` crossed with the calendar, never from the session table — a group with no users must report zero, not disappear. |
| `vw_billable_impact` | Page 3 | Applies the fitted effect to post-adoption hours at lawyer list rates; separates all-hours capacity from billable-only Hourly revenue exposure. |
| `vw_quality_observability` | Page 4 | p95 latency, error rate, median edit distance, review coverage by review type, cost per **accepted** output. |
| `vw_governance_exposure` | Page 5 | Tier exposure and coverage, with denominators attached. |
| `vw_governance_screen` | Page 5 | One row per lawyer per financial quarter — the review list. |
| `vw_pipeline_health` | Page 1 | Load completeness, staleness, referential failure trend. |
| `vw_cost_efficiency` | Pages 1 and 4 | Licence and token spend, idle seats, cost per accepted output. |
| `vw_alert_status` | Page 1 | All 16 rules evaluated against current data. |

The monthly cost rule compares **licence plus token** spend in the latest
complete calendar month with the preceding three consecutive calendar months.
Completeness uses the latest pipeline date, including non-working days. Missing
months, a missing watermark or a zero baseline produce `NULL` (unevaluated).
At 2026-08-31 this is approximately +0.04%, not breached; 8 of 16 rules breach.

`vw_billable_impact.hourly_revenue_exposure_aud` uses billable post-adoption
hours only and is `NULL` for non-Hourly matters. It is a scenario at list rates,
not realised revenue loss. `notional_capacity_released_aud` still includes all
post-adoption hours, so its Hourly total intentionally differs from revenue
exposure: about AUD 12.33m versus AUD 10.14m.

Regression tests execute the real view definitions in an automatically created
and removed test database, without rebuilding the project database:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

`sql/05_validation/02_reporting_fixes.sql` provides focused, read-only checks
for the current cost alert, breach count and billable revenue exposure, with
bounded query memory for a laptop running Docker and a Windows VM together.

## Constraints are part of the metric definitions

The CHECK constraints are not decoration. `review_policy_base` is restricted to
its three values because a load that lower-cased `'Mandatory'` would break every
governance measure silently. `fact_ai_session` requires that a `Success` row has
no `error_type` and a failed row has one; `fact_incident` requires that a
resolved incident has a resolution time and an unresolved one does not. Those
two constraints also verify the load itself: they are the reason the CSV import
is known to have preserved NULLs rather than turning them into empty strings.

`dim_matter` requires `barrier_group_id` exactly when the tier is `Barrier`.

`dim_lawyer` has no `policy_cohort` column, deliberately — see
`DATA_MODEL.md` §5.

## Thresholds are data, not code

`vw_alert_status` computes a current value for each rule and then applies the
rule's own `threshold_value` and `comparison` from `dim_alert_rule`. Changing a
threshold is a row update; nobody has to find the SQL that hard-coded it. The
`rationale` travels with the alert, so the reason a governance threshold is
tighter than an operational one arrives with the breach rather than living in
someone's head.

The branches are keyed on `rule_name`, not `rule_id`, and `rule_name` carries a
UNIQUE constraint. This is not fastidiousness: inserting a rule mid-catalogue
renumbers every rule after it, and a view keyed on position then evaluates each
one against the *next* rule's threshold — silently, with every number still
looking plausible. It happened here, when a cost rule was added in the middle.
The join is also a LEFT JOIN, so a rule nobody has implemented appears with a
NULL current value rather than quietly dropping off the list.

## Two things the SQL layer found

**A zero-row load is not a failure signal.** Every feed loads nothing at
weekends, and the Harvey feed loads nothing before it was deployed. Written
naively, "rows_loaded = 0" fires on **586 days** in this window — a rule that
would be switched off within a week. Scoped to days a source was expected to be
carrying traffic, it fires on **3**: exactly the planted outage. The view exposes
`is_unexpected_zero_load` alongside the raw flag so the distinction is visible
rather than buried in a WHERE clause.

**A rule that its own evaluation had already disproved.** The first draft of the
catalogue screened lawyers by restricted-tier session volume. Evaluated against
held-out ground truth that screen runs at 10% precision, and in SQL it returned
67 names — not a short list. It has been replaced as the governance screen by
per-lawyer mandatory review completion (65% precision, 81% recall) and demoted
to a low-severity context rule, with its measured weakness written into its
`rationale` so nobody rebuilds it as a screen. See
`results/screening_comparison.csv`.

**Lawyers using a tool they had no licence for.** An earlier version generated
sessions first and allocated seats afterwards, which produced 231 Harvey seats
and 257 lawyers who had used it. Seat allocation now happens before any usage
exists — the order it happens in at a firm — and a lawyer without a seat cannot
generate a session for that tool. The same fix removed departed lawyers from the
session table, which had also been inflating the adoption numerator against a
denominator that already excluded them.

## Cross-validation

The views and `src/consistency_checks.py` are independent implementations of the
same definitions, and `sql/05_validation/01_dax_expected_values.sql` publishes
the value every DAX measure has to reproduce. They agree: review coverage by
tier 0.828 / 0.863 / 0.879, median edit distance by practice area 33.0 / 25.3 /
15.1 / 13.25 / 11.1, adoption by level 0.741 / 0.637 / 0.522 / 0.201. Where a
metric is worth trusting, it has been computed twice.
