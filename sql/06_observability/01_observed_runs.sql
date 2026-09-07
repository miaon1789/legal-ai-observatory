/* Additive migration. Run against the selected database, never via run_all.sh.
   These objects do not reference or modify the synthetic dbo warehouse. */
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET NUMERIC_ROUNDABORT OFF;
GO
IF SCHEMA_ID('telemetry') IS NULL EXEC('CREATE SCHEMA telemetry');
GO
IF OBJECT_ID('telemetry.event_log') IS NULL
BEGIN
    CREATE TABLE telemetry.event_log (
        event_id UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
        event_type VARCHAR(24) NOT NULL,
        source_id VARCHAR(64) NOT NULL,
        data_origin VARCHAR(10) NOT NULL,
        run_kind VARCHAR(16) NOT NULL,
        request_id UNIQUEIDENTIFIER NULL,
        trace_id CHAR(32) NOT NULL,
        revision INT NOT NULL,
        occurred_at DATETIMEOFFSET(3) NOT NULL,
        received_at DATETIMEOFFSET(3) NOT NULL DEFAULT SYSDATETIMEOFFSET(),
        payload_hash BINARY(32) NOT NULL,
        event_json NVARCHAR(MAX) NOT NULL,
        CONSTRAINT ck_telemetry_json CHECK (ISJSON(event_json) = 1),
        CONSTRAINT ck_telemetry_type CHECK (event_type IN ('request.completed','review.recorded','source.heartbeat')),
        CONSTRAINT ck_telemetry_origin CHECK (data_origin IN ('observed','simulated')),
        CONSTRAINT ck_telemetry_kind CHECK (run_kind IN ('benchmark','pilot','fault_drill')),
        CONSTRAINT ck_telemetry_revision CHECK (revision >= 1),
        CONSTRAINT ck_telemetry_request CHECK (
            (event_type = 'source.heartbeat' AND request_id IS NULL) OR
            (event_type <> 'source.heartbeat' AND request_id IS NOT NULL))
    );
    CREATE UNIQUE INDEX ux_telemetry_revision
        ON telemetry.event_log(request_id, event_type, revision) WHERE request_id IS NOT NULL;
    CREATE INDEX ix_telemetry_source ON telemetry.event_log(source_id, data_origin, run_kind, occurred_at);
END;
IF OBJECT_ID('telemetry.ingestion_run') IS NULL
    CREATE TABLE telemetry.ingestion_run (
        ingestion_id UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
        started_at DATETIMEOFFSET(3) NOT NULL,
        finished_at DATETIMEOFFSET(3) NOT NULL,
        status VARCHAR(10) NOT NULL CHECK (status IN ('success','failed')),
        event_count INT NOT NULL,
        inserted_count INT NOT NULL,
        error_code VARCHAR(64) NULL,
        ingestion_sequence BIGINT IDENTITY NOT NULL
    );
IF OBJECT_ID('telemetry.alert_state') IS NULL
    CREATE TABLE telemetry.alert_state (
        source_id VARCHAR(64) NOT NULL,
        data_origin VARCHAR(10) NOT NULL,
        run_kind VARCHAR(16) NOT NULL,
        rule_key VARCHAR(40) NOT NULL,
        is_open BIT NOT NULL,
        last_checked_at DATETIMEOFFSET(3) NOT NULL,
        last_changed_at DATETIMEOFFSET(3) NOT NULL,
        PRIMARY KEY (source_id, data_origin, run_kind, rule_key)
    );
IF OBJECT_ID('telemetry.alert_transition') IS NULL
    CREATE TABLE telemetry.alert_transition (
        transition_id BIGINT IDENTITY PRIMARY KEY,
        source_id VARCHAR(64) NOT NULL,
        data_origin VARCHAR(10) NOT NULL,
        run_kind VARCHAR(16) NOT NULL,
        rule_key VARCHAR(40) NOT NULL,
        state VARCHAR(10) NOT NULL CHECK (state IN ('open','resolved')),
        changed_at DATETIMEOFFSET(3) NOT NULL
    );
