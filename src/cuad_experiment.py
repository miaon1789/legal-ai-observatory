"""A fixed, private CUAD experiment. This module has no external inference path."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
import uuid

import cuad_intake as intake
from observability.events import canonical, digest
from observability.evidence import gold_intervals
from observability import retrieval

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "benchmarks/cuad/protocol-v1.json"
OUTPUT = ROOT / "private/cuad_experiment"
LIMITS = {"external_inference": "not_approved", "publication_of_source_text": "not_approved",
          "legal_clearance": "not_established", "human_full_text_review": "pending"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def document_id(title: str) -> str:
    return "cuad-" + sha(title)[:12]


def normalized_hash(text: str) -> str:
    return sha(re.sub(r"\s+", " ", text).strip().casefold())


def read_json(path: Path, private: bool = False) -> dict:
    if private:
        intake.require_private(path)
    if not path.is_file() or path.stat().st_size > 80_000_000:
        raise ValueError("invalid_json_file_size")

    def unique(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate_json_key")
            value[key] = item
        return value

    def finite(value):
        raise ValueError("nonfinite_json")

    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique, parse_constant=finite)
    if not isinstance(result, dict):
        raise ValueError("expected_json_object")
    return result


def validate_protocol(protocol: dict) -> None:
    if (protocol["dataset_revision"] != intake.REVISION or protocol["limits"] != LIMITS
            or protocol["execution_origin"] != "local_algorithm"
            or protocol["selection"] != "length_quartiles_then_seeded_title_hash"
            or protocol["duplicate_policy"] != "exclude_all_members_of_whitespace_casefold_duplicate_groups_before_selection"
            or protocol["tokenization"] != "unicode_word_regex_casefold_no_stemming_no_stopwords"
            or protocol["ranking"] != "descending_score_then_ascending_chunk_start_no_score_threshold"):
        raise ValueError("unsupported_protocol_or_permissions")
    quotas = protocol["split_quotas"]
    if set(quotas) != {"development", "evaluation"}:
        raise ValueError("invalid_partitions")
    for values in quotas.values():
        if len(values) != 4 or any(type(v) is not int or v < 1 for v in values):
            raise ValueError("invalid_quotas")
    if sum(sum(v) for v in quotas.values()) > 100:
        raise ValueError("document_limit")
    categories = protocol["categories"]
    if (not 1 <= len(categories) <= 41 or len({c["category"] for c in categories}) != len(categories)
            or any(not c["category"] or not retrieval.tokens(c["query"]) for c in categories)):
        raise ValueError("invalid_categories")
    configs = protocol["configurations"]
    if (not 1 <= len(configs) <= 4 or len({c["config_id"] for c in configs}) != len(configs)
            or any(type(c["top_k"]) is not int or not 1 <= c["top_k"] <= 20 for c in configs)):
        raise ValueError("invalid_configurations")
    settings = protocol["retrieval"]
    if (settings != {"library": "rank-bm25", "version": "0.2.2", "algorithm": "BM25Okapi",
                     "k1": 1.5, "b": .75, "epsilon": .25}):
        raise ValueError("unsupported_retrieval_settings")
    retrieval.chunks("fixture", **protocol["chunking"])


def select_documents(full: list[dict], train: list[dict], test: list[dict], protocol: dict):
    """Selection uses membership, text length, duplicate identity and title hashes only."""
    all_docs = {d["title"]: d for d in full}
    official = {"development": {d["title"]: d for d in train},
                "evaluation": {d["title"]: d for d in test}}
    if (len(all_docs) != len(full) or len(official["development"]) != len(train)
            or len(official["evaluation"]) != len(test)
            or set(official["development"]) & set(official["evaluation"])
            or set(official["development"]) | set(official["evaluation"]) != set(all_docs)):
        raise ValueError("official_partitions_do_not_reconcile")
    groups = defaultdict(list)
    for doc in full:
        groups[normalized_hash(doc["paragraphs"][0]["context"])].append(doc["title"])
    duplicates = {title for titles in groups.values() if len(titles) > 1 for title in titles}
    prior = set(protocol["exclude_previously_reviewed_ids"])
    selected, strata = [], []
    for partition, members in official.items():
        eligible = [all_docs[title] for title in members
                    if title not in duplicates and document_id(title) not in prior]
        eligible.sort(key=lambda d: (len(d["paragraphs"][0]["context"]), sha(d["title"])))
        for band, quota in enumerate(protocol["split_quotas"][partition]):
            pool = eligible[len(eligible) * band // 4:len(eligible) * (band + 1) // 4]
            if len(pool) < quota:
                raise ValueError("insufficient_documents_in_length_stratum")
            ranked = sorted(pool, key=lambda d: sha(protocol["selection_seed"] + "\n" + d["title"]))
            strata.append({"partition": partition, "length_quartile": band + 1,
                           "eligible": len(pool), "selected": quota,
                           "min_characters": len(pool[0]["paragraphs"][0]["context"]),
                           "max_characters": len(pool[-1]["paragraphs"][0]["context"])})
            for doc in ranked[:quota]:
                text = doc["paragraphs"][0]["context"]
                if members[doc["title"]]["paragraphs"][0]["context"] != text:
                    raise ValueError("canonical_partition_text_mismatch")
                selected.append((partition, band + 1, doc))
    return selected, {"official_documents": {"canonical": len(full), "train": len(train), "test": len(test)},
                      "normalized_duplicate_groups": sum(len(v) > 1 for v in groups.values()),
                      "normalized_duplicate_documents_excluded": len(duplicates),
                      "previously_reviewed_documents_excluded": sum(document_id(t) in prior for t in all_docs),
                      "strata": strata, "near_duplicate_or_related_party_check": "not_performed"}


def build_bundle(protocol: dict, full, train, test, archive_sha: str) -> dict:
    validate_protocol(protocol)
    if archive_sha != protocol["archive_sha256"]:
        raise ValueError("archive_sha256_mismatch")
    selected, selection = select_documents(full, train, test, protocol)
    common = {"protocol_sha256": digest(protocol), "case_origin": "public_real_contract", "limits": LIMITS}
    inputs = {"format": "cuad-retrieval-inputs-v1", **common, "documents": [], "tasks": []}
    references = {"format": "cuad-retrieval-references-v1", **common, "references": []}
    manifest = {"format": "cuad-retrieval-manifest-v1", **common, "archive_sha256": archive_sha,
                "selection": selection, "documents": [], "offset_mismatches": [],
                "authorization_context": "Owner agreed to a 50-document real-data experiment; local computation only.",
                "text_changes": "none; canonical source and annotations retained verbatim in private storage",
                "review_status": "automated_triage_only; not individually source-verified or cleared"}
    for partition, band, doc in selected:
        doc_id = document_id(doc["title"])
        text = doc["paragraphs"][0]["context"]
        inputs["documents"].append({"document_id": doc_id, "text": text,
                                    "partition": partition, "length_quartile": band})
        manifest["documents"].append({"document_id": doc_id, "title": doc["title"],
                                      "partition": partition, "length_quartile": band,
                                      "text_sha256": sha(text), "characters": len(text),
                                      "normalized_text_sha256": normalized_hash(text),
                                      "triage_marker_counts": {k: len(v) for k, v in intake.markers(text).items()}})
        qas = {q["id"].rsplit("__", 1)[-1]: q for q in doc["paragraphs"][0]["qas"]}
        if len(qas) != len(doc["paragraphs"][0]["qas"]):
            raise ValueError("duplicate_category_in_document")
        for definition in protocol["categories"]:
            qa = qas[definition["category"]]
            case_id = doc_id + "-" + sha(definition["category"])[:12]
            inputs["tasks"].append({"case_id": case_id, "document_id": doc_id,
                                    "category": definition["category"], "question": qa["question"],
                                    "query": definition["query"]})
            references["references"].append({"case_id": case_id, "document_id": doc_id,
                                             "source_qa_id": qa["id"], "is_impossible": qa["is_impossible"],
                                             "answers": qa["answers"]})
            try:
                gold_intervals(text, qa["answers"], qa["is_impossible"])
            except ValueError:
                manifest["offset_mismatches"].append(case_id)
    if (len({d["document_id"] for d in inputs["documents"]}) != len(selected)
            or len({t["case_id"] for t in inputs["tasks"]}) != len(inputs["tasks"])):
        raise ValueError("identifier_collision")
    manifest["counts"] = {"documents": len(selected), "tasks": len(inputs["tasks"]),
                           "answerable": sum(not r["is_impossible"] for r in references["references"]),
                           "unanswerable": sum(r["is_impossible"] for r in references["references"]),
                           "gold_spans": sum(len(r["answers"]) for r in references["references"])}
    return {"inputs.json": inputs, "references.json": references, "manifest.json": manifest,
            "protocol.json": protocol}


def prepare() -> dict:
    protocol = read_json(PROTOCOL)
    full, train, test, archive_sha, _ = intake.load_partitioned_documents()
    files = build_bundle(protocol, full, train, test, archive_sha)
    hashes = {name: digest(value) for name, value in files.items()}
    bundle_id = digest(hashes)
    directory = OUTPUT / "bundles" / bundle_id
    for name, value in files.items():
        path = directory / name
        if path.exists():
            if digest(read_json(path, private=True)) != hashes[name]:
                raise ValueError("existing_bundle_was_modified")
        else:
            intake.write_json(path, value)
    receipt_path = directory / "receipt.json"
    receipt = {"bundle_id": bundle_id, "created_at": intake.now(), "file_sha256": hashes,
               "status": "blocked_offset_mismatch" if files["manifest.json"]["offset_mismatches"] else "ready_local_only"}
    if not receipt_path.exists():
        intake.write_json(receipt_path, receipt)
    else:
        saved = read_json(receipt_path, private=True)
        if saved["bundle_id"] != bundle_id or saved["file_sha256"] != hashes or saved["status"] != receipt["status"]:
            raise ValueError("receipt_mismatch")
    return {"bundle_directory": str(directory), "status": receipt["status"], **files["manifest.json"]["counts"],
            "offset_mismatches": len(files["manifest.json"]["offset_mismatches"])}


def read_bundle_file(directory: Path, name: str, receipt: dict) -> dict:
    value = read_json(directory / name, private=True)
    if digest(value) != receipt["file_sha256"][name]:
        raise ValueError("bundle_file_hash_mismatch")
    return value


def run(directory: Path) -> dict:
    receipt = read_json(directory / "receipt.json", private=True)
    if (receipt["status"] != "ready_local_only" or digest(receipt["file_sha256"]) != receipt["bundle_id"]
            or set(receipt["file_sha256"]) != {"inputs.json", "references.json", "manifest.json", "protocol.json"}):
        raise ValueError("invalid_or_blocked_bundle")
    protocol = read_bundle_file(directory, "protocol.json", receipt)
    validate_protocol(protocol)
    if digest(read_json(PROTOCOL)) != digest(protocol):
        raise ValueError("working_protocol_differs_from_prepared_protocol")
    inputs = read_bundle_file(directory, "inputs.json", receipt)
    manifest = read_bundle_file(directory, "manifest.json", receipt)
    if manifest["offset_mismatches"] or inputs["limits"] != LIMITS:
        raise ValueError("unready_inputs")
    versions = {name: importlib.metadata.version(name) for name in ("rank-bm25", "numpy")}
    if versions["rank-bm25"] != protocol["retrieval"]["version"]:
        raise ValueError("retriever_version_mismatch")
    run_id = uuid.uuid4().hex
    output = OUTPUT / "runs" / run_id
    started = intake.now()
    source_files = [Path(__file__), ROOT / "src/observability/retrieval.py",
                    ROOT / "src/observability/evidence.py", ROOT / "src/cuad_intake.py",
                    ROOT / "src/observability/events.py"]
    execution = {"run_id": run_id, "bundle_id": receipt["bundle_id"], "started_at": started,
                 "protocol_sha256": digest(protocol), "case_origin": "public_real_contract",
                 "execution_origin": "local_algorithm", "is_llm_evaluation": False,
                 "external_inference_calls": 0, "api_spend_usd": 0,
                 "environment": {"python": platform.python_version(), "system": platform.system(),
                                 "machine": platform.machine(), **versions},
                 "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in source_files}, "limits": LIMITS,
                 "status": "running", "evaluation_labels_accessed": False}
    intake.write_json(output / "execution.json", execution)
    events_path = output / "query_events.jsonl"
    intake.require_private(events_path)
    start = time.perf_counter_ns()
    try:
        with open(events_path, "x", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            def emit(row):
                event = row | {"run_id": run_id, "recorded_at": intake.now(),
                               "bundle_id": receipt["bundle_id"], "protocol_sha256": digest(protocol)}
                stream.write(canonical(event) + "\n")
                stream.flush()

            predictions = retrieval.execute(inputs, protocol, emit)
        intake.write_json(output / "predictions.json", {"run_id": run_id, "predictions": predictions})
        # Persist all predictions before opening the reference file for scoring.
        execution["evaluation_labels_accessed"] = True
        intake.write_json(output / "execution.json", execution)
        references = read_bundle_file(directory, "references.json", receipt)
        results, scored = retrieval.evaluate(inputs, references, predictions, protocol)
        intake.write_json(output / "case_scores.json", {"run_id": run_id, "cases": scored})
        execution.update(status="complete", completed_at=intake.now(),
                         total_wall_ms=(time.perf_counter_ns() - start) / 1_000_000,
                         query_attempts=len(predictions), failed_queries=sum(p["status"] == "error" for p in predictions))
        indexes = {r["document_id"]: r["document_index_latency_ms"] for r in predictions}
        summary = {"format": "cuad-retrieval-results-v1", "execution": execution,
                   "counts": manifest["counts"], "selection": manifest["selection"],
                   "indexing": {"documents": len(indexes), "total_ms": sum(indexes.values()),
                                "p50_ms": retrieval.percentile(list(indexes.values()), .5),
                                "p95_ms": retrieval.percentile(list(indexes.values()), .95)},
                   "results": results,
                   "limitations": ["Project-specific retrieval metrics, not official CUAD scores or answer accuracy.",
                                   "Gold annotations are not independently adjudicated; no legal correctness claim.",
                                   "No answer generation or abstention classifier; negative tasks have no accuracy score.",
                                   "Evaluation labels have now been accessed; do not tune against these 40 documents.",
                                   "Macro metrics average questions; 400 evaluation questions are clustered in 40 documents.",
                                   "Near duplicates and related counterparties are not screened; exact normalized duplicates are excluded.",
                                   "Triage markers are not complete privacy detection or external-processing clearance.",
                                   "Latency is measured local warm-process retrieval, excludes indexing, and is not API latency.",
                                   "No observed law-firm usage, adoption, billable impact or production reliability is measured."]}
        intake.write_json(output / "summary.json", summary)
        intake.write_json(output / "execution.json", execution)
        return {"run_directory": str(output), "bundle_id": receipt["bundle_id"],
                "query_attempts": len(predictions), "failed_queries": execution["failed_queries"],
                "results": {p: {c: v["overall"] for c, v in configs.items()} for p, configs in results.items()}}
    except Exception as exc:
        execution.update(status="failed", completed_at=intake.now(), error_type=type(exc).__name__)
        intake.write_json(output / "execution.json", execution)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare", help="Freeze 50 real documents and 500 tasks, private storage only")
    command = sub.add_parser("run", help="Execute both fixed local BM25 settings; no provider calls")
    command.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare() if args.command == "prepare" else run(args.bundle)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, KeyError, OSError, ImportError) as exc:
        print(f"CUAD experiment stopped ({type(exc).__name__}). Check the private receipt and execution record.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
