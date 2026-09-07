# Contract QA Observability Pilot

## Status and scope

The original five-page report still uses the synthetic `dbo` warehouse.
This extension adds an independently runnable contract-clause extraction task
and a separate `telemetry` schema in the same SQL Server database. It does not
load requests into `dbo.fact_ai_session` or imply access to a real firm's logs.

Implemented and tested locally: offline runner, optional live adapter, append-only
logs, transactional idempotent import, versioned reviews, request/attempt views,
configuration comparison, fault/recovery exercises, and private exports.
The optional Power BI page has paste-ready DAX; it still needs to be built and
checked in Windows. No live API calls or actual human evaluations were performed
as part of implementation. There is no measured prompt improvement to report yet.
See the [validation record](OBSERVABILITY_VALIDATION.md) for the checked results.

External data is a separate, pending stage. The [data-use and hybrid evaluation
plan](DATA_GOVERNANCE.md) distinguishes synthetic regression cases from a proposed
CUAD sample and requires separate acquisition, external-processing and publication
checks. [Local intake](CUAD_INTAKE_REVIEW.md) has triaged eight training documents;
no external contract ingestion into this runner is enabled by that work.
A separate [CUAD preparation tool](CUAD_PREPARATION.md) now writes a private review
bundle, not telemetry events. Its multi-span references are not compatible with
the current single-answer synthetic grader and must not be relabelled synthetic.
An independent [multi-evidence scorer](EVIDENCE_EVALUATION.md) is now validated on
fictional scripted fixtures. It does not replace this pilot's response schema,
write telemetry events or enable live/real-contract evaluation.

## Two independent provenance labels

| Field | Meaning |
|---|---|
| `data_origin=simulated` | Oracle fixture or controlled failure. Not an AI inference. |
| `data_origin=observed` | An attempted live API call made by this runner. Not necessarily successful. |
| `case_origin=self_authored_synthetic` | Fictional contract text and questions, including on live runs. |
| `run_kind=benchmark` | Configuration comparison, separated from operational drills. |
| `run_kind=fault_drill` | Deliberate failure, retry, stale-feed or recovery exercise. |
| `review_method=human` | A person entered an assessment. Identity is self-declared, not authenticated. |
| `review_method=scripted_fixture` | Demonstration feedback, excluded from all human-review measures. |

The `pilot` run kind is reserved for future integrations. The CLI currently runs
only the bundled benchmark; it does not accept client contracts or free-text prompts.

## Task and experimental design

`benchmarks/contract_qa/v1.json` contains six self-authored fictional documents
and 36 questions. Each document covers payment, convenience termination,
liability cap, renewal, confidentiality, and an absent fact requiring abstention.
One distinguishes a superseded draft from an operative term; another includes
an explicitly untrusted instruction to test document-instruction separation.
These are narrow extraction smoke tests, not independent legal-domain validation.

`baseline-v1` asks for an answer and supporting quote. `grounded-v2` adds explicit
grounding, abstention and untrusted-document instructions. Both use the same
structured output schema. Only the document and question go to the API, not
the expected answer. The fixture deliberately reads the expected answer; its
100% score tests the plumbing, not a model or the better prompt.

Within each experiment the runner alternates configuration order by case and
repetition. Both configurations receive the same cases. Case-pack and prompt
hashes identify the exact materials; a run-settings hash separates timeout,
output-token and retry regimes. Requested and resolved model names are retained.
The private experiment manifest records settings, case IDs and order.

`exports/comparison_checks.json` checks matched case repetitions, stable prompt
versions and a single resolved model. A ready flag means descriptive comparison
is possible, not statistically proven improvement. Compare the same experiment,
provenance, task version, provider, model and run settings. Repeated questions
from one document are not independent observations. Broader claims require
unseen documents, more repeated runs and a pre-agreed evaluation rubric.

## Data flow and contracts

