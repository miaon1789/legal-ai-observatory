"""Within-document lexical retrieval and gold-free execution on real source text."""
from __future__ import annotations

import math
import re
import statistics
import time

from .evidence import gold_intervals, overlap, union


def tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold(), flags=re.UNICODE)


def chunks(text: str, words: int, overlap_words: int) -> list[dict]:
    if (type(words) is not int or type(overlap_words) is not int
            or not 1 <= words <= 2000 or not 0 <= overlap_words < words):
        raise ValueError("invalid_chunk_settings")
    positions = list(re.finditer(r"\S+", text))
    if not positions:
        raise ValueError("empty_document")
    result = []
    for i in range(0, len(positions), words - overlap_words):
        end_word = min(i + words, len(positions))
        start = 0 if i == 0 else positions[i].start()
        end = len(text) if end_word == len(positions) else positions[end_word].start()
        result.append({"start": start, "end": end})
        if end_word == len(positions):
            break
    return result


class Retriever:
    """Receives only source text and queries. Never receives reference answers."""

    def __init__(self, text: str, chunking: dict, settings: dict):
        from rank_bm25 import BM25Okapi

        self.chunks = chunks(text, **chunking)
        corpus = [tokens(text[c["start"]:c["end"]]) for c in self.chunks]
        if not any(corpus):
            raise ValueError("empty_vocabulary")
        self.index = BM25Okapi(corpus, k1=settings["k1"], b=settings["b"], epsilon=settings["epsilon"])

    def retrieve(self, query: str, top_k: int) -> list[dict]:
        if type(top_k) is not int or not 1 <= top_k <= 20 or not tokens(query):
            raise ValueError("invalid_query_or_k")
        scores = self.index.get_scores(tokens(query))
        if not all(math.isfinite(float(s)) for s in scores):
            raise ValueError("nonfinite_retrieval_score")
        ranking = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), self.chunks[i]["start"]))
        # Zero/negative scores are still rankings, not evidence of unanswerability.
        return [self.chunks[i] | {"score": float(scores[i])} for i in ranking[:top_k]]


def execute(inputs: dict, protocol: dict, emit) -> list[dict]:
    """Persist every attempted query, including failures; references are absent."""
    if any("answers" in c or "is_impossible" in c for c in inputs["tasks"]):
        raise ValueError("gold_in_execution_input")
    rows = []
    by_doc = {d["document_id"]: [] for d in inputs["documents"]}
    for task in inputs["tasks"]:
        by_doc[task["document_id"]].append(task)
    for doc_index, doc in enumerate(inputs["documents"]):
        start = time.perf_counter_ns()
        index, index_error = None, None
        try:
            index = Retriever(doc["text"], protocol["chunking"], protocol["retrieval"])
        except Exception as exc:
            index_error = type(exc).__name__
        index_ms = (time.perf_counter_ns() - start) / 1_000_000
        for task_index, task in enumerate(by_doc[doc["document_id"]]):
            # Alternate order to reduce systematic warm-cache bias between top-k settings.
            configs = protocol["configurations"]
            if (doc_index + task_index) % 2:
                configs = list(reversed(configs))
            for config in configs:
                start = time.perf_counter_ns()
                prediction, error = [], index_error
                if index is not None:
                    try:
                        prediction = index.retrieve(task["query"], config["top_k"])
                    except Exception as exc:
                        error = type(exc).__name__
                row = {"case_id": task["case_id"], "document_id": doc["document_id"],
                       "partition": doc["partition"], "category": task["category"],
                       "length_quartile": doc["length_quartile"], "config_id": config["config_id"],
                       "status": "error" if error else "ok", "error_type": error,
                       "chunks": prediction, "retrieval_latency_ms": (time.perf_counter_ns() - start) / 1_000_000,
                       "document_index_latency_ms": index_ms,
                       "index_chunk_count": len(index.chunks) if index is not None else 0,
                       "case_origin": "public_real_contract", "execution_origin": "local_algorithm",
                       "provider": None, "input_tokens": None, "output_tokens": None, "api_cost_usd": None}
                emit(row)
                rows.append(row)
    return rows