GO
CREATE OR ALTER PROCEDURE telemetry.usp_ingest_events @events NVARCHAR(MAX)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    DECLARE @id UNIQUEIDENTIFIER = NEWID(), @started DATETIMEOFFSET(3) = SYSDATETIMEOFFSET();
    DECLARE @count INT = 0, @inserted INT = 0;
    BEGIN TRY
        IF ISJSON(@events) <> 1 OR LEFT(LTRIM(@events),1) <> '['
            THROW 51000, 'Expected a JSON event array', 1;
        DECLARE @incoming TABLE (
            ordinal INT, event_id UNIQUEIDENTIFIER, event_type VARCHAR(24), source_id VARCHAR(64),
            data_origin VARCHAR(10), run_kind VARCHAR(16), request_id UNIQUEIDENTIFIER,
            trace_id CHAR(32), revision INT, occurred_at DATETIMEOFFSET(3),
            payload_hash BINARY(32), event_json NVARCHAR(MAX)
        );
        INSERT @incoming
        SELECT CONVERT(INT,j.[key]), TRY_CONVERT(UNIQUEIDENTIFIER,JSON_VALUE(j.value,'$.event_id')),
            JSON_VALUE(j.value,'$.event_type'), JSON_VALUE(j.value,'$.source_id'),
            JSON_VALUE(j.value,'$.data_origin'), JSON_VALUE(j.value,'$.run_kind'),
            TRY_CONVERT(UNIQUEIDENTIFIER,JSON_VALUE(j.value,'$.request_id')),
            JSON_VALUE(j.value,'$.trace_id'), TRY_CONVERT(INT,JSON_VALUE(j.value,'$.revision')),
            TRY_CONVERT(DATETIMEOFFSET(3),JSON_VALUE(j.value,'$.occurred_at')),
            HASHBYTES('SHA2_256',CONVERT(VARBINARY(MAX),j.value)),j.value
        FROM OPENJSON(@events) j;
        SET @count = @@ROWCOUNT;
        IF EXISTS (SELECT 1 FROM @incoming WHERE JSON_VALUE(event_json,'$.schema_version') <> '1'
                   OR JSON_VALUE(event_json,'$.schema_version') IS NULL
                   OR JSON_QUERY(event_json,'$.payload') IS NULL)
            THROW 51001, 'Unsupported or malformed event envelope', 1;
        BEGIN TRANSACTION;
        DECLARE @lock INT;
        EXEC @lock = sys.sp_getapplock @Resource='legalai_telemetry_ingest',
            @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=10000;
        IF @lock < 0 THROW 51002, 'Ingestion lock unavailable', 1;
        IF EXISTS (SELECT 1 FROM @incoming a JOIN @incoming b ON a.event_id=b.event_id
                   WHERE a.payload_hash<>b.payload_hash)
           OR EXISTS (SELECT 1 FROM @incoming i JOIN telemetry.event_log e ON i.event_id=e.event_id
                      WHERE i.payload_hash<>e.payload_hash)
            THROW 51003, 'An event ID was reused with different content', 1;
        /* Provenance cannot change when a late review or correction arrives. */
        IF EXISTS (
            SELECT 1 FROM @incoming i JOIN telemetry.event_log e ON i.request_id=e.request_id
            WHERE i.source_id<>e.source_id OR i.data_origin<>e.data_origin
               OR i.run_kind<>e.run_kind OR i.trace_id<>e.trace_id
        ) OR EXISTS (
            SELECT 1 FROM @incoming i JOIN @incoming e ON i.request_id=e.request_id
            WHERE i.source_id<>e.source_id OR i.data_origin<>e.data_origin
               OR i.run_kind<>e.run_kind OR i.trace_id<>e.trace_id
        ) THROW 51004, 'Request provenance changed across events', 1;
        ;WITH unique_events AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY ordinal) AS n
            FROM @incoming
        )
        INSERT telemetry.event_log(event_id,event_type,source_id,data_origin,run_kind,request_id,
                                    trace_id,revision,occurred_at,payload_hash,event_json)
        SELECT event_id,event_type,source_id,data_origin,run_kind,request_id,trace_id,revision,
               occurred_at,payload_hash,event_json
        FROM unique_events i WHERE n=1
          AND NOT EXISTS (SELECT 1 FROM telemetry.event_log e WHERE e.event_id=i.event_id);
        SET @inserted = @@ROWCOUNT;
        INSERT telemetry.ingestion_run
            (ingestion_id,started_at,finished_at,status,event_count,inserted_count,error_code)
        VALUES(@id,@started,SYSDATETIMEOFFSET(),'success',@count,@inserted,NULL);
        COMMIT;
        SELECT @count AS event_count,@inserted AS inserted_count,@count-@inserted AS duplicate_count
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER;
    END TRY
    BEGIN CATCH
        IF XACT_STATE()<>0 ROLLBACK;
        INSERT telemetry.ingestion_run
            (ingestion_id,started_at,finished_at,status,event_count,inserted_count,error_code)
        VALUES(@id,@started,SYSDATETIMEOFFSET(),'failed',@count,0,CONVERT(VARCHAR(64),ERROR_NUMBER()));
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER VIEW telemetry.vw_ingestion_health AS
SELECT TOP(1) r.ingestion_sequence,r.ingestion_id,r.started_at,r.finished_at,r.status,
    r.event_count,r.inserted_count,r.error_code,
    CONVERT(BIT,CASE WHEN r.status='failed' THEN 1 ELSE 0 END) AS is_open,
    f.finished_at AS last_failure_at,
    CASE WHEN r.status='success' THEN recovered.finished_at END AS recovered_at
