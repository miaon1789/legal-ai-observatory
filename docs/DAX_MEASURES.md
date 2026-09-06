# DAX measures, with the number each one has to return

Written before the Power BI model exists, so that building it is mechanical:
create the model, paste the measures, check each against the expected value.
Every number below comes from `sql/05_validation/01_dax_expected_values.sql`,
which runs against the same database Power BI imports from. **A measure that
disagrees with its expected value is wrong in the model, not in the database.**

The usual cause of a disagreement is one of two things: a ratio written as a
calculated column, or a denominator the fact table has quietly filtered. Both
have a worked example below.

---

## Model setup

**Import mode**, not DirectQuery. The whole model is about 450,000 rows.

Import these:

| Import | As | Note |
|---|---|---|
| `dim_date` | dimension | **mark as date table**; switch off Auto date/time |
| `dim_lawyer`, `dim_matter`, `dim_tool`, `dim_task_type` | dimensions | |
| `dim_alert_rule` | dimension | drives the Page 1 status strip |
| `fact_ai_session` | fact | |
| **`vw_ai_output_enriched`** | fact, rename to `fact_ai_output` | **not the raw table** — the view carries `effective_review_policy`, so no measure can reach acceptance and review while bypassing the policy rule |
| `fact_time_entry` | fact | carries `is_post_adoption` |
| `fact_incident` | fact | |
| **`vw_retention_cohort`** | disconnected | Page 2 cohort table, 65 rows — do **not** relate it to `dim_date` |
| **`vw_pipeline_health`** | fact, in place of `fact_pipeline_run` | carries `is_unexpected_zero_load`, staleness and the trailing mean — the logic that separates a planted outage from a weekend |
| **`vw_alert_status`** | disconnected, 16 rows | every rule evaluated against current data; drives the Page 1 status strip |
| `fact_seat_month` | fact | one licensed seat × month; carries the licence cost |
| `fact_billable_impact_estimate` | disconnected | 3 rows, no relationship; read with `LOOKUPVALUE` |

**Relationships**: one direction only, dimension → fact. Both `fact_ai_session`
and `fact_ai_output` carry the full key set, so they sit side by side under one
set of dimensions and nothing needs a bidirectional relationship.

| From (dimension) | To (fact) | On |
|---|---|---|
| `dim_date[date_id]` | `fact_ai_session` · `fact_ai_output` · `fact_time_entry` · `fact_incident` · `vw_pipeline_health` · `fact_seat_month` | `date_id` |
| `dim_lawyer[lawyer_id]` | `fact_ai_session` · `fact_ai_output` · `fact_time_entry` · `fact_incident` · `fact_seat_month` | `lawyer_id` |
| `dim_matter[matter_id]` | `fact_ai_session` · `fact_ai_output` · `fact_time_entry` · `fact_incident` | `matter_id` |
| `dim_tool[tool_id]` | `fact_ai_session` · `fact_ai_output` · `fact_seat_month` | `tool_id` |
| `dim_task_type[task_type_id]` | `fact_ai_session` · `fact_ai_output` | `task_type_id` |

`vw_retention_cohort` is imported for Page 2 and stays **disconnected as
well**. Its `week_start` is the week a cohort *began*, not a date activity
happened on; relating it to `dim_date` would let a date filter cut the cohorts
themselves, which is the classic way to break a cohort chart.

`dim_alert_rule` and `fact_billable_impact_estimate` stay disconnected — the
first drives a status list of its own, the second is read with `LOOKUPVALUE`.
`vw_pipeline_health` connects only to `dim_date`; it is about feeds, not lawyers.
`vw_alert_status` connects to nothing — it is already one row per rule.

**A practical trap**: SQL `BIT` columns arrive in Power BI as `True`/`False`,
not 1/0, so `SUM ( fact_ai_output[accepted] )` will not compile. Every measure
below uses `COUNTROWS` with a filter instead.

---

## Base measures

