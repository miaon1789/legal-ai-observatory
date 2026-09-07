"""Versioned, append-only event envelopes. No prompt or response text in events."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import uuid
from pathlib import Path

UTC = dt.timezone.utc
KINDS = {"benchmark", "pilot", "fault_drill"}
ORIGINS = {"observed", "simulated"}
TYPES = {"request.completed", "review.recorded", "source.heartbeat"}


def timestamp(value: dt.datetime | None = None) -> str:
    value = value or dt.datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("Timestamp needs a timezone")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp needs a timezone")
    return result.astimezone(UTC)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value: object, field: str, limit: int = 100) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1," + str(limit) + r"}", value):
        raise ValueError(f"Invalid {field}; use a short non-sensitive identifier")


def number(value: object, field: str, *, integer: bool = False,
           nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid {field}")
    if not math.isfinite(value) or value < 0 or value > 2_000_000_000:
        raise ValueError(f"Out-of-range {field}")
    if integer and not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")


def boolean(value: object, field: str, nullable: bool = False) -> None:
    if type(value) is not bool and not (nullable and value is None):
        raise ValueError(f"{field} must be true/false" + ("/null" if nullable else ""))


def exact_keys(value: dict, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Unexpected or missing event fields")


def validate(event: dict) -> dict:
    exact_keys(event, {"schema_version", "event_id", "event_type", "source_id",
                       "data_origin", "run_kind", "request_id", "trace_id",
                       "revision", "occurred_at", "payload"})
    if type(event["schema_version"]) is not int or event["schema_version"] != 1:
        raise ValueError("Unsupported schema version")
    if event["event_type"] not in TYPES or event["data_origin"] not in ORIGINS or event["run_kind"] not in KINDS:
        raise ValueError("Invalid event type or provenance")
    identifier(event["source_id"], "source_id", 64)
    for key in ("event_id", "request_id"):
        if key == "request_id" and event["event_type"] == "source.heartbeat":
            if event[key] is not None:
                raise ValueError("Heartbeat cannot have a request ID")
        elif str(uuid.UUID(event[key])) != event[key]:
            raise ValueError(f"{key} must be a canonical UUID")
    if not isinstance(event["trace_id"], str) or not re.fullmatch("[0-9a-f]{32}", event["trace_id"]):
        raise ValueError("Invalid trace ID")
    number(event["revision"], "revision", integer=True)
    if event["revision"] < 1:
        raise ValueError("Revisions start at 1")
    occurred = parse_time(event["occurred_at"])
    p = event["payload"]
    if event["event_type"] == "source.heartbeat":
        exact_keys(p, {"status"})
        if p["status"] not in {"ok", "failed"}:
            raise ValueError("Invalid heartbeat status")
    elif event["event_type"] == "review.recorded":
        exact_keys(p, {"reviewer_id", "review_method", "task_pass", "citations_correct",
                       "major_rework", "edit_distance_ratio", "reason_code",
                       "request_revision", "answer_sha256"})
        identifier(p["reviewer_id"], "reviewer_id", 64)
        identifier(p["reason_code"], "reason_code", 64)
        number(p["request_revision"], "request_revision", integer=True)
        if p["request_revision"] < 1 or not re.fullmatch("[0-9a-f]{64}", p["answer_sha256"]):
            raise ValueError("Review must identify the evaluated answer and revision")
        if p["review_method"] not in {"human", "scripted_fixture"}:
            raise ValueError("Invalid review method")
        if p["review_method"] == "scripted_fixture" and event["data_origin"] != "simulated":
            raise ValueError("Scripted feedback is only permitted for simulated events")
        for key in ("task_pass", "citations_correct", "major_rework"):
            boolean(p[key], key, nullable=True)
        number(p["edit_distance_ratio"], "edit_distance_ratio", nullable=True)
        if p["edit_distance_ratio"] is not None and p["edit_distance_ratio"] > 1:
            raise ValueError("Edit distance must be between 0 and 1")
    else:
        exact_keys(p, {"experiment_id", "case_id", "case_version", "case_origin",
                       "config_id", "prompt_version", "run_config_version", "provider", "requested_model",
                       "response_model", "started_at", "finished_at", "duration_ms",
                       "status", "error_type", "attempts", "auto_pass", "answer_sha256"})
        for key in ("experiment_id", "case_id", "case_version", "config_id", "prompt_version", "run_config_version",
                    "provider", "requested_model"):
            identifier(p[key], key)
        if p["response_model"] is not None:
            identifier(p["response_model"], "response_model")
        if p["case_origin"] != "self_authored_synthetic":
            raise ValueError("This runner accepts only the bundled synthetic case set")
        if p["provider"] == "replay" and event["data_origin"] != "simulated":
            raise ValueError("Replay is never an observed application run")
        if p["provider"] == "openai" and event["data_origin"] != "observed":
            raise ValueError("Live provider calls require observed provenance")
        start, end = parse_time(p["started_at"]), parse_time(p["finished_at"])
        if end < start or occurred < end:
            raise ValueError("Invalid request timestamps")
        number(p["duration_ms"], "duration_ms")
        boolean(p["auto_pass"], "auto_pass", nullable=True)
        if p["answer_sha256"] is not None and not re.fullmatch("[0-9a-f]{64}", p["answer_sha256"]):
            raise ValueError("Invalid answer hash")
        attempts = p["attempts"]
        if not isinstance(attempts, list) or not 1 <= len(attempts) <= 5:
            raise ValueError("A completed request needs 1 to 5 attempts")
        for i, a in enumerate(attempts, 1):
            exact_keys(a, {"attempt_number", "duration_ms", "status", "error_type",
                           "provider_request_id", "input_tokens", "output_tokens",
                           "cached_input_tokens", "estimated_cost", "currency", "rate_version"})
            if type(a["attempt_number"]) is not int or a["attempt_number"] != i:
                raise ValueError("Attempt numbers must be consecutive")
            number(a["duration_ms"], "duration_ms")
            for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
                number(a[key], key, integer=True, nullable=True)
            if a["cached_input_tokens"] is not None and (a["input_tokens"] is None or a["cached_input_tokens"] > a["input_tokens"]):
                raise ValueError("Invalid cached token count")
            number(a["estimated_cost"], "estimated_cost", nullable=True)
            if a["estimated_cost"] is not None:
                if any(a[k] is None for k in ("input_tokens", "output_tokens", "cached_input_tokens")):
                    raise ValueError("Estimated cost requires complete usage")
                if not isinstance(a["currency"], str) or not re.fullmatch("[A-Z]{3}", a["currency"]):
                    raise ValueError("Estimated cost requires a currency")
                identifier(a["rate_version"], "rate_version")
            elif a["currency"] is not None or a["rate_version"] is not None:
                raise ValueError("Unknown cost must not imply a price basis")
            if a["provider_request_id"] is not None:
                identifier(a["provider_request_id"], "provider_request_id", 200)
            if a["status"] not in {"success", "failed"}:
                raise ValueError("Invalid attempt status")
            if (a["status"] == "success") != (a["error_type"] is None):
                raise ValueError("Attempt status/error mismatch")
            if a["error_type"] is not None:
                identifier(a["error_type"], "error_type", 64)
            if i < len(attempts) and a["status"] != "failed":
                raise ValueError("A successful attempt cannot be retried")
        if p["status"] != attempts[-1]["status"] or p["error_type"] != attempts[-1]["error_type"]:
            raise ValueError("Request outcome must match its final attempt")
        if p["status"] == "failed" and (p["auto_pass"] is not None or p["answer_sha256"] is not None):
            raise ValueError("Failed requests have no completed answer or quality grade")
        if p["status"] == "success" and (p["auto_pass"] is None or p["answer_sha256"] is None):
            raise ValueError("Successful requests require an answer hash and automatic grade")
    if len(canonical(event).encode()) > 32000:
        raise ValueError("Event exceeds 32 KB metadata limit")
    return event


def make_event(event_type: str, payload: dict, *, source_id: str = "contract-qa",
               data_origin: str, run_kind: str, request_id: str | None = None,
               trace_id: str | None = None, revision: int = 1,
               occurred_at: str | None = None) -> dict:
    return validate(dict(schema_version=1, event_id=str(uuid.uuid4()), event_type=event_type,
                         source_id=source_id, data_origin=data_origin, run_kind=run_kind,
                         request_id=request_id, trace_id=trace_id or uuid.uuid4().hex,
                         revision=revision, occurred_at=occurred_at or timestamp(), payload=payload))


def append_event(path: Path, event: dict) -> None:
    import fcntl

    validate(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(canonical(event) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_events(path: Path) -> tuple[list[dict], bool]:
    """Replay complete lines; a concurrently written trailing line is deferred."""
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Rotate the event log before it exceeds the 20 MiB pilot limit")
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    deferred = bool(lines and not lines[-1].endswith(b"\n"))
    if deferred:
        lines.pop()
    result = []
    for i, line in enumerate(lines, 1):
        try:
            result.append(validate(json.loads(line)))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"Invalid event at line {i}; no events from this file imported") from exc
    return result, deferred