FROM telemetry.ingestion_run r
OUTER APPLY (SELECT TOP(1) finished_at,ingestion_sequence FROM telemetry.ingestion_run
            WHERE status='failed' ORDER BY ingestion_sequence DESC) f
OUTER APPLY (SELECT TOP(1) finished_at FROM telemetry.ingestion_run
            WHERE status='success' AND ingestion_sequence>f.ingestion_sequence
            ORDER BY ingestion_sequence) recovered
ORDER BY r.ingestion_sequence DESC;
GO
CREATE OR ALTER VIEW telemetry.vw_requests AS
WITH ranked AS (
    SELECT *,ROW_NUMBER() OVER (PARTITION BY request_id,event_type ORDER BY revision DESC) AS n
    FROM telemetry.event_log WHERE request_id IS NOT NULL
), requests AS (
    SELECT * FROM ranked WHERE event_type='request.completed' AND n=1
), reviews AS (
    SELECT * FROM ranked WHERE event_type='review.recorded' AND n=1
)
SELECT r.request_id,r.event_id,r.trace_id,r.revision,r.source_id,r.data_origin,r.run_kind,
    r.occurred_at,r.received_at,
    JSON_VALUE(r.event_json,'$.payload.experiment_id') AS experiment_id,
    JSON_VALUE(r.event_json,'$.payload.case_id') AS case_id,
    JSON_VALUE(r.event_json,'$.payload.case_version') AS case_version,
    JSON_VALUE(r.event_json,'$.payload.case_origin') AS case_origin,
    JSON_VALUE(r.event_json,'$.payload.config_id') AS config_id,
    JSON_VALUE(r.event_json,'$.payload.prompt_version') AS prompt_version,
    JSON_VALUE(r.event_json,'$.payload.run_config_version') AS run_config_version,
    JSON_VALUE(r.event_json,'$.payload.provider') AS provider,
    JSON_VALUE(r.event_json,'$.payload.requested_model') AS requested_model,
    JSON_VALUE(r.event_json,'$.payload.response_model') AS response_model,
    TRY_CONVERT(DATETIMEOFFSET(3),JSON_VALUE(r.event_json,'$.payload.started_at')) AS started_at,
    TRY_CONVERT(DATETIMEOFFSET(3),JSON_VALUE(r.event_json,'$.payload.finished_at')) AS finished_at,
    TRY_CONVERT(FLOAT,JSON_VALUE(r.event_json,'$.payload.duration_ms')) AS duration_ms,
    JSON_VALUE(r.event_json,'$.payload.status') AS status,
    JSON_VALUE(r.event_json,'$.payload.error_type') AS error_type,
    TRY_CONVERT(BIT,JSON_VALUE(r.event_json,'$.payload.auto_pass')) AS auto_pass,
    JSON_VALUE(r.event_json,'$.payload.answer_sha256') AS answer_sha256,
    a.attempt_count,a.attempt_count-1 AS retry_count,a.unknown_usage_attempts,
    a.input_tokens,a.output_tokens,a.cached_input_tokens,a.estimated_cost,a.known_estimated_cost,
    a.currency,a.cost_known_attempts,
    JSON_VALUE(v.event_json,'$.payload.review_method') AS review_method,
    JSON_VALUE(v.event_json,'$.payload.reviewer_id') AS reviewer_id,
    v.occurred_at AS reviewed_at,v.revision AS review_revision,
    TRY_CONVERT(BIT,JSON_VALUE(v.event_json,'$.payload.task_pass')) AS human_task_pass,
    TRY_CONVERT(BIT,JSON_VALUE(v.event_json,'$.payload.citations_correct')) AS citations_correct,
    TRY_CONVERT(BIT,JSON_VALUE(v.event_json,'$.payload.major_rework')) AS major_rework,
    TRY_CONVERT(FLOAT,JSON_VALUE(v.event_json,'$.payload.edit_distance_ratio')) AS edit_distance_ratio,
    JSON_VALUE(v.event_json,'$.payload.reason_code') AS review_reason_code
