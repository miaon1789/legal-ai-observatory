/*  Legal AI Operations Observatory — star schema
    Six dimensions, five fact tables, and one result table.

    CHECK constraints carry the domains that the KPI definitions depend on. A
    review policy of 'Mandatory' that a load could quietly turn into 'mandatory'
    would break every governance measure silently, so the database refuses it.   */

IF DB_ID('LegalAIObservatory') IS NULL
    EXEC('CREATE DATABASE LegalAIObservatory');
GO
USE LegalAIObservatory;
GO

DROP TABLE IF EXISTS dbo.fact_billable_impact_estimate;
DROP TABLE IF EXISTS dbo.fact_seat_month;
DROP TABLE IF EXISTS dbo.fact_pipeline_run;
DROP TABLE IF EXISTS dbo.fact_incident;
DROP TABLE IF EXISTS dbo.fact_time_entry;
DROP TABLE IF EXISTS dbo.fact_ai_output;
DROP TABLE IF EXISTS dbo.fact_ai_session;
DROP TABLE IF EXISTS dbo.dim_alert_rule;
DROP TABLE IF EXISTS dbo.dim_matter;
DROP TABLE IF EXISTS dbo.dim_lawyer;
DROP TABLE IF EXISTS dbo.dim_task_type;
DROP TABLE IF EXISTS dbo.dim_tool;
DROP TABLE IF EXISTS dbo.dim_date;
GO

/* ---------------------------------------------------------------- dimensions */

CREATE TABLE dbo.dim_date (
    date_id          INT          NOT NULL PRIMARY KEY,
    [date]           DATE         NOT NULL,
    [year]           SMALLINT     NOT NULL,
    [quarter]        TINYINT      NOT NULL,
    [month]          TINYINT      NOT NULL,
    month_name       CHAR(3)      NOT NULL,
    iso_year_week    CHAR(8)      NOT NULL,
    day_of_week      TINYINT      NOT NULL,
    is_business_day  BIT          NOT NULL,
    fy_year          SMALLINT     NOT NULL,   -- Australian FY, 1 July - 30 June
    fy_quarter       TINYINT      NOT NULL
);

CREATE TABLE dbo.dim_lawyer (
    lawyer_id                INT           NOT NULL PRIMARY KEY,
    display_name             NVARCHAR(80)  NOT NULL,
    [level]                  VARCHAR(20)   NOT NULL,
    practice_group           VARCHAR(40)   NOT NULL,
    office                   VARCHAR(20)   NOT NULL,
    admitted_year            SMALLINT      NOT NULL,
    standard_charge_rate_aud DECIMAL(9,2)  NOT NULL,
    is_active                BIT           NOT NULL,
    CONSTRAINT ck_lawyer_level CHECK ([level] IN
        ('Partner','Senior Associate','Associate','Graduate'))
);
/*  No policy_cohort column, deliberately. The non-compliant population is held
    out of the warehouse so that Page 5 has to find it from behaviour rather
    than filter on the answer. See docs/DATA_MODEL.md section 5.               */

CREATE TABLE dbo.dim_tool (
    tool_id            INT          NOT NULL PRIMARY KEY,
    tool_name          VARCHAR(30)  NOT NULL,
    deployment_date    DATE         NOT NULL,
    cost_per_1k_input  DECIMAL(9,5) NOT NULL,
    cost_per_1k_output DECIMAL(9,5) NOT NULL,
    licence_cost_per_seat_month DECIMAL(9,2) NOT NULL
);

CREATE TABLE dbo.dim_task_type (
    task_type_id       INT          NOT NULL PRIMARY KEY,
    task_name          VARCHAR(40)  NOT NULL,
    risk_class         VARCHAR(10)  NOT NULL,
    review_policy_base VARCHAR(12)  NOT NULL,
    review_type        VARCHAR(30)  NOT NULL,
    CONSTRAINT ck_task_risk   CHECK (risk_class IN ('Low','Medium','High')),
    CONSTRAINT ck_task_policy CHECK (review_policy_base IN
        ('None','Optional','Mandatory'))
);
/*  review_policy_base is a default, not the applicable policy. The policy that
    applies is computed in vw_effective_review_policy.                         */

