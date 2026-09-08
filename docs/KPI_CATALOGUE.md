# KPI Catalogue

One row per metric: what business question it answers, how it is computed, at
what grain, the definitional traps, and who owns it. Metrics are grouped by the
dashboard page that carries them.

Conventions used throughout:

- **Every ratio is a measure**, evaluated at the grain of the visual. None is a
  calculated column. A ratio stored per row and then summed produces nonsense on
  subtotal rows — the single most common Power BI modelling error.
- **Denominators that describe a population** are built from the dimension, not
  from the fact table, so that "adoption among partners" divides by *all*
  partners rather than by partners who already appear in the session table. They
  are **not** protected with `REMOVEFILTERS()` on a fact table: a fact table's
  expanded table includes every dimension reached by a many-to-one relationship,
  so clearing it clears the grouping as well. See `DAX_MEASURES.md` §① — that
  mistake made every stratified adoption rate wrong while leaving the total
  correct.
- Metrics below marked ⚠ carry a definitional choice a reader could reasonably
  disagree with. The choice and its consequence are stated.

---

## Page 1 — Executive Overview

### Monthly Active Lawyers
- **Question**: how many lawyers touched an AI tool at all this month?
- **Formula**: `DISTINCTCOUNT(fact_ai_session[lawyer_id])`
- **Grain**: month × (optional level / practice group / office / tool)
- **Notes**: a *floor*, not a success measure. One session in a month counts the
  same as forty; that is why it never appears without Adoption Rate and
  Retention beside it.
- **Owner**: AI Operations

### Adoption Rate
- **Question**: what share of a given population of lawyers is using AI?
- **Formula**: `DISTINCTCOUNT(session[lawyer_id]) / CALCULATE(DISTINCTCOUNT(dim_lawyer[lawyer_id]), KEEPFILTERS(dim_lawyer[is_active] = TRUE()))`
- **Grain**: month × level × practice_group (× tool)
- **Notes** ⚠: only meaningful **stratified**. A firm-wide adoption rate hides
  the fact that graduates and partners are different products with different
  problems. The denominator counts the population from `dim_lawyer`, so a group
  with no users reports zero rather than disappearing.
- **Owner**: AI Operations

### Accepted Outputs
- **Question**: how much AI work actually survived into a lawyer's work product?
- **Formula**: `SUM(fact_ai_output[accepted])`
- **Grain**: month × task type × practice area
- **Notes**: acceptance is recorded at output level, not session level; failed
  sessions produce no output row and are therefore excluded from the denominator
  of acceptance rate rather than counted as rejections.
- **Owner**: AI Operations

### Total AI Cost (AUD)
- **Question**: what did the AI programme cost this period?
- **Formula**: `SUM(fact_seat_month[licence_cost_aud]) + SUM(fact_ai_session[cost_aud])`
- **Grain**: month × tool
- **Notes** ⚠ **licences are the cost; tokens are a rounding error.** Token
  spend across the window is AUD 6,670 against AUD 787,710 of licences — 0.8%.
  A cost measure built from the session table alone is wrong by two orders of
  magnitude and makes the programme look free. Training and review time remain
  out of scope, so this is still an understatement, and the page says so.
- **Owner**: Finance / AI Operations

### Idle Licence Cost (AUD) ⚠
- **Question**: what are we paying for seats nobody opens?
- **Formula**: `CALCULATE(SUM(fact_seat_month[licence_cost_aud]), fact_seat_month[used_in_month] = FALSE())`
- **Grain**: month × tool × practice group
- **Notes** ⚠ AUD 401,570, 51% of the licence bill. Seats are allocated at
  rollout and renewed annually; usage is checked, if at all, by someone glancing
  at a session count. This number exists nowhere in the session table, which is
  why the model carries a seat fact at all. It is also the cheapest problem on
  the dashboard to act on: unlike adoption it requires no behaviour change from
  anyone, only a reconciliation before renewal. Some idle share is legitimate —
  a seat kept open for occasional need — so the threshold is deliberately loose.