def score_case(text: str, reference: dict, prediction: dict | None) -> dict:
    gold = union(gold_intervals(text, reference["answers"], reference["is_impossible"]))
    predicted, state = [], "missing"
    if prediction is not None:
        state = prediction["status"]
        if state not in {"ok", "error"}:
            raise ValueError("unknown_retrieval_status")
        if state == "error" and prediction["chunks"]:
            raise ValueError("error_with_chunks")
        if state == "ok":
            for chunk in prediction["chunks"]:
                start, end = chunk["start"], chunk["end"]
                if (type(start) is not int or type(end) is not int
                        or not 0 <= start < end <= len(text)):
                    raise ValueError("invalid_retrieved_offsets")
                predicted.append((start, end))
    selected = union(predicted)
    hit = overlap(gold, selected)
    gold_size, context_size = sum(e - s for s, e in gold), sum(e - s for s, e in selected)
    return {"response_state": state, "is_impossible": reference["is_impossible"],
            "gold_character_recall": hit / gold_size if gold_size else None,
            "any_gold_hit": bool(hit) if gold_size else None,
            "all_gold_covered": hit == gold_size if gold_size else None,
            "context_precision": (hit / context_size if context_size else 0.0) if gold_size else None,
            "retrieved_characters": context_size,
            "retrieved_characters_with_overlap": sum(e - s for s, e in predicted),
            "answer_accuracy": None, "abstention_accuracy": None}


def average(values):
    return statistics.mean(values) if values else None


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def summarize(rows: list[dict]) -> dict:
    positive = [r for r in rows if not r["is_impossible"]]
    negative = [r for r in rows if r["is_impossible"]]
    latency = [r["retrieval_latency_ms"] for r in rows if r["response_state"] == "ok"]
    return {
        "documents": len({r["document_id"] for r in rows}), "tasks": len(rows),
        "answerable": len(positive), "unanswerable": len(negative),
        "successful_queries": sum(r["response_state"] == "ok" for r in rows),
        "failed_queries": sum(r["response_state"] == "error" for r in rows),
        "missing_queries": sum(r["response_state"] == "missing" for r in rows),
        "macro_gold_character_recall": average([r["gold_character_recall"] for r in positive]),
        "any_gold_hit_rate": average([r["any_gold_hit"] for r in positive]),
        "all_gold_covered_rate": average([r["all_gold_covered"] for r in positive]),
        "macro_context_precision": average([r["context_precision"] for r in positive]),
        "mean_retrieved_characters": average([r["retrieved_characters"] for r in rows]),
        "mean_retrieved_characters_with_overlap": average([r["retrieved_characters_with_overlap"] for r in rows]),
        "unanswerable_mean_retrieved_characters": average([r["retrieved_characters"] for r in negative]),
        "query_latency_ms_p50": percentile(latency, .5), "query_latency_ms_p95": percentile(latency, .95),
        "answer_accuracy": None, "abstention_accuracy": None,
    }


def evaluate(inputs: dict, references: dict, predictions: list[dict], protocol: dict) -> tuple[dict, list[dict]]:
    tasks = {t["case_id"]: t for t in inputs["tasks"]}
    docs = {d["document_id"]: d for d in inputs["documents"]}
    refs = {r["case_id"]: r for r in references["references"]}
    if len(refs) != len(references["references"]) or set(refs) != set(tasks):
        raise ValueError("references_do_not_reconcile")
    configs = {c["config_id"]: c for c in protocol["configurations"]}
    indexed = {}
    for row in predictions:
        key = row["case_id"], row["config_id"]
        if key in indexed or key[0] not in tasks or key[1] not in configs:
            raise ValueError("unknown_or_duplicate_prediction")
        task = tasks[key[0]]
        doc = docs[task["document_id"]]
        if (row["document_id"] != doc["document_id"] or row["partition"] != doc["partition"]
                or row["category"] != task["category"]
                or row["length_quartile"] != doc["length_quartile"]
                or len(row["chunks"]) > configs[key[1]]["top_k"]):
            raise ValueError("prediction_identity_or_size_mismatch")
        indexed[key] = row
    scored = []
    for task in inputs["tasks"]:
        doc = docs[task["document_id"]]
        reference = refs[task["case_id"]]
        if reference["document_id"] != doc["document_id"]:
            raise ValueError("reference_document_mismatch")
        for config_id in configs:
            prediction = indexed.get((task["case_id"], config_id))
            scored.append({"case_id": task["case_id"], "document_id": doc["document_id"],
                           "partition": doc["partition"], "category": task["category"],
                           "length_quartile": doc["length_quartile"], "config_id": config_id,
                           "retrieval_latency_ms": prediction["retrieval_latency_ms"] if prediction else None,
                           **score_case(doc["text"], reference, prediction)})
    result = {}
    for partition in protocol["split_quotas"]:
        result[partition] = {}
        for config_id in configs:
            rows = [r for r in scored if r["partition"] == partition and r["config_id"] == config_id]
            result[partition][config_id] = {
                "overall": summarize(rows),
                "by_category": {c["category"]: summarize([r for r in rows if r["category"] == c["category"]])
                                for c in protocol["categories"]},
                "by_length_quartile": {str(q): summarize([r for r in rows if r["length_quartile"] == q])
                                       for q in range(1, 5)},
            }
    return result, scored
