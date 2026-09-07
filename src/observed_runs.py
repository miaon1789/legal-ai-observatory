"""Contract QA pilot CLI. Offline by default; live requests require explicit opt-in."""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
from pathlib import Path
import sys
import uuid

from observability.benchmark import PROMPTS, ROOT, load_benchmark
from observability.events import KINDS, ORIGINS, timestamp
from observability.review import record_review
from observability.runner import OpenAIProvider, ReplayProvider, heartbeat, load_rates, run_case
from observability.warehouse import VIEWS, Warehouse, WarehouseError

DEFAULT_DIRECTORY = ROOT / "private/observability"


def private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def comparison_checks(rows: list[dict]) -> list[dict]:
    groups = collections.defaultdict(list)
    fields = ("source_id", "data_origin", "run_kind", "experiment_id", "case_version",
              "provider", "requested_model", "run_config_version")
    for row in rows:
        groups[tuple(row[key] for key in fields)].append(row)
    checks = []
    for key, group in groups.items():
        configs = sorted({r["config_id"] for r in group})
        coverage = {c: collections.Counter(r["case_id"] for r in group if r["config_id"] == c)
                    for c in configs}
        prompts_stable = all(len({r["prompt_version"] for r in group if r["config_id"] == c}) == 1
                             for c in configs)
        models = sorted({r["response_model"] for r in group if r["response_model"] is not None})
        matched = (len(configs) == 2 and coverage[configs[0]] == coverage[configs[1]])
        checks.append(dict(zip(fields, key), configs=configs, case_counts=coverage,
                           matched_case_repetitions=matched, stable_prompt_versions=prompts_stable,
                           resolved_models=models,
                           descriptive_comparison_ready=matched and prompts_stable and len(models) == 1,
                           live_evidence=key[1] == "observed",
                           caution="Small self-authored benchmark; no causal or legal-quality claim."))
    return checks


