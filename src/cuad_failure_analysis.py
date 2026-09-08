"""Read-only v2 development diagnostics. Source excerpts are written privately only."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import cuad_budget_experiment as experiment
import cuad_experiment as base
import cuad_intake as intake
from observability import retrieval
from observability.evidence import gold_intervals, overlap, union
from observability.events import digest


def trace_context(index, query, category, text_length, config, protocol):
    scores = index.index.get_scores(retrieval.tokens(query))
    ranked = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), index.chunks[i]["start"]))
    ranking = [index.chunks[i] | {"score": float(scores[i]), "rank": rank}
               for rank, i in enumerate(ranked, 1)]
    proposals = []
    if category in protocol["preamble_categories"] and config["preamble_characters"]:
        proposals.append({"start": 0, "end": min(config["preamble_characters"], text_length), "score": None})
    proposals.extend(ranking)
    selected, reached, used = [], [], 0
    for proposal in proposals:
        reached.append(proposal)
        spans = [(c["start"], c["end"]) for c in selected]
        for start, end in experiment.uncovered(proposal["start"], proposal["end"], spans):
            end = min(end, start + protocol["character_budget"] - used)
            if start < end:
                selected.append({"start": start, "end": end, "score": proposal["score"]})
                used += end - start
            if used == protocol["character_budget"]:
                spans = [(c["start"], c["end"]) for c in selected]
                deferred = experiment.uncovered(proposal["start"], proposal["end"], spans)
                return {"selected": selected, "reached": reached, "ranking": ranking, "deferred": deferred}
    return {"selected": selected, "reached": reached, "ranking": ranking, "deferred": []}


def evidence_diagnostics(text, reference, query, trace, character_budget):
    gold = union(gold_intervals(text, reference["answers"], reference["is_impossible"]))
    selected = union([(c["start"], c["end"]) for c in trace["selected"]])
    gold_size = sum(b - a for a, b in gold)
    hit = overlap(gold, selected)
    regions = []
    for start, end in gold:
        covered = overlap([(start, end)], selected)
        if covered == end - start:
            continue
        boundaries = [p for p in trace["reached"] if p["score"] is not None and
                      (start < p["start"] < end or start < p["end"] < end)]
        gold_ranks = [p["rank"] for p in trace["ranking"] if p["start"] < end and start < p["end"]]
        missing = experiment.uncovered(start, end, selected)
        regions.append({"start": start, "end": end, "characters": end - start,
                        "covered_characters": covered,
                        "query_terms_in_region": sorted(set(retrieval.tokens(query)) &
                                                        set(retrieval.tokens(text[start:end]))),
                        "best_overlapping_chunk_rank": min(gold_ranks) if gold_ranks else None,
                        "shares_reached_window_boundary": bool(boundaries) and covered > 0,
                        "missing_fragments": [{"start": a, "end": b, "excerpt": text[a:min(b, a + 360)],
                                               "surrounding_excerpt": text[max(0, a - 80):min(len(text), b + 80, a + 440)]}
                                              for a, b in missing]})
    cut_gold = overlap(gold, union(trace["deferred"]))
    return {"is_answerable": bool(gold), "gold_characters": gold_size, "gold_regions": len(gold),
            "missing_gold_characters": gold_size - hit,
            "gold_exceeds_character_budget": gold_size > min(character_budget, len(text)),
            "missing_in_budget_cut_proposal": cut_gold,
            "other_missing_gold_characters": gold_size - hit - cut_gold,
            "incomplete_regions": regions}


def analyze(run_directory: Path):
    summary = base.read_json(run_directory / "summary.json", private=True)
    if (summary.get("format") != "cuad-budget-results-v2" or summary.get("phase") != "development"
            or summary.get("status") != "complete"):
        raise ValueError("completed_development_only")
    read = lambda name: base.read_json(run_directory / name, private=True)
    protocol, inputs, references = read("protocol.json"), read("inputs.json"), read("references.json")
    saved_scores, predictions, selection = read("case_scores.json"), read("predictions.json"), read("selection.json")
    if (digest(protocol) != summary["protocol_sha256"] or digest(inputs) != summary["inputs_sha256"]
            or digest(references) != summary["references_sha256"]
            or digest(saved_scores["cases"]) != summary["case_scores_sha256"]
            or digest(summary) != selection["summary_sha256"]
            or summary["source_sha256"] != experiment.source_hashes()
            or selection["protocol_sha256"] != summary["protocol_sha256"]
            or selection["source_sha256"] != summary["source_sha256"]
            or selection["development_run_id"] != summary["run_id"]
            or saved_scores["run_id"] != summary["run_id"] or predictions["run_id"] != summary["run_id"]):
        raise ValueError("frozen_artifacts_changed")
    if any(d["partition"] != "development" for d in inputs["documents"]):
        raise ValueError("evaluation_document_in_development")
    results, rescored = experiment.evaluate(inputs, references, predictions["predictions"], protocol["configurations"])
    if digest(rescored) != digest(saved_scores["cases"]) or digest(results) != digest(summary["results"]):
        raise ValueError("predictions_do_not_reconcile")
    _, frozen = experiment.base_bundle(protocol)
    baseline, candidate = protocol["baseline_config_id"], selection["selected_candidate"]
    if candidate != experiment.choose_candidate(results, protocol):
        raise ValueError("candidate_selection_changed")
    config = next(c for c in protocol["configurations"] if c["config_id"] == candidate)
    docs = {d["document_id"]: d for d in inputs["documents"]}
    refs = {r["case_id"]: r for r in references["references"]}
    scores = {(r["case_id"], r["config_id"]): r for r in rescored}
    saved = {(r["case_id"], r["config_id"]): r for r in predictions["predictions"]}
    indexes, cases = {}, []
    for task in inputs["tasks"]:
        doc = docs[task["document_id"]]
        if doc["document_id"] not in indexes:
            indexes[doc["document_id"]] = retrieval.Retriever(doc["text"],
                {k: config[k] for k in ("words", "overlap_words")}, frozen["retrieval"])
        trace = trace_context(indexes[doc["document_id"]], task["query"], task["category"], len(doc["text"]), config, protocol)
        prediction = saved[(task["case_id"], candidate)]
        if prediction["status"] != "ok" or digest(trace["selected"]) != digest(prediction["chunks"]):
            raise ValueError("diagnostic_replay_differs_from_saved_context")
        diagnostics = evidence_diagnostics(doc["text"], refs[task["case_id"]], task["query"], trace, protocol["character_budget"])
        metrics = {c["config_id"]: scores[(task["case_id"], c["config_id"])] for c in protocol["configurations"]}
        a, b = metrics[baseline]["gold_character_recall"], metrics[candidate]["gold_character_recall"]
        change = "not_scored" if a is None else "improved" if b > a else "regressed" if b < a else "unchanged"
        cases.append({"case_id": task["case_id"], "document_id": doc["document_id"], "category": task["category"],
                      "length_quartile": doc["length_quartile"], "question": task["question"],
                      "query": task["query"], "change": change,
                      "recall_by_configuration": {c: m["gold_character_recall"] for c, m in metrics.items()},
                      "selected_context_characters": metrics[candidate]["retrieved_characters"],
                      "human_review_status": "pending", **diagnostics})
    positive = [r for r in cases if r["is_answerable"]]
    failures = [r for r in positive if r["missing_gold_characters"]]
    negatives = [r for r in cases if not r["is_answerable"]]
    controls = [r for r in positive if not r["missing_gold_characters"]]
    queue = sorted(failures, key=lambda r: (r["change"] != "regressed", r["category"], r["case_id"]))
    for pool in (negatives, controls):
        chosen_categories = set()
        for row in sorted(pool, key=lambda r: (r["category"], r["case_id"])):
            if row["category"] not in chosen_categories and len(chosen_categories) < 3:
                queue.append(row)
                chosen_categories.add(row["category"])
    aggregate = {"documents": len(docs), "tasks": len(cases), "answerable": len(positive), "unanswerable": len(negatives),
                 "candidate_incomplete": len(failures), "candidate_complete": len(controls),
                 "baseline_to_candidate_changes": dict(Counter(r["change"] for r in positive)),
                 "incomplete_by_category": dict(Counter(r["category"] for r in failures)),
                 "incomplete_by_quartile": dict(Counter(r["length_quartile"] for r in failures)),
                 "failure_features_nonexclusive": {
                     "multiple_gold_regions": sum(r["gold_regions"] > 1 for r in failures),
                     "gold_exceeds_character_budget": sum(r["gold_exceeds_character_budget"] for r in failures),
                     "budget_cut_intersects_missing_gold": sum(r["missing_in_budget_cut_proposal"] > 0 for r in failures),
                     "partially_covered_region": sum(any(s["covered_characters"] > 0 for s in r["incomplete_regions"]) for r in failures),
                     "partial_region_shares_window_boundary": sum(any(s["shares_reached_window_boundary"] for s in r["incomplete_regions"]) for r in failures),
                     "wholly_missed_region_without_literal_query_term": sum(any(s["covered_characters"] == 0 and
                         not s["query_terms_in_region"] for s in r["incomplete_regions"]) for r in failures)},
                 "negative_contexts_returned": sum(r["selected_context_characters"] > 0 for r in negatives),
                 "review_queue_cases": len(queue), "human_reviews_completed": 0}
    report = {"format": "cuad-development-diagnostics-v1", "run_id": summary["run_id"],
              "generated_at": intake.now(), "analysis_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "source_summary_sha256": digest(summary), "predictions_sha256": digest(predictions),
              "source_files": {name: str((run_directory / name).resolve())
                               for name in ("inputs.json", "references.json", "predictions.json")},
              "scope": "posthoc_development_diagnostics_not_new_evaluation_or_human_adjudication",
              "original_scores_verified": True, "candidate_contexts_replayed": len(cases),
              "external_inference_calls": 0, "aggregate": aggregate, "cases": cases,
              "review_queue": [{"review_id": f"R{i:02d}", "case_id": r["case_id"], "status": "pending"}
                               for i, r in enumerate(queue, 1)]}
    output = base.ROOT / "private/cuad_failure_analysis" / summary["run_id"] / "diagnostics.json"
    intake.write_json(output, report)
    return {"path": str(output), "aggregate": aggregate}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development-run", type=Path, required=True)
    print(json.dumps(analyze(parser.parse_args().development_run), indent=2))


if __name__ == "__main__":
    main()