- **Owner**: Finance

### Open Incidents
- **Question**: how much unresolved risk is outstanding right now?
- **Formula**: `CALCULATE(COUNTROWS(fact_incident), fact_incident[resolved] = 0)`
- **Grain**: as-at date × severity × incident type
- **Notes** ⚠: a *state* measure, not a period measure. It does not respect a
  date-range slicer the way flow measures do; the visual is labelled "as at" to
  prevent that misreading.
- **Owner**: Risk & Compliance

### Review Coverage
- **Question**: of the outputs that required human review, how many got it?
- **Formula**: `DIVIDE(CALCULATE(SUM(output[reviewed]), output[effective_review_policy]="Mandatory"), CALCULATE(COUNTROWS(output), output[effective_review_policy]="Mandatory"))`
- **Grain**: month × review type × confidentiality tier
- **Notes**: the denominator is outputs *requiring* review, not all outputs.
  Diluting it with no-review-needed outputs would make coverage look better the
  more low-risk work the firm does. The denominator comes from
  `vw_effective_review_policy`, never from a stored flag — see
  `DATA_MODEL.md` §3. **Always reported split by `review_type`**: citation
  verification and supervisory review are different risks (a fabricated
  authority versus an unsupervised judgement call) and a blended figure hides
  whichever one is failing.
- **Owner**: Risk & Compliance

---

## Page 2 — Adoption & Retention

### Rolling 12-Week Adoption Rate
- **Question**: is adoption trending, or is a good month noise?
- **Formula**: Adoption Rate evaluated over `DATESINPERIOD(dim_date[date], MAX(dim_date[date]), -84, DAY)`
- **Grain**: week × level × practice group
- **Notes**: requires `dim_date` marked as the date table.
- **Owner**: AI Operations

### Week-4 Retention
- **Question**: do lawyers come back, or try it twice and stop?
- **Formula**: of lawyers whose *first ever* session fell in week W, the share with ≥1 session in week W+4
- **Grain**: cohort week × level
- **Notes** ⚠: the metric that separates a genuine rollout from a training-day
  spike. Cohorts in the last four weeks of the window are incomplete and are
  suppressed rather than shown as low.
- **Owner**: AI Operations

### Sessions per Active Lawyer
- **Question**: among people who use it, how deep is the use?
- **Formula**: `DIVIDE(COUNTROWS(fact_ai_session), DISTINCTCOUNT(fact_ai_session[lawyer_id]))`
- **Grain**: month × level × tool
- **Notes**: intensity among adopters, deliberately separate from breadth.
- **Owner**: AI Operations

---

## Page 3 — Billable Impact

### Hours per Matter
- **Question**: how much recorded time does a matter consume?
- **Formula**: `DIVIDE(SUM(fact_time_entry[hours]), DISTINCTCOUNT(fact_time_entry[matter_id]))`
- **Grain**: matter × month, rolled up by practice area × level × fee arrangement
- **Notes** ⚠: matters differ enormously in size. Comparisons are made *within*
  practice area and lead-lawyer level, never across.
- **Owner**: Finance / Practice Management

