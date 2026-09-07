"""Project-specific offline evidence metrics, not the official CUAD scorer."""
from __future__ import annotations

from .events import digest, identifier

BENCHMARK_FORMAT = "evidence-benchmark-v1"
PREDICTION_FORMAT = "evidence-predictions-v1"
METRIC_VERSION = "evidence-union-v1"
MAX_SPANS = 128


def fields(value: object, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("unexpected_fields")


def interval(text: str, value: object) -> tuple[int, int]:
    fields(value, {"start", "end", "quote"})
    start, end, quote = value["start"], value["end"], value["quote"]
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise ValueError("invalid_offsets")
    if not isinstance(quote, str) or not quote.strip() or text[start:end] != quote:
        raise ValueError("quote_mismatch")
    return start, end


def gold_intervals(text: str, answers: object, is_impossible: object) -> list[tuple[int, int]]:
    if (type(is_impossible) is not bool or not isinstance(answers, list)
            or len(answers) > MAX_SPANS or is_impossible != (len(answers) == 0)):
        raise ValueError("invalid_reference")
    result = []
    for answer in answers:
        fields(answer, {"answer_start", "text"})
        if type(answer["answer_start"]) is not int or not isinstance(answer["text"], str):
            raise ValueError("invalid_reference")
        result.append(interval(text, {"start": answer["answer_start"],
                                     "end": answer["answer_start"] + len(answer["text"]),
                                     "quote": answer["text"]}))
    return result


def union(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def overlap(left: list[tuple[int, int]], right: list[tuple[int, int]]) -> int:
    i = j = total = 0
    while i < len(left) and j < len(right):
        total += max(0, min(left[i][1], right[j][1]) - max(left[i][0], right[j][0]))
        if left[i][1] < right[j][1]:
            i += 1
        else:
            j += 1
    return total


def validate_benchmark(pack: object) -> tuple[dict, dict]:
    fields(pack, {"format", "benchmark_id", "case_origin", "offset_unit", "documents", "cases"})
    if (pack["format"] != BENCHMARK_FORMAT or pack["case_origin"] != "self_authored_synthetic"
            or pack["offset_unit"] != "unicode_codepoint"):
        raise ValueError("This stage accepts only the explicitly synthetic evidence benchmark format")
    identifier(pack["benchmark_id"], "benchmark_id")
    if (not isinstance(pack["documents"], list) or not 1 <= len(pack["documents"]) <= 100
            or not isinstance(pack["cases"], list) or not 1 <= len(pack["cases"]) <= 1000):
        raise ValueError("benchmark_size_limit")
    documents, cases = {}, {}
    total_characters = 0
    for doc in pack["documents"]:
        fields(doc, {"document_id", "text", "scope_note"})
        identifier(doc["document_id"], "document_id")
        if (doc["document_id"] in documents or not isinstance(doc["text"], str)
                or not 1 <= len(doc["text"]) <= 200_000
                or not isinstance(doc["scope_note"], str) or not doc["scope_note"].strip()):
            raise ValueError("invalid_document")
        total_characters += len(doc["text"])
        documents[doc["document_id"]] = doc
    if total_characters > 2_000_000:
        raise ValueError("benchmark_size_limit")
    for case in pack["cases"]:
        fields(case, {"case_id", "document_id", "category", "question", "is_impossible", "answers"})
        for name in ("case_id", "document_id", "category"):
            identifier(case[name], name)
        if (case["case_id"] in cases or case["document_id"] not in documents
                or not isinstance(case["question"], str) or not case["question"].strip()):
            raise ValueError("invalid_case")
        gold_intervals(documents[case["document_id"]]["text"], case["answers"], case["is_impossible"])
        cases[case["case_id"]] = case
    return documents, cases


def grade(text: str, answers: list[dict], is_impossible: bool, response: object) -> dict:
    gold = union(gold_intervals(text, answers, is_impossible))
    result = {"is_impossible": is_impossible, "response_state": "missing",
              "outcome": "missing", "reason": None, "correct_abstention": False,
              "incorrect_abstention": False, "false_answer": False,
              "evidence_precision": None if is_impossible else 0.0,
              "evidence_recall": None if is_impossible else 0.0,
              "evidence_f1": None if is_impossible else 0.0,
              "all_gold_covered": None if is_impossible else False,
              "exact_evidence_union": None if is_impossible else False}
    if response is None:
        return result
    try:
        fields(response, {"status", "abstain", "spans"})
        if response["status"] == "error":
            if response["abstain"] is not None or response["spans"] != []:
                raise ValueError("inconsistent_error_response")
            return result | {"response_state": "error", "outcome": "error"}
        if (response["status"] != "ok" or type(response["abstain"]) is not bool
                or not isinstance(response["spans"], list) or len(response["spans"]) > MAX_SPANS
                or response["abstain"] != (len(response["spans"]) == 0)):
            raise ValueError("inconsistent_response")
        predicted = union([interval(text, s) for s in response["spans"]])
    except ValueError as exc:
        return result | {"response_state": "invalid", "outcome": "invalid", "reason": str(exc)}
    result["response_state"] = "valid"
    if is_impossible:
        return result | {"outcome": "correct_abstention" if response["abstain"] else "false_answer",
                         "correct_abstention": response["abstain"], "false_answer": not response["abstain"]}
    if response["abstain"]:
        return result | {"outcome": "incorrect_abstention", "incorrect_abstention": True}
    predicted_size = sum(end - start for start, end in predicted)
    gold_size = sum(end - start for start, end in gold)
    shared = overlap(predicted, gold)
    precision, recall = shared / predicted_size, shared / gold_size
    f1 = 2 * precision * recall / (precision + recall) if shared else 0.0
    exact = predicted == gold
    return result | {"outcome": "exact_evidence" if exact else ("partial_evidence" if shared else "off_target_evidence"),
                     "evidence_precision": precision, "evidence_recall": recall, "evidence_f1": f1,
                     "all_gold_covered": shared == gold_size, "exact_evidence_union": exact}


def summarize(rows: list[dict]) -> dict:
    positive = [r for r in rows if not r["is_impossible"]]
    negative = [r for r in rows if r["is_impossible"]]
    states = {state: sum(r["response_state"] == state for r in rows)
              for state in ("valid", "missing", "invalid", "error")}
    mean = lambda selected, key: sum(r[key] for r in selected) / len(selected) if selected else None
    return {
        "coverage": {"expected": len(rows), "submitted": len(rows) - states["missing"],
                     "valid": states["valid"], "missing": states["missing"],
                     "invalid": states["invalid"], "error": states["error"],
                     "valid_response_rate": states["valid"] / len(rows) if rows else None},
        "answerable": {"count": len(positive),
                       "evidence_precision_macro": mean(positive, "evidence_precision"),
                       "evidence_recall_macro": mean(positive, "evidence_recall"),
                       "evidence_f1_macro": mean(positive, "evidence_f1"),
                       "all_gold_covered_rate": mean(positive, "all_gold_covered"),
                       "exact_evidence_union_rate": mean(positive, "exact_evidence_union"),
                       "incorrect_abstention_count": sum(r["incorrect_abstention"] for r in positive),
                       "incorrect_abstention_rate": mean(positive, "incorrect_abstention")},
        "unanswerable": {"count": len(negative),
                         "correct_abstention_count": sum(r["correct_abstention"] for r in negative),
                         "correct_abstention_rate": mean(negative, "correct_abstention"),
                         "false_answer_count": sum(r["false_answer"] for r in negative),
                         "false_answer_rate": mean(negative, "false_answer")},
    }


def evaluate(benchmark: dict, predictions: object) -> dict:
    documents, cases = validate_benchmark(benchmark)
    fields(predictions, {"format", "benchmark_sha256", "prediction_origin", "config_id", "predictions"})
    if (predictions["format"] != PREDICTION_FORMAT or predictions["benchmark_sha256"] != digest(benchmark)
            or predictions["prediction_origin"] != "scripted_fixture"):
        raise ValueError("Expected hash-matched scripted predictions; real/model evaluation is not enabled here")
    identifier(predictions["config_id"], "config_id")
    if not isinstance(predictions["predictions"], list) or len(predictions["predictions"]) > len(cases):
        raise ValueError("invalid_prediction_count")
    by_id = {}
    for prediction in predictions["predictions"]:
        # Invalid response bodies count as failures; ambiguous run membership stops the run.
        if not isinstance(prediction, dict) or not isinstance(prediction.get("case_id"), str):
            raise ValueError("missing_prediction_case_id")
        case_id = prediction["case_id"]
        if case_id not in cases or case_id in by_id:
            raise ValueError("unknown_or_duplicate_prediction_case_id")
        by_id[case_id] = prediction
    rows = []
    for case_id, case in cases.items():
        submitted = by_id.get(case_id)
        bad_envelope = submitted is not None and (
            set(submitted) != {"case_id", "document_id", "status", "abstain", "spans"}
            or submitted.get("document_id") != case["document_id"])
        response = (None if submitted is None else
                    {k: submitted[k] for k in ("status", "abstain", "spans") if k in submitted})
        result = grade(documents[case["document_id"]]["text"], case["answers"],
                       case["is_impossible"], {} if bad_envelope else response)
        if bad_envelope:
            result["reason"] = "invalid_prediction_envelope_or_document"
        rows.append({"case_id": case_id, "document_id": case["document_id"],
                     "category": case["category"], **result})
    return {"metric_version": METRIC_VERSION, "benchmark_id": benchmark["benchmark_id"],
            "benchmark_sha256": digest(benchmark), "predictions_sha256": digest(predictions),
            "case_origin": benchmark["case_origin"], "prediction_origin": predictions["prediction_origin"],
            "config_id": predictions["config_id"], "is_model_evaluation": False,
            "offset_unit": "unicode_codepoint", "summary": summarize(rows),
            "by_category": {category: summarize([r for r in rows if r["category"] == category])
                            for category in sorted({r["category"] for r in rows})},
            "cases": rows,
            "limitations": ["Project-specific character-union metrics, not official CUAD benchmark scores.",
                            "All gold spans are required evidence, not alternative acceptable answers.",
                            "Whitespace counts; touching/overlapping intervals are merged without double counting.",
                            "Exact union ignores segmentation; precision penalizes unrelated extra evidence.",
                            "Missing, failed and invalid responses stay in denominators and receive no credit.",
                            "Absent groups have null metrics; unanswerable cases never inflate evidence F1.",
                            "The scorer checks extraction, not legal interpretation or free-form answer correctness.",
                            "Synthetic scripted outputs are software checks, not model performance."]}
