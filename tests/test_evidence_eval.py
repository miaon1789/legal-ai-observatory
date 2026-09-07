"""Offline evidence metric and CLI tests; invented text only, no provider or SQL."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import random
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cuad_intake as local_files
import evidence_eval as cli
from observability.events import digest
from observability.evidence import evaluate, gold_intervals, grade, overlap, union, validate_benchmark


def span(text, quote, start=None):
    start = text.index(quote) if start is None else start
    return {"start": start, "end": start + len(quote), "quote": quote}


def answer(text, quote, start=None):
    return {"answer_start": text.index(quote) if start is None else start, "text": quote}


def response(*spans, abstain=False):
    return {"status": "ok", "abstain": abstain, "spans": list(spans)}


class EvidenceMetricTests(unittest.TestCase):
    def setUp(self):
        self.pack = cli.read_json(cli.BENCHMARK)
        self.oracle = cli.scripted_predictions(self.pack, "oracle_replay")

    def test_full_multi_span_evidence(self):
        text = "Alpha then Beta."
        result = grade(text, [answer(text, "Alpha"), answer(text, "Beta")], False,
                       response(span(text, "Alpha"), span(text, "Beta")))
        self.assertEqual(result["evidence_f1"], 1)
        self.assertTrue(result["exact_evidence_union"])

    def test_missing_required_span_is_partial_not_full_credit(self):
        text = "AAAA then BBBB."
        result = grade(text, [answer(text, "AAAA"), answer(text, "BBBB")], False,
                       response(span(text, "AAAA")))
        self.assertEqual(result["evidence_precision"], 1)
        self.assertEqual(result["evidence_recall"], 0.5)
        self.assertAlmostEqual(result["evidence_f1"], 2 / 3)
        self.assertFalse(result["all_gold_covered"])

    def test_whole_document_does_not_receive_perfect_evidence_score(self):
        text = "Noise. Due in 30 days. More noise."
        result = grade(text, [answer(text, "30 days")], False, response(span(text, text)))
        self.assertEqual(result["evidence_recall"], 1)
        self.assertLess(result["evidence_precision"], 1)
        self.assertFalse(result["exact_evidence_union"])

    def test_duplicates_and_overlaps_do_not_inflate_counts(self):
        text = "Northbridge Labs"
        refs = [answer(text, "Northbridge Labs"), answer(text, "Northbridge"), answer(text, "Northbridge Labs")]
        result = grade(text, refs, False, response(span(text, text), span(text, "Northbridge"), span(text, text)))
        self.assertEqual(result["evidence_f1"], 1)
        self.assertTrue(result["exact_evidence_union"])

    def test_touching_segments_have_the_same_union(self):
        text = "ABCD"
        result = grade(text, [answer(text, text)], False, response(span(text, "AB"), span(text, "CD")))
        self.assertTrue(result["exact_evidence_union"])

    def test_same_quote_at_wrong_occurrence_gets_no_overlap_credit(self):
        text = "Draft: 30 days. Operative: 30 days."
        refs = [answer(text, "30 days", text.rindex("30 days"))]
        result = grade(text, refs, False, response(span(text, "30 days")))
        self.assertEqual(result["response_state"], "valid")
        self.assertEqual(result["outcome"], "off_target_evidence")
        self.assertEqual(result["evidence_f1"], 0)

    def test_quotes_must_match_whitespace_and_case_exactly(self):
        text = "Net  30 Days"
        refs = [answer(text, text)]
        for quote in ("Net 30 Days", "Net  30 days", "invented answer"):
            result = grade(text, refs, False, response({"start": 0, "end": len(text), "quote": quote}))
            self.assertEqual(result["response_state"], "invalid")
            self.assertEqual(result["evidence_f1"], 0)

    def test_unicode_offsets_are_codepoints_not_utf16_or_bytes(self):
        text = "\U0001d11e fee: EUR 90"
        correct = span(text, "EUR 90")
        self.assertEqual(grade(text, [answer(text, "EUR 90")], False, response(correct))["evidence_f1"], 1)
        shifted = correct | {"start": correct["start"] + 1, "end": correct["end"] + 1}
        self.assertEqual(grade(text, [answer(text, "EUR 90")], False, response(shifted))["response_state"], "invalid")

    def test_one_hallucinated_span_invalidates_the_response(self):
        text = "Pay in 30 days."
        result = grade(text, [answer(text, "30 days")], False,
                       response(span(text, "30 days"), {"start": 0, "end": 3, "quote": "BAD"}))
        self.assertEqual(result["response_state"], "invalid")
        self.assertEqual(result["evidence_f1"], 0)

    def test_correct_abstention_has_no_evidence_f1(self):
        result = grade("No renewal specified.", [], True, response(abstain=True))
        self.assertTrue(result["correct_abstention"])
        self.assertIsNone(result["evidence_f1"])
        self.assertIsNone(result["exact_evidence_union"])

    def test_false_answer_and_incorrect_abstention_are_separate(self):
        text = "A term."
        false_answer = grade(text, [], True, response(span(text, "term")))
        refused = grade(text, [answer(text, "term")], False, response(abstain=True))
        self.assertTrue(false_answer["false_answer"])
        self.assertFalse(false_answer["correct_abstention"])
        self.assertTrue(refused["incorrect_abstention"])
        self.assertEqual(refused["evidence_f1"], 0)

    def test_bad_response_shapes_and_offsets_are_failures(self):
        text = "Term"
        valid = span(text, text)
        malformed = [[], {}, response(), response(valid, abstain=True), response(valid) | {"answer": "Term"},
                     response(valid) | {"abstain": 1}, response(valid) | {"spans": "Term"},
                     {"status": "error", "abstain": True, "spans": []}]
        malformed += [response(valid | values) for values in
                      ({"start": True}, {"start": -1}, {"end": 100}, {"end": 0}, {"end": 4.0}, {"quote": None})]
        for prediction in malformed:
            with self.subTest(prediction=prediction):
                result = grade(text, [answer(text, text)], False, prediction)
                self.assertEqual(result["response_state"], "invalid")
                self.assertEqual(result["evidence_f1"], 0)

    def test_failed_and_missing_responses_never_count_as_abstention(self):
        for prediction, state in ((None, "missing"), ({"status": "error", "abstain": None, "spans": []}, "error")):
            result = grade("Term", [], True, prediction)
            self.assertEqual(result["response_state"], state)
            self.assertFalse(result["correct_abstention"])

    def test_bad_gold_annotations_stop_scoring(self):
        for refs, impossible in (([], False), ([answer("Term", "Term")], True),
                                 ([{"answer_start": True, "text": "Term"}], False),
                                 ([{"answer_start": 0, "text": "wrong"}], False)):
            with self.assertRaises(ValueError):
                gold_intervals("Term", refs, impossible)

    def test_interval_algorithm_matches_independent_position_sets(self):
        rng = random.Random(20260907)
        for _ in range(500):
            groups = [[(start, rng.randint(start + 1, 50)) for start in
                       [rng.randrange(0, 45) for _ in range(rng.randrange(0, 12))]] for _ in range(2)]
            sets = [{position for start, end in group for position in range(start, end)} for group in groups]
            merged = [union(g) for g in groups]
            self.assertEqual(overlap(*merged), len(sets[0] & sets[1]))
            self.assertEqual(sum(end - start for start, end in merged[0]), len(sets[0]))

    def test_fixture_shape_and_oracle_is_only_a_scripted_check(self):
        docs, cases = validate_benchmark(self.pack)
        self.assertEqual((len(docs), len(cases)), (8, 12))
        report = evaluate(self.pack, self.oracle)
        self.assertFalse(report["is_model_evaluation"])
        self.assertEqual(report["prediction_origin"], "scripted_fixture")
        self.assertEqual(report["summary"]["answerable"]["count"], 8)
        self.assertEqual(report["summary"]["unanswerable"]["count"], 4)
        self.assertEqual(report["summary"]["answerable"]["evidence_f1_macro"], 1)
        self.assertEqual(report["summary"]["unanswerable"]["correct_abstention_rate"], 1)

    def test_all_abstain_cannot_inflate_evidence_scores(self):
        report = evaluate(self.pack, cli.scripted_predictions(self.pack, "all_abstain"))
        self.assertEqual(report["summary"]["answerable"]["evidence_f1_macro"], 0)
        self.assertEqual(report["summary"]["answerable"]["incorrect_abstention_rate"], 1)
        self.assertEqual(report["summary"]["unanswerable"]["correct_abstention_rate"], 1)

    def test_missing_half_keeps_original_denominators(self):
        report = evaluate(self.pack, cli.scripted_predictions(self.pack, "missing_half"))
        summary = report["summary"]
        self.assertEqual(summary["coverage"]["missing"], 6)
        self.assertEqual(summary["coverage"]["expected"], 12)
        self.assertEqual(summary["answerable"]["evidence_f1_macro"], 0.5)
        self.assertEqual(summary["unanswerable"]["correct_abstention_rate"], 0.5)

    def test_scripted_errors_and_invalid_offsets_remain_visible(self):
        report = evaluate(self.pack, cli.scripted_predictions(self.pack, "scripted_errors"))
        self.assertEqual(report["summary"]["coverage"]["error"], 12)
        self.assertEqual(report["summary"]["unanswerable"]["correct_abstention_rate"], 0)
        report = evaluate(self.pack, cli.scripted_predictions(self.pack, "invalid_offsets"))
        self.assertEqual(report["summary"]["coverage"]["invalid"], 8)
        self.assertEqual(report["summary"]["answerable"]["evidence_f1_macro"], 0)

    def test_unknown_duplicate_or_unidentified_predictions_stop_the_run(self):
        for kind in ("unknown", "duplicate", "missing_id"):
            predictions = copy.deepcopy(self.oracle)
            if kind == "unknown":
                predictions["predictions"][0]["case_id"] = "not-in-benchmark"
            elif kind == "duplicate":
                predictions["predictions"][1] = copy.deepcopy(predictions["predictions"][0])
            else:
                predictions["predictions"][0].pop("case_id")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                evaluate(self.pack, predictions)

    def test_wrong_document_or_extra_prediction_fields_fail_the_case(self):
        for update in ({"document_id": "wrong-document"}, {"extra": "not allowed"}):
            predictions = copy.deepcopy(self.oracle)
            predictions["predictions"][0].update(update)
            report = evaluate(self.pack, predictions)
            self.assertEqual(report["summary"]["coverage"]["invalid"], 1)
            self.assertEqual(report["cases"][0]["evidence_f1"], 0)

    def test_stale_hash_or_real_execution_labels_are_rejected(self):
        for update in ({"benchmark_sha256": "0" * 64}, {"prediction_origin": "observed"}):
            with self.assertRaises(ValueError):
                evaluate(self.pack, self.oracle | update)
        for update in ({"case_origin": "public_real"}, {"offset_unit": "utf16"},
                       {"format": "cuad-local-preparation-v1"}):
            with self.assertRaises(ValueError):
                validate_benchmark(self.pack | update)

    def test_invalid_benchmark_membership_and_gold_are_rejected(self):
        for kind in ("duplicate_document", "duplicate_case", "missing_document", "bad_gold", "empty"):
            pack = copy.deepcopy(self.pack)
            if kind == "duplicate_document":
                pack["documents"].append(copy.deepcopy(pack["documents"][0]))
            elif kind == "duplicate_case":
                pack["cases"].append(copy.deepcopy(pack["cases"][0]))
            elif kind == "missing_document":
                pack["cases"][0]["document_id"] = "absent"
            elif kind == "bad_gold":
                pack["cases"][0]["answers"][0]["answer_start"] += 1
            else:
                pack["cases"] = []
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                validate_benchmark(pack)

    def test_absent_groups_are_null_not_perfect(self):
        for impossible in (True, False):
            pack = copy.deepcopy(self.pack)
            pack["cases"] = [c for c in pack["cases"] if c["is_impossible"] == impossible]
            report = evaluate(pack, cli.scripted_predictions(pack, "oracle_replay"))
            if impossible:
                self.assertIsNone(report["summary"]["answerable"]["evidence_f1_macro"])
            else:
                self.assertIsNone(report["summary"]["unanswerable"]["correct_abstention_rate"])

    def test_empty_prediction_list_is_total_nonresponse_not_success(self):
        report = evaluate(self.pack, self.oracle | {"predictions": []})
        self.assertEqual(report["summary"]["coverage"]["missing"], 12)
        self.assertEqual(report["summary"]["answerable"]["evidence_f1_macro"], 0)
        self.assertEqual(report["summary"]["unanswerable"]["correct_abstention_rate"], 0)

    def test_prediction_order_does_not_change_scores(self):
        original = evaluate(self.pack, self.oracle)
        changed = evaluate(self.pack, self.oracle | {"predictions": list(reversed(self.oracle["predictions"]))})
        for key in ("summary", "cases", "by_category"):
            self.assertEqual(original[key], changed[key])

    def test_summary_uses_case_macro_means_and_omits_raw_text(self):
        report = evaluate(self.pack, cli.scripted_predictions(self.pack, "first_gold_span"))
        positive = [r for r in report["cases"] if not r["is_impossible"]]
        expected = sum(r["evidence_f1"] for r in positive) / 8
        self.assertEqual(report["summary"]["answerable"]["evidence_f1_macro"], expected)
        self.assertNotIn('"quote":', json.dumps(report))
        self.assertNotIn('"answers":', json.dumps(report))
        self.assertEqual(report["benchmark_sha256"], digest(self.pack))


class EvidenceCliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        patcher = patch.object(local_files, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        network = patch.object(local_files.urllib.request, "urlopen", side_effect=AssertionError("Network forbidden"))
        self.network = network.start()
        self.addCleanup(network.stop)
        self.output = self.root / "private/evidence_evaluation"

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(list(args))
        return status, out.getvalue(), err.getvalue()

    def test_demo_is_reproducible_private_and_keeps_gold_out_of_inputs(self):
        first = self.run_cli("demo", "--output", str(self.output))
        second = self.run_cli("demo", "--output", str(self.output))
        self.assertEqual(first, second)
        self.assertEqual(first[0], 0, first[2])
        result = json.loads(first[1])
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(result["scenarios"], 7)
        directory = Path(result["directory"])
        inputs = cli.read_json(directory / "inputs.json")
        for case in inputs["tasks"]:
            self.assertEqual(set(case), {"case_id", "document_id", "category", "question"})
        self.assertNotIn('"answers":', json.dumps(inputs))
        self.assertIn("not model results", (directory / "summary.md").read_text())
        for file in directory.iterdir():
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.network.assert_not_called()

    def test_score_command_replays_saved_predictions(self):
        status, output, error = self.run_cli("demo", "--output", str(self.output))
        self.assertEqual(status, 0, error)
        directory = Path(json.loads(output)["directory"])
        status, output, error = self.run_cli("score", "--benchmark", str(directory / "benchmark.json"),
                                            "--predictions", str(directory / "02-predictions.json"),
                                            "--output", str(self.output))
        self.assertEqual(status, 0, error)
        report = cli.read_json(Path(json.loads(output)["directory"]) / "01-result.json")
        self.assertEqual(report["config_id"], "all_abstain")
        self.assertEqual(report["summary"]["answerable"]["evidence_f1_macro"], 0)

    def test_public_and_symlinked_output_paths_are_rejected(self):
        status, _, _ = self.run_cli("demo", "--output", str(self.root / "public"))
        self.assertEqual(status, 1)
        self.assertFalse((self.root / "public").exists())
        self.output.parent.mkdir(parents=True)
        self.output.symlink_to(self.root, target_is_directory=True)
        status, _, _ = self.run_cli("demo", "--output", str(self.output))
        self.assertEqual(status, 1)

    def test_json_duplicate_fields_and_nonfinite_values_are_rejected(self):
        path = self.root / "fixture.json"
        for text in ('{"format":1,"format":2}', '{"nested":{"a":1,"a":2}}', '{"x":NaN}', '{"x":Infinity}'):
            path.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                cli.read_json(path)

    def test_invalid_input_does_not_leave_partial_results(self):
        path = self.root / "fixture.json"
        path.write_text('{"format":"cuad-local-preparation-v1"}')
        status, _, _ = self.run_cli("demo", "--benchmark", str(path), "--output", str(self.output))
        self.assertEqual(status, 1)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