```dax
Active Lawyers = DISTINCTCOUNT ( fact_ai_session[lawyer_id] )

Sessions = COUNTROWS ( fact_ai_session )

Token Cost AUD = SUM ( fact_ai_session[cost_aud] )

Licence Cost AUD = SUM ( fact_seat_month[licence_cost_aud] )

Total AI Cost AUD = [Token Cost AUD] + [Licence Cost AUD]

Accepted Outputs =
CALCULATE ( COUNTROWS ( fact_ai_output ), fact_ai_output[accepted] = TRUE () )
```

| Measure | Scope | Expected |
|---|---|---|
| Sessions | all time | 112,442 |
| Token Cost AUD | all time | 6,670.47 |
| Licence Cost AUD | all time | **787,710.00** |
| Total AI Cost AUD | all time | 794,380.47 |
| Accepted Outputs | all time | 75,049 |
| Active Lawyers | mean of the monthly value, Dec 2025 onward | 208.9 |

Token spend is **0.8%** of what the firm pays. Any cost measure built from the
session table alone is off by two orders of magnitude, and reports a programme
that appears to cost nothing.

```dax
Open Incidents =
CALCULATE ( COUNTROWS ( fact_incident ), fact_incident[resolved] = FALSE () )

Breached Rules =
CALCULATE ( COUNTROWS ( vw_alert_status ), vw_alert_status[is_breached] = TRUE () )

Alert Rules Title =
"Alert rules - " & FORMAT ( [Breached Rules], "0" )
    & " of " & FORMAT ( COUNTROWS ( vw_alert_status ), "0" ) & " breached"
```

| Measure | Scope | Expected |
|---|---|---|
| Open Incidents | as at, all time | **100** of 450 |
| Breached Rules | as at | **8** of 16 |

The monthly cost alert compares licence plus token cost in the latest complete
calendar month with the previous three consecutive months. The extract's latest
pipeline date determines which month is complete. Missing months or a zero
baseline return an unevaluated (`NULL`) status. At 2026-08-31 the change is about
**0.04%**, below 30%, so this rule is not breached.
Use `Alert Rules Title` for the table's expression-based title instead of a
hard-coded breach count.

`Open Incidents` is a **state** measure, not a period measure: it does not
respond to a date range the way a flow measure does, so the visual is labelled
"as at" rather than inviting a month-on-month reading.

---

## ① A denominator the fact table must not filter

This is the measure that separates a working adoption rate from a broken one.

```dax
Lawyer Population =
CALCULATE (
    DISTINCTCOUNT ( dim_lawyer[lawyer_id] ),
    KEEPFILTERS ( dim_lawyer[is_active] = TRUE () )
)

Adoption Rate = DIVIDE ( [Active Lawyers], [Lawyer Population] )
```

The denominator has to be *everyone in the group*, including the lawyers who
have never opened the tool, and it has to keep responding to the slicers.
`KEEPFILTERS` on `is_active` does the second part: it intersects with the
existing filter context instead of replacing it, so filtering the visual to
partners still counts partners.

### ⚠️ What this measure must **not** contain

An earlier version of this measure carried `REMOVEFILTERS ( fact_ai_session )`
as a defensive modifier, on the reasoning that the denominator must never be
narrowed by a filter on the session table. It is the wrong tool, and it broke
the measure in a way that is easy to miss.

In DAX a table's **expanded table** includes the columns of every table reached
through a many-to-one relationship. `fact_ai_session` expanded therefore
contains `dim_lawyer`, `dim_matter`, `dim_date`, `dim_tool` and
`dim_task_type`, so `REMOVEFILTERS ( fact_ai_session )` clears the filters on
**all of them**. Grouped by level, the denominator returned 385 — the whole
active firm — on every row, while the numerator still respected the level. Each
row then read as that level's share of the firm rather than its adoption rate:

| level | shown | correct |
|---|---|---|
| Graduate | 0.16 | 0.78 |
| Associate | 0.22 | 0.62 |
| Senior Associate | 0.12 | 0.52 |
| Partner | 0.04 | 0.22 |

The total row was right (0.55), which is what made it survive a first look.

The defence was unnecessary in any case: every relationship in this model runs
one way, dimension to fact, so a filter on the session table cannot reach
`dim_lawyer`. The protection it was meant to provide costs nothing to lose, and
the version above is what the model uses.

