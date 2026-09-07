"""Pinned, local-only CUAD acquisition. No SQL import or inference-provider calls."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = "67faa0e6023b04fcaae6cc09497ab00e5d63a2a2"
BLOB_SHA1 = "1ae94ff0a9b70b2e3b9b8d215737c8bfae460ddc"
ARCHIVE_BYTES = 18_309_308
SOURCE = f"https://raw.githubusercontent.com/The-Atticus-Project/cuad/{REVISION}/data.zip"
DIRECTORY = ROOT / "private/datasets/cuad" / REVISION[:12]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def require_private(path: Path) -> None:
    root = ROOT.resolve() / "private"
    if not path.resolve().is_relative_to(root):
        raise ValueError("External data must stay under private/")
    if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)):
        raise ValueError("Symlinks are not allowed in the private intake path")


def write_private(path: Path, value: bytes) -> None:
    require_private(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Refusing to replace a symlink")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            os.chmod(tmp, 0o600)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path: Path, value: object) -> None:
    write_private(path, (json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode())


def verify_archive(raw: bytes) -> str:
    if len(raw) != ARCHIVE_BYTES:
        raise ValueError("Archive size differs from the pinned upstream metadata")
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if blob != BLOB_SHA1:
        raise ValueError("Archive differs from the pinned Git blob")
    return hashlib.sha256(raw).hexdigest()


def read_cached_archive(path: Path) -> bytes:
    require_private(path)
    if not path.is_file() or path.stat().st_size != ARCHIVE_BYTES:
        raise ValueError("Unexpected cached archive type or size")
    with path.open("rb") as stream:
        raw = stream.read(ARCHIVE_BYTES + 1)
    verify_archive(raw)
    return raw


def archive_inventory(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 5000 or sum(i.file_size for i in infos) > 2_000_000_000:
            raise ValueError("Archive exceeds inspection limits")
        names = set()
        rows = []
        for item in infos:
            name = PurePosixPath(item.filename)
            if (name.is_absolute() or ".." in name.parts or "\\" in item.filename
                    or ":" in item.filename or item.filename in names
                    or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1):
                raise ValueError("Unsafe, duplicate or encrypted archive member")
            names.add(item.filename)
            rows.append({"name": item.filename, "bytes": item.file_size,
                         "compressed_bytes": item.compress_size, "is_directory": item.is_dir()})
    return rows


def acquire(acknowledge: bool) -> dict:
    if not acknowledge:
        raise ValueError("Read the data-use plan; pass --accept-local-review to acquire the fixed archive")
    require_private(DIRECTORY)
    path = DIRECTORY / "data.zip"
    require_private(path)
    if path.exists():
        raw = read_cached_archive(path)
    else:
        request = urllib.request.Request(SOURCE, headers={"User-Agent": "legal-ai-observatory-local-intake/1"})
        with urllib.request.urlopen(request, timeout=45) as response:
            if response.url != SOURCE:
                raise ValueError("Unexpected archive redirect; review the new source before acquisition")
            raw = response.read(ARCHIVE_BYTES + 1)
        verify_archive(raw)
        write_private(path, raw)
    sha256 = verify_archive(raw)
    inventory = archive_inventory(path)
    manifest_path = DIRECTORY / "acquisition.json"
    manifest = {
        "dataset": "CUAD", "distribution": "official GitHub data.zip", "revision": REVISION,
        "source_url": SOURCE, "archive_bytes": len(raw), "git_blob_sha1": BLOB_SHA1,
        "archive_sha256": sha256, "acquired_at": now(),
        "license_declared_by_publisher": "CC-BY-4.0",
        "license_page": "https://www.atticusprojectai.org/legal/",
        "license_terms": "https://creativecommons.org/licenses/by/4.0/legalcode.en",
        "publisher_disclaimer": "https://www.atticusprojectai.org/disclaimer/",
        "publisher_dataset_card": "https://huggingface.co/datasets/theatticusproject/cuad-qa/blob/main/README.md",
        "underlying_contract_rights": "Publisher does not warrant the license status of underlying contracts",
        "purpose": "independent portfolio research; bounded local sample inspection",
        "sample_clearance": "pending", "external_inference": "not_approved",
        "publication_of_source_text": "not_approved", "archive_members": inventory,
    }
    if not manifest_path.exists():
        write_json(manifest_path, manifest)
    return {"archive_sha256": sha256, "bytes": len(raw), "archive_members": inventory,
            "directory": str(DIRECTORY), "external_inference_calls": 0}


MARKERS = {
    "email": r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    "phone_like": r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}(?!\d)",
    "ssn_like": r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
    "bank_details_language": r"\b(?:routing number|bank account|account number|wire transfer instructions)\b",
    "signature_marker": r"/s/|\bin witness whereof\b",
    "confidential_treatment": r"\b(?:confidential treatment|redacted|filed separately with the securities)\b",
    "redaction_or_blank": r"\*{3,}|_{3,}|\[\s*\*[^\]]{0,30}\]",
    "rights_notice": r"\bcopyright\b|\ball rights reserved\b|\u00a9",
    "redistribution_language": r"\binternal use only\b|\bno part.{0,60}\b(?:reproduc|distribut)",
}


def markers(text: str) -> dict[str, list[dict]]:
    # Triage indicators, not a PII detector, confidentiality decision or legal judgment.
    return {name: [{"start": m.start(), "end": m.end()} for m in re.finditer(pattern, text, re.I)]
            for name, pattern in MARKERS.items()}


def mask_preview(text: str) -> str:
    for name in ("email", "phone_like", "ssn_like"):
        text = re.sub(MARKERS[name], f"[{name.upper()}]", text, flags=re.I)
    return text


def parse_documents(payload: dict) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Expected CUAD/SQuAD document structure")
    titles = set()
    for doc in payload["data"]:
        if (not isinstance(doc, dict) or not isinstance(doc.get("title"), str)
                or not doc["title"] or doc["title"] in titles
                or not isinstance(doc.get("paragraphs"), list) or len(doc["paragraphs"]) != 1):
            raise ValueError("Unexpected or duplicate document structure")
        titles.add(doc["title"])
        paragraph = doc["paragraphs"][0]
        if (not isinstance(paragraph, dict) or not isinstance(paragraph.get("context"), str)
                or not isinstance(paragraph.get("qas"), list)):
            raise ValueError("Missing document text or questions")
        ids = set()
        for qa in paragraph["qas"]:
            if (not isinstance(qa, dict) or not isinstance(qa.get("id"), str) or not qa["id"] or qa["id"] in ids
                    or not isinstance(qa.get("question"), str) or not isinstance(qa.get("answers"), list)
                    or type(qa.get("is_impossible")) is not bool):
                raise ValueError("Unexpected question structure")
            ids.add(qa["id"])
            if qa["is_impossible"] != (len(qa["answers"]) == 0):
                raise ValueError("Inconsistent impossible/answer annotation")
            for answer in qa["answers"]:
                if (not isinstance(answer, dict) or not isinstance(answer.get("text"), str)
                        or not answer["text"] or type(answer.get("answer_start")) is not int
                        or answer["answer_start"] < 0):
                    raise ValueError("Unexpected answer structure")
    return payload["data"]


def load_partitioned_documents() -> tuple[list[dict], list[dict], list[dict], str, list[dict]]:
    archive_path = DIRECTORY / "data.zip"
    sha256 = hashlib.sha256(read_cached_archive(archive_path)).hexdigest()
    inventory = archive_inventory(archive_path)
    required = {"CUADv1.json", "train_separate_questions.json", "test.json"}
    if not required.issubset({i["name"] for i in inventory if not i["is_directory"]}):
        raise ValueError("Missing canonical data or official partition files")
    with zipfile.ZipFile(archive_path) as archive:
        full = parse_documents(json.loads(archive.read("CUADv1.json")))
        train = parse_documents(json.loads(archive.read("train_separate_questions.json")))
        test = parse_documents(json.loads(archive.read("test.json")))
    train_by_title = {d["title"]: d for d in train}
    test_titles = {d["title"] for d in test}
    full_by_title = {d["title"]: d for d in full}
    if set(train_by_title) & test_titles or set(train_by_title) | test_titles != set(full_by_title):
        raise ValueError("Official document partitions do not reconcile")
    return full, train, test, sha256, inventory


def inspect_samples(sample_count: int = 8) -> dict:
    if type(sample_count) is not int or not 5 <= sample_count <= 10:
        raise ValueError("Local intake inspection is limited to 5-10 training documents")
    full, train, test, sha256, inventory = load_partitioned_documents()
    full_by_title = {d["title"]: d for d in full}
    if len(train) < sample_count:
        raise ValueError("Insufficient training documents for the requested inspection")
    # Fixed ordering, selected before reading marker counts; no performance-based selection.
    selected = sorted(train, key=lambda d: hashlib.sha256(d["title"].encode()).hexdigest())[:sample_count]
    reports = []
    for doc in selected:
        title = doc["title"]
        original = full_by_title[title]
        paragraph = original["paragraphs"][0]
        content = paragraph["context"]
        if content != doc["paragraphs"][0]["context"]:
            raise ValueError("Training text differs from canonical document")
        sample_id = "cuad-" + hashlib.sha256(title.encode()).hexdigest()[:12]
        flags = markers(content)
        mismatches = []
        answers = []
        for qa in paragraph["qas"]:
            for answer in qa["answers"]:
                start, text = answer["answer_start"], answer["text"]
                exact = content[start:start + len(text)] == text
                if not exact:
                    mismatches.append({"qa_id": qa["id"], "answer_start": start})
                answers.append(exact)
        previews = []
        for kind in ("confidential_treatment", "redaction_or_blank", "rights_notice", "redistribution_language"):
            for hit in flags[kind][:3]:
                start, end = max(0, hit["start"] - 80), min(len(content), hit["end"] + 140)
                previews.append({"kind": kind, "start": start, "end": end,
                                 "text": mask_preview(content[start:end])})
        report = {
            "sample_id": sample_id, "original_title": title, "split": "train",
            "source_revision": REVISION, "archive_member": "CUADv1.json",
            "original_filing_url": None, "original_filing_verified": False,
            "text_sha256": hashlib.sha256(content.encode()).hexdigest(), "characters": len(content),
            "question_count": len(paragraph["qas"]), "answer_spans": len(answers),
            "unanswerable_questions": sum(q["is_impossible"] for q in paragraph["qas"]),
            "exact_offset_mismatches": mismatches, "marker_counts": {k: len(v) for k, v in flags.items()},
            "marker_positions": flags, "triage_previews": previews,
            "decision": "local_review_only", "full_text_human_review": "pending",
            "external_inference": "not_approved", "publication_of_source_text": "not_approved",
            "notes": ["Markers are heuristic; zero hits do not establish absence of personal data or restrictions.",
                      "The archive gives document titles but no per-document filing URL or rights warranty.",
                      "An ordinary confidentiality clause is not itself proof this public copy was unlawfully disclosed."]}
        write_json(DIRECTORY / "samples" / f"{sample_id}.json", original)
        write_private(DIRECTORY / "samples" / f"{sample_id}.txt", content.encode())
        write_json(DIRECTORY / "reviews" / f"{sample_id}.json", report)
        reports.append(report)
    summary = {
        "dataset": "CUAD", "revision": REVISION, "checked_at": now(),
        "archive_sha256": sha256,
        "full_documents": len(full), "train_documents": len(train), "test_documents": len(test),
        "bundled_notice_files": [i["name"] for i in inventory
                                 if re.search(r"license|notice|readme|copying|disclaimer", i["name"], re.I)],
        "selection": "first N training titles ordered by SHA-256(title); not representative or performance-selected",
        "inspection_count": len(reports), "source_urls_verified": 0,
        "scope": "automated local triage; assistant review can inspect reported contexts; no legal clearance",
        "external_inference_calls": 0,
        "samples": [{k: r[k] for k in ("sample_id", "split", "characters", "question_count", "answer_spans",
                     "unanswerable_questions", "marker_counts", "decision")} |
                    {"offset_mismatches": len(r["exact_offset_mismatches"])} for r in reports],
    }
    write_json(DIRECTORY / "local_review_summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accept-local-review", action="store_true")
    parser.add_argument("--inspect", action="store_true", help="Offline triage of 5-10 training documents")
    parser.add_argument("--sample-count", type=int, default=8)
    args = parser.parse_args()
    try:
        result = inspect_samples(args.sample_count) if args.inspect else acquire(args.accept_local_review)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        print(f"Intake stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
