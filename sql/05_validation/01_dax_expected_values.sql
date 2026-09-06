/*  Expected values for every DAX measure in docs/DAX_MEASURES.md.

    Each DAX measure has to reproduce the number this script returns for the
    same scope. Anything that does not match is wrong in the model, not in the
    database -- and the usual cause is a ratio written as a calculated column,
    or a denominator that the fact table has quietly filtered.

    Run:  docker exec -i mssql-legalai /opt/mssql-tools18/bin/sqlcmd -S localhost \
              -U sa -P "$MSSQL_SA_PASSWORD" -C -d LegalAIObservatory \
              -i sql/05_validation/01_dax_expected_values.sql

    MEMORY. The percentile branches (p95 latency, median edit distance) ask for
    a ~175 MB query workspace grant. On a 2 GB container that is only available
    when the host has room, so with a Windows VM up alongside Docker the script
    parks on RESOURCE_SEMAPHORE instead of failing, and looks like it has hung.
    Two consequences worth knowing:

      - Shut the VM, or run the branch you actually need on its own. Every
        branch here is a standalone SELECT and returns in seconds.
      - Do not launch a second copy while the first is waiting. Each one
        reserves its own grant and they starve each other; a killed sqlcmd
        client does not kill the query it started.

    Diagnose with:
      SELECT session_id, status, wait_type FROM sys.dm_exec_requests
      WHERE session_id > 50;                                                   */

USE LegalAIObservatory;
GO
SET NOCOUNT ON;

WITH plateau AS (
    SELECT s.lawyer_id, d.[year] * 100 + d.[month] AS ym
    FROM dbo.fact_ai_session s JOIN dbo.dim_date d ON d.date_id = s.date_id
    WHERE d.[year] * 100 + d.[month] >= 202512
),
pop AS (SELECT COUNT(*) AS n FROM dbo.dim_lawyer WHERE is_active = 1),
tool_cost AS (   -- licence + token per tool, the same basis as the corrected measure
    SELECT t.tool_name,
           (MAX(lic.licence) + SUM(s.cost_aud))
               / NULLIF(SUM(CAST(o.accepted AS INT)), 0) AS cpa
    FROM      dbo.fact_ai_session s
    JOIN      dbo.dim_tool        t ON t.tool_id    = s.tool_id
    LEFT JOIN dbo.fact_ai_output  o ON o.session_id = s.session_id
    JOIN      (SELECT tool_id, SUM(licence_cost_aud) AS licence
               FROM dbo.fact_seat_month GROUP BY tool_id) lic ON lic.tool_id = s.tool_id
    GROUP BY t.tool_name
)
SELECT * FROM (

SELECT 1 AS ord, 'Lawyer Population' AS measure, 'active lawyers' AS scope,
       CAST(n AS DECIMAL(18,4)) AS expected FROM pop
UNION ALL SELECT 2, 'Active Lawyers', 'mean over plateau months',
       CAST(AVG(CAST(c AS FLOAT)) AS DECIMAL(18,4))
FROM (SELECT ym, COUNT(DISTINCT lawyer_id) AS c FROM plateau GROUP BY ym) x
UNION ALL SELECT 3, 'Adoption Rate', 'firm-wide, plateau months',
       CAST(AVG(CAST(c AS FLOAT)) / (SELECT n FROM pop) AS DECIMAL(18,4))
FROM (SELECT ym, COUNT(DISTINCT lawyer_id) AS c FROM plateau GROUP BY ym) y

UNION ALL SELECT 4, 'Adoption Rate', 'level = ' + l.[level],
       CAST(AVG(CAST(x.c AS FLOAT)) / MAX(x.pop) AS DECIMAL(18,4))
FROM (SELECT p.ym, dl.[level], COUNT(DISTINCT p.lawyer_id) AS c,
             (SELECT COUNT(*) FROM dbo.dim_lawyer z
              WHERE z.is_active = 1 AND z.[level] = dl.[level]) AS pop
      FROM plateau p JOIN dbo.dim_lawyer dl ON dl.lawyer_id = p.lawyer_id
      GROUP BY p.ym, dl.[level]) x
JOIN dbo.dim_lawyer l ON l.[level] = x.[level]
GROUP BY l.[level]

UNION ALL SELECT 5, 'Total Cost AUD', 'all time',
       CAST(SUM(cost_aud) AS DECIMAL(18,4)) FROM dbo.fact_ai_session
UNION ALL SELECT 6, 'Accepted Outputs', 'all time',
       CAST(SUM(CAST(accepted AS INT)) AS DECIMAL(18,4)) FROM dbo.fact_ai_output
UNION ALL SELECT 7, 'Cost per Accepted Output', 'CORRECT: token only, for contrast',
       CAST((SELECT SUM(cost_aud) FROM dbo.fact_ai_session)
          / (SELECT SUM(CAST(accepted AS INT)) FROM dbo.fact_ai_output) AS DECIMAL(18,4))
UNION ALL SELECT 8, 'Cost per Accepted Output', 'WRONG: sum of per-tool ratios',
       CAST(SUM(cpa) AS DECIMAL(18,4)) FROM tool_cost
UNION ALL SELECT 9, 'Cost per Accepted Output', 'WRONG: average of per-tool ratios',
       CAST(AVG(cpa) AS DECIMAL(18,4)) FROM tool_cost

UNION ALL SELECT 10, 'Review Coverage', 'firm-wide, mandatory only',
       CAST(AVG(CAST(reviewed AS FLOAT)) AS DECIMAL(18,4))
FROM dbo.vw_ai_output_enriched WHERE effective_review_policy = 'Mandatory'
UNION ALL SELECT 11, 'Review Coverage', 'tier = ' + m.confidentiality_tier,
       CAST(AVG(CAST(o.reviewed AS FLOAT)) AS DECIMAL(18,4))
FROM dbo.vw_ai_output_enriched o JOIN dbo.dim_matter m ON m.matter_id = o.matter_id
WHERE o.effective_review_policy = 'Mandatory' GROUP BY m.confidentiality_tier
UNION ALL SELECT 12, 'Review Coverage', 'review type = ' + review_type,
       CAST(AVG(CAST(reviewed AS FLOAT)) AS DECIMAL(18,4))
FROM dbo.vw_ai_output_enriched WHERE effective_review_policy = 'Mandatory'
GROUP BY review_type
UNION ALL SELECT 13, 'Mandatory Review Gap', 'count, all time',
       CAST(SUM(CAST(is_review_gap AS INT)) AS DECIMAL(18,4))
FROM dbo.vw_ai_output_enriched

UNION ALL SELECT 14, 'p95 Latency (ms)', 'all tools, successful sessions',
       CAST(MAX(p) AS DECIMAL(18,4)) FROM (
    SELECT DISTINCT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY CAST(latency_ms AS FLOAT))
           OVER () AS p FROM dbo.fact_ai_session WHERE status = 'Success') a