### Hours Delta (AI-assisted vs comparable)
- **Question**: do matters with heavy AI use record fewer hours?
- **Formula**: mean recorded hours per lawyer-matter-day, on entries after the lawyer's first session on that matter, against **matters worked by lawyers who never adopted**, held within fee arrangement × practice area × level
- **Grain**: practice area × level × fee arrangement
- **Notes** ⚠ **the most important caveat in this catalogue.** Three simpler
  versions of this measure are wrong, and the page names the one it uses:
  - *Total hours per matter, AI vs non-AI* gives **+41%** — the wrong sign.
    Bigger matters attract more AI. That is selection, not effect.
  - *Before versus after first use, within the same matter* cannot be computed:
    39% of AI lawyer-matters have no pre-period at all and the median has one
    timesheet line before first use. Lawyers reach for AI as soon as they pick
    up a matter.
  - *Using the same lawyers' other matters as the control* is contaminated,
    because reinvested capacity lands on exactly those matters.

  What remains is observational. Stratification removes the composition bias it
  can see; it does not establish causation, and no page claims it does.

  Reported with 95% intervals, standard errors clustered on the lawyer:
  Capped −8.9% [−11.5, −6.3], Fixed Fee −14.3% [−18.1, −10.4], Hourly −8.3%
  [−10.8, −5.8]. **Fixed fee separates from both others**; **capped and hourly
  do not separate from each other** (−0.7pp, [−4.5, +3.4]), which is the right
  answer — they differ by 1pp in the design. The page therefore contrasts
  fixed-price against hourly work and declines to rank capped against hourly. The estimate
  is read from `fact_billable_impact_estimate`, which stores the control
  definition, cell count and cluster count alongside the number — a bare effect
  size without its control group is not a finding.
- **Owner**: Finance / AI Operations

### Notional Capacity Released — Fixed Fee & Capped (AUD)
- **Question**: what is the saved time worth on matters where the fee is fixed?
- **Formula**: `hours saved × dim_lawyer[standard_charge_rate_aud]`, filtered to `fee_arrangement IN ("Fixed Fee","Capped")`
- **Grain**: month × practice area × level
- **Notes** ⚠ **not "margin uplift", and the name is the point.** Hours saved on
  a fixed-price matter do not become profit by themselves; they become capacity.
  They convert to profit only if reinvested in chargeable work — a lawyer who
  saves ten hours and leaves at five has improved nothing. Valued at charge-out
  rate, which is revenue value, not internal cost. This measure is meaningless
  without Reallocation Ratio beside it.
- **Owner**: Finance

### Revenue at Risk — Hourly (AUD)
- **Question**: what is the estimated billable-time revenue exposure on Hourly matters?
- **Formula**: implied hours released × lawyer list rate, filtered to `fee_arrangement = "Hourly"`, `is_post_adoption = TRUE()` and `billable = TRUE()`
- **Grain**: month × practice area × level
- **Notes**: the corrected full-window value is about AUD 10.14m. The AUD 12.33m
  all-hours capacity value includes AUD 2.19m from non-billable records and is
  not an income measure. Apply the all-hours fitted percentage to the billable
  subset as an explicit scenario assumption. Negotiated rates, write-offs,
  collections and redeployment are not modelled, so this is not realised lost
  revenue. Use the visual title "Estimated hourly revenue exposure (AUD)".
  Fixed-price capacity remains an all-hours measure and is not realised profit.
- **Owner**: Finance / Partnership

### Reallocation Ratio
- **Question**: where did the saved time go — onto other matters, or nowhere?
- **Formula**: change in a lawyer's total recorded hours, divided by the change in their hours on AI-assisted matters
- **Grain**: lawyer × quarter
- **Notes** ⚠: ≈1 means the freed time moved onto other chargeable work; ≈0
  means total recorded time simply fell. This is the measure that stops
  Notional Capacity Released being read as profit. In this dataset roughly 60%
  of lawyers reinvest and 40% do not — a split that decides whether AI adoption
  is a growth story or a utilisation problem, and that no adoption dashboard
  answers. In this dataset 41% of released hours reappear firm-wide — so 59% of
  what Notional Capacity Released reports has not turned into anything, which is
  precisely why the two measures must appear together.
- **Owner**: Finance / Practice Management

---

## Page 4 — Quality & Observability

### Acceptance Rate
- **Question**: is the output good enough to keep?
- **Formula**: `DIVIDE(SUM(output[accepted]), COUNTROWS(output))`
- **Grain**: task type × practice area × tool
- **Notes**: denominator is outputs produced, so infrastructure failures do not
  depress apparent quality — they are measured separately as error rate.
- **Owner**: AI Operations