CREATE TABLE dbo.dim_matter (
    matter_id            INT           NOT NULL PRIMARY KEY,
    matter_name          NVARCHAR(120) NOT NULL,
    practice_area        VARCHAR(40)   NOT NULL,
    client_sector        VARCHAR(40)   NOT NULL,
    fee_arrangement      VARCHAR(20)   NOT NULL,
    confidentiality_tier VARCHAR(20)   NOT NULL,
    barrier_group_id     INT           NULL,
    lead_lawyer_id       INT           NOT NULL,
    opened_date          DATE          NOT NULL,
    closed_date          DATE          NULL,
    CONSTRAINT fk_matter_lead FOREIGN KEY (lead_lawyer_id)
        REFERENCES dbo.dim_lawyer (lawyer_id),
    CONSTRAINT ck_matter_fee  CHECK (fee_arrangement IN
        ('Hourly','Capped','Fixed Fee')),
    CONSTRAINT ck_matter_tier CHECK (confidentiality_tier IN
        ('Standard','Restricted','Barrier')),
    CONSTRAINT ck_matter_barrier CHECK (
        (confidentiality_tier = 'Barrier' AND barrier_group_id IS NOT NULL)
     OR (confidentiality_tier <> 'Barrier' AND barrier_group_id IS NULL))
);

CREATE TABLE dbo.dim_alert_rule (
    rule_id         INT            NOT NULL PRIMARY KEY,
    rule_name       NVARCHAR(120)  NOT NULL UNIQUE,
    category        VARCHAR(20)    NOT NULL,
    condition_text  NVARCHAR(200)  NOT NULL,
    threshold_value DECIMAL(12,4)  NOT NULL,
    comparison      VARCHAR(4)     NOT NULL,
    severity        VARCHAR(10)    NOT NULL,
    owner_role      VARCHAR(40)    NOT NULL,
    rationale       NVARCHAR(1000) NOT NULL,
    CONSTRAINT ck_rule_category CHECK (category IN
        ('Operational','Governance','Quality','Cost'))
);

/* -------------------------------------------------------------------- facts */

CREATE TABLE dbo.fact_ai_session (
    session_id    INT           NOT NULL PRIMARY KEY,
    lawyer_id     INT           NOT NULL,
    matter_id     INT           NOT NULL,
    tool_id       INT           NOT NULL,
    task_type_id  INT           NOT NULL,
    date_id       INT           NOT NULL,
    started_at    DATETIME2(0)  NOT NULL,
    tokens_in     INT           NOT NULL,
    tokens_out    INT           NOT NULL,
    cost_aud      DECIMAL(10,4) NOT NULL,
    latency_ms    INT           NOT NULL,
    status        VARCHAR(10)   NOT NULL,
    retry_count   TINYINT       NOT NULL,
    error_type    VARCHAR(30)   NULL,
    source_system VARCHAR(30)   NOT NULL,
    CONSTRAINT fk_sess_lawyer FOREIGN KEY (lawyer_id)    REFERENCES dbo.dim_lawyer(lawyer_id),
    CONSTRAINT fk_sess_matter FOREIGN KEY (matter_id)    REFERENCES dbo.dim_matter(matter_id),
    CONSTRAINT fk_sess_tool   FOREIGN KEY (tool_id)      REFERENCES dbo.dim_tool(tool_id),
    CONSTRAINT fk_sess_task   FOREIGN KEY (task_type_id) REFERENCES dbo.dim_task_type(task_type_id),
    CONSTRAINT fk_sess_date   FOREIGN KEY (date_id)      REFERENCES dbo.dim_date(date_id),
    CONSTRAINT ck_sess_status CHECK (status IN ('Success','Error','Timeout')),
    CONSTRAINT ck_sess_error  CHECK (
        (status = 'Success' AND error_type IS NULL)
     OR (status <> 'Success' AND error_type IS NOT NULL))
);