```text
versioned fictional documents + questions
    -> replay OR explicitly enabled OpenAI Responses call
    -> private JSONL request/review/heartbeat events
    -> Python validation -> one SQL import transaction
    -> telemetry.event_log (immutable event IDs and payload hashes)
    -> latest-request and latest-review projections
    -> request/attempt metrics + scoped alert transitions
    -> Power BI Import / private CSV and JSON exports
```

`request.completed` includes every attempted call, including failures and retries.
Request latency is end-to-end elapsed time including retry backoff. An SDK timeout
may have unknown usage even if the provider charged for the attempted request.
Automatic SDK retries are disabled so they cannot silently inflate cost.

`review.recorded` refers to a request ID, exact request revision and answer hash.
Review corrections append a higher review revision. Late old revisions do not
replace new ones. Updating a request revision invalidates the old review until
the new answer is reviewed. Events with the same ID and same canonical content
are ignored on replay; ID/content conflicts or duplicate logical revisions with
different event IDs reject the whole import. Source, origin, run kind and trace
cannot change between events for the same request.

The importer re-reads a pilot-sized JSONL file; only new event IDs are inserted.
This is idempotent incremental insertion, **not** CDC or an offset-based streaming
consumer. It validates the entire file before importing. A malformed complete
line or unterminated final line defers the entire file; nothing is partially
accepted. Files are limited to 20 MiB and individual events to 32 KB. Split/rotate
logs before exceeding this limit. Use one local writer/reviewer per directory;
append operations are locked, but concurrent review-number allocation is not a
multi-user service. SQL rejects revision collisions instead of silently merging.

## Metrics

| Metric | Definition / missing-data treatment |
|---|---|
| Request failure rate | Final failed logical requests / all completed logical requests. |
| Failed attempts | All failed attempts, including those recovered by a later retry. |
| P95 request ms | Inclusive interpolated 95th percentile of all completed requests, including failures and backoff. |
| Automatic pass rate | Exact normalized answer + expected clause + exact nonempty quote substring, or correct abstention, divided by all requests. Failures are not passes. |
| Human pass rate | Passed / actually assessed answers; unreviewed is NULL, not failure. |
| Human review coverage | Answers with a human task-pass decision / successful requests. |
| Citation accuracy | Correct / human-reviewed citation decisions with a known value. |
| Major rework rate | Major-rework decisions / human-reviewed rework decisions with a known value. |
| Edit ratio | `1 - SequenceMatcher(original answer text, edited answer text).ratio()`, with autojunk disabled. Not Levenshtein or a literal percentage of words rewritten. |
| Estimated API cost | Sum of every attempt's tokens at the explicitly supplied versioned rate card; NULL if any attempt is unpriced or currencies differ. |
| Known estimated cost | Sum of only priced attempts, for a single known currency. A partial lower bound, never a substitute for total spend. |
| Cost per automatic pass | Complete estimated cost / automatic passes. NULL when cost is incomplete or there are no passes. |
| Cost per human pass | Complete estimated cost / human passes, only after all successful answers have a human task decision. Failed request costs stay in the numerator. |

Token counts include all attempts only if the respective counts are complete.
Zero known usage is different from unknown usage. No currency conversion is
performed. These API-only estimates exclude licences, tax, reviewer labour and
other platform costs, and are not provider invoices. Do not combine them with
the baseline report's AUD licence-plus-token cost measure.

Automatic grading checks that a quote occurs in the expected clause, not that
every word in that quote logically entails the answer. Human review must assess
entailment, misleading omissions and whether the answer is usable for the task.

## Alert semantics

`telemetry.usp_check_source` evaluates one explicit source/origin/run-kind scope:

- `heartbeat_stale`: no heartbeat, latest heartbeat marked failed, or older than
  the chosen threshold (default 10 minutes). A healthy fresh heartbeat resolves it.
- `latest_request_failed`: latest completed request failed; a later successful
  request resolves it. This is not a rolling error-rate threshold or SLO.

Repeated checks of an unchanged state do not duplicate transitions.
Monitoring timestamps must not move backwards. `alert_state` is current state;
`alert_transition` is the open/resolved audit trail. There are no automatic
notifications or scheduler. Run `monitor` explicitly; refreshing Power BI only
reads the last evaluated state. Use heartbeat monitoring only while a source is
expected to be running, not to call a finished one-off benchmark an outage.

