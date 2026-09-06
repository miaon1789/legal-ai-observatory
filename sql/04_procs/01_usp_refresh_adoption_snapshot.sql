USE LegalAIObservatory;
GO

/*  A materialised monthly adoption snapshot.

    vw_adoption_by_group recomputes the cross join of every population cell
    against every month on each query, which is fine interactively and wasteful
    as the source of a dashboard refresh. This proc lands it once per month and
    keeps history, so a restated month is visible rather than silently
    overwritten.

    Idempotent: rerunning for a month replaces that month and nothing else.  */

IF OBJECT_ID('dbo.fact_adoption_snapshot') IS NULL
CREATE TABLE dbo.fact_adoption_snapshot (
    year_month      INT           NOT NULL,
    [level]         VARCHAR(20)   NOT NULL,
    practice_group  VARCHAR(40)   NOT NULL,
    office          VARCHAR(20)   NOT NULL,
    lawyers         INT           NOT NULL,
    active_lawyers  INT           NOT NULL,
    sessions        INT           NOT NULL,
    adoption_rate   DECIMAL(9,6)  NOT NULL,
    refreshed_at    DATETIME2(0)  NOT NULL,
    CONSTRAINT pk_adoption_snapshot
        PRIMARY KEY (year_month, [level], practice_group, office)
);
GO

IF OBJECT_ID('dbo.etl_run_log') IS NULL
CREATE TABLE dbo.etl_run_log (
    log_id       INT IDENTITY PRIMARY KEY,
    proc_name    SYSNAME       NOT NULL,
    parameters   NVARCHAR(200) NULL,
    started_at   DATETIME2(0)  NOT NULL,
    finished_at  DATETIME2(0)  NULL,
    rows_written INT           NULL,
    status       VARCHAR(10)   NOT NULL,
    message      NVARCHAR(1000) NULL
);
GO

CREATE OR ALTER PROCEDURE dbo.usp_refresh_adoption_snapshot
    @year_month INT  = NULL,   -- NULL rebuilds every month in the window
    @verbose    BIT  = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @started DATETIME2(0) = SYSDATETIME(), @rows INT = 0, @log_id INT;

    INSERT INTO dbo.etl_run_log (proc_name, parameters, started_at, status)
    VALUES (OBJECT_NAME(@@PROCID),
            CONCAT('@year_month=', ISNULL(CAST(@year_month AS VARCHAR(8)), 'ALL')),
            @started, 'Running');
    SET @log_id = SCOPE_IDENTITY();

    BEGIN TRY
        BEGIN TRANSACTION;

        WITH src AS (
            SELECT year_month, [level], practice_group, office,
                   lawyers, active_lawyers, sessions,
                   CAST(ISNULL(adoption_rate, 0) AS DECIMAL(9,6)) AS adoption_rate
            FROM   dbo.vw_adoption_by_group
            WHERE  @year_month IS NULL OR year_month = @year_month
        )
        MERGE dbo.fact_adoption_snapshot AS tgt
        USING src
           ON  tgt.year_month     = src.year_month
           AND tgt.[level]        = src.[level]
           AND tgt.practice_group = src.practice_group
           AND tgt.office         = src.office
        WHEN MATCHED AND (tgt.active_lawyers <> src.active_lawyers
                       OR tgt.sessions       <> src.sessions
                       OR tgt.lawyers        <> src.lawyers)
            THEN UPDATE SET tgt.lawyers        = src.lawyers,
                            tgt.active_lawyers = src.active_lawyers,
                            tgt.sessions       = src.sessions,
                            tgt.adoption_rate  = src.adoption_rate,
                            tgt.refreshed_at   = SYSDATETIME()
        WHEN NOT MATCHED BY TARGET
            THEN INSERT (year_month, [level], practice_group, office, lawyers,
                         active_lawyers, sessions, adoption_rate, refreshed_at)
                 VALUES (src.year_month, src.[level], src.practice_group, src.office,
                         src.lawyers, src.active_lawyers, src.sessions,
                         src.adoption_rate, SYSDATETIME())
        /* a cell that has left the source for a refreshed month is stale data,
           not history: delete it rather than leave a number nobody can source */
        WHEN NOT MATCHED BY SOURCE
             AND (@year_month IS NULL OR tgt.year_month = @year_month)
            THEN DELETE;

        SET @rows = @@ROWCOUNT;
        COMMIT TRANSACTION;

        UPDATE dbo.etl_run_log
        SET    finished_at = SYSDATETIME(), rows_written = @rows, status = 'Success'
        WHERE  log_id = @log_id;

        IF @verbose = 1
            SELECT TOP (20) * FROM dbo.fact_adoption_snapshot
            WHERE  @year_month IS NULL OR year_month = @year_month
            ORDER  BY year_month DESC, adoption_rate DESC;

        RETURN 0;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        UPDATE dbo.etl_run_log
        SET    finished_at = SYSDATETIME(), status = 'Failed',
               message = CONCAT('Line ', ERROR_LINE(), ': ', ERROR_MESSAGE())
        WHERE  log_id = @log_id;
        THROW;
    END CATCH
END
GO