### Median Edit Distance
- **Question**: when output is kept, how much rework does it need?
- **Formula**: `MEDIANX(output, output[edit_distance_pct])`
- **Grain**: task type × practice area
- **Notes** ⚠: a better quality signal than usage counts. "Accepted" is binary
  and generous — a lawyer who rewrites 60% of a draft still clicked accept.
  Median rather than mean, because the distribution is long-tailed.
- **Owner**: AI Operations / Knowledge

### Mandatory Review Gap
- **Question**: is high-risk output going out unreviewed?
- **Formula**: `CALCULATE(COUNTROWS(output), output[effective_review_policy]="Mandatory", output[reviewed]=0)`
- **Grain**: month × review type × practice area × confidentiality tier
- **Notes**: a count, not a rate. A rate invites tolerance of a small
  percentage; for mandatory review the acceptable number is zero, so the visual
  shows the raw count and the alert fires on any occurrence.
- **Owner**: Risk & Compliance

### p95 Latency
- **Question**: is the tool fast enough to stay in a lawyer's workflow?
- **Formula**: `PERCENTILEX.INC(fact_ai_session, fact_ai_session[latency_ms], 0.95)`
- **Grain**: day × tool × task type
- **Notes**: p95 not mean — the mean hides the slow tail that causes abandonment.
- **Owner**: AI Operations / IT

### Error Rate
- **Question**: how often does the platform fail outright?
- **Formula**: `DIVIDE(CALCULATE(COUNTROWS(session), session[status] <> "Success"), COUNTROWS(session))`
- **Grain**: day × tool × error type
- **Notes**: paired with retry count; a retried timeout is a worse user
  experience than the rate alone suggests.
- **Owner**: IT Operations

### Cost per Accepted Output
- **Question**: what does one usable piece of work cost?
- **Formula**: `DIVIDE([Total AI Cost AUD], [Accepted Outputs])`
- **Grain**: month × tool × task type
- **Notes** ⚠: **per accepted output, not per call, and licence-inclusive.**
  Cost per call rewards a tool that is cheap and useless; token-only cost makes
  every tool look free. Firm-wide the figure is **AUD 10.58**, ranging from
  2.56 for the internal chat tool to 28.14 for the most expensive one — which is
  the comparison a renewal conversation actually needs.

  This is a ratio of two sums and must be a measure. Stored per tool as a column
  and averaged, it returns 13.00 against a true 10.58 — close enough to pass a
  glance, and on the wrong side of the $12 alert threshold. The broken version
  raises a cost alert the correct one does not.
- **Owner**: Finance / AI Operations

---

## Page 5 — Governance & Risk

### Governance Exposure %
- **Question**: how much AI activity is happening on confidentiality-restricted matters?
- **Formula**: `DIVIDE(CALCULATE(COUNTROWS(session), matter[confidentiality_tier] IN {"Restricted","Barrier"}), COUNTROWS(session))`
- **Grain**: month × practice area × tool × office
- **Notes**: expected to be small (~3%). The number alone is not the finding —
  it is the pairing with review coverage below.
- **Owner**: Risk & Compliance / General Counsel

### Review Coverage by Confidentiality Tier
- **Question**: is the work that most needs review the work that gets it?
- **Formula**: Review Coverage, sliced by `dim_matter[confidentiality_tier]`
- **Grain**: month × tier × practice area
- **Notes** ⚠ coverage runs 87.9% on Standard, 86.3% on Restricted and 82.8% on
  Barrier — worst where policy is strictest, the inverse of what it requires.
  The mechanism is not a separate effect: the same small cohort who ignore the
  tier restriction also skip mandatory review (51% completion against 90%), and
  they are over-represented in sensitive-tier sessions. One cause, two symptoms.
  **The Barrier figure rests on roughly 800 outputs and moves several points
  between periods** — it is shown with its denominator and is a prompt to look,
  not a number to report upward. The actionable output is the per-lawyer view
  below.
- **Owner**: Risk & Compliance

