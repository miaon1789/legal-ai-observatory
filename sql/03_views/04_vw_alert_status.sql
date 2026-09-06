USE LegalAIObservatory;
GO

/* ==========================================================================
   vw_alert_status
   Evaluates every rule in dim_alert_rule against the current data.

   Each branch computes a current_value chosen so that the rule's own
   threshold_value and comparison, as stored in the dimension, decide the
   breach. The operator is therefore data, not code: changing a threshold is a
   row update, and nobody has to find the SQL that hard-coded it.

   Governance thresholds are tighter than operational ones by design. The
   rationale column carries the reason, and it travels with the alert.
   ========================================================================== */
CREATE OR ALTER VIEW dbo.vw_alert_status AS
WITH asof AS (
    SELECT MAX(d.[date]) AS as_at, MAX(d.date_id) AS as_at_id
    FROM dbo.fact_ai_session s JOIN dbo.dim_date d ON d.date_id = s.date_id
),
monthly AS (
    SELECT d.[year] * 100 + d.[month] AS year_month,
           SUM(s.cost_aud)            AS cost_aud,
           SUM(CAST(o.accepted AS INT)) AS accepted,
           COUNT(o.output_id)         AS outputs
    FROM      dbo.fact_ai_session s
    JOIN      dbo.dim_date        d ON d.date_id    = s.date_id
    LEFT JOIN dbo.fact_ai_output  o ON o.session_id = s.session_id
    GROUP BY d.[year] * 100 + d.[month]
),
cost_window AS (
    /* Pipeline dates include non-working days; a last session need not fall
       on month end. This is the extract watermark, not the wall clock. */
    SELECT EOMONTH(MAX(run_date),
                   CASE WHEN MAX(run_date) = EOMONTH(MAX(run_date))
                        THEN 0 ELSE -1 END) AS month_end
    FROM dbo.fact_pipeline_run
),
cost_components AS (
    SELECT EOMONTH(d.[date]) AS month_end, SUM(s.cost_aud) AS cost_aud
    FROM dbo.fact_ai_session s
    JOIN dbo.dim_date d ON d.date_id = s.date_id
    GROUP BY EOMONTH(d.[date])
    UNION ALL
    SELECT EOMONTH(d.[date]), SUM(sm.licence_cost_aud)
    FROM dbo.fact_seat_month sm
    JOIN dbo.dim_date d ON d.date_id = sm.date_id
    GROUP BY EOMONTH(d.[date])
),
monthly_total_cost AS (
    SELECT month_end, SUM(cost_aud) AS total_cost_aud
    FROM cost_components GROUP BY month_end
),
cost_change AS (
    /* Four distinct calendar months are required. Missing months and a zero
       baseline are unknown, rather than a zero change or a shorter baseline. */
    SELECT CASE WHEN COUNT(*) = 4 THEN
               3 * CAST(SUM(CASE WHEN c.month_end = w.month_end
                                 THEN c.total_cost_aud END) AS DECIMAL(24,6))
               / NULLIF(CAST(SUM(CASE WHEN c.month_end < w.month_end
                                      THEN c.total_cost_aud END) AS DECIMAL(24,6)), 0) - 1
           END AS current_value
    FROM monthly_total_cost c CROSS JOIN cost_window w
    WHERE c.month_end BETWEEN EOMONTH(w.month_end, -3) AND w.month_end
),
evaluated AS (
    /* 1 - p95 latency, trailing 24h, worst tool */
    SELECT 'p95 latency above 8s' AS rule_name,
           MAX(p95) AS current_value, COUNT(*) AS affected
    FROM (SELECT DISTINCT s.tool_id,
                 PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY CAST(s.latency_ms AS FLOAT))
                     OVER (PARTITION BY s.tool_id) AS p95
          FROM dbo.fact_ai_session s, asof a
          WHERE s.date_id = a.as_at_id AND s.status = 'Success') x

    UNION ALL
    /* 2 - error rate over the last 24h */
    SELECT 'Error rate above 5% over 24h', CAST(SUM(CASE WHEN s.status <> 'Success' THEN 1 ELSE 0 END) AS DECIMAL(12,6))
                 / NULLIF(COUNT(*), 0),
           SUM(CASE WHEN s.status <> 'Success' THEN 1 ELSE 0 END)
    FROM dbo.fact_ai_session s, asof a WHERE s.date_id = a.as_at_id

    UNION ALL
    /* 3 - a feed loaded nothing on a day it was expected to be active */
    SELECT 'Feed loaded zero rows', MIN(CAST(h.rows_loaded AS DECIMAL(12,4))),
           SUM(CAST(h.is_unexpected_zero_load AS INT))
    FROM dbo.vw_pipeline_health h, asof a
    WHERE h.is_expected_active = 1 AND h.run_date > DATEADD(DAY, -30, a.as_at)

    UNION ALL
    /* 4 - staleness, worst source */
    SELECT 'Feed stale beyond 36 hours', MAX(CAST(h.hours_since_good_run AS DECIMAL(12,4))), COUNT(DISTINCT h.source_system)
    FROM dbo.vw_pipeline_health h, asof a WHERE h.run_date = a.as_at

    UNION ALL
    /* 5 - chronic referential failure, 7-day rate, worst source */
    SELECT 'Referential failures above 1% of a source', MAX(rate), COUNT(*)
    FROM (SELECT h.source_system,
                 CAST(SUM(h.referential_failures) AS DECIMAL(12,6))
                     / NULLIF(SUM(h.rows_loaded + h.rows_rejected), 0) AS rate
          FROM dbo.vw_pipeline_health h, asof a
          WHERE h.run_date > DATEADD(DAY, -7, a.as_at)
          GROUP BY h.source_system) x

    UNION ALL
    /* 6 - median edit distance by task type, latest month */
    SELECT 'Median edit distance above 45%', MAX(med), SUM(CASE WHEN med > 45 THEN 1 ELSE 0 END)
    FROM (SELECT DISTINCT o.task_type_id,
                 PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY CAST(o.edit_distance_pct AS FLOAT))
                     OVER (PARTITION BY o.task_type_id) AS med
          FROM dbo.fact_ai_output o
          JOIN dbo.dim_date d ON d.date_id = o.date_id, asof a
          WHERE d.[date] > DATEADD(DAY, -30, a.as_at)) x

    UNION ALL
    /* 7 - month-on-month fall in acceptance rate */
    SELECT 'Acceptance rate falls 15pp month on month', MIN(delta), SUM(CASE WHEN delta < -0.15 THEN 1 ELSE 0 END)
    FROM (SELECT CAST(accepted AS DECIMAL(12,6)) / NULLIF(outputs, 0)
                 - LAG(CAST(accepted AS DECIMAL(12,6)) / NULLIF(outputs, 0))
                       OVER (ORDER BY year_month) AS delta
          FROM monthly) x

    UNION ALL
    /* 8 - cost per accepted output: licence + token, not token alone.
           Counting tokens only gives about 10 cents and makes the programme
           look free; licences are 99% of what the firm actually pays. */
    SELECT 'Cost per accepted output above $12',
           CAST(((SELECT SUM(licence_cost_aud) FROM dbo.fact_seat_month)
                + (SELECT SUM(cost_aud) FROM dbo.fact_ai_session))
                / NULLIF((SELECT SUM(CAST(accepted AS INT))
                          FROM dbo.fact_ai_output), 0) AS DECIMAL(12,4)),
           (SELECT SUM(CAST(accepted AS INT)) FROM dbo.fact_ai_output)

    UNION ALL
    /* 9 - seats paid for and not opened, worst tool over the last quarter */
    SELECT 'Idle seat share above 40% for a paid tool', MAX(idle), COUNT(*)
    FROM (SELECT sm.tool_id,
                 AVG(CASE WHEN sm.used_in_month = 0 THEN 1.0 ELSE 0.0 END) AS idle
          FROM dbo.fact_seat_month sm
          JOIN dbo.dim_date d ON d.date_id = sm.date_id, asof a
          WHERE d.[date] > DATEADD(MONTH, -3, a.as_at)
          GROUP BY sm.tool_id) z

    UNION ALL
    /* 10 - latest complete month's total cost against the previous 3 months */
    SELECT 'Monthly cost 30% above trailing 3-month mean', current_value,
           CASE WHEN current_value IS NOT NULL THEN 1 END
    FROM cost_change

    UNION ALL
    /* 10 - review coverage on Barrier matters */
    SELECT 'Review coverage below 95% on Barrier matters', CAST(SUM(CASE WHEN o.reviewed = 1 THEN 1 ELSE 0 END) AS DECIMAL(12,6))
                  / NULLIF(COUNT(*), 0), COUNT(*)
    FROM dbo.vw_ai_output_enriched o
    JOIN dbo.dim_matter m ON m.matter_id = o.matter_id
    WHERE m.confidentiality_tier = 'Barrier' AND o.effective_review_policy = 'Mandatory'

    UNION ALL
    /* 11 - unreviewed output where review is mandatory. A count, not a rate:
            the acceptable number is zero, and a rate invites a tolerance. */
    SELECT 'Any unreviewed output where review is Mandatory', CAST(COUNT(*) AS DECIMAL(12,4)), COUNT(*)
    FROM dbo.vw_ai_output_enriched o
    JOIN dbo.dim_date d ON d.date_id = o.date_id, asof a
    WHERE o.is_review_gap = 1 AND d.[date] > DATEADD(DAY, -30, a.as_at)

    UNION ALL
    /* 12 - a newly deployed tool reaching a restricted matter */
    SELECT 'First use of a newly deployed tool on a Restricted matter', CAST(COUNT(*) AS DECIMAL(12,4)), COUNT(*)
    FROM (SELECT s.tool_id, s.lawyer_id, MIN(s.date_id) AS first_use
          FROM dbo.fact_ai_session s
          JOIN dbo.dim_matter m ON m.matter_id = s.matter_id
          JOIN dbo.dim_tool   t ON t.tool_id   = s.tool_id
          JOIN dbo.dim_date   d ON d.date_id   = s.date_id
          WHERE m.confidentiality_tier IN ('Restricted','Barrier')
            AND d.[date] <= DATEADD(DAY, 90, t.deployment_date)
          GROUP BY s.tool_id, s.lawyer_id) x

    UNION ALL
    /* 13 - one lawyer active on both sides of an information barrier */
    SELECT 'Lawyer active across two barrier groups in 90 days', MAX(CAST(groups AS DECIMAL(12,4))), COUNT(*)
    FROM (SELECT s.lawyer_id, d.fy_year, d.fy_quarter,
                 COUNT(DISTINCT m.barrier_group_id) AS groups
          FROM dbo.fact_ai_session s
          JOIN dbo.dim_matter m ON m.matter_id = s.matter_id
          JOIN dbo.dim_date   d ON d.date_id   = s.date_id
          WHERE m.barrier_group_id IS NOT NULL
          GROUP BY s.lawyer_id, d.fy_year, d.fy_quarter
          HAVING COUNT(DISTINCT m.barrier_group_id) >= 2) x

    UNION ALL
    /* 14 - the screen that works: per-lawyer mandatory review completion */
    SELECT 'Lawyer mandatory review completion below 70%', MIN(review_completion),
           SUM(CASE WHEN review_completion < 0.70 THEN 1 ELSE 0 END)
    FROM dbo.vw_governance_screen WHERE is_screenable = 1

    UNION ALL
    /* 15 - exposure rate, kept as context. Scored so that nobody rebuilds it
            as a screen: it runs at 15% precision against 55% for rule 14. */
    SELECT 'Restricted-tier session rate above 3x the firm median', MAX(rate / NULLIF(med, 0)),
           SUM(CASE WHEN rate / NULLIF(med, 0) > 3 THEN 1 ELSE 0 END)
    FROM (SELECT g.lawyer_id,
                 CAST(SUM(g.sensitive_sessions) AS DECIMAL(12,6))
                     / NULLIF(SUM(g.sessions), 0) AS rate,
                 (SELECT DISTINCT PERCENTILE_CONT(0.5)
                         WITHIN GROUP (ORDER BY x.r) OVER ()
                  FROM (SELECT CAST(SUM(sensitive_sessions) AS FLOAT)
                               / NULLIF(SUM(sessions), 0) AS r
                        FROM dbo.vw_governance_screen GROUP BY lawyer_id) x) AS med
          FROM dbo.vw_governance_screen g GROUP BY g.lawyer_id) y
)
SELECT
    r.rule_id, r.rule_name, r.category, r.severity, r.owner_role,
    r.condition_text, r.threshold_value, r.comparison,
    CAST(e.current_value AS DECIMAL(14,4)) AS current_value,
    CASE WHEN r.rule_name = 'Monthly cost 30% above trailing 3-month mean'
         THEN CAST(b.is_breached AS INT)
         ELSE e.affected END AS affected_count,
    b.is_breached,
    r.rationale
FROM      dbo.dim_alert_rule r
LEFT JOIN evaluated        e ON e.rule_name = r.rule_name
CROSS APPLY (
    SELECT CAST(CASE WHEN e.current_value IS NULL THEN NULL ELSE CASE r.comparison
             WHEN '>'  THEN CASE WHEN e.current_value >  r.threshold_value THEN 1 ELSE 0 END
             WHEN '>=' THEN CASE WHEN e.current_value >= r.threshold_value THEN 1 ELSE 0 END
             WHEN '<'  THEN CASE WHEN e.current_value <  r.threshold_value THEN 1 ELSE 0 END
             WHEN '='  THEN CASE WHEN e.current_value =  r.threshold_value THEN 1 ELSE 0 END
             ELSE 0 END END AS BIT) AS is_breached
) b;
/*  Joined on the name, not the id. Inserting a rule renumbers every rule after
    it, and a view keyed on position would then evaluate each one against the
    next rule's threshold -- silently, with every number still looking
    plausible. A LEFT JOIN also means a rule nobody has implemented shows up
    with a NULL current value rather than vanishing from the list.            */
GO
