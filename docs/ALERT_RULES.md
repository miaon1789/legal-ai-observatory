# Alert rules

Generated from `dim_alert_rule` and `results/screening_comparison.csv` by
`src/export_alert_rules.py`. Edit the source data, not this file.

The catalogue defines conditions, thresholds, owners and reasons for follow-up.
It supports the synthetic operations report. It does not send notifications.

**Thresholds are data.** `vw_alert_status` computes a current value for each
rule and applies the `threshold_value` and `comparison` stored here, so changing
a threshold is a row update rather than an edit to SQL that hard-coded it. The
`rationale` travels with the breach.

Governance and operational rules use different thresholds because their risks
differ. These are scenario assumptions. A real deployment would need its own
policy owners to agree the thresholds and response process.

16 rules: 6 governance, 5 operational, 2 quality, 3 cost.

## Screening Reference

Full synthetic window, March 2025 to August 2026. Each screen ranks the same eligible population with at least 25 mandatory outputs. Ground truth is withheld from the report, not from a separate test sample.

| Screen, 20 names | True positives | Precision | Recall of full cohort |
|---|---:|---:|---:|
| Mandatory review completion | 13/20 | 65% | 81% |
| Restricted-tier session rate | 2/20 | 10% | 13% |
| Restricted-tier session count | 4/20 | 20% | 25% |
| Rank sum of completion and exposure rate | 2/20 | 10% | 13% |

Results come from `results/screening_comparison.csv`. Recall is rounded to whole percentages here. These results do not describe the quarterly alert threshold or arbitrary report filters. Low-volume users are excluded by the minimum-volume rule, not because every such rate is mathematically undefined. The list supports review, not a verdict.

## Governance

Tight thresholds, set against the cost of the failure they guard rather than against a service level.

### 11. Review coverage below 95% on Barrier matters
| | |
|---|---|
| **Condition** | `review coverage where confidentiality_tier = Barrier` |
| **Fires when** | value `<` 0.95 |
| **Severity** | Critical |
| **Owner** | General Counsel |

The operational rules above tolerate a few percent of failure because the cost of a failure is a lawyer's wasted minute. Here the cost of a failure is privileged information crossing an information barrier, which is not recoverable and is reportable. A tolerance that is sensible for latency is negligent for a barrier.

### 12. Any unreviewed output where review is Mandatory
| | |
|---|---|
| **Condition** | `count of outputs, effective policy Mandatory and reviewed = 0` |
| **Fires when** | value `>` 0 |
| **Severity** | Critical |
| **Owner** | Risk & Compliance |

Expressed as a count, not a rate. A rate invites a tolerance, and the acceptable number of unverified citations relied on in client work is zero.

### 13. First use of a newly deployed tool on a Restricted matter
| | |
|---|---|
| **Condition** | `first session for a tool where tier in (Restricted, Barrier)` |
| **Fires when** | value `>=` 1 |
| **Severity** | High |
| **Owner** | General Counsel |

New tools reach sensitive work before the policy that governs them does. This fires on the first occurrence, not on a volume threshold, because the point is to review the decision before it becomes a pattern.

### 14. Lawyer active across two barrier groups in 90 days
| | |
|---|---|
| **Condition** | `distinct barrier_group_id per lawyer, rolling 90 days` |
| **Fires when** | value `>=` 2 |
| **Severity** | Critical |
| **Owner** | General Counsel |

A screening signal, not a finding: barriers lift, people get reassigned, groups close. It is deliberately sensitive because the cost of one missed crossing outweighs the cost of checking a short list by hand.

### 15. Lawyer mandatory review completion below 70%
| | |
|---|---|
| **Condition** | `reviewed / mandatory outputs per lawyer per quarter, minimum 25 outputs` |
| **Fires when** | value `<` 0.7 |
| **Severity** | Critical |
| **Owner** | Risk & Compliance |

Low mandatory review completion identifies cases for follow-up, not a finding of misconduct. The full-window Top 20 comparison is recorded in results/screening_comparison.csv and rendered in the rule catalogue. It uses synthetic ground truth withheld from the report, not an independent test sample. Lawyers with fewer than 25 mandatory outputs are excluded by the eligibility rule. A quarterly alert or a different filter requires its own evaluation. Incomplete review can also reflect delay rather than deliberate non-compliance.

### 16. Restricted-tier session rate above 3x the firm median
| | |
|---|---|
| **Condition** | `share of a lawyer's sessions on Restricted or Barrier matters, per quarter` |
| **Fires when** | value `>` 3 |
| **Severity** | Low |
| **Owner** | Risk & Compliance |

