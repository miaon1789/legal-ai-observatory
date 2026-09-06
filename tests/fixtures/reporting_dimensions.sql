DECLARE @day DATE = '20260401';
WHILE @day <= '20260831'
BEGIN
    INSERT dbo.dim_date VALUES
        (CONVERT(INT, CONVERT(CHAR(8), @day, 112)), @day, YEAR(@day),
         DATEPART(QUARTER, @day), MONTH(@day), LEFT(DATENAME(MONTH, @day),3),
         CONCAT(YEAR(@day), '-W', RIGHT('0' + CAST(DATEPART(ISO_WEEK,@day) AS VARCHAR(2)),2)),
         DATEPART(WEEKDAY,@day), 1, 2026, 4);
    SET @day = DATEADD(DAY, 1, @day);
END;
INSERT dbo.dim_lawyer VALUES
    (1,'Test One','Associate','M&A','Sydney',2020,100,1),
    (2,'Test Two','Partner','M&A','Sydney',2010,200,1);
INSERT dbo.dim_tool VALUES (1,'Firm Chat','20260401',0,0,90);
INSERT dbo.dim_task_type VALUES (1,'Drafting','High','Mandatory','Supervisory review');
INSERT dbo.dim_matter VALUES
    (1,'Hourly test','M&A','Technology','Hourly','Standard',NULL,1,'20260401',NULL),
    (2,'Fixed test','M&A','Technology','Fixed Fee','Standard',NULL,1,'20260401',NULL);
INSERT dbo.dim_alert_rule VALUES
    (10,'Monthly cost 30% above trailing 3-month mean','Cost','test',0.30,'>','Low','Finance','test');
INSERT dbo.fact_billable_impact_estimate VALUES
    (1,'Hourly',-10,-11,-9,1,100,100,'Hours','test','test','test',1,2,'20260831'),
    (2,'Fixed Fee',-10,-11,-9,1,100,100,'Hours','test','test','test',1,2,'20260831');