UNION ALL SELECT 15, 'p95 Latency (ms)', 'tool = ' + t.tool_name,
       CAST(MAX(b.p) AS DECIMAL(18,4)) FROM (
    SELECT DISTINCT tool_id, PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY CAST(latency_ms AS FLOAT))
           OVER (PARTITION BY tool_id) AS p
    FROM dbo.fact_ai_session WHERE status = 'Success') b
JOIN dbo.dim_tool t ON t.tool_id = b.tool_id GROUP BY t.tool_name

UNION ALL SELECT 16, 'Error Rate', 'all time',
       CAST(AVG(CASE WHEN status <> 'Success' THEN 1.0 ELSE 0.0 END) AS DECIMAL(18,4))
FROM dbo.fact_ai_session
UNION ALL SELECT 17, 'Error Rate', 'outage window 2025-11-10..14',
       CAST(AVG(CASE WHEN s.status <> 'Success' THEN 1.0 ELSE 0.0 END) AS DECIMAL(18,4))
FROM dbo.fact_ai_session s JOIN dbo.dim_date d ON d.date_id = s.date_id
WHERE d.[date] BETWEEN '2025-11-10' AND '2025-11-14'

UNION ALL SELECT 18, 'Median Edit Distance', 'area = ' + c.practice_area,
       CAST(MAX(c.med) AS DECIMAL(18,4)) FROM (
    SELECT DISTINCT m.practice_area,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY CAST(o.edit_distance_pct AS FLOAT))
               OVER (PARTITION BY m.practice_area) AS med
    FROM dbo.fact_ai_output o JOIN dbo.dim_matter m ON m.matter_id = o.matter_id) c
GROUP BY c.practice_area

UNION ALL SELECT 19, 'Governance Exposure %', 'all time',
       CAST(AVG(CASE WHEN m.confidentiality_tier IN ('Restricted','Barrier')
                     THEN 1.0 ELSE 0.0 END) AS DECIMAL(18,4))