FROM requests r
OUTER APPLY (
    SELECT COUNT(*) AS attempt_count,
        SUM(CASE WHEN input_tokens IS NULL OR output_tokens IS NULL THEN 1 ELSE 0 END) AS unknown_usage_attempts,
        CASE WHEN COUNT(input_tokens)=COUNT(*) THEN SUM(CONVERT(BIGINT,input_tokens)) END AS input_tokens,
        CASE WHEN COUNT(output_tokens)=COUNT(*) THEN SUM(CONVERT(BIGINT,output_tokens)) END AS output_tokens,
        CASE WHEN COUNT(cached_input_tokens)=COUNT(*) THEN SUM(CONVERT(BIGINT,cached_input_tokens)) END AS cached_input_tokens,
        CASE WHEN COUNT(estimated_cost)=COUNT(*) AND COUNT(DISTINCT currency)=1 THEN SUM(estimated_cost) END AS estimated_cost,
        CASE WHEN COUNT(DISTINCT currency)<=1 THEN SUM(estimated_cost) END AS known_estimated_cost,
        CASE WHEN COUNT(DISTINCT currency)=1 THEN MAX(currency) END AS currency,
        COUNT(estimated_cost) AS cost_known_attempts
    FROM OPENJSON(r.event_json,'$.payload.attempts') WITH (
        input_tokens INT '$.input_tokens',output_tokens INT '$.output_tokens',
        cached_input_tokens INT '$.cached_input_tokens',estimated_cost DECIMAL(24,12) '$.estimated_cost',
        currency VARCHAR(3) '$.currency'
    )
) a
LEFT JOIN reviews v ON v.request_id=r.request_id
    AND TRY_CONVERT(INT,JSON_VALUE(v.event_json,'$.payload.request_revision'))=r.revision
    AND JSON_VALUE(v.event_json,'$.payload.answer_sha256')=JSON_VALUE(r.event_json,'$.payload.answer_sha256')
    AND JSON_VALUE(r.event_json,'$.payload.status')='success';
