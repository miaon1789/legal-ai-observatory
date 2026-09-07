"""Opt-in live requests and clearly labelled offline fixtures."""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .benchmark import ANSWER_SCHEMA, PROMPTS, grade, valid_answer
from .events import append_event, canonical, digest, identifier, make_event, parse_time, timestamp


@dataclass
class Reply:
    answer: dict | None
    model: str | None = None
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    error_type: str | None = None
    retryable: bool = False


class OpenAIProvider:
    name = "openai"
    data_origin = "observed"

    def __init__(self, model: str, timeout: float = 45, max_output_tokens: int = 1200):
        identifier(model, "model")
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("Set OPENAI_API_KEY locally; never put it in a command argument")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ValueError("Install requirements-observability.txt for live calls") from exc
        # Disable hidden SDK retries so every attempted call is accounted for.
        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0,
                             timeout=timeout, base_url="https://api.openai.com/v1")
        self.model, self.max_output_tokens, self.timeout = model, max_output_tokens, timeout

    def respond(self, document: dict, case: dict, config_id: str) -> Reply:
        try:
            response = self.client.responses.create(
                model=self.model, instructions=PROMPTS[config_id],
                input=canonical({"document": document, "question": case["question"]}),
                text={"format": {"type": "json_schema", "name": "contract_answer",
                                 "schema": ANSWER_SCHEMA, "strict": True}},
                max_output_tokens=self.max_output_tokens, store=False,
            )
        except Exception as exc:
            # Provider error bodies can echo request content. Keep only a category.
            code = type(exc).__name__
            retryable = code in {"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError"}
            return Reply(None, request_id=getattr(exc, "request_id", None), error_type=code, retryable=retryable)
        usage = response.usage
        details = getattr(usage, "input_tokens_details", None)
        result = Reply(None, model=response.model,
                       request_id=getattr(response, "_request_id", None),
                       input_tokens=getattr(usage, "input_tokens", None),
                       output_tokens=getattr(usage, "output_tokens", None),
                       cached_input_tokens=getattr(details, "cached_tokens", None))
        if response.status != "completed":
            result.error_type = "IncompleteResponse"
        else:
            try:
                result.answer = json.loads(response.output_text)
                if not valid_answer(result.answer):
                    raise ValueError("Invalid schema")
            except (ValueError, TypeError):
                result.answer, result.error_type = None, "InvalidStructuredOutput"
        return result


class ReplayProvider:
    """An oracle fixture, NOT a local AI model or evidence of prompt quality."""
    name = "replay"
    data_origin = "simulated"
    model = "fixture-oracle-v1"

    def __init__(self, failures: int = 0):
        self.failures = failures

    def respond(self, document: dict, case: dict, config_id: str) -> Reply:
        if self.failures:
            self.failures -= 1
            return Reply(None, error_type="ControlledTimeout", retryable=True)
        clauses = {c["id"]: c["text"] for c in document["clauses"]}
        answer = {"answer": case["answer"], "clause_id": case["clause_id"],
                  "quote": clauses.get(case["clause_id"]), "abstain": case["answer"] is None}
        return Reply(answer, model=self.model)


def load_rates(path: Path | None) -> dict | None:
    if path is None:
        return None
    rates = json.loads(path.read_text())
    fields = {"version", "model", "currency", "effective_from", "input_per_million",
              "cached_input_per_million", "output_per_million"}
    if not isinstance(rates, dict) or set(rates) != fields:
        raise ValueError("Rate card has missing or unexpected fields")
    for field in ("version", "model"):
        identifier(rates[field], field)
    if (not isinstance(rates["currency"], str) or len(rates["currency"]) != 3
            or not rates["currency"].isascii() or not rates["currency"].isupper()
            or not rates["currency"].isalpha()):
        raise ValueError("Use an ISO currency code")
    if not isinstance(rates["effective_from"], str):
        raise ValueError("Rate effective_from must be a timezone-aware timestamp")
    parse_time(rates["effective_from"])
    for key in ("input_per_million", "cached_input_per_million", "output_per_million"):
        try:
            value = Decimal(str(rates[key]))
        except InvalidOperation as exc:
            raise ValueError("Rates must be finite, nonnegative numbers") from exc
        if not value.is_finite() or value < 0:
            raise ValueError("Rates must be finite, nonnegative numbers")
        rates[key] = value
    return rates