`vw_ingestion_health` separately reports the latest import status, last failure
and first successful import after that failure. It is database-wide operational
health, not an experiment metric. A successful replay proves the importer works;
it does not prove new source data arrived. If SQL Server is unreachable, the CLI
exits nonzero and records a private local error category; a dead server cannot
write its own failure into a table. This pilot therefore has no out-of-band outage
notification. Raw events remain available for replay after recovery.

## Run locally (Mac, from repository root)

The offline commands use the existing Python environment and standard library.
SQL commands require the existing running `mssql-legalai` container. Do **not**
run the original `sql/run_all.sh` to install this extension; it rebuilds the
baseline tables. No Windows changes are required for these commands.

```bash
.venv/bin/python src/observed_runs.py demo --with-sql
.venv/bin/python src/observed_runs.py ingest
.venv/bin/python src/observed_runs.py export
```

Each demo creates a new experiment: 72 oracle requests, two scripted review
revisions, then a separate failure/retry/recovery drill. The second import of
the same log must insert zero events. It also rejects a deliberately malformed
file and proves import recovery. `private/observability/last-demo.json` contains
the latest experiment ID and all checks. Logs contain previous demo runs too;
filter by experiment before comparing configurations.

For an isolated demo directory or a different existing database, global options
go **before** the subcommand:

```bash
.venv/bin/python src/observed_runs.py --directory private/qa-demo --database LegalAIObservatory demo --with-sql
```

`deploy` can be repeated; it only creates/alters the additive telemetry objects.
`CONTAINER` can override the container name. SQL authentication is read inside
the local container from its existing `MSSQL_SA_PASSWORD`; it is not printed.
The local bridge uses the existing administrator account for convenience.
Production deployment requires separate migration and least-privilege runtime
accounts, authenticated reviewers, migrations and backups, not this admin bridge.

### Optional live run: obtain approval before execution

Choose a provider, a compatible structured-output model and a spending budget
first. The implementation currently supports only OpenAI; another provider
needs an adapter. A ChatGPT subscription is not an API key. Do not put a key
in a command argument, source file, screenshot or chat message.

```bash
.venv/bin/python -m pip install -r requirements-observability.txt

# zsh: enter the key at the hidden terminal prompt, not in shell history.
read -s 'OPENAI_API_KEY?OpenAI API key: '
export OPENAI_API_KEY

# Replace YOUR_APPROVED_MODEL only after approving the model and spend.
.venv/bin/python src/observed_runs.py run --provider openai --allow-live \
  --model YOUR_APPROVED_MODEL --case-limit 1 --max-requests 2 --save-responses

unset OPENAI_API_KEY
```

This starts with one question per configuration (two logical requests), no
retries, a 45-second per-attempt timeout and at most 1,200 output tokens per call.
Caps bound request count and output tokens, **not a hard monetary budget**.
Timeouts and network failures may still incur provider costs. Set appropriate
provider-side spending controls before a larger run. After checking the two-call
smoke test, the full benchmark needs explicit `--case-limit 36 --max-requests 72`.
`--repeats` increases the required cap. Interrupted runs can have incomplete
case pairing; inspect `comparison_checks.json` rather than comparing raw totals.