GO
CREATE OR ALTER VIEW telemetry.vw_config_comparison AS
WITH latency AS (
    SELECT *,PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)
        OVER (PARTITION BY source_id,data_origin,run_kind,experiment_id,case_version,
                           config_id,prompt_version,run_config_version,provider,requested_model) AS p95_ms
    FROM telemetry.vw_requests
)
SELECT source_id,data_origin,run_kind,experiment_id,case_version,config_id,prompt_version,run_config_version,
    provider,requested_model,COUNT(*) AS request_count,COUNT(DISTINCT case_id) AS case_count,
    SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS successful_requests,
    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_requests,
    AVG(CASE WHEN status='failed' THEN 1.0 ELSE 0.0 END) AS failure_rate,
    SUM(attempt_count) AS attempt_count,SUM(retry_count) AS retry_count,
    SUM(attempt_count)-SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS failed_attempts,
    SUM(unknown_usage_attempts) AS unknown_usage_attempts,MAX(p95_ms) AS p95_request_ms,
    AVG(duration_ms) AS mean_request_ms,
    SUM(CASE WHEN auto_pass=1 THEN 1 ELSE 0 END) AS auto_passed,
    SUM(CASE WHEN auto_pass=1 THEN 1.0 ELSE 0.0 END)/COUNT(*) AS auto_pass_rate_all_requests,
    SUM(CASE WHEN review_method='human' AND human_task_pass IS NOT NULL THEN 1 ELSE 0 END) AS human_reviewed,
    AVG(CASE WHEN review_method='human' THEN CONVERT(FLOAT,human_task_pass) END) AS human_pass_rate_reviewed,
    SUM(CASE WHEN review_method='human' AND human_task_pass=1 THEN 1 ELSE 0 END) AS human_passed,
    SUM(CASE WHEN review_method='human' AND human_task_pass IS NOT NULL THEN 1.0 ELSE 0.0 END)
        /NULLIF(SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),0) AS human_review_coverage,
    AVG(CASE WHEN review_method='human' THEN CONVERT(FLOAT,citations_correct) END) AS citation_accuracy_reviewed,
    AVG(CASE WHEN review_method='human' THEN CONVERT(FLOAT,major_rework) END) AS major_rework_rate_reviewed,
    AVG(CASE WHEN review_method='human' THEN edit_distance_ratio END) AS mean_edit_distance_ratio,
    COUNT(estimated_cost) AS cost_known_requests,
    CASE WHEN COUNT(DISTINCT currency)=1 THEN MAX(currency) END AS currency,
    CASE WHEN COUNT(DISTINCT currency)<=1 THEN SUM(known_estimated_cost) END AS known_estimated_cost,
    CASE WHEN COUNT(estimated_cost)=COUNT(*) AND COUNT(DISTINCT currency)=1 THEN SUM(estimated_cost) END AS estimated_cost,
    CASE WHEN COUNT(estimated_cost)=COUNT(*) AND COUNT(DISTINCT currency)=1
         THEN SUM(estimated_cost)/NULLIF(SUM(CASE WHEN auto_pass=1 THEN 1 ELSE 0 END),0) END AS cost_per_auto_pass,
    CASE WHEN COUNT(estimated_cost)=COUNT(*) AND COUNT(DISTINCT currency)=1
          AND SUM(CASE WHEN status='success' AND review_method='human' AND human_task_pass IS NOT NULL THEN 1 ELSE 0 END)
             =SUM(CASE WHEN status='success' THEN 1 ELSE 0 END)
         THEN SUM(estimated_cost)/NULLIF(SUM(CASE WHEN review_method='human' AND human_task_pass=1 THEN 1 ELSE 0 END),0)
         END AS cost_per_human_pass
FROM latency
GROUP BY source_id,data_origin,run_kind,experiment_id,case_version,config_id,prompt_version,run_config_version,provider,requested_model;
GO
CREATE OR ALTER VIEW telemetry.vw_attempts AS
SELECT r.request_id,r.trace_id,r.source_id,r.data_origin,r.run_kind,r.experiment_id,r.case_id,
    r.config_id,r.revision,r.started_at,a.*
