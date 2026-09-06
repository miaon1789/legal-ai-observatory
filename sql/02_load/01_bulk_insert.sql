/*  Load the CSV extracts. Dimensions first, then facts, so the foreign keys
    hold at every point rather than being disabled and re-checked.

    The extracts are mounted into the container at /data/raw. FORMAT='CSV' with
    FIELDQUOTE handles the quoted free text in dim_alert_rule.rationale, and
    leaves empty fields as NULL, which the CHECK constraints then verify:
    a Success session must have no error_type, an unresolved incident must have
    no resolution time. If the load corrupted either, the insert fails here
    rather than surfacing as a wrong number on a dashboard.                    */

USE LegalAIObservatory;
GO
SET NOCOUNT ON;

DELETE FROM dbo.fact_billable_impact_estimate;
DELETE FROM dbo.fact_seat_month;
DELETE FROM dbo.fact_pipeline_run;
DELETE FROM dbo.fact_incident;
DELETE FROM dbo.fact_time_entry;
DELETE FROM dbo.fact_ai_output;
DELETE FROM dbo.fact_ai_session;
DELETE FROM dbo.dim_alert_rule;
DELETE FROM dbo.dim_matter;
DELETE FROM dbo.dim_lawyer;
DELETE FROM dbo.dim_task_type;
DELETE FROM dbo.dim_tool;
DELETE FROM dbo.dim_date;
GO

DECLARE @opts NVARCHAR(400) =
    N'FORMAT = ''CSV'', FIELDQUOTE = ''"'', FIRSTROW = 2, TABLOCK, KEEPNULLS';

DECLARE @t TABLE (seq INT IDENTITY, tbl SYSNAME);
INSERT INTO @t (tbl) VALUES
    ('dim_date'), ('dim_lawyer'), ('dim_tool'), ('dim_task_type'),
    ('dim_matter'), ('dim_alert_rule'),
    ('fact_ai_session'), ('fact_ai_output'), ('fact_time_entry'),
    ('fact_incident'), ('fact_pipeline_run'), ('fact_seat_month'),
    ('fact_billable_impact_estimate');

DECLARE @i INT = 1, @n INT = (SELECT COUNT(*) FROM @t), @tbl SYSNAME, @sql NVARCHAR(MAX);
WHILE @i <= @n
BEGIN
    SELECT @tbl = tbl FROM @t WHERE seq = @i;
    SET @sql = N'BULK INSERT dbo.' + QUOTENAME(@tbl)
             + N' FROM ''/data/raw/' + @tbl + N'.csv'' WITH (' + @opts + N');';
    EXEC sp_executesql @sql;
    SET @i += 1;
END
GO

SELECT  t.name AS [table],
        SUM(p.rows) AS [rows]
FROM    sys.tables t
JOIN    sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
GROUP BY t.name
ORDER BY [rows] DESC;
GO
