USE LegalAIObservatory;
GO

/* ==========================================================================
   vw_governance_exposure
   Page 5, aggregate half. How much AI activity sits on matters the firm has
   restricted, and whether the work that most needs review is getting it.
   Denominators travel with the rates: the Barrier row rests on a few hundred
   outputs and moves several points between periods.
   ========================================================================== */
CREATE OR ALTER VIEW dbo.vw_governance_exposure AS
WITH sess AS (
    SELECT d.[year] * 100 + d.[month] AS year_month, d.fy_year, d.fy_quarter,
           m.confidentiality_tier, m.practice_area, t.tool_name,
           s.session_id
    FROM dbo.fact_ai_session s
    JOIN dbo.dim_date   d ON d.date_id   = s.date_id
    JOIN dbo.dim_matter m ON m.matter_id = s.matter_id
    JOIN dbo.dim_tool   t ON t.tool_id   = s.tool_id
),
outp AS (
    SELECT d.[year] * 100 + d.[month] AS year_month,
           m.confidentiality_tier, m.practice_area, t.tool_name,
           p.effective_review_policy, o.reviewed
    FROM dbo.fact_ai_output o
    JOIN dbo.dim_date   d ON d.date_id   = o.date_id
    JOIN dbo.dim_matter m ON m.matter_id = o.matter_id
    JOIN dbo.dim_tool   t ON t.tool_id   = o.tool_id
    JOIN dbo.vw_effective_review_policy p ON p.output_id = o.output_id
)
SELECT
    s.year_month, s.fy_year, s.fy_quarter,
    s.confidentiality_tier, s.practice_area, s.tool_name,
    COUNT(*) AS sessions,
    CAST(COUNT(*) AS DECIMAL(12,6))
        / NULLIF(SUM(COUNT(*)) OVER (PARTITION BY s.year_month), 0) AS session_share,
    MAX(o.mandatory_outputs)  AS mandatory_outputs,
    MAX(o.mandatory_reviewed) AS mandatory_reviewed,
    CAST(MAX(o.mandatory_reviewed) AS DECIMAL(12,6))
        / NULLIF(MAX(o.mandatory_outputs), 0) AS review_coverage
FROM sess s
LEFT JOIN (
    SELECT year_month, confidentiality_tier, practice_area, tool_name,
           SUM(CASE WHEN effective_review_policy = 'Mandatory' THEN 1 ELSE 0 END)
               AS mandatory_outputs,
           SUM(CASE WHEN effective_review_policy = 'Mandatory' AND reviewed = 1
                    THEN 1 ELSE 0 END) AS mandatory_reviewed
    FROM outp
    GROUP BY year_month, confidentiality_tier, practice_area, tool_name
) o ON o.year_month           = s.year_month
   AND o.confidentiality_tier = s.confidentiality_tier
   AND o.practice_area        = s.practice_area
   AND o.tool_name            = s.tool_name
GROUP BY s.year_month, s.fy_year, s.fy_quarter,
         s.confidentiality_tier, s.practice_area, s.tool_name;
GO

/* ==========================================================================
   vw_governance_screen
   Page 5, actionable half. One row per lawyer per financial quarter.

   Ranked by mandatory review completion, not by exposure. Which signal to use
   was measured against held-out ground truth rather than assumed: completion
   recovers 11 of 16 non-compliant lawyers in a 20-name list at 55% precision,
   while exposure rate recovers 3 and combining the two is worse than
   completion alone. How much sensitive work a lawyer is staffed on dominates
   how much they choose to use AI on it, so exposure is context, not signal.
   See results/screening_comparison.csv.

   is_screenable is on the view because it is a real limit: a lawyer with too
   few outputs requiring review has no computable rate and cannot appear on any
   list, which caps recall at about 69% regardless of ranking.
   ========================================================================== */