### Mandatory Review Completion per Lawyer ⚠
- **Question**: which individuals are not doing the review the policy requires?
- **Formula**: reviewed outputs / outputs where `effective_review_policy = "Mandatory"`, per lawyer, minimum 25 mandatory outputs
- **Grain**: lawyer × quarter
- **Notes** ⚠ **this is what makes Page 5 actionable, and which signal to use was
  measured rather than assumed** (`results/screening_comparison.csv`). The
  comparison below covers the full synthetic window with at least 25 mandatory
  outputs per lawyer. It does not evaluate the quarterly alert. The candidate
  screens share one scenario and are not tested on an independent sample:

  | Screen, 20 names | Precision | Recall | Avg precision |
  |---|---|---|---|
  | Mandatory review completion | **65%** | **81%** | **0.88** |
  | Restricted-tier session rate | 10% | 13% | 0.13 |
  | Both, rank-summed | 10% | 13% | 0.08 |

  Exposure barely separates them — how much sensitive work a lawyer is *staffed
  on* dominates how much they choose to use AI on it — and combining the two
  signals is worse than the strong one alone. **Recall is capped at 81%** because
  three of the sixteen fail the minimum of 25 mandatory outputs. This is an
  eligibility threshold, not a claim that every low-volume rate is undefined.

  So the honest answer to "how accurate is this list?" is **about two thirds
  right by design**: 13 genuinely non-compliant and 7 merely careless. No measure
  here separates the two, which is why the output is a conversation rather
  than a verdict, and why the threshold is set to over-collect.
- **Owner**: General Counsel / Risk

### Restricted-Tier Session Rate per Lawyer
- **Question**: how much of a lawyer's AI use lands on sensitive matters?
- **Formula**: sessions on `confidentiality_tier IN ("Restricted","Barrier")` / that lawyer's total sessions
- **Grain**: lawyer × quarter
- **Notes** ⚠ shown as **context beside** the completion measure, never as the
  ranking. As a rate rather than a count it at least normalises for how much AI
  a lawyer uses, but it still cannot normalise for how much sensitive work they
  are given, and on its own it produces a list that is mostly false positives.
- **Owner**: Risk & Compliance

### Barrier Crossing Exposure
- **Question**: is one lawyer touching matters on both sides of an information barrier?
- **Formula**: count of lawyers with sessions on matters in ≥2 distinct `barrier_group_id` within a rolling 90 days
- **Grain**: lawyer × rolling 90 days
- **Notes** ⚠: a screening signal, not a breach finding. Legitimate explanations
  exist (barrier lifted, lawyer reassigned, group closed). It is designed to
  produce a short list a human checks, which is why the threshold is deliberately
  sensitive.
- **Owner**: General Counsel / Risk

### Incident Profile
- **Question**: what is going wrong, how badly, and for how long?
- **Formula**: count by `incident_type` × `severity`; `AVERAGE(days_to_resolve)`; open count
- **Grain**: month × type × severity
- **Notes**: resolution time is only computed on resolved incidents, so it is
  biased downward while a long-running incident is still open. The open count
  beside it is what stops that bias misleading.
- **Owner**: Risk & Compliance

---

## Pipeline health (Page 1 strip)

### Load Completeness
- **Formula**: `rows_loaded` per source per day vs its 30-day median
- **Notes**: a zero-row load is the failure this metric exists to catch. It is
  the condition that fires on the planted three-day Copilot gap.
- **Owner**: Data Engineering

### Referential Integrity Failures
- **Formula**: `SUM(fact_pipeline_run[referential_failures])`, and as a share of `rows_loaded`
- **Notes**: a persistent low-level mismatch (~2% of `matter_id` values on one
  source) never trips a threshold on any single day, which is why it is shown as
  a trend rather than a status light. Silent, chronic data loss is more dangerous
  than a loud outage because every metric downstream is quietly understated.
- **Owner**: Data Engineering

### Freshness
- **Formula**: hours since the most recent successful run per source
- **Notes**: staleness is invisible on every other page — a dashboard built on a
  three-day-old extract looks entirely healthy.
- **Owner**: Data Engineering