| Scope | Expected |
|---|---|
| Lawyer Population, no slicers | **385** |
| Adoption Rate, firm-wide, mean of monthly values Dec 2025 onward | **0.5426** |
| Adoption Rate, level = Graduate | **0.7407** |
| Adoption Rate, level = Associate | **0.6370** |
| Adoption Rate, level = Senior Associate | **0.5222** |
| Adoption Rate, level = Partner | **0.2011** |

**Read the total row carefully.** Put months on rows and the column averages
0.5426, but the total row shows **0.6675** — distinct lawyers active in *any* of
those months, over the population. Both are right; they answer different
questions. Adoption is not additive over time, and the total row is not a
mistake to be fixed by a `SUMX`. Label the visual so a reader is not invited to
add the column up.

---

## ② Ratios must be measures, never calculated columns

```dax
Cost per Accepted Output = DIVIDE ( [Total AI Cost AUD], [Accepted Outputs] )
```

| Version | Value | |
|---|---|---|
| **Correct**: ratio of sums, evaluated at the grain of the visual | **10.5848** | |
| Wrong: per-tool ratio stored as a column, then summed | 39.0056 | obviously broken |
| Wrong: per-tool ratio stored as a column, then averaged | **13.0019** | **fires an alert that should not fire** |

The summed version is nearly 4× too high and nobody would ship it. The
*averaged* version is the dangerous one: 13.00 against a true 10.58. It is close
enough to pass a glance, it is wrong for a reason nobody will chase — it weights
a tool with 800 accepted outputs the same as one with 45,000 — and it sits on
the **wrong side of the $12 alert threshold**. The broken measure raises a cost
alert; the correct one does not. Per-tool values for reference: Harvey 28.14,
Copilot 8.31, Firm Chat 2.56.

This is the single most common Power BI modelling error, and the reason every
ratio in `docs/KPI_CATALOGUE.md` is defined as a measure.

```dax
Acceptance Rate = DIVIDE ( [Accepted Outputs], COUNTROWS ( fact_ai_output ) )

Review Coverage =
DIVIDE (
    CALCULATE (
        COUNTROWS ( fact_ai_output ),
        KEEPFILTERS ( fact_ai_output[effective_review_policy] = "Mandatory" ),
        KEEPFILTERS ( fact_ai_output[reviewed] = TRUE () )
    ),
    CALCULATE (
        COUNTROWS ( fact_ai_output ),
        KEEPFILTERS ( fact_ai_output[effective_review_policy] = "Mandatory" )
    )
)
```

The denominator is outputs *requiring* review, not all outputs. Diluting it with
work that never needed review makes coverage improve every time the firm does
more low-risk work.

| Scope | Expected |
|---|---|
| Acceptance Rate, all time | 0.7172 |
| Review Coverage, firm-wide | **0.8775** |
| Review Coverage, tier = Standard | 0.8787 |
| Review Coverage, tier = Restricted | 0.8634 |
| Review Coverage, tier = Barrier | **0.8278** |
| Review Coverage, review type = Citation verification | 0.8751 |
| Review Coverage, review type = Sample audit | 0.8789 |
| Review Coverage, review type = Supervisory review | 0.8778 |

The Barrier row rests on about 800 outputs. Show it with its denominator.

---

## ③ Time intelligence

All three depend on `dim_date` being marked as the date table.

```dax
Adoption Rate (Rolling 12 Weeks) =
DIVIDE (
    CALCULATE (
        DISTINCTCOUNT ( fact_ai_session[lawyer_id] ),
        DATESINPERIOD ( dim_date[date], MAX ( dim_date[date] ), -84, DAY )
    ),
    [Lawyer Population]
)

Total Cost AUD FYTD = TOTALYTD ( [Token Cost AUD], dim_date[date], "6-30" )

Adoption Rate MoM =
[Adoption Rate] - CALCULATE ( [Adoption Rate], DATEADD ( dim_date[date], -1, MONTH ) )
```

The `"6-30"` argument is not decoration: the Australian financial year ends
30 June, and `TOTALYTD` defaults to 31 December. Without it every year-to-date
figure on the page is for the wrong year, and it will look completely
reasonable.

