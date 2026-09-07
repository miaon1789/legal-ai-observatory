# Observability Validation Record

Checked locally on 7 September 2026 (Australia/Sydney), using Python 3.12.2,
the project's existing SQL Server Docker container, and OpenAI SDK 2.54.0.
This is an implementation check, not a live model-quality evaluation.

## Automated checks

```bash
RUN_SQL_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

Result: **40 tests passed**: 17 offline/adapter tests, 14 telemetry SQL tests and
9 pre-existing reporting regression tests. SQL tests use disposable databases.
The SDK test uses local HTTP mock transport; no real API request was made.

## End-to-end local drill

The completed demo experiment `demo-30e705266ceb` produced 72 oracle requests
across 36 cases and two configurations. A separate `fault_drill` scope exercised
a final failure and a recovered retry. The private JSONL also retains an earlier
72-request setup run, so whole-database totals must not be read as this experiment.

| Check | Observed outcome |
|---|---|
| Same JSONL imported again | Zero new events; no duplicate requests. |
| Failed heartbeat and failed request | Both scoped alert rules opened. |
| Healthy heartbeat and successful retry | Both rules resolved; four transitions in total. |
| Malformed JSONL | Rejected before event import; failure recorded. |
| Subsequent valid replay | Import status returned to success; recovery timestamp recorded. |
| Original `dbo` tables | All 15 table row counts unchanged across deployment. |
| Live-origin requests in project database | Zero. |
| Genuine human review in the demo | Zero; scripted corrections are labelled separately. |

The full-batch drill revealed a sqlcmd input-line wrapping issue that small
requests did not expose. The bridge now assembles bounded string fragments
server-side before invoking one transaction. A 72-request batch/replay regression
test covers that failure mode.

## Still pending

- A provider/model/budget-approved live experiment and actual human assessments.
- Review of paired coverage, costs and quality on those live runs.
- Building and validating the optional DAX page in Windows Power BI Desktop.
- Broader unseen-document evaluation and production-grade operations/security.

Fixture success rates are plumbing checks, not evidence of improved prompts,
legal reliability, cost savings or real-firm adoption. Raw runtime files remain
under ignored `private/`; this summary contains no credentials or response text.