Sensitive-session exposure provides context for the review list. Assignment to sensitive matters can drive this rate without implying misconduct. The full-window Top 20 comparison in results/screening_comparison.csv shows why exposure alone is a weaker screen in this synthetic scenario. The catalogue renders those results separately from this quarterly alert threshold.

## Operational

Set at the point where a lawyer abandons the tool, or where a feed has stopped telling the truth.

### 1. p95 latency above 8s
| | |
|---|---|
| **Condition** | `p95 latency over trailing 24h` |
| **Fires when** | value `>` 8000 |
| **Severity** | High |
| **Owner** | IT Operations |

Past roughly eight seconds a lawyer stops waiting and goes back to doing it by hand. The threshold is set at the point of abandonment, not at a system limit.

### 2. Error rate above 5% over 24h
| | |
|---|---|
| **Condition** | `failed or timed-out sessions / all sessions` |
| **Fires when** | value `>` 0.05 |
| **Severity** | High |
| **Owner** | IT Operations |

Below 5% users retry and carry on; above it they stop trusting the tool, and trust is far slower to rebuild than uptime.

### 3. Feed loaded zero rows
| | |
|---|---|
| **Condition** | `rows_loaded for any source on any day` |
| **Fires when** | value `=` 0 |
| **Severity** | Critical |
| **Owner** | Data Engineering |

A silent zero-row load is indistinguishable on every other page from a genuine drop in usage. This is the only rule that catches it.

### 4. Feed stale beyond 36 hours
| | |
|---|---|
| **Condition** | `hours since last successful run` |
| **Fires when** | value `>` 36 |
| **Severity** | High |
| **Owner** | Data Engineering |

A dashboard built on a stale extract looks entirely healthy. Staleness is invisible everywhere except here.

### 5. Referential failures above 1% of a source
| | |
|---|---|
| **Condition** | `referential_failures / rows_loaded, 7-day` |
| **Fires when** | value `>` 0.01 |
| **Severity** | Medium |
| **Owner** | Data Engineering |

A chronic low-level mismatch never trips a daily threshold, yet every metric downstream is quietly understated. Trend, not status light.

## Quality

Aimed at the output rather than the platform: a tool can be fast, cheap and available while producing work nobody keeps.

### 6. Median edit distance above 45%
| | |
|---|---|
| **Condition** | `median edit_distance_pct by task type` |
| **Fires when** | value `>` 45 |
| **Severity** | Medium |
| **Owner** | AI Operations |

Beyond roughly half rewritten, the tool is costing more time than it saves for that task, whatever the acceptance rate says.

### 7. Acceptance rate falls 15pp month on month
| | |
|---|---|
| **Condition** | `change in acceptance rate` |
| **Fires when** | value `<` -0.15 |
| **Severity** | Medium |
| **Owner** | AI Operations |

A sharp drop usually means a model or prompt change reached production without anyone telling the people who rely on it.

## Cost

Unit economics, not spend. Spend rises with adoption and that is the plan working.

### 8. Cost per accepted output above $12
| | |
|---|---|
| **Condition** | `(licence + token cost) / accepted outputs` |
| **Fires when** | value `>` 12 |
| **Severity** | Medium |
| **Owner** | Finance |

Cost per call rewards a tool that is cheap and useless. Only cost per output a lawyer actually kept is a real unit cost, and it has to include licences: token spend is under 1% of what the firm pays. Counting tokens alone gives about 10 cents an output and makes the whole programme look free. The real figure is around $10.60, which is what the threshold is set against. Review time is still excluded, so this remains an understatement.

### 9. Idle seat share above 40% for a paid tool
| | |
|---|---|
| **Condition** | `seat-months with no session / seat-months, per tool per quarter` |
| **Fires when** | value `>` 0.4 |
| **Severity** | Medium |
| **Owner** | Finance |

Seats are bought at rollout and renewed annually; usage is checked, if at all, by someone looking at a session count. The gap between the two is money already spent. It is also the cheapest thing on this list to fix, because unlike adoption it needs no behaviour change from anyone -- just a reconciliation before renewal. The threshold is loose on purpose: some idle share is the cost of keeping a seat open for occasional need.

### 10. Monthly cost 30% above trailing 3-month mean
| | |
|---|---|
| **Condition** | `latest complete month (licence + token cost) / previous 3 calendar months mean - 1` |
| **Fires when** | value `>` 0.3 |
| **Severity** | Low |
| **Owner** | Finance |

Compares licence plus token cost in the latest complete calendar month with the previous three consecutive calendar months. Completeness uses the latest pipeline date, including non-working days. Missing months or a zero baseline are unevaluated, not healthy. Historical peaks do not keep the current alert open. Planned rollout or renewal changes still need Finance review.
