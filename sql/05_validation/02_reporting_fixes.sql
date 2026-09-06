/* Focused checks for the September reporting corrections. Read-only.
   Small memory grants keep this usable alongside the Windows VM. */
USE LegalAIObservatory;
GO
SET NOCOUNT ON;

SELECT rule_name, current_value, threshold_value, comparison, is_breached,
       affected_count
FROM dbo.vw_alert_status
WHERE rule_name = 'Monthly cost 30% above trailing 3-month mean'
OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 1);

SELECT COUNT(*) AS rules, SUM(CAST(is_breached AS INT)) AS breached_rules,
       SUM(CASE WHEN is_breached IS NULL THEN 1 ELSE 0 END) AS unevaluated_rules
FROM dbo.vw_alert_status
OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 1);

SELECT fee_arrangement,
       SUM(notional_capacity_released_aud) AS all_hours_capacity_aud,
       SUM(hourly_revenue_exposure_aud) AS billable_hourly_revenue_exposure_aud,
       SUM(post_adoption_billable_hours) AS post_adoption_billable_hours
FROM dbo.vw_billable_impact
GROUP BY fee_arrangement
OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 1);
