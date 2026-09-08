"""Single-variable, equal-budget English stemming experiment on frozen development data."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import time
import uuid

import cuad_boundary_experiment as shared
import cuad_budget_experiment as budget
import cuad_experiment as base
import cuad_intake as intake
from observability import retrieval
from observability.events import canonical, digest

PROTOCOL = base.ROOT / "benchmarks/cuad/protocol-stemming-v4.json"
CONFIG_IDS = ("v2-window-6000", "english-stem-6000")


def load_protocol():
    protocol = base.read_json(PROTOCOL)
    if (protocol["phase"] != "development_only" or protocol["external_inference"] != "not_approved"
            or type(protocol["character_budget"]) is not int or protocol["character_budget"] != 6000
            or tuple(c["config_id"] for c in protocol["configurations"]) != CONFIG_IDS
            or protocol["normalizer"] != {"library": "snowballstemmer", "version": "3.1.1",
                "implementation": "snowballstemmer.english_stemmer.EnglishStemmer", "algorithm": "English_Porter2"}
            or importlib.metadata.version("snowballstemmer") != "3.1.1"
            or importlib.metadata.version("rank-bm25") != "0.2.2"):
        raise ValueError("unsupported_stemming_protocol")
    return protocol


class StemmedScores:
    """Adapt the existing selector's token-list API without changing its code."""
    def __init__(self, corpus, settings):
        from rank_bm25 import BM25Okapi
        from snowballstemmer.english_stemmer import EnglishStemmer
        self.stemmer = EnglishStemmer()
        normalized = [self.stemmer.stemWords(tokens) for tokens in corpus]
        self.raw_vocabulary_size = len({t for row in corpus for t in row})
        self.stem_vocabulary_size = len({t for row in normalized for t in row})
        self.bm25 = BM25Okapi(normalized, k1=settings["k1"], b=settings["b"], epsilon=settings["epsilon"])

    def get_scores(self, query_tokens):
        return self.bm25.get_scores(self.stemmer.stemWords(query_tokens))


class StemmedRetriever:
    def __init__(self, text, chunking, settings):
        self.chunks = retrieval.chunks(text, **chunking)
        corpus = [retrieval.tokens(text[c["start"]:c["end"]]) for c in self.chunks]
        if not any(corpus):
            raise ValueError("empty_vocabulary")
        self.index = StemmedScores(corpus, settings)


def execute(inputs, protocol, old_protocol, settings, emit):
    if (any(d["partition"] != "development" for d in inputs["documents"])
            or any("answers" in t or "is_impossible" in t for t in inputs["tasks"])):
        raise ValueError("gold_or_evaluation_in_execution_input")
    config = next(c for c in old_protocol["configurations"] if c["config_id"] == protocol["parent_candidate_id"])
    rows, preprocessing = [], []
    for di, doc in enumerate(inputs["documents"]):
        indexes = {}
        for config_id, factory in zip(CONFIG_IDS, (retrieval.Retriever, StemmedRetriever)):
            index, error = None, None
            start = time.perf_counter_ns()
            try:
                index = factory(doc["text"], {k: config[k] for k in ("words", "overlap_words")}, settings)
            except Exception as exc:
                error = type(exc).__name__
            indexes[config_id] = (index, error)
            preprocessing.append({"document_id": doc["document_id"], "config_id": config_id,
                "index_latency_ms": (time.perf_counter_ns() - start) / 1_000_000, "error_type": error,
                "raw_vocabulary_size": index.index.raw_vocabulary_size if index is not None and config_id == CONFIG_IDS[1] else None,
                "stem_vocabulary_size": index.index.stem_vocabulary_size if index is not None and config_id == CONFIG_IDS[1] else None})
        if all(indexes[c][0] is not None for c in CONFIG_IDS) and indexes[CONFIG_IDS[0]][0].chunks != indexes[CONFIG_IDS[1]][0].chunks:
            raise ValueError("stemmer_changed_source_windows")
        for ti, task in enumerate(t for t in inputs["tasks"] if t["document_id"] == doc["document_id"]):
            order = CONFIG_IDS if (di + ti) % 2 == 0 else CONFIG_IDS[::-1]
            for config_id in order:
                start = time.perf_counter_ns()
                index, error = indexes[config_id]
                context = []
                if index is not None:
                    try:
                        context = budget.budgeted_context(index, task["query"], task["category"], len(doc["text"]), config, old_protocol)
                    except Exception as exc:
                        error = type(exc).__name__
                row = {"case_id": task["case_id"], "document_id": doc["document_id"], "partition": "development",
                       "category": task["category"], "length_quartile": doc["length_quartile"], "config_id": config_id,
                       "status": "error" if error else "ok", "error_type": error, "chunks": context,
                       "retrieval_latency_ms": (time.perf_counter_ns() - start) / 1_000_000,
                       "case_origin": "public_real_contract", "execution_origin": "local_algorithm"}
                emit(row)
                rows.append(row)
    return rows, preprocessing


