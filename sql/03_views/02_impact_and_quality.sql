USE LegalAIObservatory;
GO

/* ==========================================================================
   vw_billable_impact
   Page 3. Grain: lawyer x matter x month.

   The effect size itself is not computed here -- it is a fixed-effects
   regression, which no view can express -- it is read from
   fact_billable_impact_estimate and applied.

   Algebra: observed hours are baseline x (1 + e) where e is the (negative)
   effect, so the hours the AI removed are observed x -e / (1 + e). Valuing
   those at the lawyer's charge-out rate gives notional capacity released.
   Hourly revenue exposure uses billable hours only. It is a scenario at list
   rates, not realised lost revenue; total-hours capacity remains separate.
   ========================================================================== */
CREATE OR ALTER VIEW dbo.vw_billable_impact AS
WITH ai_use AS (
    SELECT lawyer_id, matter_id,
           COUNT(*)        AS ai_sessions,
           MIN(date_id)    AS first_session_date_id
    FROM   dbo.fact_ai_session
    GROUP BY lawyer_id, matter_id
    HAVING COUNT(*) >= 3
),
entries AS (
    SELECT
        te.lawyer_id, te.matter_id,
        d.[year] * 100 + d.[month]              AS year_month,
        d.fy_year,
        m.fee_arrangement, m.practice_area, m.confidentiality_tier,
        l.[level], l.practice_group, l.office, l.standard_charge_rate_aud,
        te.hours,
        CASE WHEN te.billable = 1 THEN te.hours ELSE 0 END AS billable_hours,
        a.ai_sessions,
        te.is_post_adoption
    FROM      dbo.fact_time_entry te
    JOIN      dbo.dim_date   d ON d.date_id   = te.date_id
    JOIN      dbo.dim_matter m ON m.matter_id = te.matter_id
    JOIN      dbo.dim_lawyer l ON l.lawyer_id = te.lawyer_id
    LEFT JOIN ai_use         a ON a.lawyer_id = te.lawyer_id
                              AND a.matter_id = te.matter_id
)
SELECT
    e.lawyer_id, e.matter_id, e.year_month, e.fy_year,
    e.fee_arrangement, e.practice_area, e.confidentiality_tier,
    e.[level], e.practice_group, e.office,
    e.standard_charge_rate_aud,
    SUM(e.hours)                                    AS hours,
    SUM(e.billable_hours)                           AS billable_hours,
    MAX(ISNULL(e.ai_sessions, 0))                   AS ai_sessions,
    SUM(CASE WHEN e.is_post_adoption = 1 THEN e.hours ELSE 0 END)
                                                    AS post_adoption_hours,
    CAST(MAX(CASE WHEN e.ai_sessions IS NOT NULL THEN 1 ELSE 0 END) AS BIT)
                                                    AS is_ai_assisted,
    /* hours removed, implied by the estimated effect for this fee arrangement */
    SUM(CASE WHEN e.is_post_adoption = 1 THEN e.hours ELSE 0 END)
        * (-est.estimate_pct / 100.0) / (1 + est.estimate_pct / 100.0)
                                                    AS implied_hours_released,
    SUM(CASE WHEN e.is_post_adoption = 1 THEN e.hours ELSE 0 END)
        * (-est.estimate_pct / 100.0) / (1 + est.estimate_pct / 100.0)
        * e.standard_charge_rate_aud                AS notional_capacity_released_aud,
    est.estimate_pct, est.ci_low_pct, est.ci_high_pct,
    SUM(CASE WHEN e.is_post_adoption = 1 THEN e.billable_hours ELSE 0 END)
                                                    AS post_adoption_billable_hours,
    CASE WHEN e.fee_arrangement = 'Hourly' THEN
          CAST(SUM(CASE WHEN e.is_post_adoption = 1 THEN e.billable_hours ELSE 0 END)
               AS DECIMAL(18,2))
          * (-est.estimate_pct / (100 + est.estimate_pct))
          * e.standard_charge_rate_aud END           AS hourly_revenue_exposure_aud
FROM dbo.fact_billable_impact_estimate est
JOIN entries e ON e.fee_arrangement = est.fee_arrangement
GROUP BY
    e.lawyer_id, e.matter_id, e.year_month, e.fy_year,
    e.fee_arrangement, e.practice_area, e.confidentiality_tier,
    e.[level], e.practice_group, e.office, e.standard_charge_rate_aud,
    est.estimate_pct, est.ci_low_pct, est.ci_high_pct;
GO

/* ==========================================================================
   vw_quality_observability
   Page 4. Grain: month x tool x task type x practice area.
   Cost is per accepted output, not per call: cost per call rewards a tool that
   is cheap and useless.
   ========================================================================== */