CREATE TABLE dbo.fact_ai_output (
    output_id         INT          NOT NULL PRIMARY KEY,
    session_id        INT          NOT NULL UNIQUE,
    lawyer_id         INT          NOT NULL,
    matter_id         INT          NOT NULL,
    tool_id           INT          NOT NULL,
    task_type_id      INT          NOT NULL,
    date_id           INT          NOT NULL,
    accepted          BIT          NOT NULL,
    edit_distance_pct DECIMAL(5,1) NOT NULL,
    is_client_facing  BIT          NOT NULL,
    reviewed          BIT          NOT NULL,
    reviewed_by_level VARCHAR(20)  NULL,
    minutes_to_review INT          NULL,
    CONSTRAINT fk_out_session FOREIGN KEY (session_id)   REFERENCES dbo.fact_ai_session(session_id),
    CONSTRAINT fk_out_lawyer  FOREIGN KEY (lawyer_id)    REFERENCES dbo.dim_lawyer(lawyer_id),
    CONSTRAINT fk_out_matter  FOREIGN KEY (matter_id)    REFERENCES dbo.dim_matter(matter_id),
    CONSTRAINT fk_out_tool    FOREIGN KEY (tool_id)      REFERENCES dbo.dim_tool(tool_id),
    CONSTRAINT fk_out_task    FOREIGN KEY (task_type_id) REFERENCES dbo.dim_task_type(task_type_id),
    CONSTRAINT fk_out_date    FOREIGN KEY (date_id)      REFERENCES dbo.dim_date(date_id)
);
/*  The dimension keys are carried here as well as on fact_ai_session. Without
    them this fact reaches the dimensions only through another fact, and
    filtering acceptance by level would need a bidirectional relationship in
    Power BI -- the ambiguity a star schema exists to avoid.                   */

CREATE TABLE dbo.fact_time_entry (
    time_entry_id INT          NOT NULL PRIMARY KEY,
    lawyer_id     INT          NOT NULL,
    matter_id     INT          NOT NULL,
    date_id       INT          NOT NULL,
    hours         DECIMAL(6,2) NOT NULL,
    billable      BIT          NOT NULL,
    task_code     VARCHAR(40)  NOT NULL,
    is_post_adoption BIT       NOT NULL,   -- recorded after this lawyer's first
                                           -- AI session on this matter
    CONSTRAINT fk_te_lawyer FOREIGN KEY (lawyer_id) REFERENCES dbo.dim_lawyer(lawyer_id),
    CONSTRAINT fk_te_matter FOREIGN KEY (matter_id) REFERENCES dbo.dim_matter(matter_id),
    CONSTRAINT fk_te_date   FOREIGN KEY (date_id)   REFERENCES dbo.dim_date(date_id)
);

CREATE TABLE dbo.fact_incident (
    incident_id     INT         NOT NULL PRIMARY KEY,
    session_id      INT         NULL,
    matter_id       INT         NOT NULL,
    lawyer_id       INT         NOT NULL,
    date_id         INT         NOT NULL,
    incident_type   VARCHAR(30) NOT NULL,
    severity        TINYINT     NOT NULL,
    days_to_resolve INT         NULL,
    resolved        BIT         NOT NULL,
    CONSTRAINT fk_inc_session FOREIGN KEY (session_id) REFERENCES dbo.fact_ai_session(session_id),
    CONSTRAINT fk_inc_matter  FOREIGN KEY (matter_id)  REFERENCES dbo.dim_matter(matter_id),
    CONSTRAINT fk_inc_lawyer  FOREIGN KEY (lawyer_id)  REFERENCES dbo.dim_lawyer(lawyer_id),
    CONSTRAINT fk_inc_date    FOREIGN KEY (date_id)    REFERENCES dbo.dim_date(date_id),
    CONSTRAINT ck_inc_sev     CHECK (severity BETWEEN 1 AND 4),
    CONSTRAINT ck_inc_res     CHECK (
        (resolved = 1 AND days_to_resolve IS NOT NULL)
     OR (resolved = 0 AND days_to_resolve IS NULL))
);

