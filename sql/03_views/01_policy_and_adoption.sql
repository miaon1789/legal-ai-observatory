USE LegalAIObservatory;
GO

/* ==========================================================================
   vw_effective_review_policy
   Review policy is a rule, not an attribute. It depends on the task type, the
   confidentiality tier of the matter and whether the output leaves the firm,
   so it is computed here rather than stored on a dimension -- the rule can
   change without reloading anything.
   ========================================================================== */
CREATE OR ALTER VIEW dbo.vw_effective_review_policy AS
SELECT
    o.output_id,
    o.session_id,
    t.review_type,
    t.review_policy_base,
    CASE
        WHEN t.review_policy_base = 'Mandatory'                 THEN 'Mandatory'
        WHEN m.confidentiality_tier IN ('Restricted','Barrier') THEN 'Mandatory'
        WHEN o.is_client_facing = 1                             THEN 'Mandatory'
        ELSE 'Optional'
    END AS effective_review_policy,
    CASE
        WHEN t.review_policy_base = 'Mandatory'                 THEN 'Task type'
        WHEN m.confidentiality_tier IN ('Restricted','Barrier') THEN 'Confidentiality tier'
        WHEN o.is_client_facing = 1                             THEN 'Client-facing output'
        ELSE 'Not required'
    END AS policy_driver
FROM dbo.fact_ai_output  o
JOIN dbo.dim_task_type   t ON t.task_type_id = o.task_type_id
JOIN dbo.dim_matter      m ON m.matter_id    = o.matter_id;
GO

/* ==========================================================================
   vw_ai_output_enriched
   What Power BI imports in place of fact_ai_output, so that no measure can
   reach the acceptance and review facts while bypassing the policy rule above.
   ========================================================================== */
CREATE OR ALTER VIEW dbo.vw_ai_output_enriched AS
SELECT
    o.output_id, o.session_id, o.lawyer_id, o.matter_id, o.tool_id,
    o.task_type_id, o.date_id,
    o.accepted, o.edit_distance_pct, o.is_client_facing,
    o.reviewed, o.reviewed_by_level, o.minutes_to_review,
    p.effective_review_policy,
    p.policy_driver,
    p.review_type,
    CAST(CASE WHEN p.effective_review_policy = 'Mandatory' AND o.reviewed = 0
              THEN 1 ELSE 0 END AS BIT) AS is_review_gap
FROM dbo.fact_ai_output           o
JOIN dbo.vw_effective_review_policy p ON p.output_id = o.output_id;
GO

/* ==========================================================================
   vw_adoption_by_group
   Adoption rate, stratified. The denominator is the whole population of the
   group, built from dim_lawyer crossed with the calendar -- never from the
   session table, or a group with no users would silently disappear instead of
   reporting zero.
   ========================================================================== */
CREATE OR ALTER VIEW dbo.vw_adoption_by_group AS
WITH months AS (
    SELECT DISTINCT fy_year, [year] * 100 + [month] AS year_month,
           MIN([date]) OVER (PARTITION BY [year], [month]) AS month_start
    FROM dbo.dim_date
    WHERE [date] BETWEEN (SELECT MIN(d.[date]) FROM dbo.dim_date d
                          JOIN dbo.fact_ai_session s ON s.date_id = d.date_id)
                     AND (SELECT MAX(d.[date]) FROM dbo.dim_date d
                          JOIN dbo.fact_ai_session s ON s.date_id = d.date_id)
),
population AS (
    SELECT [level], practice_group, office, COUNT(*) AS lawyers
    FROM   dbo.dim_lawyer
    WHERE  is_active = 1
    GROUP BY [level], practice_group, office
),
active AS (
    SELECT d.[year] * 100 + d.[month] AS year_month,
           l.[level], l.practice_group, l.office,
           COUNT(DISTINCT s.lawyer_id) AS active_lawyers,
           COUNT(*)                    AS sessions
    FROM   dbo.fact_ai_session s
    JOIN   dbo.dim_date   d ON d.date_id   = s.date_id
    JOIN   dbo.dim_lawyer l ON l.lawyer_id = s.lawyer_id
    GROUP BY d.[year] * 100 + d.[month], l.[level], l.practice_group, l.office
)
SELECT
    m.year_month,
    m.month_start,
    p.[level],
    p.practice_group,
    p.office,
    p.lawyers,
    ISNULL(a.active_lawyers, 0) AS active_lawyers,
    ISNULL(a.sessions, 0)       AS sessions,
    CAST(ISNULL(a.active_lawyers, 0) AS DECIMAL(9,4)) / NULLIF(p.lawyers, 0)
        AS adoption_rate,
    CAST(ISNULL(a.sessions, 0) AS DECIMAL(12,4))
        / NULLIF(a.active_lawyers, 0) AS sessions_per_active_lawyer
FROM       months     m
CROSS JOIN population p
LEFT JOIN  active     a
       ON  a.year_month     = m.year_month
      AND  a.[level]        = p.[level]
      AND  a.practice_group = p.practice_group
      AND  a.office         = p.office;
GO
