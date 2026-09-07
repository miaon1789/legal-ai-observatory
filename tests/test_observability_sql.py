"""Actual SQL Server integration tests in a disposable, isolated database.

RUN_SQL_TESTS=1 .venv/bin/python -m unittest discover -s tests -p 'test_observability_sql.py' -v
"""
from __future__ import annotations

import copy
import datetime as dt
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from observability.benchmark import load_benchmark
from observability.events import append_event, make_event, timestamp
from observability.review import record_review
from observability.runner import ReplayProvider, heartbeat, run_case
from observability.warehouse import Warehouse, WarehouseError


@unittest.skipUnless(os.environ.get("RUN_SQL_TESTS") == "1", "Opt in with RUN_SQL_TESTS=1; requires Docker")
class ObservabilitySQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = "LegalAITelemetryTest_" + uuid.uuid4().hex[:12]
        cls.admin = Warehouse("master")
        cls.admin.execute(f"CREATE DATABASE [{cls.database}];")
        cls.addClassCleanup(cls.cleanup_database)
        cls.db = Warehouse(cls.database)
        cls.db.deploy()
        cls.db.deploy()

    @classmethod
    def cleanup_database(cls):
        if not cls.database.startswith("LegalAITelemetryTest_"):
            raise RuntimeError("Refusing to drop a non-test database")
        cls.admin.execute(f"ALTER DATABASE [{cls.database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;\n"
                          f"DROP DATABASE [{cls.database}];")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "events.jsonl"
        docs, cases, self.version = load_benchmark()
        self.case = next(iter(cases.values()))
        self.document = docs[self.case["document_id"]]
        self.db.execute("DELETE telemetry.event_log; DELETE telemetry.ingestion_run; "
                        "DELETE telemetry.alert_state; DELETE telemetry.alert_transition;")

    def request(self, provider=None, **kwargs):
        return run_case(provider or ReplayProvider(), self.document, self.case, self.version,
                        "baseline-v1", self.log, experiment_id="sql-test", **kwargs)

    def review(self, event, decision=True, method="scripted_fixture"):
        return record_review(self.log, event["request_id"], reviewer_id="fixture", task_pass=decision,
                             citations_correct=True, major_rework=False, reason_code="test", method=method)

    @staticmethod
    def priced(event, cost=0.25, currency="USD"):
        for attempt in event["payload"]["attempts"]:
            attempt.update(input_tokens=100, output_tokens=20, cached_input_tokens=0,
                           estimated_cost=cost, currency=currency, rate_version="test-only")
        return event

    def test_idempotent_replay_and_conflicting_event_atomic_rollback(self):
        event = self.request()
        self.assertEqual(self.db.ingest([event, event])["inserted_count"], 1)
        self.assertEqual(self.db.ingest([event])["inserted_count"], 0)
        conflict = copy.deepcopy(event)
        conflict["payload"]["duration_ms"] += 1
        another = self.request()
        with self.assertRaises(WarehouseError):
            self.db.ingest([another, conflict])
        self.assertEqual(len(self.db.rows("requests")), 1)
        self.assertTrue(self.db.rows("ingestion")[0]["is_open"])
        self.db.ingest([event])
        health = self.db.rows("ingestion")[0]
        self.assertFalse(health["is_open"])
        self.assertIsNotNone(health["recovered_at"])

    def test_full_72_request_batch_survives_sqlcmd_transport(self):
        events = [self.request() for _ in range(72)]
        self.assertEqual(self.db.ingest(events)["inserted_count"], 72)
        self.assertEqual(self.db.ingest(events)["inserted_count"], 0)
        self.assertEqual(len(self.db.rows("requests")), 72)

    def test_out_of_order_request_and_review_revisions(self):
        event = self.request()
        older = self.review(event, False)
        newer = self.review(event, True)
        self.db.ingest([newer])
        self.db.ingest([event, older])
        row = self.db.rows("requests")[0]
        self.assertEqual(row["review_revision"], 2)
        self.assertTrue(row["human_task_pass"])
        revised = copy.deepcopy(event)
        revised["event_id"], revised["revision"] = str(uuid.uuid4()), 2
        revised["payload"]["answer_sha256"] = "0" * 64
        self.db.ingest([revised])
        row = self.db.rows("requests")[0]
        self.assertEqual(row["revision"], 2)
        self.assertIsNone(row["review_method"])
        self.db.ingest([event])
        self.assertEqual(self.db.rows("requests")[0]["revision"], 2)

    def test_new_id_same_logical_revision_rejected(self):
        event = self.request()
        other = copy.deepcopy(event)
        other["event_id"] = str(uuid.uuid4())
        with self.assertRaises(WarehouseError):
            self.db.ingest([event, other])
        self.assertEqual(self.db.rows("requests"), [])

    def test_request_provenance_cannot_change_via_review(self):
        event = self.request()
        review = self.review(event)
        review["source_id"] = "other-source"
        with self.assertRaises(WarehouseError):
            self.db.ingest([event, review])
        self.assertEqual(self.db.rows("requests"), [])

    def test_unknown_cost_stays_null_and_scripted_review_not_human(self):
        event = self.request()
        review = self.review(event)
        self.db.ingest([event, review])
        row = self.db.rows("comparison")[0]
        self.assertEqual(row["request_count"], 1)
        self.assertEqual(row["auto_passed"], 1)
        self.assertEqual(row["human_reviewed"], 0)
        self.assertIsNone(row["human_pass_rate_reviewed"])
        self.assertIsNone(row["estimated_cost"])
        self.assertIsNone(row["cost_per_auto_pass"])
        self.assertIsNone(row["cost_per_human_pass"])

    def test_failure_denominator_p95_and_cost_include_failed_attempts(self):
        success = self.priced(self.request())
        failed = self.priced(self.request(ReplayProvider(1)))
        success["payload"]["duration_ms"] = 100
        failed["payload"]["duration_ms"] = 1000
        self.db.ingest([success, failed])
        row = self.db.rows("comparison")[0]
        self.assertAlmostEqual(row["failure_rate"], 0.5)
        self.assertAlmostEqual(row["auto_pass_rate_all_requests"], 0.5)
        self.assertAlmostEqual(row["p95_request_ms"], 955)
        self.assertEqual(row["failed_attempts"], 1)
        self.assertEqual(row["estimated_cost"], 0.5)
        self.assertEqual(row["cost_per_auto_pass"], 0.5)

    def test_retry_unknown_usage_never_understates_total_cost(self):
        event = self.request(ReplayProvider(1), max_attempts=2, sleep=lambda _: None)
        self.priced(event)
        event["payload"]["attempts"][0].update(input_tokens=None, output_tokens=None,
            cached_input_tokens=None, estimated_cost=None, currency=None, rate_version=None)
        self.db.ingest([event])
        row = self.db.rows("requests")[0]
        self.assertEqual(row["retry_count"], 1)
        self.assertEqual(row["known_estimated_cost"], 0.25)
        self.assertIsNone(row["estimated_cost"])
        self.assertIsNone(row["input_tokens"])
        self.assertEqual(len(self.db.rows("attempts")), 2)

    def test_mixed_currencies_not_added(self):
        self.db.ingest([self.priced(self.request(), currency="USD"),
                        self.priced(self.request(), currency="AUD")])
        row = self.db.rows("comparison")[0]
        self.assertIsNone(row["estimated_cost"])
        self.assertIsNone(row["known_estimated_cost"])
        self.assertIsNone(row["currency"])

    def test_unreviewed_answers_do_not_become_human_failures(self):
        first, second = self.priced(self.request()), self.priced(self.request())
        review = self.review(first, True, method="human")
        self.db.ingest([first, second, review])
        row = self.db.rows("comparison")[0]
        self.assertEqual(row["human_review_coverage"], 0.5)
        self.assertEqual(row["human_pass_rate_reviewed"], 1)
        self.assertIsNone(row["cost_per_human_pass"])
        self.db.ingest([self.review(second, False, method="human")])
        row = self.db.rows("comparison")[0]
        self.assertEqual(row["cost_per_human_pass"], 0.5)

    def test_origins_and_run_settings_are_separate_groups(self):
        fixture = self.request()
        # Database-boundary fixture only, never a real API call or portfolio export.
        observed = copy.deepcopy(fixture)
        observed.update(event_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()),
                        trace_id=uuid.uuid4().hex, data_origin="observed")
        observed["payload"]["provider"] = "openai"
        self.db.ingest([fixture, observed, self.request(max_attempts=2)])
        self.assertEqual(len(self.db.rows("comparison")), 3)

    def test_alert_open_unchanged_recovery_and_monotonic_clock(self):
        base = dt.datetime.now(dt.timezone.utc)
        def at(minutes):
            return timestamp(base + dt.timedelta(minutes=minutes))
        self.db.ingest([heartbeat(self.log, "drill", "simulated", "fault_drill", at=at(0))])
        self.assertFalse(any(r["is_open"] for r in self.db.monitor("drill", "simulated", "fault_drill", as_at=at(0))))
        self.db.monitor("drill", "simulated", "fault_drill", as_at=at(11))
        self.db.monitor("drill", "simulated", "fault_drill", as_at=at(12))
        self.assertEqual(len(self.db.rows("transitions")), 1)
        self.db.ingest([heartbeat(self.log, "drill", "simulated", "fault_drill", at=at(13))])
        self.db.monitor("drill", "simulated", "fault_drill", as_at=at(13))
        states = [r["state"] for r in self.db.rows("transitions")]
        self.assertCountEqual(states, ["open", "resolved"])
        with self.assertRaises(WarehouseError):
            self.db.monitor("drill", "simulated", "fault_drill", as_at=at(10))

    def test_latest_request_failure_recovers_on_success(self):
        event = self.request(ReplayProvider(1), source_id="drill", run_kind="fault_drill")
        beat = heartbeat(self.log, "drill", "simulated", "fault_drill")
        self.db.ingest([event, beat])
        states = self.db.monitor("drill", "simulated", "fault_drill")
        self.assertTrue(next(r for r in states if r["rule_key"] == "latest_request_failed")["is_open"])
        self.db.ingest([self.request(source_id="drill", run_kind="fault_drill")])
        self.assertFalse(any(r["is_open"] for r in self.db.monitor("drill", "simulated", "fault_drill")))

    def test_invalid_or_torn_file_imports_nothing_then_recovers(self):
        self.request()
        with self.log.open("a") as stream:
            stream.write('{"unfinished":')
        with self.assertRaises(ValueError):
            self.db.ingest_file(self.log)
        self.assertEqual(self.db.rows("requests"), [])
        self.assertTrue(self.db.rows("ingestion")[0]["is_open"])
        # Repair only this test fixture's torn line.
        self.log.write_text(self.log.read_text().splitlines()[0] + "\n")
        self.assertEqual(self.db.ingest_file(self.log)["inserted_count"], 1)
        self.assertFalse(self.db.rows("ingestion")[0]["is_open"])


if __name__ == "__main__":
    unittest.main()