The client sends `store=False`; this is not a promise of zero provider retention.
See the official [Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create),
[structured output guide](https://developers.openai.com/api/docs/guides/structured-outputs)
and [endpoint data policies](https://platform.openai.com/docs/models/default-usage-policies-by-endpoint).
Only the bundled fictional material should be sent. External integration needs
privacy/consent review, redaction, access control and explicit data provenance.

### Optional rate card

Without `--rates private/observability/rates.json`, costs stay unknown. Create a
local JSON object with `version`, exact resolved `model`, `currency` (three
uppercase letters), `effective_from` (timezone-aware ISO date/time), and numeric
`input_per_million`, `cached_input_per_million`, `output_per_million`. Supply
verified rates for the approved model, not invented demonstration prices. A
future effective date or resolved-model mismatch leaves cost NULL. Pricing is
attached at collection time; later editing a rate file does not rewrite old logs.

### Human feedback

Read the fictional source document, its question, and the saved answer in
`private/observability/responses/REQUEST_UUID.json`. Decide task pass, citation
correctness and major rework independently. For absent-fact questions, citations
can be `unknown` when not applicable. A pass requires the answer to be correct,
grounded and usable without material correction. Use a pseudonymous reviewer ID.

```bash
.venv/bin/python src/observed_runs.py review REQUEST_UUID --reviewer reviewer-01 \
  --task-pass yes --citations-correct yes --major-rework no --reason verified
.venv/bin/python src/observed_runs.py ingest
.venv/bin/python src/observed_runs.py export
```

Re-run `review` for the same UUID to correct a decision; it appends a new review
revision. To calculate the optional text-change proxy, add `--original` and
`--edited`, each pointing to a local answer JSON file with the original schema.
Do not change the original saved answer; its hash must match the request event.
Human assessment is not legal advice or permission to use a generated answer
without the safeguards appropriate to its actual context.

### Monitoring and Power BI refresh

```bash
.venv/bin/python src/observed_runs.py ingest
.venv/bin/python src/observed_runs.py monitor --source contract-qa --origin observed --kind benchmark
.venv/bin/python src/observed_runs.py export
```

Only run the observed-source example after real calls exist and while that source
is expected to be active. The offline demo already records its own scoped checks.
For an active collector, `heartbeat --source ... --origin ... --kind ...` appends
a heartbeat; it still needs ingestion before SQL sees it.

For a new Power BI page, import `telemetry.vw_requests` as `obs_requests`, then
create measures from `powerbi/observability_measures.dax` one at a time. Keep it
disconnected from the old `dbo` model; do not relate its dates to the old synthetic
calendar. Use single selections for origin and experiment, filter `run_kind` to
`benchmark`, and show the evidence label in the title. Compare configurations
using the measures, not sums/averages of pre-aggregated rates or P95 values.

For operational drill evidence, import `telemetry.alert_transition` and
`telemetry.vw_ingestion_health` into a separate page/table with its own scope.
The ingestion health table is database-wide and must not inherit an experiment
filter. Save an extended PBIX under `private/` until its embedded data has been
reviewed for publication. `.gitignore` does not sanitize an already tracked PBIX.
Connection steps follow Microsoft's [SQL Server connector documentation](https://learn.microsoft.com/en-us/power-query/connectors/sql-server);
the request-percentile measure uses [PERCENTILEX.INC](https://learn.microsoft.com/en-us/dax/percentilex-inc-function-dax).

## Privacy, limits and tests

Defaults keep JSONL, response content, review edits, manifests and exports under
the ignored `private/observability/` directory. New runtime files have mode 0600.
Events use an exact metadata allowlist: no prompt, answer, customer identifier,
free-text reviewer comment or raw exception body. The optional response files
are private, not SQL columns. File permissions and `.gitignore` are not encryption,
retention enforcement, authentication or protection against local administrators.
Do not override the output directory to a public path when collecting live data.

The event only records a request when it finishes. Abrupt process death during a
call can leave an unknown-cost request unlogged. An intent/attempt-start journal,
crash reconciliation, proper streaming checkpoints, rate-limit pacing, blinded
review workflow and independent hold-out documents are next-stage work. This is
a bounded local pilot, not production observability or a firm deployment.

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_observability.py' -v
RUN_SQL_TESTS=1 .venv/bin/python -m unittest discover -s tests -p 'test_observability_sql.py' -v
```

The SDK test uses local HTTP mock transport, never a paid endpoint. SQL tests
create and remove a uniquely named test database, including fabricated boundary
records; these are not exported as live evidence. The integration suite verifies
large-batch transport, replay, atomic rollback, revision ordering, provenance,
NULL costs, currencies, denominators, P95 and alert recovery. The SQL views are
tested; the optional DAX still requires verification in Power BI Desktop.
