"""Offline contract/telemetry tests. No credentials, network or database needed."""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from observability.benchmark import PROMPTS, grade, load_benchmark
from observability.events import append_event, canonical, digest, read_events, validate
from observability.review import record_review
from observability.runner import OpenAIProvider, ReplayProvider, Reply, load_rates, price, run_case
from observed_runs import comparison_checks, main


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.log = self.directory / "events.jsonl"
        self.docs, self.cases, self.version = load_benchmark()
        self.case = next(iter(self.cases.values()))
        self.document = self.docs[self.case["document_id"]]

    def request(self, provider=None, **kwargs):
        return run_case(provider or ReplayProvider(), self.document, self.case, self.version,
                        "baseline-v1", self.log, experiment_id="test", **kwargs)

    def test_36_cases_two_configs_all_fixtures_pass(self):
        self.assertEqual(len(self.cases), 36)
        self.assertEqual(len(self.docs), 6)
        for case in self.cases.values():
            doc = self.docs[case["document_id"]]
            for config in PROMPTS:
                self.assertTrue(grade(ReplayProvider().respond(doc, case, config).answer, case, doc))
        self.assertEqual(sum(c["answer"] is None for c in self.cases.values()), 6)

    def test_wrong_answer_or_citation_or_abstention_fails(self):
        answer = ReplayProvider().respond(self.document, self.case, "baseline-v1").answer
        for field, value in (("answer", "999 days"), ("quote", "invented"),
                             ("clause_id", "missing"), ("abstain", True), ("quote", "")):
            with self.subTest(field=field, value=value):
                altered = dict(answer, **{field: value})
                self.assertFalse(grade(altered, self.case, self.document))

    def test_fixture_is_simulated_and_usage_unknown(self):
        event = self.request()
        self.assertEqual(event["data_origin"], "simulated")
        a = event["payload"]["attempts"][0]
        for key in ("input_tokens", "output_tokens", "estimated_cost", "currency"):
            self.assertIsNone(a[key])
        self.assertNotIn("quote", canonical(event))
        self.assertEqual(self.log.stat().st_mode & 0o777, 0o600)

    def test_retries_visible_and_bounded(self):
        sleep = Mock()
        event = self.request(ReplayProvider(1), max_attempts=2, sleep=sleep)
        self.assertEqual([a["status"] for a in event["payload"]["attempts"]], ["failed", "success"])
        sleep.assert_called_once_with(1)
        event = self.request(ReplayProvider(10), max_attempts=2, sleep=Mock())
        self.assertEqual(event["payload"]["status"], "failed")
        self.assertIsNone(event["payload"]["auto_pass"])
        self.assertIsNone(event["payload"]["answer_sha256"])

    def test_validation_rejects_pii_field_and_invalid_provenance(self):
        event = self.request()
        bad = copy.deepcopy(event)
        bad["payload"]["answer_text"] = "private"
        with self.assertRaises(ValueError):
            validate(bad)
        bad = copy.deepcopy(event)
        bad["data_origin"] = "observed"
        with self.assertRaises(ValueError):
            validate(bad)

    def test_bad_usage_timestamps_nan_and_false_success_rejected(self):
        event = self.request()
        bad_values = (("duration_ms", float("nan")), ("auto_pass", 1),
                      ("answer_sha256", None), ("started_at", "2099-01-01T00:00:00Z"))
        for field, value in bad_values:
            with self.subTest(field=field):
                bad = copy.deepcopy(event)
                bad["payload"][field] = value
                with self.assertRaises(ValueError):
                    validate(bad)
        bad = copy.deepcopy(event)
        bad["payload"]["attempts"][0]["cached_input_tokens"] = 1
        with self.assertRaises(ValueError):
            validate(bad)

    def test_complete_lines_and_torn_tail(self):
        event = self.request()
        with self.log.open("a") as stream:
            stream.write('{"incomplete":')
        events, deferred = read_events(self.log)
        self.assertEqual(events, [event])
        self.assertTrue(deferred)
        with self.log.open("a") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ValueError, "line 2"):
            read_events(self.log)

    def test_review_revisions_and_answer_hash(self):
        event = self.request(content_dir=self.directory / "responses")
        original = json.loads((self.directory / "responses" / f'{event["request_id"]}.json').read_text())
        options = dict(reviewer_id="tester", task_pass=False, citations_correct=True,
                       major_rework=True, reason_code="wrong-answer", method="scripted_fixture")
        r1 = record_review(self.log, event["request_id"], **options)
        edited = dict(original, answer="corrected answer")
        r2 = record_review(self.log, event["request_id"], **options, original=original, edited=edited)
        self.assertEqual((r1["revision"], r2["revision"]), (1, 2))
        self.assertGreater(r2["payload"]["edit_distance_ratio"], 0)
        self.assertEqual(r2["payload"]["answer_sha256"], digest(original))
        with self.assertRaises(ValueError):
            record_review(self.log, event["request_id"], **options, original=edited, edited=edited)

    def test_failed_request_not_reviewable(self):
        event = self.request(ReplayProvider(1))
        with self.assertRaises(ValueError):
            record_review(self.log, event["request_id"], reviewer_id="tester", task_pass=False,
                          citations_correct=None, major_rework=None, reason_code="timeout")

    def test_uppercase_request_id_copied_from_sql_can_be_reviewed(self):
        event = self.request()
        review = record_review(self.log, event["request_id"].upper(), reviewer_id="fixture",
                               task_pass=True, citations_correct=True, major_rework=False,
                               reason_code="fixture", method="scripted_fixture")
        self.assertEqual(review["request_id"], event["request_id"])

    def test_invalid_rate_cards_fail_with_safe_validation_errors(self):
        path = self.directory / "rates.json"
        base = dict(version="test-only", model="test-model", currency="USD",
                    effective_from="2026-01-01T00:00:00Z", input_per_million=1,
                    cached_input_per_million=0.5, output_per_million=2)
        for bad in ({}, dict(base, input_per_million=None), dict(base, currency=123),
                    dict(base, output_per_million="NaN"), dict(base, effective_from=None)):
            path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                load_rates(path)

    def test_cost_exact_model_effective_date_and_unknown_usage(self):
        rates = dict(model="exact-model", version="test-only", currency="USD",
                     effective_from="2026-01-01T00:00:00Z", input_per_million=Decimal(2),
                     cached_input_per_million=Decimal(1), output_per_million=Decimal(4))
        reply = Reply({}, model="exact-model", input_tokens=100, output_tokens=20, cached_input_tokens=30)
        at = "2026-09-07T00:00:00Z"
        self.assertEqual(price(reply, rates, at), (0.00025, "USD", "test-only"))
        self.assertEqual(price(reply, rates, "2025-01-01T00:00:00Z"), (None, None, None))
        reply.model = "other-model"
        self.assertEqual(price(reply, rates, at), (None, None, None))
        reply.model, reply.cached_input_tokens = "exact-model", None
        self.assertEqual(price(reply, rates, at), (None, None, None))

    def test_live_adapter_structured_output_and_no_expected_answer_leak(self):
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.client = Mock()
        provider.model, provider.max_output_tokens = "test-model", 1200
        answer = ReplayProvider().respond(self.document, self.case, "baseline-v1").answer
        response = SimpleNamespace(status="completed", output_text=json.dumps(answer), model="test-model",
                                   _request_id="req_test", usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                   input_tokens_details=SimpleNamespace(cached_tokens=0)))
        provider.client.responses.create.return_value = response
        reply = provider.respond(self.document, self.case, "baseline-v1")
        kwargs = provider.client.responses.create.call_args.kwargs
        self.assertFalse(kwargs["store"])
        self.assertTrue(kwargs["text"]["format"]["strict"])
        self.assertEqual(set(json.loads(kwargs["input"])), {"document", "question"})
        self.assertEqual(reply.input_tokens, 10)
        response.output_text = "not JSON"
        self.assertEqual(provider.respond(self.document, self.case, "baseline-v1").error_type,
                         "InvalidStructuredOutput")
        response.status = "incomplete"
        self.assertEqual(provider.respond(self.document, self.case, "baseline-v1").error_type,
                         "IncompleteResponse")

    def test_live_adapter_error_body_not_logged(self):
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.client, provider.model, provider.max_output_tokens = Mock(), "test-model", 1200
        timeout_error = type("APITimeoutError", (Exception,), {})
        provider.client.responses.create.side_effect = timeout_error("secret response text")
        reply = provider.respond(self.document, self.case, "baseline-v1")
        self.assertTrue(reply.retryable)
        self.assertEqual(reply.error_type, "APITimeoutError")
        self.assertNotIn("secret", repr(reply))

    def test_real_sdk_with_local_http_transport(self):
        try:
            import httpx
            from openai import OpenAI
        except ImportError:
            self.skipTest("Install optional requirements-observability.txt for SDK transport test")
        answer = ReplayProvider().respond(self.document, self.case, "baseline-v1").answer
        requests = []
        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, headers={"x-request-id": "req_mock_only"}, json={
                "id": "resp_mock_only", "object": "response", "created_at": 1700000000,
                "status": "completed", "model": "mock-model", "error": None,
                "output": [{"id": "msg_mock_only", "type": "message", "role": "assistant",
                            "status": "completed", "content": [{"type": "output_text",
                            "text": json.dumps(answer), "annotations": []}]}],
                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                          "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 0}},
            })
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model, provider.max_output_tokens = "mock-model", 1200
        with OpenAI(api_key="test-only-not-a-key", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
            provider.client = client
            reply = provider.respond(self.document, self.case, "grounded-v2")
        self.assertEqual(len(requests), 1)
        self.assertEqual(reply.request_id, "req_mock_only")
        self.assertEqual(reply.input_tokens, 100)
        self.assertEqual(reply.cached_input_tokens, 0)
        self.assertTrue(grade(reply.answer, self.case, self.document))
        self.assertEqual(requests[0]["model"], "mock-model")

    def test_cli_live_and_request_caps_guard_before_calls(self):
        with patch("observed_runs.OpenAIProvider") as provider, patch("sys.stderr"):
            self.assertEqual(main(["--directory", str(self.directory), "run", "--provider", "openai"]), 1)
            self.assertEqual(main(["--directory", str(self.directory), "run", "--provider", "openai",
                                   "--allow-live", "--model", "test", "--case-limit", "36"]), 1)
            provider.assert_not_called()

    def test_comparison_refuses_unequal_case_coverage(self):
        event = self.request()
        row = dict(event["payload"], source_id="test", data_origin="simulated", run_kind="benchmark")
        row["config_id"] = "baseline-v1"
        second = dict(row, config_id="grounded-v2")
        self.assertTrue(comparison_checks([row, second])[0]["descriptive_comparison_ready"])
        self.assertFalse(comparison_checks([row, row, second])[0]["descriptive_comparison_ready"])
        self.assertFalse(comparison_checks([row, second])[0]["live_evidence"])


if __name__ == "__main__":
    unittest.main()