def export(warehouse: Warehouse, directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    exported = {}
    requests = []
    for name in VIEWS:
        rows = warehouse.rows(name)
        private_json(directory / f"{name}.json", rows)
        path = directory / f"{name}.csv"
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", newline="") as stream:
            if rows:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        exported[name] = len(rows)
        if name == "requests":
            requests = rows
    private_json(directory / "comparison_checks.json", comparison_checks(requests))
    return exported


def execute_run(args) -> dict:
    if not 1 <= args.case_limit <= 36 or not 1 <= args.repeats <= 10:
        raise ValueError("case-limit must be 1..36 and repeats 1..10")
    if not 1 <= args.max_attempts <= 5 or not 100 <= args.max_output_tokens <= 8000:
        raise ValueError("Invalid attempt or output-token cap")
    if not 1 <= args.timeout <= 120:
        raise ValueError("Timeout must be 1..120 seconds")
    docs, all_cases, version = load_benchmark()
    cases = list(all_cases.values())[:args.case_limit]
    configs = list(PROMPTS) if args.config == "both" else [args.config]
    count = len(cases) * len(configs) * args.repeats
    if count > args.max_requests:
        raise ValueError(f"This run needs {count} logical requests; explicitly increase --max-requests")
    if args.provider == "openai":
        if not args.allow_live or not args.model:
            raise ValueError("Live calls require --allow-live and --model after provider/budget approval")
        provider = OpenAIProvider(args.model, args.timeout, args.max_output_tokens)
    else:
        if args.allow_live or args.model or args.rates:
            raise ValueError("Live flags/rate cards do not belong on an offline fixture run")
        provider = ReplayProvider()
    rates = load_rates(args.rates)
    log = args.directory / "events.jsonl"
    experiment = "exp-" + uuid.uuid4().hex[:16]
    private_json(args.directory / "experiments" / f"{experiment}.json", {
        "experiment_id": experiment, "created_at": timestamp(), "data_origin": provider.data_origin,
        "source_id": args.source, "provider": provider.name, "requested_model": provider.model,
        "case_version": version, "case_ids": [c["case_id"] for c in cases],
        "prompts": {c: PROMPTS[c] for c in configs}, "repeats": args.repeats,
        "max_attempts": args.max_attempts, "timeout_seconds": args.timeout,
        "max_output_tokens": args.max_output_tokens, "planned_requests": count,
        "rate_card": {k: str(v) for k, v in rates.items()} if rates else None,
        "order": "case-major, alternating configuration order by case and repetition",
    })
    heartbeat(log, args.source, provider.data_origin, "benchmark")
    failures = 0
    for repeat in range(args.repeats):
        for index, case in enumerate(cases):
            # Alternate order to reduce (not eliminate) time/order confounding.
            order = configs if (repeat + index) % 2 == 0 else list(reversed(configs))
            for config in order:
                event = run_case(provider, docs[case["document_id"]], case, version, config, log,
                                 experiment_id=experiment, source_id=args.source,
                                 max_attempts=args.max_attempts, rates=rates,
                                 content_dir=args.directory / "responses" if args.save_responses else None)
                failures += event["payload"]["status"] == "failed"
                print(f'{event["request_id"]} {case["case_id"]} {config} '
                      f'{event["data_origin"]} {event["payload"]["status"]}', flush=True)
                heartbeat(log, args.source, provider.data_origin, "benchmark")
    return {"experiment_id": experiment, "data_origin": provider.data_origin, "requests": count,
            "failed_requests": failures,
            "max_possible_api_attempts": count * args.max_attempts if args.provider == "openai" else 0,
            "log": str(log), "note": "Request/token caps are not a monetary spending guarantee."}


def demo(args, warehouse: Warehouse) -> dict:
    docs, cases, version = load_benchmark()
    experiment = "demo-" + uuid.uuid4().hex[:12]
    source = experiment
    log = args.directory / "events.jsonl"
    outcomes = []
    for index, case in enumerate(cases.values()):
        configs = list(PROMPTS) if index % 2 == 0 else list(reversed(PROMPTS))
        for config in configs:
            outcomes.append(run_case(ReplayProvider(), docs[case["document_id"]], case, version, config,
                                     log, experiment_id=experiment, source_id=source,
                                     content_dir=args.directory / "responses"))
    first = outcomes[0]
    # Exercise review corrections, explicitly excluded from human review metrics.
    for decision in (False, True):
        record_review(log, first["request_id"], reviewer_id="fixture-reviewer", task_pass=decision,
                      citations_correct=True, major_rework=False, reason_code="fixture-correction",
                      method="scripted_fixture")
    heartbeat(log, source, "simulated", "benchmark")
    summary = {"experiment_id": experiment, "source_id": source, "data_origin": "simulated",
               "benchmark_requests": len(outcomes), "live_api_calls": 0,
               "human_reviews": 0, "log": str(log)}
    if args.with_sql:
        warehouse.deploy()
        summary["initial_import"] = warehouse.ingest_file(log)
        summary["replay_import"] = warehouse.ingest_file(log)
        if summary["replay_import"]["inserted_count"] != 0:
            raise RuntimeError("Idempotence check failed")
        case = next(iter(cases.values()))
        document = docs[case["document_id"]]
        heartbeat(log, source, "simulated", "fault_drill", status="failed")
        run_case(ReplayProvider(failures=1), document, case, version, "baseline-v1", log,
                 experiment_id=experiment + "-drill", source_id=source, run_kind="fault_drill")
        warehouse.ingest_file(log)
        summary["alerts_open"] = warehouse.monitor(source, "simulated", "fault_drill")
        run_case(ReplayProvider(failures=1), document, case, version, "baseline-v1", log,
                 experiment_id=experiment + "-drill", source_id=source, run_kind="fault_drill",
                 max_attempts=2)
        heartbeat(log, source, "simulated", "fault_drill")
        warehouse.ingest_file(log)
        summary["alerts_recovered"] = warehouse.monitor(source, "simulated", "fault_drill")
        # Actual ingestion failure using a separate disposable malformed fixture file.
        broken = args.directory / "drill-invalid.jsonl"
        private_json(broken, {"not_an_event": True})
        try:
            warehouse.ingest_file(broken)
        except ValueError:
            summary["ingestion_failure"] = warehouse.rows("ingestion")
        else:
            raise RuntimeError("Malformed file was unexpectedly accepted")
        warehouse.ingest_file(log)
        summary["ingestion_recovered"] = warehouse.rows("ingestion")
        if (len(summary["alerts_open"]) != 2 or not all(r["is_open"] for r in summary["alerts_open"])
                or any(r["is_open"] for r in summary["alerts_recovered"])
                or not summary["ingestion_failure"][0]["is_open"]
                or summary["ingestion_recovered"][0]["is_open"]
                or summary["ingestion_recovered"][0]["recovered_at"] is None):
            raise RuntimeError("Fault/recovery verification did not meet expected states")
        summary["exported_rows"] = export(warehouse, args.directory / "exports")
    private_json(args.directory / "last-demo.json", summary)
    return summary


def optional_bool(value: str) -> bool | None:
    return {"yes": True, "no": False, "unknown": None}[value]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    root.add_argument("--database", default="LegalAIObservatory")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("deploy", help="Add telemetry schema; leave dbo and PBIX unchanged")
    d = sub.add_parser("demo", help="36 cases x 2 oracle fixtures; never calls an API")
    d.add_argument("--with-sql", action="store_true")
    r = sub.add_parser("run", help="Run benchmark (offline unless explicitly opted in)")
    r.add_argument("--provider", choices=("replay", "openai"), default="replay")
    r.add_argument("--allow-live", action="store_true")
    r.add_argument("--model")
    r.add_argument("--source", default="contract-qa")
    r.add_argument("--config", choices=(*PROMPTS, "both"), default="both")
    r.add_argument("--case-limit", type=int, default=1)
    r.add_argument("--repeats", type=int, default=1)
    r.add_argument("--max-requests", type=int, default=2)
    r.add_argument("--max-attempts", type=int, default=1)
    r.add_argument("--max-output-tokens", type=int, default=1200)
    r.add_argument("--timeout", type=float, default=45)
    r.add_argument("--rates", type=Path)
    r.add_argument("--save-responses", action="store_true")
    i = sub.add_parser("ingest", help="Validate and replay an entire JSONL file transactionally")
    i.add_argument("--log", type=Path)
    v = sub.add_parser("review", help="Record your actual assessment of a saved answer")
    v.add_argument("request_id")
    v.add_argument("--reviewer", required=True)
    for field in ("task-pass", "citations-correct", "major-rework"):
        v.add_argument("--" + field, choices=("yes", "no", "unknown"), default="unknown")
    v.add_argument("--reason", required=True)
    v.add_argument("--original", type=Path)
    v.add_argument("--edited", type=Path)
    for command in ("heartbeat", "monitor"):
        m = sub.add_parser(command)
        m.add_argument("--source", default="contract-qa")
        m.add_argument("--origin", choices=sorted(ORIGINS), required=True)
        m.add_argument("--kind", choices=sorted(KINDS), default="benchmark")
        if command == "monitor":
            m.add_argument("--stale-minutes", type=int, default=10)
    sub.add_parser("export", help="Write private CSV/JSON exports and comparison coverage checks")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    log = args.directory / "events.jsonl"
    try:
        warehouse = Warehouse(args.database)
        if args.command == "deploy":
            warehouse.deploy()
            result = {"deployed": "telemetry", "database": args.database}
        elif args.command == "demo":
            result = demo(args, warehouse)
        elif args.command == "run":
            result = execute_run(args)
        elif args.command == "ingest":
            result = warehouse.ingest_file(args.log or log)
        elif args.command == "heartbeat":
            result = heartbeat(log, args.source, args.origin, args.kind)
        elif args.command == "monitor":
            result = warehouse.monitor(args.source, args.origin, args.kind,
                                       stale_minutes=args.stale_minutes)
        elif args.command == "review":
            result = record_review(
                log, args.request_id, reviewer_id=args.reviewer,
                task_pass=optional_bool(args.task_pass), citations_correct=optional_bool(args.citations_correct),
                major_rework=optional_bool(args.major_rework), reason_code=args.reason,
                original=json.loads(args.original.read_text()) if args.original else None,
                edited=json.loads(args.edited.read_text()) if args.edited else None)
        else:
            result = export(warehouse, args.directory / "exports")
        print(json.dumps(result, indent=2, allow_nan=False))
        return 1 if args.command == "run" and result["failed_requests"] else 0
    except (ValueError, OSError, WarehouseError) as exc:
        if args.command in {"deploy", "demo", "ingest", "monitor", "export"}:
            # Available even when the database is down; never store raw error bodies.
            try:
                private_json(args.directory / "last-client-error.json",
                             {"at": timestamp(), "command": args.command, "error_type": type(exc).__name__})
            except OSError:
                pass
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Completed events are retained; an in-flight call may have unknown usage.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