FROM telemetry.vw_requests r
JOIN telemetry.event_log e ON e.event_id=r.event_id
CROSS APPLY OPENJSON(e.event_json,'$.payload.attempts') WITH (
    attempt_number INT '$.attempt_number',duration_ms FLOAT '$.duration_ms',
    status VARCHAR(10) '$.status',error_type VARCHAR(64) '$.error_type',
    provider_request_id VARCHAR(200) '$.provider_request_id',
    input_tokens INT '$.input_tokens',output_tokens INT '$.output_tokens',
    cached_input_tokens INT '$.cached_input_tokens',estimated_cost DECIMAL(24,12) '$.estimated_cost',
    currency VARCHAR(3) '$.currency',rate_version VARCHAR(100) '$.rate_version'
) a;
GO
CREATE OR ALTER PROCEDURE telemetry.usp_check_source
    @source_id VARCHAR(64),@data_origin VARCHAR(10),@run_kind VARCHAR(16),
    @as_at DATETIMEOFFSET(3)=NULL,@stale_minutes INT=10
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @data_origin NOT IN ('observed','simulated') OR @run_kind NOT IN ('benchmark','pilot','fault_drill')
       OR @stale_minutes<1 THROW 51005, 'Invalid monitor scope', 1;
    SET @as_at=COALESCE(@as_at,SYSDATETIMEOFFSET());
    DECLARE @heartbeat DATETIMEOFFSET(3),@heartbeat_status VARCHAR(10),@request_status VARCHAR(10);
    SELECT TOP(1) @heartbeat=occurred_at,@heartbeat_status=JSON_VALUE(event_json,'$.payload.status')
    FROM telemetry.event_log WHERE source_id=@source_id AND data_origin=@data_origin AND run_kind=@run_kind
      AND event_type='source.heartbeat' AND occurred_at<=@as_at ORDER BY occurred_at DESC,event_id DESC;
    SELECT TOP(1) @request_status=status FROM telemetry.vw_requests
    WHERE source_id=@source_id AND data_origin=@data_origin AND run_kind=@run_kind AND occurred_at<=@as_at
    ORDER BY occurred_at DESC,request_id DESC;
    DECLARE @checks TABLE(rule_key VARCHAR(40) PRIMARY KEY,is_open BIT);
    INSERT @checks VALUES
      ('heartbeat_stale',CASE WHEN @heartbeat IS NULL OR @heartbeat<DATEADD(MINUTE,-@stale_minutes,@as_at)
                              OR @heartbeat_status='failed' THEN 1 ELSE 0 END),
      ('latest_request_failed',CASE WHEN @request_status='failed' THEN 1 ELSE 0 END);
    BEGIN TRANSACTION;
    DECLARE @lock INT;
    EXEC @lock=sys.sp_getapplock @Resource='legalai_telemetry_monitor',@LockMode='Exclusive',
        @LockOwner='Transaction',@LockTimeout=10000;
    IF @lock<0 BEGIN ROLLBACK; THROW 51006,'Monitor lock unavailable',1; END;
    IF EXISTS(SELECT 1 FROM telemetry.alert_state WHERE source_id=@source_id AND data_origin=@data_origin
              AND run_kind=@run_kind AND last_checked_at>@as_at)
    BEGIN ROLLBACK; THROW 51007,'Monitor clock cannot move backwards within a scope',1; END;
    INSERT telemetry.alert_transition(source_id,data_origin,run_kind,rule_key,state,changed_at)
    SELECT @source_id,@data_origin,@run_kind,c.rule_key,CASE WHEN c.is_open=1 THEN 'open' ELSE 'resolved' END,@as_at
    FROM @checks c LEFT JOIN telemetry.alert_state s ON s.source_id=@source_id AND s.data_origin=@data_origin
      AND s.run_kind=@run_kind AND s.rule_key=c.rule_key
    WHERE (s.rule_key IS NULL AND c.is_open=1) OR s.is_open<>c.is_open;
    UPDATE s SET last_changed_at=CASE WHEN s.is_open<>c.is_open THEN @as_at ELSE s.last_changed_at END,
                 is_open=c.is_open,last_checked_at=@as_at
    FROM telemetry.alert_state s JOIN @checks c ON c.rule_key=s.rule_key
    WHERE s.source_id=@source_id AND s.data_origin=@data_origin AND s.run_kind=@run_kind;
    INSERT telemetry.alert_state
    SELECT @source_id,@data_origin,@run_kind,c.rule_key,c.is_open,@as_at,@as_at FROM @checks c
    WHERE NOT EXISTS(SELECT 1 FROM telemetry.alert_state s WHERE s.source_id=@source_id AND s.data_origin=@data_origin
                      AND s.run_kind=@run_kind AND s.rule_key=c.rule_key);
    COMMIT;
    SELECT source_id,data_origin,run_kind,rule_key,is_open,last_checked_at,last_changed_at
    FROM telemetry.alert_state WHERE source_id=@source_id AND data_origin=@data_origin AND run_kind=@run_kind
    ORDER BY rule_key FOR JSON PATH;
END;
GO