| Scope | Expected |
|---|---|
| Adoption Rate (Rolling 12 Weeks), as at 2026-08-31 | **0.6208** |
| Total Cost AUD FYTD, at 2026-06-30 (FY2026 complete) | **5,209.18** |

---

## Adoption and retention

```dax
Sessions per Active Lawyer = DIVIDE ( [Sessions], [Active Lawyers] )

Lawyers Never Adopted =
VAR EverAdopted =
    CALCULATE ( DISTINCTCOUNT ( fact_ai_session[lawyer_id] ), REMOVEFILTERS ( dim_date ) )
RETURN
    [Lawyer Population] - EverAdopted

Never Adopted % = DIVIDE ( [Lawyers Never Adopted], [Lawyer Population] )

Cohort Lawyers = SUM ( vw_retention_cohort[cohort_lawyers] )

Week-4 Retention =
DIVIDE (
    SUM ( vw_retention_cohort[retained_lawyers] ),
    SUMX (
        FILTER (
            vw_retention_cohort,
            NOT ISBLANK ( vw_retention_cohort[retained_lawyers] )
        ),
        vw_retention_cohort[cohort_lawyers]
    )
)
```

| Scope | Expected |
|---|---|
| Sessions per Active Lawyer, Mar 2025 | 24.3 |
| Sessions per Active Lawyer, Aug 2026 | **39.2** |
| Lawyers Never Adopted, all time | **122** |
| Never Adopted %, level = Partner | **0.7342** |
| Never Adopted %, level = Graduate | **0.0988** |
| Never Adopted %, group = Litigation | **0.6250** |
| Never Adopted %, group = M&A | 0.1923 |
| Week-4 Retention, all complete cohorts | **0.9087** |
| Week-4 Retention, level = Partner | 0.8095 |
| Week-4 Retention, level = Associate | 0.9279 |
| Cohort Lawyers, all complete cohorts | 263 |

**`Lawyers Never Adopted` removes the date filter, and only the date filter.**
"Never" is a statement about the whole window, so the count of adopters has to
be evaluated over the whole window even when the visual is showing one month.
`REMOVEFILTERS ( dim_date )` does that while leaving level and practice group
alone, which is what lets the same measure sit in a matrix crossed by both.

Using `REMOVEFILTERS ( fact_ai_session )` instead would clear the expanded
table — level and practice group with it — and every cell in that matrix would
report 122. That is the bug from ① in a second costume; it is worth recognising
the shape rather than the specific measure.

**Retention is read only on complete cohorts.** A cohort whose week *W+4* falls
past the end of the extract has not failed to come back, it has not had the
chance. The view returns those with `retained_lawyers` blank; the measure drops
them from the denominator rather than letting them draw a cliff at the
right-hand edge that is an artefact of when the data was pulled.

The filter is written on the blank numerator rather than on the view's
`is_complete` flag, and that is deliberate. Filtering on the flag makes the
measure depend on whether the column arrived as a boolean or as a 1/0 integer —
`[is_complete] = TRUE ()` throws *"DAX comparison operations do not support
comparing values of type Integer with values of type True/False"* against an
integer column, and `= 1` throws the mirror image against a boolean one. The
completeness rule already lives in one place, which is the NULL the view puts in
`retained_lawyers`; reading it there means the measure cannot disagree with the
view about what "complete" means. On this
extract no cohort is incomplete — the last first-time user appears in March
2026 — so the guard changes nothing here. It stays because the number it
protects against is the one a reader would believe.

---

## Quality and observability