CREATE OR ALTER VIEW dbo.vw_quality_observability AS
WITH base AS (
    SELECT
        d.[year] * 100 + d.[month] AS year_month,
        t.tool_name, tt.task_name, tt.review_type, m.practice_area,
        s.session_id, s.latency_ms, s.status, s.retry_count, s.cost_aud,
        o.accepted, o.edit_distance_pct, o.reviewed,
        p.effective_review_policy
    FROM      dbo.fact_ai_session s
    JOIN      dbo.dim_date        d  ON d.date_id      = s.date_id
    JOIN      dbo.dim_tool        t  ON t.tool_id      = s.tool_id
    JOIN      dbo.dim_task_type   tt ON tt.task_type_id = s.task_type_id
    JOIN      dbo.dim_matter      m  ON m.matter_id    = s.matter_id
    LEFT JOIN dbo.fact_ai_output  o  ON o.session_id   = s.session_id
    LEFT JOIN dbo.vw_effective_review_policy p ON p.output_id = o.output_id
),
pct AS (
    SELECT DISTINCT year_month, tool_name, task_name, practice_area,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY CAST(latency_ms AS FLOAT))
               OVER (PARTITION BY year_month, tool_name, task_name, practice_area)
               AS p95_latency_ms,
           PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY CAST(edit_distance_pct AS FLOAT))
               OVER (PARTITION BY year_month, tool_name, task_name, practice_area)
               AS median_edit_distance_pct
    FROM base
),
agg AS (
    SELECT
        year_month, tool_name, task_name, review_type, practice_area,
        COUNT(*)                                                  AS sessions,
        SUM(CASE WHEN status <> 'Success' THEN 1 ELSE 0 END)      AS failed_sessions,
        SUM(CASE WHEN retry_count > 0 THEN 1 ELSE 0 END)          AS retried_sessions,
        SUM(cost_aud)                                             AS cost_aud,
        COUNT(accepted)                                           AS outputs,
        SUM(CAST(accepted AS INT))                                AS accepted_outputs,
        SUM(CASE WHEN effective_review_policy = 'Mandatory' THEN 1 ELSE 0 END)
                                                                  AS mandatory_outputs,
        SUM(CASE WHEN effective_review_policy = 'Mandatory' AND reviewed = 1
                 THEN 1 ELSE 0 END)                               AS mandatory_reviewed,
        SUM(CASE WHEN effective_review_policy = 'Mandatory' AND reviewed = 0
                 THEN 1 ELSE 0 END)                               AS review_gap
    FROM base
    GROUP BY year_month, tool_name, task_name, review_type, practice_area
)
SELECT
    a.*,
    p.p95_latency_ms,
    p.median_edit_distance_pct,
    CAST(a.failed_sessions AS DECIMAL(12,6)) / NULLIF(a.sessions, 0)    AS error_rate,
    CAST(a.accepted_outputs AS DECIMAL(12,6)) / NULLIF(a.outputs, 0)    AS acceptance_rate,
    CAST(a.mandatory_reviewed AS DECIMAL(12,6))
        / NULLIF(a.mandatory_outputs, 0)                                AS review_coverage,
    a.cost_aud / NULLIF(a.accepted_outputs, 0)                          AS cost_per_accepted_output
FROM agg a
JOIN pct p ON p.year_month    = a.year_month
          AND p.tool_name     = a.tool_name
          AND p.task_name     = a.task_name
          AND p.practice_area = a.practice_area;
GO

/* ==========================================================================
   vw_cost_efficiency
   Page 4, cost half. Grain: month x tool.

   Token spend is under 1% of what these tools cost. A cost page built from the
   session table alone reports a few thousand dollars over eighteen months and
   invites the conclusion that the programme is free. What the firm actually
   buys is seats, and the seats nobody opened are money already spent -- the
   cheapest problem on the dashboard to fix, because unlike adoption it needs no
   behaviour change from anyone, only a reconciliation before renewal.
   ========================================================================== */
CREATE OR ALTER VIEW dbo.vw_cost_efficiency AS
WITH seats AS (
    SELECT d.[year] * 100 + d.[month] AS year_month, d.fy_year, sm.tool_id,
           SUM(sm.seats)                                            AS seats,
           SUM(sm.licence_cost_aud)                                 AS licence_cost_aud,
           SUM(CASE WHEN sm.used_in_month = 0 THEN sm.licence_cost_aud ELSE 0 END)
                                                                    AS idle_licence_cost_aud,
           SUM(CASE WHEN sm.used_in_month = 0 THEN 1 ELSE 0 END)    AS idle_seats
    FROM dbo.fact_seat_month sm
    JOIN dbo.dim_date d ON d.date_id = sm.date_id
    GROUP BY d.[year] * 100 + d.[month], d.fy_year, sm.tool_id
),
usage AS (
    SELECT d.[year] * 100 + d.[month] AS year_month, s.tool_id,
           COUNT(*)                                     AS sessions,
           SUM(s.cost_aud)                              AS token_cost_aud,
           SUM(CASE WHEN o.accepted = 1 THEN 1 ELSE 0 END) AS accepted_outputs
    FROM      dbo.fact_ai_session s
    JOIN      dbo.dim_date       d ON d.date_id    = s.date_id
    LEFT JOIN dbo.fact_ai_output o ON o.session_id = s.session_id
    GROUP BY d.[year] * 100 + d.[month], s.tool_id
)
SELECT
    se.year_month, se.fy_year, t.tool_name,
    se.seats, se.idle_seats,
    CAST(se.idle_seats AS DECIMAL(12,6)) / NULLIF(se.seats, 0)   AS idle_seat_share,
    se.licence_cost_aud,
    se.idle_licence_cost_aud,
    ISNULL(u.token_cost_aud, 0)                                  AS token_cost_aud,
    se.licence_cost_aud + ISNULL(u.token_cost_aud, 0)            AS total_cost_aud,
    ISNULL(u.sessions, 0)                                        AS sessions,
    ISNULL(u.accepted_outputs, 0)                                AS accepted_outputs,
    (se.licence_cost_aud + ISNULL(u.token_cost_aud, 0))
        / NULLIF(u.accepted_outputs, 0)                          AS cost_per_accepted_output,
    ISNULL(u.token_cost_aud, 0)
        / NULLIF(se.licence_cost_aud + ISNULL(u.token_cost_aud, 0), 0)
                                                                 AS token_share_of_cost
FROM      seats        se
JOIN      dbo.dim_tool t ON t.tool_id = se.tool_id
LEFT JOIN usage        u ON u.year_month = se.year_month AND u.tool_id = se.tool_id;
GO