def price(reply: Reply, rates: dict | None, started_at: str) -> tuple[float | None, str | None, str | None]:
    if (rates is None or reply.model != rates["model"]
            or parse_time(started_at) < parse_time(rates["effective_from"])
            or any(v is None for v in (reply.input_tokens, reply.output_tokens, reply.cached_input_tokens))):
        return None, None, None
    if not 0 <= reply.cached_input_tokens <= reply.input_tokens:
        raise ValueError("Provider returned inconsistent usage")
    cost = ((reply.input_tokens - reply.cached_input_tokens) * rates["input_per_million"]
            + reply.cached_input_tokens * rates["cached_input_per_million"]
            + reply.output_tokens * rates["output_per_million"]) / Decimal(1_000_000)
    return float(cost.quantize(Decimal("0.000000000001"))), rates["currency"], rates["version"]


def heartbeat(log: Path, source: str, origin: str, kind: str, *, at: str | None = None,
              status: str = "ok") -> dict:
    event = make_event("source.heartbeat", {"status": status}, source_id=source,
                       data_origin=origin, run_kind=kind, occurred_at=at)
    append_event(log, event)
    return event


def run_case(provider, document: dict, case: dict, case_version: str, config_id: str,
             log: Path, *, experiment_id: str, source_id: str = "contract-qa",
             run_kind: str = "benchmark", max_attempts: int = 1, rates: dict | None = None,
             content_dir: Path | None = None, sleep=time.sleep,
             clock=lambda: dt.datetime.now(dt.timezone.utc), timer=time.perf_counter) -> dict:
    if config_id not in PROMPTS or not 1 <= max_attempts <= 5:
        raise ValueError("Invalid configuration or retry limit")
    identifier(experiment_id, "experiment_id")
    identifier(source_id, "source_id", 64)
    start, tick = timestamp(clock()), timer()
    request_id = str(uuid.uuid4())
    attempts = []
    reply = None
    for attempt in range(1, max_attempts + 1):
        attempt_tick = timer()
        reply = provider.respond(document, case, config_id)
        cost, currency, rate_version = price(reply, rates, start)
        attempts.append(dict(attempt_number=attempt, duration_ms=round((timer() - attempt_tick) * 1000, 3),
                             status="failed" if reply.error_type else "success", error_type=reply.error_type,
                             provider_request_id=reply.request_id, input_tokens=reply.input_tokens,
                             output_tokens=reply.output_tokens, cached_input_tokens=reply.cached_input_tokens,
                             estimated_cost=cost, currency=currency, rate_version=rate_version))
        if not reply.error_type or not reply.retryable or attempt == max_attempts:
            break
        sleep(min(2 ** (attempt - 1), 8))
    end = timestamp(clock())
    payload = dict(experiment_id=experiment_id, case_id=case["case_id"], case_version=case_version,
                   case_origin="self_authored_synthetic", config_id=config_id,
                   prompt_version=digest({"instructions": PROMPTS[config_id], "schema": ANSWER_SCHEMA}),
                   run_config_version=digest({"adapter_version": 1, "max_attempts": max_attempts,
                                              "timeout": getattr(provider, "timeout", None),
                                              "max_output_tokens": getattr(provider, "max_output_tokens", None)}),
                   provider=provider.name, requested_model=provider.model, response_model=reply.model,
                   started_at=start, finished_at=end, duration_ms=round((timer() - tick) * 1000, 3),
                   status=attempts[-1]["status"], error_type=reply.error_type, attempts=attempts,
                   auto_pass=grade(reply.answer, case, document) if not reply.error_type else None,
                   answer_sha256=digest(reply.answer) if not reply.error_type else None)
    event = make_event("request.completed", payload, source_id=source_id, data_origin=provider.data_origin,
                       run_kind=run_kind, request_id=request_id, occurred_at=end)
    append_event(log, event)
    if content_dir is not None and reply.answer is not None:
        content_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(content_dir / f"{request_id}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(reply.answer, stream, indent=2)
    return event