```dax
p95 Latency (ms) =
PERCENTILEX.INC (
    FILTER ( fact_ai_session, fact_ai_session[status] = "Success" ),
    fact_ai_session[latency_ms],
    0.95
)

Error Rate =
DIVIDE (
    CALCULATE ( COUNTROWS ( fact_ai_session ),
                KEEPFILTERS ( fact_ai_session[status] <> "Success" ) ),
    COUNTROWS ( fact_ai_session )
)

Median Edit Distance = MEDIANX ( fact_ai_output, fact_ai_output[edit_distance_pct] )

Mandatory Review Gap =
CALCULATE ( COUNTROWS ( fact_ai_output ), fact_ai_output[is_review_gap] = TRUE () )

Retry Rate =
DIVIDE (
    CALCULATE ( COUNTROWS ( fact_ai_session ),
                KEEPFILTERS ( fact_ai_session[retry_count] > 0 ) ),
    COUNTROWS ( fact_ai_session )
)

Heavy Rework Share =
DIVIDE (
    CALCULATE (
        COUNTROWS ( fact_ai_output ),
        KEEPFILTERS ( fact_ai_output[accepted] = TRUE () ),
        KEEPFILTERS ( fact_ai_output[edit_distance_pct] > 40 )
    ),
    [Accepted Outputs]
)
```

p95 rather than the mean, because the mean hides the slow tail that causes
abandonment. `PERCENTILEX.INC` and SQL's `PERCENTILE_CONT` interpolate the same
way, so these match to the decimal.

The review gap is a **count, not a rate**. A rate invites a tolerance, and the
acceptable number of unverified citations relied on in client work is zero.

| Scope | Expected |
|---|---|
| p95 Latency, all tools | **4,534** |
| p95 Latency, tool = Harvey | **5,579.2** |
| p95 Latency, tool = Copilot | 4,238 |
| p95 Latency, tool = Firm Chat | 4,252.3 |
| Error Rate, all time | 0.0201 |
| Error Rate, 10–14 Nov 2025 | **0.1420** |
| Median Edit Distance, Litigation | **33.0** |
| Median Edit Distance, M&A | **11.1** |
| Median Edit Distance, Banking & Finance | 15.1 |
| Median Edit Distance, Employment | 25.3 |
| Median Edit Distance, Real Estate | 13.25 |
| Mandatory Review Gap, all time | 10,869 |
| Retry Rate, all time | 0.0689 |
| Heavy Rework Share, all outputs | 0.0714 |
| Heavy Rework Share, task = Research | **0.1634** |
| Heavy Rework Share, task = Due Diligence Review | **0.0203** |

`Heavy Rework Share` is the measure that keeps `Acceptance Rate` honest. Accept
is one click and it is generous: a lawyer who rewrote two fifths of a research
memo still clicked it. Among **accepted** Research outputs, 16.3% were rewritten
by more than 40% — eight times the rate for due diligence review, where the
model is extracting rather than composing. Acceptance rate cannot see that
difference; it reports 67.6% against 74.7% and calls the gap seven points.

---

## Governance

```dax
Governance Exposure % =
DIVIDE (
    CALCULATE (
        COUNTROWS ( fact_ai_session ),
        KEEPFILTERS ( dim_matter[confidentiality_tier] IN { "Restricted", "Barrier" } )
    ),
    COUNTROWS ( fact_ai_session )
)
```

| Scope | Expected |
|---|---|
| Governance Exposure %, all time | **0.0486** |

Against 14% of matters carrying one of those tiers. Most lawyers self-restrict;
the page exists for the ones who do not.

That is one number for the whole firm. The Page 5 table needs the two shares
beside each other, because the finding is the *gap* between them: the Barrier
tier carries 2.5% of matters but only 0.8% of sessions.

```dax
Matters = COUNTROWS ( dim_matter )

Share of Sessions % =
DIVIDE (
    [Sessions],
    CALCULATE ( [Sessions], REMOVEFILTERS ( dim_matter[confidentiality_tier] ) )
)

Share of Matters % =
DIVIDE (
    [Matters],
    CALCULATE ( [Matters], REMOVEFILTERS ( dim_matter[confidentiality_tier] ) )
)
```

| Scope | Sessions | Share of Sessions % | Matters | Share of Matters % |
|---|---|---|---|---|
| tier = Standard | **106,982** | **0.9514** | **946** | **0.8600** |
| tier = Restricted | **4,578** | **0.0407** | **127** | **0.1155** |
| tier = Barrier | **882** | **0.0078** | **27** | **0.0245** |