def decision(results, paired):
    a, b = (results[c]["overall"] for c in CONFIG_IDS)
    checks = {"full_coverage_strictly_improves": b["all_gold_covered_rate"] is not None and
              a["all_gold_covered_rate"] is not None and b["all_gold_covered_rate"] > a["all_gold_covered_rate"],
              "mean_recall_not_lower": b["macro_gold_character_recall"] is not None and
              a["macro_gold_character_recall"] is not None and b["macro_gold_character_recall"] >= a["macro_gold_character_recall"],
              "no_previously_complete_task_lost": paired["fully_covered_lost"] == 0,
              "both_arms_complete": all(not r["overall"]["failed_queries"] and not r["overall"]["missing_queries"] for r in results.values())}
    return {"checks": checks, "qualifies_for_future_frozen_evaluation": all(checks.values()), "promoted_to_sql_or_powerbi": False}


def run():
    protocol = load_protocol()
    parent_dir, parent, inputs, old_protocol, old_predictions, frozen = shared.load_development(protocol)
    if old_protocol["character_budget"] != protocol["character_budget"]:
        raise ValueError("parent_budget_mismatch")
    run_id = uuid.uuid4().hex
    output = base.ROOT / "private/cuad_stemming_v4/runs" / run_id
    source_paths = (Path(__file__), Path(shared.__file__))
    record = {"format": "cuad-stemming-development-v4", "run_id": run_id, "phase": "development_only", "status": "running",
              "started_at": intake.now(), "protocol_sha256": digest(protocol), "parent_summary_sha256": digest(parent),
              "inputs_sha256": digest(inputs), "parent_predictions_sha256": digest(old_predictions),
              "source_sha256": budget.source_hashes() | {str(p.relative_to(base.ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
              "environment": {"python": platform.python_version(), "machine": platform.machine(),
                              **{p: importlib.metadata.version(p) for p in ("snowballstemmer", "rank-bm25", "numpy")}},
              "case_origin": "public_real_contract", "execution_origin": "local_algorithm",
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
        intake.write_json(output / "preprocessing.json", {"indexes": preprocessing})
        parent_context = {r["case_id"]: r for r in old_predictions["predictions"] if r["config_id"] == protocol["parent_candidate_id"]}
        docs = {d["document_id"]: d for d in inputs["documents"]}
        keys = set()
        for row in predictions:
            key = row["case_id"], row["config_id"]
            if key in keys:
                raise ValueError("duplicate_prediction")
            keys.add(key)
            if row["status"] == "ok" and shared.context_size(row["chunks"]) != min(protocol["character_budget"], len(docs[row["document_id"]]["text"])):
                raise ValueError("equal_character_budget_failed")
            if row["config_id"] == CONFIG_IDS[0] and (row["status"] != parent_context[row["case_id"]]["status"] or
                    digest(row["chunks"]) != digest(parent_context[row["case_id"]]["chunks"])):
                raise ValueError("parent_baseline_replay_changed")
        if keys != {(t["case_id"], c) for t in inputs["tasks"] for c in CONFIG_IDS}:
            raise ValueError("prediction_membership_changed")
        references = base.read_json(parent_dir / "references.json", private=True)
        if digest(references) != parent["references_sha256"]:
            raise ValueError("references_changed")
        record["new_run_references_loaded"] = True
        intake.write_json(output / "execution.json", record)
        intake.write_json(output / "references.json", references)
        results, scores = budget.evaluate(inputs, references, predictions, protocol["configurations"])
        paired, changes = shared.paired_changes(scores, *CONFIG_IDS)
        intake.write_json(output / "case_scores.json", {"run_id": run_id, "cases": scores})
        intake.write_json(output / "case_changes.json", {"baseline_to_candidate": changes})
        summaries = {c: v["overall"] for c, v in results.items()}
        parent_metrics = parent["results"][protocol["parent_candidate_id"]]["overall"]
        for field in ("macro_gold_character_recall", "all_gold_covered_rate", "any_gold_hit_rate", "mean_retrieved_characters"):
            if summaries[CONFIG_IDS[0]][field] != parent_metrics[field]:
                raise ValueError("parent_baseline_metric_changed")
        record.update(status="complete", completed_at=intake.now(), predictions_sha256=digest(predictions),
                      references_sha256=digest(references), scores_sha256=digest(scores))
        summary = record | {"results": results, "baseline_to_candidate": paired, "decision": decision(results, paired),
                            "query_attempts": len(predictions), "indexes_built": sum(r["error_type"] is None for r in preprocessing)}
        intake.write_json(output / "summary.json", summary)
        intake.write_json(output / "execution.json", record)
        return {"run_directory": str(output), "results": summaries, "paired": paired, "decision": summary["decision"]}
    except Exception as exc:
        record.update(status="failed", error_type=type(exc).__name__)
        intake.write_json(output / "execution.json", record)
        raise


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(run(), indent=2))
