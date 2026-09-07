"""Offline evidence-scoring demonstrations using explicitly synthetic scripted outputs."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

import cuad_intake as local_files
from observability.events import digest
from observability.evidence import PREDICTION_FORMAT, evaluate, validate_benchmark

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmarks/evidence/v1.json"
OUTPUT = ROOT / "private/evidence_evaluation"
SCENARIOS = ("oracle_replay", "all_abstain", "first_gold_span", "whole_document",
             "invalid_offsets", "missing_half", "scripted_errors")


def unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON values are not accepted")


def read_json(path: Path) -> dict:
    if not path.is_file():
        raise ValueError("Expected a JSON file")
    with path.open("rb") as stream:
        raw = stream.read(10_000_001)
    if len(raw) > 10_000_000:
        raise ValueError("JSON file exceeds the 10 MB limit")
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def scripted_predictions(benchmark: dict, scenario: str) -> dict:
    if scenario not in SCENARIOS:
        raise ValueError("Unknown scripted scenario")
    documents, cases = validate_benchmark(benchmark)
    predictions = []
    for index, case in enumerate(cases.values()):
        if scenario == "missing_half" and index % 2:
            continue
        spans = [{"start": answer["answer_start"], "end": answer["answer_start"] + len(answer["text"]),
                  "quote": answer["text"]} for answer in case["answers"]]
        response = {"case_id": case["case_id"], "document_id": case["document_id"],
                    "status": "ok", "abstain": case["is_impossible"], "spans": spans}
        if scenario == "all_abstain":
            response.update(abstain=True, spans=[])
        elif scenario == "first_gold_span":
            response["spans"] = spans[:1]
        elif scenario == "whole_document":
            text = documents[case["document_id"]]["text"]
            response.update(abstain=False, spans=[{"start": 0, "end": len(text), "quote": text}])
        elif scenario == "invalid_offsets":
            response["spans"] = [s | {"end": s["end"] + 1} for s in spans]
        elif scenario == "scripted_errors":
            response.update(status="error", abstain=None, spans=[])
        predictions.append(response)
    return {"format": PREDICTION_FORMAT, "benchmark_sha256": digest(benchmark),
            "prediction_origin": "scripted_fixture", "config_id": scenario, "predictions": predictions}


def input_only(benchmark: dict) -> dict:
    validate_benchmark(benchmark)
    return {"format": "evidence-inputs-v1", "benchmark_sha256": digest(benchmark),
            "case_origin": "self_authored_synthetic", "documents": copy.deepcopy(benchmark["documents"]),
            "tasks": [{k: case[k] for k in ("case_id", "document_id", "category", "question")}
                      for case in benchmark["cases"]]}


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def render_summary(reports: list[dict]) -> str:
    lines = ["# Offline Evidence Scorer Checks", "",
             "All rows below are scripted outputs on project-created fictional text. They are not model results.",
             "Oracle and first-span scenarios consult gold annotations only to test the scorer, not retrieval.", "",
             "| Scripted configuration | Valid / expected | Evidence precision | Evidence recall | Evidence F1 | Exact union | Correct abstention | Missing / invalid / errors |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for report in reports:
        s = report["summary"]
        coverage, positive, negative = s["coverage"], s["answerable"], s["unanswerable"]
        metrics = [percent(positive[k]) for k in ("evidence_precision_macro", "evidence_recall_macro",
                                                  "evidence_f1_macro", "exact_evidence_union_rate")]
        lines.append(f"| {report['config_id']} | {coverage['valid']} / {coverage['expected']} | "
                     + " | ".join(metrics)
                     + f" | {percent(negative['correct_abstention_rate'])} | "
                     + f"{coverage['missing']} / {coverage['invalid']} / {coverage['error']} |")
    lines.extend(["", "Evidence metrics average over all answerable cases only, including unsuccessful responses as zero.",
                  "Correct abstention uses all unanswerable cases; missing, invalid and failed responses receive no credit.",
                  "Empty groups show n/a, never an invented 100%. There is no combined quality score.",
                  "", "Offsets are half-open Unicode code-point intervals. Duplicates and overlap are counted once.",
                  "Identical quoted text at the wrong occurrence does not match the gold position.",
                  "Exact union tests evidence coverage without extras, not legal interpretation or natural-language reasoning.",
                  "", "These are project-specific metrics, not official CUAD scores. No CUAD document was loaded by this demonstration.",
                  "Input-only tasks are in inputs.json. Gold annotations are in benchmark.json; gold-derived predictions are test artifacts, never model inputs.",
                  "Per-case and per-category details are in the result JSON files.", ""])
    return "\n".join(lines)


def save_run(benchmark: dict, predictions: list[dict], reports: list[dict], output: Path) -> dict:
    destination = output.absolute() / digest(reports)
    local_files.require_private(destination)
    files = {"benchmark.json": benchmark, "inputs.json": input_only(benchmark)}
    for index, (prediction, report) in enumerate(zip(predictions, reports), 1):
        files[f"{index:02d}-predictions.json"] = prediction
        files[f"{index:02d}-result.json"] = report
    for name in (*files, "summary.md"):
        local_files.require_private(destination / name)
    for name, value in files.items():
        local_files.write_json(destination / name, value)
    local_files.write_private(destination / "summary.md", render_summary(reports).encode())
    return {"directory": str(destination), "scenarios": len(reports), "cases_per_scenario": len(benchmark["cases"]),
            "prediction_origin": "scripted_fixture", "is_model_evaluation": False,
            "model_calls": 0, "sql_writes": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Run seven explicitly scripted scorer checks")
    demo.add_argument("--benchmark", type=Path, default=BENCHMARK)
    demo.add_argument("--output", type=Path, default=OUTPUT)
    score = sub.add_parser("score", help="Score a hash-matched synthetic scripted prediction file")
    score.add_argument("--benchmark", type=Path, required=True)
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    try:
        local_files.require_private(args.output.absolute())
        benchmark = read_json(args.benchmark)
        predictions = ([scripted_predictions(benchmark, s) for s in SCENARIOS] if args.command == "demo"
                       else [read_json(args.predictions)])
        reports = [evaluate(benchmark, p) for p in predictions]
        print(json.dumps(save_run(benchmark, predictions, reports, args.output), indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Evaluation stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