FROM dbo.fact_ai_session s JOIN dbo.dim_matter m ON m.matter_id = s.matter_id

UNION ALL SELECT 19, 'Share of Sessions %', 'tier = ' + c.tier,
       CAST(1.0 * c.sessions / SUM(c.sessions) OVER () AS DECIMAL(18,4))
FROM (SELECT m.confidentiality_tier AS tier, COUNT(*) AS sessions
      FROM dbo.fact_ai_session s JOIN dbo.dim_matter m ON m.matter_id = s.matter_id
      GROUP BY m.confidentiality_tier) c

UNION ALL SELECT 19, 'Share of Matters %', 'tier = ' + c.tier,
       CAST(1.0 * c.matters / SUM(c.matters) OVER () AS DECIMAL(18,4))
FROM (SELECT confidentiality_tier AS tier, COUNT(*) AS matters
      FROM dbo.dim_matter GROUP BY confidentiality_tier) c

UNION ALL SELECT 20, 'Notional Capacity Released AUD', 'fee = ' + fee_arrangement,
       CAST(SUM(notional_capacity_released_aud) AS DECIMAL(18,4))
FROM dbo.vw_billable_impact WHERE is_ai_assisted = 1 GROUP BY fee_arrangement

UNION ALL SELECT 20, 'Revenue at Risk - Hourly AUD', 'billable post-adoption hours only; scenario',
       CAST(SUM(hourly_revenue_exposure_aud) AS DECIMAL(18,4))
FROM dbo.vw_billable_impact WHERE fee_arrangement = 'Hourly'

UNION ALL SELECT 21, 'Total Cost AUD (FY to date)', 'FY2026 complete',
       CAST(SUM(s.cost_aud) AS DECIMAL(18,4))
FROM dbo.fact_ai_session s JOIN dbo.dim_date d ON d.date_id = s.date_id
WHERE d.fy_year = 2026

UNION ALL SELECT 23, 'Licence Cost AUD', 'all time',
       CAST(SUM(licence_cost_aud) AS DECIMAL(18,4)) FROM dbo.fact_seat_month
UNION ALL SELECT 24, 'Idle Licence Cost AUD', 'all time',
       CAST(SUM(licence_cost_aud) AS DECIMAL(18,4)) FROM dbo.fact_seat_month
       WHERE used_in_month = 0
UNION ALL SELECT 25, 'Idle Seat Share', 'all time',
       CAST(AVG(CASE WHEN used_in_month = 0 THEN 1.0 ELSE 0.0 END) AS DECIMAL(18,4))
       FROM dbo.fact_seat_month
UNION ALL SELECT 26, 'Idle Seat Share', 'tool = ' + t.tool_name,
       CAST(AVG(CASE WHEN sm.used_in_month = 0 THEN 1.0 ELSE 0.0 END) AS DECIMAL(18,4))
       FROM dbo.fact_seat_month sm JOIN dbo.dim_tool t ON t.tool_id = sm.tool_id
       GROUP BY t.tool_name
UNION ALL SELECT 27, 'Total AI Cost AUD', 'licence + token, all time',
       CAST((SELECT SUM(licence_cost_aud) FROM dbo.fact_seat_month)
          + (SELECT SUM(cost_aud) FROM dbo.fact_ai_session) AS DECIMAL(18,4))
UNION ALL SELECT 28, 'Cost per Accepted Output', 'CORRECT: licence + token',
       CAST(((SELECT SUM(licence_cost_aud) FROM dbo.fact_seat_month)
           + (SELECT SUM(cost_aud) FROM dbo.fact_ai_session))
          / (SELECT SUM(CAST(accepted AS INT)) FROM dbo.fact_ai_output) AS DECIMAL(18,4))
UNION ALL SELECT 29, 'Cost per Accepted Output', 'tool = ' + t.tool_name,
       CAST((SUM(sm.licence_cost_aud) + MAX(u.tok)) / NULLIF(MAX(u.acc), 0) AS DECIMAL(18,4))
       FROM dbo.fact_seat_month sm
       JOIN dbo.dim_tool t ON t.tool_id = sm.tool_id
       JOIN (SELECT s.tool_id, SUM(s.cost_aud) AS tok,
                    SUM(CASE WHEN o.accepted = 1 THEN 1 ELSE 0 END) AS acc
             FROM dbo.fact_ai_session s
             LEFT JOIN dbo.fact_ai_output o ON o.session_id = s.session_id
             GROUP BY s.tool_id) u ON u.tool_id = sm.tool_id
       GROUP BY t.tool_name
