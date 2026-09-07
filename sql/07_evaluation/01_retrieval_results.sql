/* Additive, source-text-free experiment reporting. Never run run_all.sh for this. */
IF SCHEMA_ID('evaluation') IS NULL EXEC('CREATE SCHEMA evaluation');
GO
IF OBJECT_ID('evaluation.experiment_run') IS NULL
CREATE TABLE evaluation.experiment_run (
    run_id VARCHAR(32) NOT NULL PRIMARY KEY,
    run_label NVARCHAR(80) NOT NULL,
    phase VARCHAR(24) NOT NULL CHECK (phase IN ('baseline','development','fresh_evaluation')),
    protocol_sha256 CHAR(64) NOT NULL,
    started_at DATETIMEOFFSET(3) NOT NULL,
    case_origin VARCHAR(32) NOT NULL CHECK (case_origin='public_real_contract'),
    execution_origin VARCHAR(24) NOT NULL CHECK (execution_origin='local_algorithm'),
    character_budget INT NULL CHECK (character_budget>0),
    expected_rows INT NOT NULL CHECK (expected_rows BETWEEN 1 AND 5000),
    packet_hash BINARY(32) NOT NULL,
    imported_at DATETIMEOFFSET(3) NOT NULL DEFAULT SYSDATETIMEOFFSET()
);
IF OBJECT_ID('evaluation.configuration') IS NULL
CREATE TABLE evaluation.configuration (
    run_id VARCHAR(32) NOT NULL REFERENCES evaluation.experiment_run(run_id),
    config_id VARCHAR(64) NOT NULL,
    config_role VARCHAR(24) NOT NULL CHECK (config_role IN ('baseline','candidate','development_only')),
    PRIMARY KEY (run_id,config_id)
);
IF OBJECT_ID('evaluation.case_result') IS NULL
CREATE TABLE evaluation.case_result (
    run_id VARCHAR(32) NOT NULL,
    case_id VARCHAR(100) NOT NULL,
    config_id VARCHAR(64) NOT NULL,
    document_id VARCHAR(32) NOT NULL,
    dataset_partition VARCHAR(16) NOT NULL CHECK (dataset_partition IN ('development','evaluation')),
    category VARCHAR(64) NOT NULL,
    length_quartile INT NOT NULL CHECK (length_quartile BETWEEN 1 AND 4),
    document_characters INT NOT NULL CHECK (document_characters>0),
    response_state VARCHAR(10) NOT NULL CHECK (response_state IN ('ok','error','missing')),
    is_answerable INT NOT NULL CHECK (is_answerable IN (0,1)),
    gold_character_recall FLOAT NULL,
    any_gold_hit INT NULL,
    all_gold_covered INT NULL,
    context_precision FLOAT NULL,
    retrieved_characters INT NOT NULL CHECK (retrieved_characters>=0),
    retrieval_latency_ms FLOAT NULL CHECK (retrieval_latency_ms>=0),
    PRIMARY KEY (run_id,case_id,config_id),
    FOREIGN KEY (run_id,config_id) REFERENCES evaluation.configuration(run_id,config_id),
    CONSTRAINT ck_evaluation_gold CHECK (
        (is_answerable=0 AND gold_character_recall IS NULL AND any_gold_hit IS NULL
         AND all_gold_covered IS NULL AND context_precision IS NULL) OR
        (is_answerable=1 AND gold_character_recall IS NOT NULL AND gold_character_recall BETWEEN 0 AND 1
         AND any_gold_hit IS NOT NULL AND any_gold_hit IN (0,1)
         AND all_gold_covered IS NOT NULL AND all_gold_covered IN (0,1)
         AND context_precision IS NOT NULL AND context_precision BETWEEN 0 AND 1)),
    CONSTRAINT ck_evaluation_failure CHECK (response_state='ok' OR
        (retrieved_characters=0 AND (is_answerable=0 OR
         (gold_character_recall=0 AND any_gold_hit=0 AND all_gold_covered=0 AND context_precision=0)))),
    CONSTRAINT ck_evaluation_size CHECK (retrieved_characters<=document_characters),
    CONSTRAINT ck_evaluation_timing CHECK (response_state<>'ok' OR retrieval_latency_ms IS NOT NULL)
);
GO
CREATE OR ALTER PROCEDURE evaluation.usp_ingest @packet NVARCHAR(MAX)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF ISJSON(@packet)<>1 OR ISNULL(JSON_VALUE(@packet,'$.format'),'')<>'cuad-sql-packet-v1'
        THROW 51100,'Invalid evaluation packet',1;
    DECLARE @run VARCHAR(32)=JSON_VALUE(@packet,'$.run.run_id');
    DECLARE @hash BINARY(32)=HASHBYTES('SHA2_256',CONVERT(VARBINARY(MAX),@packet));
    DECLARE @count INT=TRY_CONVERT(INT,JSON_VALUE(@packet,'$.run.expected_rows'));
    DECLARE @actual INT=(SELECT COUNT(*) FROM OPENJSON(@packet,'$.cases'));
    IF @count IS NULL OR @count<>@actual OR @count NOT BETWEEN 1 AND 5000
        THROW 51101,'Evaluation row count mismatch',1;
    BEGIN TRY
        BEGIN TRANSACTION;
        DECLARE @lock INT;
        EXEC @lock=sys.sp_getapplock @Resource='legalai_evaluation_ingest',
             @LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=10000;
        IF @lock<0 THROW 51102,'Cannot acquire evaluation import lock',1;
        IF EXISTS(SELECT 1 FROM evaluation.experiment_run WHERE run_id=@run)
        BEGIN
            IF EXISTS(SELECT 1 FROM evaluation.experiment_run WHERE run_id=@run AND packet_hash<>@hash)
                THROW 51103,'Run identity conflicts with existing content',1;
            COMMIT;
            SELECT @run AS run_id,0 AS inserted_rows FOR JSON PATH,WITHOUT_ARRAY_WRAPPER;
            RETURN;
        END;
        INSERT evaluation.experiment_run
            (run_id,run_label,phase,protocol_sha256,started_at,case_origin,execution_origin,
             character_budget,expected_rows,packet_hash)
        SELECT run_id,run_label,phase,protocol_sha256,started_at,case_origin,execution_origin,
               character_budget,expected_rows,@hash
        FROM OPENJSON(@packet,'$.run') WITH (
            run_id VARCHAR(32),run_label NVARCHAR(80),phase VARCHAR(24),protocol_sha256 CHAR(64),
            started_at DATETIMEOFFSET(3),case_origin VARCHAR(32),execution_origin VARCHAR(24),
            character_budget INT,expected_rows INT);
        INSERT evaluation.configuration (run_id,config_id,config_role)
        SELECT @run,config_id,config_role FROM OPENJSON(@packet,'$.configurations')
        WITH (config_id VARCHAR(64),config_role VARCHAR(24));
        INSERT evaluation.case_result
            (run_id,case_id,config_id,document_id,dataset_partition,category,length_quartile,
             document_characters,response_state,is_answerable,gold_character_recall,any_gold_hit,
             all_gold_covered,context_precision,retrieved_characters,retrieval_latency_ms)
        SELECT @run,case_id,config_id,document_id,dataset_partition,category,length_quartile,
               document_characters,response_state,is_answerable,gold_character_recall,any_gold_hit,
               all_gold_covered,context_precision,retrieved_characters,retrieval_latency_ms
        FROM OPENJSON(@packet,'$.cases') WITH (
            case_id VARCHAR(100),config_id VARCHAR(64),document_id VARCHAR(32),dataset_partition VARCHAR(16),
            category VARCHAR(64),length_quartile INT,document_characters INT,response_state VARCHAR(10),
            is_answerable INT,gold_character_recall FLOAT,any_gold_hit INT,all_gold_covered INT,
            context_precision FLOAT,retrieved_characters INT,retrieval_latency_ms FLOAT);
        IF @@ROWCOUNT<>@count THROW 51104,'Incomplete evaluation import',1;
        IF EXISTS (SELECT case_id FROM evaluation.case_result WHERE run_id=@run
                   GROUP BY case_id HAVING COUNT(*)<>(SELECT COUNT(*) FROM evaluation.configuration WHERE run_id=@run))
            THROW 51105,'Unpaired evaluation cases',1;
        IF EXISTS (SELECT 1 FROM evaluation.case_result c JOIN evaluation.experiment_run r ON r.run_id=c.run_id
                   WHERE c.run_id=@run AND r.character_budget IS NOT NULL AND c.response_state='ok'
                   AND c.retrieved_characters<>CASE WHEN c.document_characters<r.character_budget
                                                   THEN c.document_characters ELSE r.character_budget END)
            THROW 51106,'Character budget mismatch',1;
        COMMIT;
        SELECT @run AS run_id,@count AS inserted_rows FOR JSON PATH,WITHOUT_ARRAY_WRAPPER;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT>0 ROLLBACK;
        THROW;
    END CATCH;