CREATE OR ALTER VIEW dbo.vw_governance_screen AS
WITH q AS (
    SELECT s.lawyer_id, d.fy_year, d.fy_quarter,
           COUNT(*) AS sessions,
           SUM(CASE WHEN m.confidentiality_tier IN ('Restricted','Barrier')
                    THEN 1 ELSE 0 END) AS sensitive_sessions,
           COUNT(DISTINCT m.barrier_group_id) AS barrier_groups_touched
    FROM dbo.fact_ai_session s
    JOIN dbo.dim_date   d ON d.date_id   = s.date_id
    JOIN dbo.dim_matter m ON m.matter_id = s.matter_id
    GROUP BY s.lawyer_id, d.fy_year, d.fy_quarter
),
r AS (
    SELECT o.lawyer_id, d.fy_year, d.fy_quarter,
           SUM(CASE WHEN p.effective_review_policy = 'Mandatory' THEN 1 ELSE 0 END)
               AS mandatory_outputs,
           SUM(CASE WHEN p.effective_review_policy = 'Mandatory' AND o.reviewed = 1
                    THEN 1 ELSE 0 END) AS mandatory_reviewed
    FROM dbo.fact_ai_output o
    JOIN dbo.dim_date d ON d.date_id = o.date_id
    JOIN dbo.vw_effective_review_policy p ON p.output_id = o.output_id
    GROUP BY o.lawyer_id, d.fy_year, d.fy_quarter
)
SELECT
    q.lawyer_id, l.display_name, l.[level], l.practice_group, l.office,
    q.fy_year, q.fy_quarter,
    q.sessions, q.sensitive_sessions, q.barrier_groups_touched,
    CAST(q.sensitive_sessions AS DECIMAL(12,6)) / NULLIF(q.sessions, 0)
        AS sensitive_session_rate,
    ISNULL(r.mandatory_outputs, 0)  AS mandatory_outputs,
    ISNULL(r.mandatory_reviewed, 0) AS mandatory_reviewed,
    CAST(r.mandatory_reviewed AS DECIMAL(12,6))
        / NULLIF(r.mandatory_outputs, 0) AS review_completion,
    CAST(CASE WHEN ISNULL(r.mandatory_outputs, 0) >= 25 THEN 1 ELSE 0 END AS BIT)
        AS is_screenable
FROM      q
JOIN      dbo.dim_lawyer l ON l.lawyer_id = q.lawyer_id
LEFT JOIN r ON r.lawyer_id = q.lawyer_id
           AND r.fy_year   = q.fy_year
           AND r.fy_quarter = q.fy_quarter;
GO

/* ==========================================================================
   vw_pipeline_health
   The Page 1 health strip.

   "rows_loaded = 0" on its own is not a failure signal. Every source loads
   nothing at weekends, and Harvey loads nothing before it was deployed in
   August 2025 -- a naive zero-row rule fires on more than 500 days here and
   would be switched off within a week. A zero load is only an anomaly on a day
   the source was expected to be carrying traffic, which is what
   is_unexpected_zero_load encodes. Getting this wrong is the ordinary way a
   monitoring page becomes noise.
   ========================================================================== */
CREATE OR ALTER VIEW dbo.vw_pipeline_health AS
WITH live AS (
    SELECT t.source_system, MIN(t.deployment_date) AS live_from
    FROM (SELECT tool_name, deployment_date,
                 CASE tool_name WHEN 'Copilot'   THEN 'm365_audit'
                                WHEN 'Firm Chat' THEN 'firm_chat_log'
                                ELSE 'harvey_api' END AS source_system
          FROM dbo.dim_tool) t
    GROUP BY t.source_system
),
r AS (
    SELECT
        p.run_id, p.source_system, p.run_date, p.date_id, p.rows_loaded,
        p.rows_rejected, p.referential_failures, p.null_rate_key_fields,
        p.run_status, p.duration_seconds,
        d.is_business_day,
        CAST(CASE WHEN d.is_business_day = 1 AND p.run_date >= v.live_from
                  THEN 1 ELSE 0 END AS BIT) AS is_expected_active,
        AVG(CAST(p.rows_loaded AS FLOAT)) OVER (
            PARTITION BY p.source_system ORDER BY p.run_date
            ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING)            AS trailing_30d_mean,
        MAX(CASE WHEN p.rows_loaded > 0 THEN p.run_date END) OVER (
            PARTITION BY p.source_system ORDER BY p.run_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)     AS last_good_run,
        MAX(p.run_date) OVER ()                                   AS as_at
    FROM dbo.fact_pipeline_run p
    JOIN dbo.dim_date d ON d.date_id = p.date_id
    JOIN live         v ON v.source_system = p.source_system
)
SELECT
    r.*,
    CAST(r.referential_failures AS DECIMAL(12,6))
        / NULLIF(r.rows_loaded + r.rows_rejected, 0)        AS referential_failure_rate,
    CAST(r.rows_loaded AS FLOAT) / NULLIF(r.trailing_30d_mean, 0)
                                                            AS load_vs_trailing_mean,
    DATEDIFF(HOUR, r.last_good_run, r.as_at)                AS hours_since_good_run,
    CAST(CASE WHEN r.rows_loaded = 0 AND r.is_expected_active = 1
                   AND r.trailing_30d_mean >= 20
              THEN 1 ELSE 0 END AS BIT)                     AS is_unexpected_zero_load
FROM r;
GO