Both shares have to be measures. As calculated columns the denominator would be
frozen at refresh, so the column would keep reporting the firm-wide share no
matter what the visual is filtered to.

`REMOVEFILTERS` here names a **column**, not the table. `REMOVEFILTERS
( dim_matter )` would repeat the `Lawyer Population` bug above — it clears
practice area and fee arrangement as well, so the denominator would stop
responding to every other slicer on the page. Clearing the one column the rows
are grouped by is all a percent-of-total needs.

`Matters` counts `dim_matter` directly, so no date filter reaches it: matter mix
is a property of the portfolio, not of a month. `Sessions` is filtered by date.
Page 5 carries no date slicer, so the two are read over the same 18 months.

---

## The screening list

`vw_governance_screen` is at lawyer x fiscal-quarter grain and carries
`review_completion` and `sensitive_session_rate` as columns. **Neither column
may be dragged into the screening table.** Aggregating them means averaging
per-quarter rates, which weights a quarter with 30 mandatory outputs the same as
one with 300.

```dax
Screen — Mandatory Outputs = SUM ( vw_governance_screen[mandatory_outputs] )

Screen — Review Completion =
DIVIDE (
    SUM ( vw_governance_screen[mandatory_reviewed] ),
    SUM ( vw_governance_screen[mandatory_outputs] )
)

Screen — Sensitive Rate =
DIVIDE (
    SUM ( vw_governance_screen[sensitive_sessions] ),
    SUM ( vw_governance_screen[sessions] )
)
```

| Lawyer (ranked by completion, lowest first) | Mandatory Outputs | Review Completion | Sensitive Rate |
|---|---|---|---|
| Liam Whitlock | 95 | **0.4211** | 0.0000 |
| Uma Rutherford | 465 | **0.4215** | 0.1810 |
| James Kilbride | 683 | 0.4378 | 0.0996 |
| Cara Larkspur | 383 | 0.4386 | 0.0316 |
| Chloe Nakamura | 65 | 0.4462 | 0.1481 |

The averaged version is close enough to look right and wrong enough to matter:
18 of the same 20 names, in a different order, with Chloe Nakamura displaced
from fifth to seventh by 6.6 points. A list that is scored at 65% precision has
to be built the way it was scored.

**The eligibility filter is on the lawyer's total, not on the view's
`is_screenable` flag.** `is_screenable` marks a *quarter* with at least 25
mandatory outputs; `src/screening_eval.py` filters on a *lawyer* with at least
25 across the window, and the precision figure quoted on the page is measured
under that definition. Using the flag instead drops four lawyers and changes
the ranking. Filter on `[Screen — Mandatory Outputs] >= 25`.

---

## Billable impact

The effect size is not computed in DAX. It is a fixed-effects regression with
lawyer-clustered standard errors, fitted in `src/billable_impact.py` and landed
in `fact_billable_impact_estimate` with its control definition and sample sizes.
DAX applies it.

Observed hours are baseline × (1 + *e*), so the hours the tool removed are
observed × −*e* / (1 + *e*).

```dax
Post-Adoption Hours =
CALCULATE ( SUM ( fact_time_entry[hours] ),
            fact_time_entry[is_post_adoption] = TRUE () )

Notional Capacity Released (AUD) =
SUMX (
    SUMMARIZE ( fact_time_entry, dim_lawyer[lawyer_id], dim_matter[fee_arrangement] ),
    VAR e =
        DIVIDE (
            LOOKUPVALUE (
                fact_billable_impact_estimate[estimate_pct],
                fact_billable_impact_estimate[fee_arrangement],
                dim_matter[fee_arrangement]
            ),
            100
        )
    VAR ReleasedHours = DIVIDE ( [Post-Adoption Hours] * -e, 1 + e )
    RETURN ReleasedHours * CALCULATE ( MAX ( dim_lawyer[standard_charge_rate_aud] ) )
)

Revenue at Risk — Hourly (AUD) =
CALCULATE ( [Notional Capacity Released (AUD)],
            KEEPFILTERS ( dim_matter[fee_arrangement] = "Hourly" ),
            KEEPFILTERS ( fact_time_entry[billable] = TRUE () ) )

Capacity Released — Fixed Price (AUD) =
CALCULATE ( [Notional Capacity Released (AUD)],
            KEEPFILTERS ( dim_matter[fee_arrangement] IN { "Fixed Fee", "Capped" } ) )

AI Matters =
CALCULATE ( DISTINCTCOUNT ( fact_time_entry[matter_id] ),
            fact_time_entry[is_post_adoption] = TRUE () )
```