END;
GO
CREATE OR ALTER VIEW evaluation.vw_case_results AS
SELECT r.run_label,r.phase,r.protocol_sha256,r.started_at,r.case_origin,r.execution_origin,
       r.character_budget,cfg.config_role,c.*
FROM evaluation.case_result c
JOIN evaluation.experiment_run r ON r.run_id=c.run_id
JOIN evaluation.configuration cfg ON cfg.run_id=c.run_id AND cfg.config_id=c.config_id;
GO
CREATE OR ALTER VIEW evaluation.vw_config_summary AS
SELECT run_id,run_label,phase,dataset_partition,config_id,config_role,
       COUNT(*) AS query_rows,COUNT(DISTINCT case_id) AS questions,COUNT(DISTINCT document_id) AS documents,
       SUM(is_answerable) AS answerable,SUM(1-is_answerable) AS unanswerable,
       SUM(CASE WHEN response_state='error' THEN 1 ELSE 0 END) AS failed_queries,
       SUM(CASE WHEN response_state='missing' THEN 1 ELSE 0 END) AS missing_queries,
       AVG(gold_character_recall) AS macro_gold_character_recall,
       AVG(CONVERT(FLOAT,all_gold_covered)) AS all_gold_covered_rate,
       AVG(CONVERT(FLOAT,any_gold_hit)) AS any_gold_hit_rate,
       AVG(context_precision) AS macro_context_precision,
       AVG(CONVERT(FLOAT,retrieved_characters)) AS mean_retrieved_characters
FROM evaluation.vw_case_results
GROUP BY run_id,run_label,phase,dataset_partition,config_id,config_role;
GO