CREATE TABLE dbo.fact_pipeline_run (
    run_id               INT          NOT NULL PRIMARY KEY,
    source_system        VARCHAR(30)  NOT NULL,
    run_date             DATE         NOT NULL,
    date_id              INT          NOT NULL,
    rows_loaded          INT          NOT NULL,
    rows_rejected        INT          NOT NULL,
    null_rate_key_fields DECIMAL(8,5) NOT NULL,
    referential_failures INT          NOT NULL,
    run_status           VARCHAR(10)  NOT NULL,
    duration_seconds     INT          NOT NULL,
    CONSTRAINT fk_run_date FOREIGN KEY (date_id) REFERENCES dbo.dim_date(date_id),
    CONSTRAINT ck_run_status CHECK (run_status IN ('Success','Warning','Failed'))
);

CREATE TABLE dbo.fact_seat_month (
    seat_month_id    INT          NOT NULL PRIMARY KEY,
    lawyer_id        INT          NOT NULL,
    tool_id          INT          NOT NULL,
    date_id          INT          NOT NULL,   -- first day of the month
    seats            INT          NOT NULL,
    licence_cost_aud DECIMAL(9,2) NOT NULL,
    used_in_month    BIT          NOT NULL,
    CONSTRAINT fk_seat_lawyer FOREIGN KEY (lawyer_id) REFERENCES dbo.dim_lawyer(lawyer_id),
    CONSTRAINT fk_seat_tool   FOREIGN KEY (tool_id)   REFERENCES dbo.dim_tool(tool_id),
    CONSTRAINT fk_seat_date   FOREIGN KEY (date_id)   REFERENCES dbo.dim_date(date_id),
    CONSTRAINT uq_seat UNIQUE (lawyer_id, tool_id, date_id)
);
/*  A licensed seat, whether or not anyone opened it. Token spend is under 1% of
    what a firm pays for these tools; the cost that decides whether a rollout is
    defensible is per-seat licensing, and the seats nobody used are invisible on
    any dashboard built from the session table alone.                          */

CREATE TABLE dbo.fact_billable_impact_estimate (
    estimate_id        INT           NOT NULL PRIMARY KEY,
    fee_arrangement    VARCHAR(20)   NOT NULL,
    estimate_pct       DECIMAL(8,2)  NOT NULL,
    ci_low_pct         DECIMAL(8,2)  NOT NULL,
    ci_high_pct        DECIMAL(8,2)  NOT NULL,
    se_pct             DECIMAL(8,2)  NOT NULL,
    treated_entries    INT           NOT NULL,
    control_entries    INT           NOT NULL,
    metric             NVARCHAR(60)  NOT NULL,
    method             NVARCHAR(80)  NOT NULL,
    fixed_effects      NVARCHAR(80)  NOT NULL,
    control_definition NVARCHAR(250) NOT NULL,
    n_cells            INT           NOT NULL,
    n_clusters         INT           NOT NULL,
    generated_on       DATE          NOT NULL
);
/*  A fitted result, not an observation. A fixed-effects regression with
    clustered standard errors is not something a view can compute, and a point
    estimate without its control group and sample size is not auditable, so the
    provenance is stored in the row.                                           */

/* ------------------------------------------------------------------ indexes */

CREATE INDEX ix_sess_date   ON dbo.fact_ai_session (date_id) INCLUDE (cost_aud, latency_ms, status);
CREATE INDEX ix_sess_lawyer ON dbo.fact_ai_session (lawyer_id, date_id);
CREATE INDEX ix_sess_matter ON dbo.fact_ai_session (matter_id);
CREATE INDEX ix_out_date    ON dbo.fact_ai_output  (date_id) INCLUDE (accepted, reviewed, edit_distance_pct);
CREATE INDEX ix_out_lawyer  ON dbo.fact_ai_output  (lawyer_id);
CREATE INDEX ix_out_matter  ON dbo.fact_ai_output  (matter_id);
CREATE INDEX ix_te_lawyer   ON dbo.fact_time_entry (lawyer_id, date_id) INCLUDE (hours, billable);
CREATE INDEX ix_te_matter   ON dbo.fact_time_entry (matter_id);
CREATE INDEX ix_run_source  ON dbo.fact_pipeline_run (source_system, run_date);
CREATE INDEX ix_seat_date   ON dbo.fact_seat_month (date_id, tool_id) INCLUDE (licence_cost_aud, used_in_month);
GO