The two headline amounts serve different questions. Fixed-price capacity uses
all recorded post-adoption hours. Hourly revenue exposure uses only **billable**
post-adoption hours. Both apply the fitted effect and the lawyer's list rate;
neither is realised profit or proven revenue loss. The existing Hourly measure
name is retained so visuals keep their references. Label its visual
**Estimated hourly revenue exposure (AUD)**.
`AI Matters` is the denominator that has to sit beside every cell, because a
comparison of hours across fee arrangements is worthless without knowing how
many matters stand behind it.

The iteration has to be at lawyer grain because the charge-out rate varies by
lawyer; evaluating the rate at the total would value every released hour at the
firm-wide average and quietly overstate the junior work.

| Scope | Expected |
|---|---|
| Post-Adoption Hours, all time | 467,585 |
| Notional Capacity Released, fee = Hourly | **12,330,295** |
| Notional Capacity Released, fee = Fixed Fee | **9,882,927** |
| Notional Capacity Released, fee = Capped | **6,083,681** |
| Revenue at Risk — Hourly, billable only | **10,140,058** |
| Revenue at Risk — Hourly, billable = False filter | **blank** |
| Capacity Released — Fixed Price | **15,966,608** |
| AI Matters, fee = Hourly / Capped / Fixed Fee | **506 / 230 / 202** |

The all-hours Hourly capacity value remains AUD 12.33m, of which AUD 2.19m
comes from non-billable time. It must not be labelled revenue. The corrected
headline is AUD 10.14m of estimated billable revenue exposure, before negotiated
rates, write-offs, collections or redeployment. The fitted percentage is still
estimated on all recorded hours; applying it to billable hours is an explicit
scenario assumption. Keep the all-hours bar chart labelled as capacity, and do
not add its fee categories to the two headline amounts.

---

## Cost and idle seats

```dax
Idle Seat Share =
DIVIDE (
    CALCULATE ( COUNTROWS ( fact_seat_month ),
                fact_seat_month[used_in_month] = FALSE () ),
    COUNTROWS ( fact_seat_month )
)

Idle Licence Cost AUD =
CALCULATE ( SUM ( fact_seat_month[licence_cost_aud] ),
            fact_seat_month[used_in_month] = FALSE () )
```

| Scope | Expected |
|---|---|
| Licence Cost AUD, all time | 787,710 |
| Idle Licence Cost AUD, all time | **401,570** |
| Idle Seat Share, all time | **0.5398** |
| Idle Seat Share, tool = Harvey | 0.4719 |
| Idle Seat Share, tool = Copilot | 0.5527 |
| Idle Seat Share, tool = Firm Chat | 0.5563 |
| Cost per Accepted Output, tool = Harvey | 28.14 |
| Cost per Accepted Output, tool = Copilot | 8.31 |
| Cost per Accepted Output, tool = Firm Chat | 2.56 |

Just over half the licence bill — **AUD 401,570** — bought seats nobody opened
in the month they were paid for. That number does not exist anywhere in the
session table, which is the argument for having a seat fact at all.

It is also the cheapest problem on the dashboard to fix. Unlike adoption it
needs no behaviour change from anyone, only a reconciliation before renewal.

---

## What is deliberately not in DAX

**Week-4 retention.** A cohort calculation — of the lawyers whose first session
fell in week *W*, the share still active in week *W+4* — is expressible in DAX
and unpleasant to read there. It belongs in a view, alongside the other business
logic. Not everything that *can* be a measure should be.

**The billable-impact effect.** A regression with clustered standard errors is
not something DAX should attempt. Fitting it in Python and storing the result
with its provenance is what lets the page state the control group and the
interval, which is the difference between an effect size and a finding.
