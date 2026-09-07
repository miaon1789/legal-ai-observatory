"""Prepare a private, non-executable CUAD review bundle. No network or SQL calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
import zipfile

import cuad_intake as intake

FORMAT = "cuad-local-preparation-v1"
LIMITS = {
    "external_inference": "not_approved",
    "publication_of_source_text": "not_approved",
    "legal_clearance": "not_established",
    "human_full_text_review": "pending",
}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def validate_review(review: object) -> list[dict]:
    if (not isinstance(review, dict) or type(review.get("schema_version")) is not int
            or review["schema_version"] != 1
            or review.get("dataset") != "CUAD" or review.get("revision") != intake.REVISION
            or review.get("purpose") != "private_preparation_only"
            or review.get("reviewer_kind") != "coding_assistant"
            or review.get("limits") != LIMITS):
        raise ValueError("Expected a local-only, pending-clearance review record for this CUAD revision")
    docs = review.get("documents")
    if not isinstance(docs, list) or not 1 <= len(docs) <= 10:
        raise ValueError("A review record must contain 1-10 candidate documents")
    ids = set()
    for doc in docs:
        if (not isinstance(doc, dict) or not isinstance(doc.get("sample_id"), str)
                or not re.fullmatch(r"cuad-[0-9a-f]{12}", doc["sample_id"])
                or doc["sample_id"] in ids
                or not isinstance(doc.get("text_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", doc["text_sha256"])
                or doc.get("disposition") not in ("prepare_locally", "hold")
                or doc.get("assistant_full_text_review") != "complete"
                or not isinstance(doc.get("scope_note"), str) or not doc["scope_note"].strip()
                or not isinstance(doc.get("reason"), str) or not doc["reason"].strip()):
            raise ValueError("Missing or inconsistent candidate review fields")
        ids.add(doc["sample_id"])
        if doc["disposition"] == "prepare_locally":
            source = doc.get("source")
            if (not isinstance(source, dict) or source.get("kind") != "original_exhibit"
                    or source.get("identity_status") != "corroborated"
                    or not isinstance(source.get("url"), str)):
                raise ValueError("Local preparation requires a recorded original-exhibit identity check")
            url = urlsplit(source["url"])
            if (url.scheme != "https" or url.netloc != "www.sec.gov"
                    or not url.path.startswith("/Archives/edgar/data/") or url.query or url.fragment):
                raise ValueError("Unexpected SEC source URL in review record")
            if not isinstance(doc.get("masks"), list):
                raise ValueError("An explicit mask list is required, even when empty")
    return docs


def mask_text(text: str, ranges: list[dict]) -> str:
    # Explicit reviewed intervals; offsets are Unicode code points, not bytes or UTF-16 units.
    previous = 0
    parts = []
    for item in ranges:
        if (not isinstance(item, dict) or type(item.get("start")) is not int
                or type(item.get("end")) is not int or not isinstance(item.get("reason"), str)
                or not item["reason"].strip()):
            raise ValueError("Invalid reviewed masking interval")
        start, end = item["start"], item["end"]
        if not previous <= start < end <= len(text):
            raise ValueError("Mask intervals must be ordered, non-overlapping and inside the document")
        segment = text[start:end]
        if sha(segment) != item.get("segment_sha256"):
            raise ValueError("Mask interval no longer matches the reviewed text")
        parts.extend((text[previous:start], "".join(c if c.isspace() else "X" for c in segment)))
        previous = end
    parts.append(text[previous:])
    return "".join(parts)


def build_bundle(review: dict, full: list[dict], train: list[dict], archive_sha256: str) -> dict:
    records = validate_review(review)
    source_docs = {"cuad-" + sha(d["title"])[:12]: d for d in full}
    train_docs = {d["title"]: d for d in train}
    inputs, references, transformations, held = [], [], [], []
    tasks = []
    seen_case_ids = set()
    for record in records:
        sample_id = record["sample_id"]
        original = source_docs.get(sample_id)
        if original is None or original["title"] not in train_docs:
            raise ValueError("Candidate must belong to the pinned official training partition")
        paragraph = original["paragraphs"][0]
        text = paragraph["context"]
        if (sha(text) != record["text_sha256"]
                or text != train_docs[original["title"]]["paragraphs"][0]["context"]):
            raise ValueError("Candidate text no longer matches its review or training copy")
        if record["disposition"] == "hold":
            held.append({"sample_id": sample_id, "reason": record["reason"]})
            continue
        if len(text) > 100_000:
            raise ValueError("Initial preparation is limited to 100,000 code points per document")
        transformed = mask_text(text, record["masks"])
        inputs.append({"document_id": sample_id, "text": transformed, "scope_note": record["scope_note"]})
        transformations.append({"document_id": sample_id, "split": "train", "source": record["source"],
                                "original_text_sha256": sha(text), "prepared_text_sha256": sha(transformed),
                                "characters": len(text), "method": "reviewed-codepoint-mask-v1",
                                "mask_count": len(record["masks"]), "masks": record["masks"]})
        for qa in paragraph["qas"]:
            case_id = "cuadq-" + sha(sample_id + "\0" + qa["id"])[:24]
            if case_id in seen_case_ids:
                raise ValueError("Duplicate question identity")
            seen_case_ids.add(case_id)
            tasks.append({"case_id": case_id, "document_id": sample_id, "question": qa["question"]})
            for answer in qa["answers"]:
                start, answer_text = answer["answer_start"], answer["text"]
                end = start + len(answer_text)
                if text[start:end] != answer_text:
                    raise ValueError("Original annotation has an exact-offset mismatch")
                if any(m["start"] < end and start < m["end"] for m in record["masks"]):
                    raise ValueError("Mask overlaps gold evidence; hold or redesign the task explicitly")
                if transformed[start:end] != answer_text:
                    raise ValueError("Prepared text no longer preserves the annotated evidence")
            references.append({"case_id": case_id, "document_id": sample_id, "source_qa_id": qa["id"],
                               "answers": qa["answers"], "is_impossible": qa["is_impossible"]})
    if not inputs:
        raise ValueError("No candidate is eligible for local preparation")
    provenance = {"dataset": "CUAD", "revision": intake.REVISION, "split": "train",
                  "archive_sha256": archive_sha256, "review_sha256": hashlib.sha256(encoded(review)).hexdigest()}
    input_file = {"format": FORMAT, "purpose": "private_preparation_only", "limits": LIMITS,
                  "source": provenance, "documents": inputs, "tasks": tasks}
    reference_file = {"format": FORMAT, "source": provenance, "references": references}
    input_bytes, reference_bytes = encoded(input_file), encoded(reference_file)
    receipt = {
        "format": FORMAT, "source": provenance, "limits": LIMITS,
        "status": "prepared_for_local_inspection_not_model_evaluation",
        "offset_unit": "unicode_codepoint", "selection": "reviewed convenience subset of official train",
        "inputs_sha256": hashlib.sha256(input_bytes).hexdigest(),
        "references_sha256": hashlib.sha256(reference_bytes).hexdigest(),
        "prepared_documents": len(inputs), "held_documents": held, "tasks": len(tasks),
        "answerable_tasks": sum(not r["is_impossible"] for r in references),
        "unanswerable_tasks": sum(r["is_impossible"] for r in references),
        "answer_spans": sum(len(r["answers"]) for r in references),
        "evidence_offset_mismatches": 0, "transformations": transformations,
        "notes": ["Masking selected names is data minimization, not anonymization or rights clearance.",
                  "Source identity is a reviewer assertion; this offline tool does not verify URLs.",
                  "Gold spans may overlap or express multiple conditions; they are not single-value answers.",
                  "References are kept out of inputs; never use them to select retrieval context.",
                  "The existing synthetic-only runner does not load this bundle."]}
    return {"inputs.json": input_bytes, "references.json": reference_bytes, "receipt.json": encoded(receipt)}


def prepare(review_path: Path) -> dict:
    review_path = review_path.absolute()
    intake.require_private(review_path)
    if not review_path.is_file() or review_path.stat().st_size > 1_000_000:
        raise ValueError("Missing or oversized local review record")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    validate_review(review)
    full, train, _, archive_sha256, _ = intake.load_partitioned_documents()
    files = build_bundle(review, full, train, archive_sha256)
    bundle_id = hashlib.sha256(files["receipt.json"]).hexdigest()
    destination = intake.DIRECTORY / "prepared" / bundle_id
    # Validate every path before writing; the receipt is the last completion marker.
    for name in files:
        intake.require_private(destination / name)
    for name, content in files.items():
        intake.write_private(destination / name, content)
    receipt = json.loads(files["receipt.json"])
    return {"bundle_id": bundle_id, "directory": str(destination),
            **{key: receipt[key] for key in ("status", "prepared_documents", "tasks", "answerable_tasks",
                                            "unanswerable_tasks", "answer_spans", "evidence_offset_mismatches")},
            "held_document_ids": [d["sample_id"] for d in receipt["held_documents"]],
            "external_inference_calls": 0, "sql_writes": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.review), indent=2))
        return 0
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        print(f"Preparation stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
