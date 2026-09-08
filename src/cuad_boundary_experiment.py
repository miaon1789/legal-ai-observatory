"""Development-only sentence-unit ablation with a matched-context control. No API/SQL."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import time
import uuid

import cuad_budget_experiment as budget
import cuad_experiment as base
import cuad_intake as intake
from observability import retrieval
from observability.evidence import union
from observability.events import canonical, digest

PROTOCOL = base.ROOT / "benchmarks/cuad/protocol-boundaries-v3.json"
CONFIG_IDS = ("v2-window-6000", "sentence-units-6000", "v2-window-matched")


def load_protocol():
    protocol = base.read_json(PROTOCOL)
    if (protocol["phase"] != "development_only" or protocol["external_inference"] != "not_approved"
            or protocol["character_budget"] != 6000
            or tuple(c["config_id"] for c in protocol["configurations"]) != CONFIG_IDS
            or protocol["sentence_segmenter"] != {"library": "pysbd", "version": "0.3.4", "language": "en",
                                                  "clean": False, "char_span": True}
            or importlib.metadata.version("pysbd") != "0.3.4"):
        raise ValueError("unsupported_boundary_protocol")
    return protocol


def source_units(text, spans):
    if not spans:
        raise ValueError("empty_sentence_segmentation")
    previous = 0
    for span in spans:
        if (not previous <= span.start < span.end <= len(text) or text[span.start:span.end] != span.sent
                or text[previous:span.start].strip()):
            raise ValueError("invalid_sentence_offsets_or_gap")
        previous = span.end
    if text[previous:].strip():
        raise ValueError("unsegmented_source_tail")
    # Partition the unchanged source; keep leading/inter-sentence/trailing whitespace.
    starts = [0] + [s.start for s in spans[1:]]
    return list(zip(starts, starts[1:] + [len(text)]))


def sentence_units(text):
    import pysbd
    spans = pysbd.Segmenter(language="en", clean=False, char_span=True).segment(text)
    return source_units(text, spans)


def select_units(index, query, category, text_length, units, old_config, old_protocol, limit):
    values = index.index.get_scores(retrieval.tokens(query))
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError("nonfinite_score")
    ranking = sorted(range(len(values)), key=lambda i: (-float(values[i]), index.chunks[i]["start"]))
    proposals = []
    if category in old_protocol["preamble_categories"] and old_config["preamble_characters"]:
        proposals.append((0, min(text_length, old_config["preamble_characters"]), None))
    proposals.extend((index.chunks[i]["start"], index.chunks[i]["end"], float(values[i])) for i in ranking)
    starts, ends = [a for a, _ in units], [b for _, b in units]
    seen, result, used = set(), [], 0
    for start, end, score in proposals:
        for i in range(bisect_right(ends, start), bisect_left(starts, end)):
            if i in seen:
                continue
            seen.add(i)
            a, b = units[i]
            if b - a <= limit - used:
                result.append({"start": a, "end": b, "score": score})
                used += b - a
    return result


def context_size(context):
    return sum(b - a for a, b in union([(c["start"], c["end"]) for c in context]))


def load_development(protocol):
    directory = budget.OUTPUT / "runs" / protocol["parent_development_run_id"]
    read = lambda name: base.read_json(directory / name, private=True)
    summary = read("summary.json")
    if summary.get("phase") != "development" or summary.get("status") != "complete":
        raise ValueError("completed_development_only")
    inputs, old_protocol, selection, predictions = read("inputs.json"), read("protocol.json"), read("selection.json"), read("predictions.json")
    if (summary["run_id"] != protocol["parent_development_run_id"]
            or digest(summary) != selection["summary_sha256"]
            or selection["development_run_id"] != summary["run_id"]
            or selection["selected_candidate"] != protocol["parent_candidate_id"]
            or selection["source_sha256"] != summary["source_sha256"]
            or summary["source_sha256"] != budget.source_hashes()
            or digest(inputs) != summary["inputs_sha256"] or digest(old_protocol) != summary["protocol_sha256"]
            or selection["protocol_sha256"] != summary["protocol_sha256"] or predictions["run_id"] != summary["run_id"]):
        raise ValueError("parent_development_changed")
    if (len(inputs["documents"]) != 10 or len(inputs["tasks"]) != 100
            or any(d["partition"] != "development" for d in inputs["documents"])
            or any("answers" in t or "is_impossible" in t for t in inputs["tasks"])):
        raise ValueError("unexpected_development_inputs")
    bundle = base.OUTPUT / "bundles" / old_protocol["base_bundle_id"]
    receipt = base.read_json(bundle / "receipt.json", private=True)
    frozen = base.read_bundle_file(bundle, "protocol.json", receipt)
    if digest(frozen) != old_protocol["base_protocol_sha256"]:
        raise ValueError("parent_retrieval_settings_changed")
    return directory, summary, inputs, old_protocol, predictions, frozen


def execute(inputs, protocol, old_protocol, settings, emit):
    if (any(d["partition"] != "development" for d in inputs["documents"])
            or any("answers" in t or "is_impossible" in t for t in inputs["tasks"])):
        raise ValueError("gold_or_evaluation_in_execution_input")
    old_config = next(c for c in old_protocol["configurations"] if c["config_id"] == protocol["parent_candidate_id"])
    rows, preprocessing = [], []
    for di, doc in enumerate(inputs["documents"]):
        index, units, index_error, unit_error = None, None, None, None
        start = time.perf_counter_ns()
        try:
            index = retrieval.Retriever(doc["text"], {k: old_config[k] for k in ("words", "overlap_words")}, settings)
        except Exception as exc:
            index_error = type(exc).__name__
        index_ms = (time.perf_counter_ns() - start) / 1_000_000
        start = time.perf_counter_ns()
        try:
            units = sentence_units(doc["text"])
        except Exception as exc:
            unit_error = type(exc).__name__
        preprocessing.append({"document_id": doc["document_id"], "index_ms": index_ms,
            "segmentation_ms": (time.perf_counter_ns() - start) / 1_000_000,
            "index_error": index_error, "segmentation_error": unit_error,
            "units": len(units) if units else None,
            "units_over_budget": sum(b - a > protocol["character_budget"] for a, b in units) if units else None})
        for ti, task in enumerate(t for t in inputs["tasks"] if t["document_id"] == doc["document_id"]):
            attempted = {}
            order = list(CONFIG_IDS[:2])
            if (di + ti) % 2:
                order.reverse()
            for config_id in order + [CONFIG_IDS[2]]:
                started = time.perf_counter_ns()
                context, error = [], index_error
                if error is None:
                    try:
                        if config_id == CONFIG_IDS[0]:
                            context = budget.budgeted_context(index, task["query"], task["category"], len(doc["text"]), old_config, old_protocol)
                        elif config_id == CONFIG_IDS[1]:
                            if unit_error:
                                raise ValueError("sentence_segmentation_failed")
                            context = select_units(index, task["query"], task["category"], len(doc["text"]), units,
                                                   old_config, old_protocol, protocol["character_budget"])
                        else:
                            candidate = attempted[CONFIG_IDS[1]]
                            if candidate["status"] != "ok":
                                raise ValueError("candidate_control_dependency_failed")
                            limit = context_size(candidate["chunks"])
                            context = budget.budgeted_context(index, task["query"], task["category"], len(doc["text"]),
                                                             old_config, old_protocol | {"character_budget": limit}) if limit else []
                    except Exception as exc:
                        context, error = [], type(exc).__name__
                row = {"case_id": task["case_id"], "document_id": doc["document_id"], "partition": "development",
                       "category": task["category"], "length_quartile": doc["length_quartile"], "config_id": config_id,
                       "status": "error" if error else "ok", "error_type": error, "chunks": context,
                       "retrieval_latency_ms": (time.perf_counter_ns() - started) / 1_000_000,
                       "case_origin": "public_real_contract", "execution_origin": "local_algorithm"}
                attempted[config_id] = row
                emit(row)
                rows.append(row)
    return rows, preprocessing


def paired_changes(scores, left, right):
    grouped = {}
    for row in scores:
        grouped.setdefault(row["case_id"], {})[row["config_id"]] = row
    changes = []
    for case_id, pair in grouped.items():
        a, b = pair[left], pair[right]
        if a["is_impossible"]:
            continue
        delta = b["gold_character_recall"] - a["gold_character_recall"]
        changes.append({"case_id": case_id, "category": a["category"], "recall_delta": delta,
                        "before": a["gold_character_recall"], "after": b["gold_character_recall"],
                        "full_gained": not a["all_gold_covered"] and b["all_gold_covered"],
                        "full_lost": a["all_gold_covered"] and not b["all_gold_covered"]})
    return {"positive_tasks": len(changes), "improved": sum(r["recall_delta"] > 0 for r in changes),
            "regressed": sum(r["recall_delta"] < 0 for r in changes), "unchanged": sum(r["recall_delta"] == 0 for r in changes),
            "fully_covered_gained": sum(r["full_gained"] for r in changes), "fully_covered_lost": sum(r["full_lost"] for r in changes),
            "mean_recall_gain_pp": 100 * sum(r["recall_delta"] for r in changes) / len(changes) if changes else None}, changes


def decision(results, paired):
    a, b = results[CONFIG_IDS[0]]["overall"], results[CONFIG_IDS[1]]["overall"]
    checks = {"full_coverage_strictly_improves": b["all_gold_covered_rate"] is not None and
              a["all_gold_covered_rate"] is not None and b["all_gold_covered_rate"] > a["all_gold_covered_rate"],
              "mean_recall_not_lower": b["macro_gold_character_recall"] is not None and
              a["macro_gold_character_recall"] is not None and b["macro_gold_character_recall"] >= a["macro_gold_character_recall"],
              "no_previously_complete_task_lost": paired["fully_covered_lost"] == 0,
              "all_arms_complete": all(not r["overall"]["failed_queries"] and not r["overall"]["missing_queries"] for r in results.values())}
    return {"checks": checks, "qualifies_for_future_frozen_evaluation": all(checks.values()), "promoted_to_sql_or_powerbi": False}


def run():
    protocol = load_protocol()
    parent_dir, parent, inputs, old_protocol, old_predictions, frozen = load_development(protocol)
    run_id = uuid.uuid4().hex
    output = base.ROOT / "private/cuad_boundaries_v3/runs" / run_id
    record = {"format": "cuad-boundary-development-v3", "run_id": run_id, "phase": "development_only", "status": "running",
              "started_at": intake.now(), "protocol_sha256": digest(protocol), "parent_summary_sha256": digest(parent),
              "inputs_sha256": digest(inputs), "parent_predictions_sha256": digest(old_predictions),
              "source_sha256": budget.source_hashes() | {"src/cuad_boundary_experiment.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
              "environment": {"python": platform.python_version(), "machine": platform.machine(),
                              **{p: importlib.metadata.version(p) for p in ("pysbd", "rank-bm25", "numpy")}},
              "new_run_references_loaded": False, "external_inference_calls": 0, "is_llm_evaluation": False,
              "human_adjudication": "pending", "informed_by_previous_development_scores": True}
    intake.write_json(output / "protocol.json", protocol)
    intake.write_json(output / "inputs.json", inputs)
    intake.write_json(output / "execution.json", record)
    try:
        with open(output / "query_events.jsonl", "x", encoding="utf-8", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            def emit(row):
                stream.write(canonical(row | {"run_id": run_id, "recorded_at": intake.now()}) + "\n")
                stream.flush()
            predictions, preprocessing = execute(inputs, protocol, old_protocol, frozen["retrieval"], emit)
        intake.write_json(output / "predictions.json", {"run_id": run_id, "predictions": predictions})
        intake.write_json(output / "preprocessing.json", {"documents": preprocessing})
        parent_context = {r["case_id"]: r for r in old_predictions["predictions"] if r["config_id"] == protocol["parent_candidate_id"]}
        by_case = {}
        for row in predictions:
            by_case.setdefault(row["case_id"], {})[row["config_id"]] = row
            if context_size(row["chunks"]) > protocol["character_budget"]:
                raise ValueError("character_budget_exceeded")
            if row["config_id"] == CONFIG_IDS[0] and (row["status"] != parent_context[row["case_id"]]["status"] or
                    digest(row["chunks"]) != digest(parent_context[row["case_id"]]["chunks"])):
                raise ValueError("parent_baseline_replay_changed")
        for pair in by_case.values():
            candidate, control = pair[CONFIG_IDS[1]], pair[CONFIG_IDS[2]]
            if candidate["status"] != control["status"] or context_size(candidate["chunks"]) != context_size(control["chunks"]):
                raise ValueError("matched_control_changed")
        references = base.read_json(parent_dir / "references.json", private=True)
        if digest(references) != parent["references_sha256"]:
            raise ValueError("references_changed")
        record["new_run_references_loaded"] = True
        intake.write_json(output / "execution.json", record)
        intake.write_json(output / "references.json", references)
        results, scores = budget.evaluate(inputs, references, predictions, protocol["configurations"])
        paired, cases = paired_changes(scores, CONFIG_IDS[0], CONFIG_IDS[1])
        matched, _ = paired_changes(scores, CONFIG_IDS[2], CONFIG_IDS[1])
        intake.write_json(output / "case_scores.json", {"run_id": run_id, "cases": scores})
        intake.write_json(output / "case_changes.json", {"baseline_to_candidate": cases})
        summaries = {k: v["overall"] for k, v in results.items()}
        parent_metrics = parent["results"][protocol["parent_candidate_id"]]["overall"]
        for field in ("macro_gold_character_recall", "all_gold_covered_rate", "any_gold_hit_rate", "mean_retrieved_characters"):
            if summaries[CONFIG_IDS[0]][field] != parent_metrics[field]:
                raise ValueError("parent_baseline_metric_changed")
        record.update(status="complete", completed_at=intake.now(), predictions_sha256=digest(predictions),
                      references_sha256=digest(references), scores_sha256=digest(scores))
        summary = record | {"results": results, "baseline_to_candidate": paired, "matched_control_to_candidate": matched,
                            "decision": decision(results, paired), "query_attempts": len(predictions),
                            "preprocessing_summary": {"segmented_documents": sum(r["segmentation_error"] is None for r in preprocessing),
                                "total_units": sum(r["units"] or 0 for r in preprocessing),
                                "units_over_budget": sum(r["units_over_budget"] or 0 for r in preprocessing)},
                            "context_sizes": {c: {"min": min(r["retrieved_characters"] for r in scores if r["config_id"] == c),
                                "max": max(r["retrieved_characters"] for r in scores if r["config_id"] == c),
                                "empty_queries": sum(r["retrieved_characters"] == 0 for r in scores if r["config_id"] == c)} for c in CONFIG_IDS}}
        intake.write_json(output / "summary.json", summary)
        intake.write_json(output / "execution.json", record)
        return {"run_directory": str(output), "results": summaries, "paired": paired, "matched": matched, "decision": summary["decision"]}
    except Exception as exc:
        record.update(status="failed", error_type=type(exc).__name__)
        intake.write_json(output / "execution.json", record)
        raise


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(run(), indent=2))
