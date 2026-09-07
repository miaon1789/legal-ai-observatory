"""Development-first, equal-character-budget CUAD retrieval. No inference API."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import time
import uuid

import cuad_experiment as base
import cuad_intake as intake
from observability import retrieval
from observability.evidence import union
from observability.events import canonical, digest

ROOT = base.ROOT
PROTOCOL = ROOT / "benchmarks/cuad/protocol-budget-v2.json"
OUTPUT = ROOT / "private/cuad_budget_v2"


def source_hashes() -> dict:
    paths = [Path(__file__), ROOT / "src/cuad_experiment.py", ROOT / "src/cuad_intake.py",
             ROOT / "src/observability/retrieval.py", ROOT / "src/observability/evidence.py",
             ROOT / "src/observability/events.py"]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def load_protocol() -> dict:
    protocol = base.read_json(PROTOCOL)
    if (protocol["external_inference"] != "not_approved" or protocol["execution_origin"] != "local_algorithm"
            or type(protocol["character_budget"]) is not int or not 100 <= protocol["character_budget"] <= 20_000):
        raise ValueError("unsupported_budget_protocol")
    configs = protocol["configurations"]
    if len(configs) != 3 or len({c["config_id"] for c in configs}) != 3:
        raise ValueError("expected_three_fixed_configs")
    for c in configs:
        retrieval.chunks("fixture", c["words"], c["overlap_words"])
        if type(c["preamble_characters"]) is not int or not 0 <= c["preamble_characters"] <= protocol["character_budget"]:
            raise ValueError("invalid_preamble_budget")
    if protocol["baseline_config_id"] not in {c["config_id"] for c in configs}:
        raise ValueError("missing_baseline")
    if importlib.metadata.version("rank-bm25") != "0.2.2":
        raise ValueError("unexpected_retriever_version")
    return protocol


def base_bundle(protocol: dict) -> tuple[dict, dict]:
    directory = base.OUTPUT / "bundles" / protocol["base_bundle_id"]
    receipt = base.read_json(directory / "receipt.json", private=True)
    if (receipt["bundle_id"] != protocol["base_bundle_id"] or digest(receipt["file_sha256"]) != receipt["bundle_id"]
            or receipt["status"] != "ready_local_only"):
        raise ValueError("invalid_base_bundle")
    frozen = base.read_bundle_file(directory, "protocol.json", receipt)
    if digest(frozen) != protocol["base_protocol_sha256"]:
        raise ValueError("base_protocol_mismatch")
    inputs = base.read_bundle_file(directory, "inputs.json", receipt)
    return inputs, frozen


def subset(inputs: dict, partition: str) -> dict:
    docs = [d for d in inputs["documents"] if d["partition"] == partition]
    ids = {d["document_id"] for d in docs}
    return inputs | {"documents": docs, "tasks": [t for t in inputs["tasks"] if t["document_id"] in ids]}


def uncovered(start: int, end: int, selected: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result, cursor = [], start
    for a, b in union(selected):
        if b <= cursor:
            continue
        if a >= end:
            break
        if a > cursor:
            result.append((cursor, min(a, end)))
        cursor = max(cursor, b)
    if cursor < end:
        result.append((cursor, end))
    return result


def budgeted_context(index, query: str, category: str, text_length: int, config: dict, protocol: dict) -> list[dict]:
    scores = index.index.get_scores(retrieval.tokens(query))
    if not all(math.isfinite(float(s)) for s in scores):
        raise ValueError("nonfinite_score")
    ranked = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), index.chunks[i]["start"]))
    proposals = []
    if category in protocol["preamble_categories"] and config["preamble_characters"]:
        proposals.append((0, min(config["preamble_characters"], text_length), None))
    proposals.extend((index.chunks[i]["start"], index.chunks[i]["end"], float(scores[i])) for i in ranked)
    selected, used, result = [], 0, []
    for start, end, score in proposals:
        for a, b in uncovered(start, end, selected):
            b = min(b, a + protocol["character_budget"] - used)
            if b > a:
                selected.append((a, b))
                result.append({"start": a, "end": b, "score": score})
                used += b - a
            if used == protocol["character_budget"]:
                return result
    return result


def execute(inputs: dict, configs: list[dict], protocol: dict, settings: dict, emit) -> tuple[list, list]:
    if any("answers" in t or "is_impossible" in t for t in inputs["tasks"]):
        raise ValueError("gold_in_inputs")
    predictions, timings = [], []
    for di, doc in enumerate(inputs["documents"]):
        indexes = {}
        for config in configs:
            key = config["words"], config["overlap_words"]
            if key in indexes:
                continue
            start, error, index = time.perf_counter_ns(), None, None
            try:
                index = retrieval.Retriever(doc["text"], {"words": key[0], "overlap_words": key[1]}, settings)
            except Exception as exc:
                error = type(exc).__name__
            indexes[key] = index, error
            timings.append({"document_id": doc["document_id"], "words": key[0], "overlap_words": key[1],
                            "index_latency_ms": (time.perf_counter_ns() - start) / 1_000_000, "error_type": error})
        for ti, task in enumerate(t for t in inputs["tasks"] if t["document_id"] == doc["document_id"]):
            offset = (di + ti) % len(configs)
            for config in configs[offset:] + configs[:offset]:
                start, contexts = time.perf_counter_ns(), []
                index, error = indexes[(config["words"], config["overlap_words"])]
                if index is not None:
                    try:
                        contexts = budgeted_context(index, task["query"], task["category"], len(doc["text"]), config, protocol)
                    except Exception as exc:
                        error = type(exc).__name__
                row = {"case_id": task["case_id"], "document_id": doc["document_id"],
                       "partition": doc["partition"], "category": task["category"],
                       "length_quartile": doc["length_quartile"], "config_id": config["config_id"],
                       "status": "error" if error else "ok", "error_type": error, "chunks": contexts,
                       "retrieval_latency_ms": (time.perf_counter_ns() - start) / 1_000_000,
                       "case_origin": "public_real_contract", "execution_origin": "local_algorithm"}
                emit(row)
                predictions.append(row)
    return predictions, timings


def evaluate(inputs: dict, references: dict, predictions: list[dict], configs: list[dict]) -> tuple[dict, list]:
    docs = {d["document_id"]: d for d in inputs["documents"]}
    tasks = {t["case_id"]: t for t in inputs["tasks"]}
    refs = {r["case_id"]: r for r in references["references"] if r["case_id"] in tasks}
    if set(refs) != set(tasks):
        raise ValueError("reference_membership_mismatch")
    config_ids, indexed = {c["config_id"] for c in configs}, {}
    for row in predictions:
        key = row["case_id"], row["config_id"]
        if key in indexed or key[0] not in tasks or key[1] not in config_ids:
            raise ValueError("unknown_or_duplicate_prediction")
        if row["document_id"] != tasks[key[0]]["document_id"]:
            raise ValueError("wrong_prediction_document")
        indexed[key] = row
    scored = []
    for task in inputs["tasks"]:
        doc, ref = docs[task["document_id"]], refs[task["case_id"]]
        if ref["document_id"] != doc["document_id"]:
            raise ValueError("wrong_reference_document")
        for config in configs:
            prediction = indexed.get((task["case_id"], config["config_id"]))
            scored.append({"case_id": task["case_id"], "document_id": doc["document_id"],
                           "partition": doc["partition"], "category": task["category"],
                           "length_quartile": doc["length_quartile"], "config_id": config["config_id"],
                           "retrieval_latency_ms": prediction["retrieval_latency_ms"] if prediction else None,
                           **retrieval.score_case(doc["text"], ref, prediction)})
    results = {}
    for config in configs:
        rows = [r for r in scored if r["config_id"] == config["config_id"]]
        results[config["config_id"]] = {
            "overall": retrieval.summarize(rows),
            "by_category": {c: retrieval.summarize([r for r in rows if r["category"] == c])
                            for c in sorted({t["category"] for t in inputs["tasks"]})},
            "by_length_quartile": {str(q): retrieval.summarize([r for r in rows if r["length_quartile"] == q])
                                   for q in range(1, 5)},
        }
    return results, scored


def choose_candidate(results: dict, protocol: dict) -> str:
    candidates = [c["config_id"] for c in protocol["configurations"] if c["config_id"] != protocol["baseline_config_id"]]
    for metrics in results.values():
        if metrics["overall"]["failed_queries"] or metrics["overall"]["missing_queries"]:
            raise ValueError("incomplete_development_run")
        if metrics["overall"]["macro_gold_character_recall"] is None:
            raise ValueError("no_answerable_development_cases")
    return sorted(candidates, key=lambda c: (-results[c]["overall"]["macro_gold_character_recall"],
                                            -results[c]["overall"]["all_gold_covered_rate"], c))[0]


def run_phase(inputs: dict, reference_loader, configs: list[dict], protocol: dict, frozen: dict,
              phase: str, provenance: dict) -> tuple[Path, dict]:
    run_id = uuid.uuid4().hex
    directory = OUTPUT / "runs" / run_id
    record = {"format": "cuad-budget-results-v2", "run_id": run_id, "phase": phase,
              "started_at": intake.now(), "status": "running", "protocol_sha256": digest(protocol),
              "inputs_sha256": digest(inputs), "source_sha256": source_hashes(), "provenance": provenance,
              "configurations": configs, "character_budget": protocol["character_budget"],
              "case_origin": "public_real_contract", "execution_origin": "local_algorithm",
              "external_inference_calls": 0, "api_spend_usd": 0, "is_llm_evaluation": False,
              "environment": {"python": platform.python_version(), "machine": platform.machine(),
                              "rank-bm25": importlib.metadata.version("rank-bm25"),
                              "numpy": importlib.metadata.version("numpy")}, "labels_accessed": False}
    intake.write_json(directory / "protocol.json", protocol)
    intake.write_json(directory / "inputs.json", inputs)
    intake.write_json(directory / "execution.json", record)
    try:
        with open(directory / "query_events.jsonl", "x", encoding="utf-8",
                  opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            def emit(row):
                stream.write(canonical(row | {"run_id": run_id, "recorded_at": intake.now()}) + "\n")
                stream.flush()
            predictions, timing = execute(inputs, configs, protocol, frozen["retrieval"], emit)
        intake.write_json(directory / "predictions.json", {"run_id": run_id, "predictions": predictions})
        intake.write_json(directory / "index_timings.json", {"indexes": timing})
        record["labels_accessed"] = True
        intake.write_json(directory / "execution.json", record)
        references = reference_loader()
        intake.write_json(directory / "references.json", references)
        results, scored = evaluate(inputs, references, predictions, configs)
        intake.write_json(directory / "case_scores.json", {"run_id": run_id, "cases": scored})
        record.update(status="complete", completed_at=intake.now(), query_attempts=len(predictions),
                      references_sha256=digest(references), case_scores_sha256=digest(scored))
        summary = record | {"results": results}
        intake.write_json(directory / "summary.json", summary)
        intake.write_json(directory / "execution.json", record)
        return directory, summary
    except Exception as exc:
        record.update(status="failed", error_type=type(exc).__name__)
        intake.write_json(directory / "execution.json", record)
        raise


def development() -> dict:
    protocol = load_protocol()
    inputs, frozen = base_bundle(protocol)
    inputs = subset(inputs, "development")
    directory = base.OUTPUT / "bundles" / protocol["base_bundle_id"]
    receipt = base.read_json(directory / "receipt.json", private=True)

    def references():
        value = base.read_bundle_file(directory, "references.json", receipt)
        ids = {t["case_id"] for t in inputs["tasks"]}
        return value | {"references": [r for r in value["references"] if r["case_id"] in ids]}

    output, summary = run_phase(inputs, references, protocol["configurations"], protocol, frozen,
                                "development", {"base_bundle_id": protocol["base_bundle_id"]})
    selection = {"development_run_id": summary["run_id"], "protocol_sha256": digest(protocol),
                 "source_sha256": source_hashes(), "summary_sha256": digest(summary),
                 "selected_candidate": choose_candidate(summary["results"], protocol), "selected_at": intake.now()}
    intake.write_json(output / "selection.json", selection)
    return {"run_directory": str(output), "selection": selection,
            "results": {c: v["overall"] for c, v in summary["results"].items()}}


def fresh_evaluation(development_directory: Path) -> dict:
    protocol = load_protocol()
    selection = base.read_json(development_directory / "selection.json", private=True)
    dev_summary = base.read_json(development_directory / "summary.json", private=True)
    if (selection["summary_sha256"] != digest(dev_summary) or selection["protocol_sha256"] != digest(protocol)
            or selection["source_sha256"] != source_hashes() or dev_summary["phase"] != "development"
            or dev_summary["status"] != "complete" or selection["development_run_id"] != dev_summary["run_id"]
            or selection["selected_candidate"] != choose_candidate(dev_summary["results"], protocol)):
        raise ValueError("development_selection_changed")
    original, frozen = base_bundle(protocol)
    original_ids = {d["document_id"] for d in original["documents"]}
    prep_protocol = copy.deepcopy(frozen)
    prep_protocol["protocol_id"] = protocol["protocol_id"] + "-preparation"
    prep_protocol["exclude_previously_reviewed_ids"] += [d["document_id"] for d in original["documents"]
                                                         if d["partition"] == "evaluation"]
    prep_protocol["split_quotas"]["evaluation"] = protocol["fresh_evaluation"]["documents_per_length_quartile"]
    full, train, test, archive_sha, _ = intake.load_partitioned_documents()
    files = base.build_bundle(prep_protocol, full, train, test, archive_sha)
    if files["manifest.json"]["offset_mismatches"]:
        raise ValueError("fresh_gold_offsets_invalid")
    if {d["document_id"] for d in files["inputs.json"]["documents"] if d["partition"] == "development"} != {
            d["document_id"] for d in original["documents"] if d["partition"] == "development"}:
        raise ValueError("development_membership_changed")
    inputs = subset(files["inputs.json"], "evaluation")
    if original_ids & {d["document_id"] for d in inputs["documents"]}:
        raise ValueError("old_documents_in_fresh_evaluation")
    configs = [c for c in protocol["configurations"] if c["config_id"] in {
        protocol["baseline_config_id"], selection["selected_candidate"]}]
    references = files["references.json"]
    ids = {t["case_id"] for t in inputs["tasks"]}
    references = references | {"references": [r for r in references["references"] if r["case_id"] in ids]}
    provenance = {"development_run_id": selection["development_run_id"], "selection_sha256": digest(selection),
                  "selected_candidate": selection["selected_candidate"], "archive_sha256": archive_sha,
                  "preparation_protocol_sha256": digest(prep_protocol),
                  "excluded_v1_evaluation_documents": 40, "fresh_documents": len(inputs["documents"]),
                  "selection_method": "remaining_official_test_length_quartiles_then_v1_seeded_title_hash"}
    output, summary = run_phase(inputs, lambda: references, configs, protocol, frozen, "fresh_evaluation", provenance)
    # The full source manifest is private; the old eight-document review is not overwritten.
    manifest = files["manifest.json"]
    manifest["authorization_context"] = "Owner requested continued local experiments; no external inference authorized."
    intake.write_json(output / "source_manifest.json", manifest)
    intake.write_json(output / "preparation_protocol.json", prep_protocol)
    return {"run_directory": str(output), "results": {c: v["overall"] for c, v in summary["results"].items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("development")
    command = sub.add_parser("evaluate")
    command.add_argument("--development-run", type=Path, required=True)
    args = parser.parse_args()
    result = development() if args.command == "development" else fresh_evaluation(args.development_run)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