UNION ALL SELECT 22, 'Adoption Rate (Rolling 12 Weeks)', 'ending 2026-08-31',
       CAST(COUNT(DISTINCT s.lawyer_id) * 1.0 / (SELECT n FROM pop) AS DECIMAL(18,4))
FROM dbo.fact_ai_session s JOIN dbo.dim_date d ON d.date_id = s.date_id
WHERE d.[date] BETWEEN DATEADD(DAY, -83, '2026-08-31') AND '2026-08-31'

UNION ALL SELECT 30, 'Never Adopted %', 'level = ' + l.[level],
       CAST(1.0 * SUM(CASE WHEN s.lawyer_id IS NULL THEN 1 ELSE 0 END)
            / COUNT(*) AS DECIMAL(18,4))
FROM dbo.dim_lawyer l
LEFT JOIN (SELECT DISTINCT lawyer_id FROM dbo.fact_ai_session) s
       ON s.lawyer_id = l.lawyer_id
WHERE l.is_active = 1
GROUP BY l.[level]

UNION ALL SELECT 31, 'Never Adopted %', 'group = ' + l.practice_group,
       CAST(1.0 * SUM(CASE WHEN s.lawyer_id IS NULL THEN 1 ELSE 0 END)
            / COUNT(*) AS DECIMAL(18,4))
FROM dbo.dim_lawyer l
LEFT JOIN (SELECT DISTINCT lawyer_id FROM dbo.fact_ai_session) s
       ON s.lawyer_id = l.lawyer_id
WHERE l.is_active = 1
GROUP BY l.practice_group

UNION ALL SELECT 32, 'Lawyers Never Adopted', 'all time',
       CAST((SELECT COUNT(*) FROM dbo.dim_lawyer WHERE is_active = 1)
          - (SELECT COUNT(DISTINCT lawyer_id) FROM dbo.fact_ai_session) AS DECIMAL(18,4))


UNION ALL SELECT 35, 'Heavy Rework Share', 'task = ' + tt.task_name,
       CAST(1.0 * SUM(CASE WHEN o.accepted = 1 AND o.edit_distance_pct > 40
                           THEN 1 ELSE 0 END)
            / NULLIF(SUM(CASE WHEN o.accepted = 1 THEN 1 ELSE 0 END), 0) AS DECIMAL(18,4))
FROM dbo.fact_ai_output o
JOIN dbo.dim_task_type tt ON tt.task_type_id = o.task_type_id
GROUP BY tt.task_name

UNION ALL SELECT 36, 'Retry Rate', 'all time',
       CAST(AVG(CASE WHEN retry_count > 0 THEN 1.0 ELSE 0.0 END) AS DECIMAL(18,4))
FROM dbo.fact_ai_session

UNION ALL SELECT 37, 'Sessions per Active Lawyer', 'month = ' + CAST(x.ym AS VARCHAR(6)),
       CAST(x.spa AS DECIMAL(18,4))
FROM (SELECT d.[year] * 100 + d.[month] AS ym,
             1.0 * COUNT(*) / COUNT(DISTINCT s.lawyer_id) AS spa
      FROM dbo.fact_ai_session s JOIN dbo.dim_date d ON d.date_id = s.date_id
      GROUP BY d.[year] * 100 + d.[month]) x
WHERE x.ym IN (202503, 202608)

) r ORDER BY ord, scope;
GO

/*  Week-4 retention is read from vw_retention_cohort, and the cohort view is
    kept out of the UNION above on purpose: nested inside it the optimiser
    re-evaluates the cohort join per branch and the script runs for minutes
    instead of seconds. Same numbers, separate statement.                     */
SELECT 33 AS ord, 'Week-4 Retention' AS measure_name,
       'complete cohorts, all levels' AS scope,
       CAST(1.0 * SUM(retained_lawyers) / SUM(cohort_lawyers) AS DECIMAL(18,4)) AS expected
FROM dbo.vw_retention_cohort WHERE is_complete = 1
UNION ALL
SELECT 34, 'Week-4 Retention', 'level = ' + [level],
       CAST(1.0 * SUM(retained_lawyers) / SUM(cohort_lawyers) AS DECIMAL(18,4))
FROM dbo.vw_retention_cohort WHERE is_complete = 1 GROUP BY [level]
ORDER BY ord, scope;
GO
