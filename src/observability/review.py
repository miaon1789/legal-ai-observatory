"""Append feedback without mutating the request or earlier review decisions."""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
import uuid

from .events import append_event, digest, make_event, read_events


def record_review(log: Path, request_id: str, *, reviewer_id: str, task_pass: bool | None,
                  citations_correct: bool | None, major_rework: bool | None,
                  reason_code: str, original: dict | None = None, edited: dict | None = None,
                  method: str = "human") -> dict:
    # SQL Server renders UUIDs uppercase; local event IDs are canonical lowercase.
    request_id = str(uuid.UUID(request_id))
    events, deferred = read_events(log)
    if deferred:
        raise ValueError("Finish or repair the incomplete log line before recording feedback")
    requests = [e for e in events if e["request_id"] == request_id and e["event_type"] == "request.completed"]
    if not requests:
        raise ValueError("Request not found in this local log")
    request = max(requests, key=lambda e: e["revision"])
    if request["payload"]["status"] != "success":
        raise ValueError("Only a completed answer can be reviewed")
    ratio = None
    if (original is None) != (edited is None):
        raise ValueError("Supply both original and edited answers")
    if original is not None:
        from .benchmark import valid_answer

        if digest(original) != request["payload"]["answer_sha256"] or not valid_answer(edited):
            raise ValueError("Answer hash or edited answer schema does not match")
        # Text change proxy, not legal correctness or a literal fraction of tokens edited.
        ratio = 1 - SequenceMatcher(None, original["answer"] or "", edited["answer"] or "",
                                    autojunk=False).ratio()
    revision = 1 + max((e["revision"] for e in events if e["request_id"] == request_id
                       and e["event_type"] == "review.recorded"), default=0)
    payload = dict(reviewer_id=reviewer_id, review_method=method, task_pass=task_pass,
                   citations_correct=citations_correct, major_rework=major_rework,
                   edit_distance_ratio=ratio, reason_code=reason_code,
                   request_revision=request["revision"], answer_sha256=request["payload"]["answer_sha256"])
    event = make_event("review.recorded", payload, source_id=request["source_id"],
                       data_origin=request["data_origin"], run_kind=request["run_kind"],
                       request_id=request_id, trace_id=request["trace_id"], revision=revision)
    append_event(log, event)
    return event
