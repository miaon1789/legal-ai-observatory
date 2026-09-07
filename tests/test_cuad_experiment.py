"""Invented fixtures test plumbing; real-data results come only from the separate run."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cuad_experiment as experiment
import cuad_intake as intake
from observability import retrieval
from observability.events import digest


def protocol():
    result = experiment.read_json(experiment.PROTOCOL)
    result["split_quotas"] = {"development": [1, 1, 1, 1], "evaluation": [1, 1, 1, 1]}
    result["categories"] = [{"category": "Governing Law", "query": "governing law"},
                            {"category": "Audit Rights", "query": "audit books records"}]
    result["chunking"] = {"words": 8, "overlap_words": 2}
    return result


def documents():
    result = []
    for i in range(40):
        text = f"Invented record {i}. " + "General text. " * (i + 1) + "Governing law is Exampleland."
        result.append({"title": f"invented-{i:03}", "paragraphs": [{"context": text, "qas": [
            {"id": f"invented-{i}__Governing Law", "question": "Which law?", "is_impossible": False,
             "answers": [{"text": "Exampleland", "answer_start": text.index("Exampleland")}]},
            {"id": f"invented-{i}__Audit Rights", "question": "Can records be audited?",
             "is_impossible": True, "answers": []},
        ]}]})
    return result, copy.deepcopy(result[:20]), copy.deepcopy(result[20:])


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.protocol = protocol()
        self.full, self.train, self.test = documents()

    def build(self):
        return experiment.build_bundle(self.protocol, self.full, self.train, self.test,
                                       self.protocol["archive_sha256"])

    def test_sizes_partitions_and_negatives(self):
        files = self.build()
        self.assertEqual(files["manifest.json"]["counts"],
                         {"documents": 8, "tasks": 16, "answerable": 8, "unanswerable": 8, "gold_spans": 8})
        docs = files["inputs.json"]["documents"]
        train_ids = {experiment.document_id(d["title"]) for d in self.train}
        test_ids = {experiment.document_id(d["title"]) for d in self.test}
        self.assertEqual({(d["partition"], d["length_quartile"]) for d in docs},
                         {(p, q) for p in ("development", "evaluation") for q in range(1, 5)})
        for doc in docs:
            self.assertIn(doc["document_id"], train_ids if doc["partition"] == "development" else test_ids)

    def test_selection_is_order_independent_and_gold_independent(self):
        before = experiment.select_documents(self.full, self.train, self.test, self.protocol)[0]
        for doc in self.full:
            for qa in doc["paragraphs"][0]["qas"]:
                qa["answers"], qa["is_impossible"] = [], True
        after = experiment.select_documents(list(reversed(self.full)), list(reversed(self.train)),
                                            list(reversed(self.test)), self.protocol)[0]
        self.assertEqual([(p, q, d["title"]) for p, q, d in before],
                         [(p, q, d["title"]) for p, q, d in after])

    def test_whitespace_casefold_duplicates_all_excluded(self):
        self.full[20]["paragraphs"][0]["context"] = "\n  " + self.full[0]["paragraphs"][0]["context"].upper()
        self.test[0] = copy.deepcopy(self.full[20])
        selected, stats = experiment.select_documents(self.full, self.train, self.test, self.protocol)
        self.assertEqual(stats["normalized_duplicate_documents_excluded"], 2)
        self.assertNotIn(self.full[0]["title"], {d["title"] for _, _, d in selected})
        self.assertNotIn(self.full[20]["title"], {d["title"] for _, _, d in selected})

    def test_reviewed_or_held_samples_excluded(self):
        first = self.build()["inputs.json"]["documents"][0]["document_id"]
        self.protocol["exclude_previously_reviewed_ids"].append(first)
        self.assertNotIn(first, {d["document_id"] for d in self.build()["inputs.json"]["documents"]})

    def test_inputs_contain_no_gold_and_text_unchanged(self):
        files = self.build()
        inputs = files["inputs.json"]
        self.assertNotIn("is_impossible", json.dumps(inputs))
        self.assertNotIn('"answers"', json.dumps(inputs))
        self.assertNotIn("source_qa_id", json.dumps(inputs))
        source = {experiment.document_id(d["title"]): d["paragraphs"][0]["context"] for d in self.full}
        for doc in inputs["documents"]:
            self.assertEqual(doc["text"], source[doc["document_id"]])

    def test_bad_gold_recorded_not_repaired_or_silently_dropped(self):
        first = self.build()["inputs.json"]["documents"][0]["document_id"]
        doc = next(d for d in self.full if experiment.document_id(d["title"]) == first)
        doc["paragraphs"][0]["qas"][0]["answers"][0]["answer_start"] = 0
        files = self.build()
        self.assertEqual(len(files["manifest.json"]["offset_mismatches"]), 1)
        self.assertEqual(len(files["inputs.json"]["tasks"]), 16)

    def test_partition_text_mismatch_fails(self):
        for doc in self.train:
            doc["paragraphs"][0]["context"] += " altered"
        with self.assertRaisesRegex(ValueError, "text_mismatch"):
            self.build()

    def test_partition_overlap_and_missing_members_fail(self):
        self.test.append(self.train[0])
        with self.assertRaises(ValueError):
            self.build()
        self.test.pop()
        self.test.pop()
        with self.assertRaises(ValueError):
            self.build()

    def test_archive_hash_and_insufficient_pool_fail(self):
        with self.assertRaisesRegex(ValueError, "archive_sha256"):
            experiment.build_bundle(self.protocol, self.full, self.train, self.test, "bad")
        self.protocol["split_quotas"]["development"] = [10, 10, 10, 10]
        with self.assertRaisesRegex(ValueError, "insufficient"):
            self.build()

    def test_external_permissions_cannot_be_enabled(self):
        self.protocol["limits"]["external_inference"] = "approved"
        with self.assertRaisesRegex(ValueError, "permissions"):
            self.build()

    def test_bad_protocol_values_fail(self):
        for key, value in (("tokenization", "unknown"), ("selection", "best_gold_score"),
                           ("configurations", [{"config_id": "a", "top_k": True}]),
                           ("chunking", {"words": 2, "overlap_words": 2})):
            changed = self.protocol | {key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                experiment.validate_protocol(changed)


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.protocol = protocol()
        self.files = experiment.build_bundle(self.protocol, *documents(), self.protocol["archive_sha256"])
        self.inputs, self.refs = self.files["inputs.json"], self.files["references.json"]

    def execute(self):
        return retrieval.execute(self.inputs, self.protocol, lambda row: None)

    def test_chunks_cover_unicode_text_without_gaps_or_tail_duplicates(self):
        text = "  One \U0001f600 two\nthree Ren\u00e9e five six seven eight nine   "
        intervals = retrieval.chunks(text, 4, 1)
        covered = set()
        for c in intervals:
            covered.update(range(c["start"], c["end"]))
            self.assertTrue(text[c["start"]:c["end"]].strip())
        self.assertEqual(covered, set(range(len(text))))
        self.assertEqual(intervals[-1]["end"], len(text))
        self.assertEqual(retrieval.chunks("one two three", 4, 1), [{"start": 0, "end": 13}])

    def test_empty_text_and_invalid_chunk_settings_fail(self):
        for text, words, overlap in ((" ", 4, 1), ("hello", True, 0), ("hello", 4, 4), ("hello", 4, -1)):
            with self.subTest(text=text, words=words), self.assertRaises(ValueError):
                retrieval.chunks(text, words, overlap)

    def test_real_library_retrieves_relevant_chunk(self):
        text = "apple pear peach plum. " * 20 + "audit inspect accounting records accountant books. " + "cloud rain sun wind. " * 20
        retriever = retrieval.Retriever(text, {"words": 8, "overlap_words": 2}, self.protocol["retrieval"])
        found = retriever.retrieve("audit accountant records", 3)
        self.assertTrue(any("audit" in text[c["start"]:c["end"]] for c in found))

    def test_ties_have_stable_source_order_and_no_abstention_claim(self):
        retriever = retrieval.Retriever("one two three four five six seven eight nine ten",
                                         {"words": 2, "overlap_words": 0}, self.protocol["retrieval"])
        found = retriever.retrieve("nonexistent", 3)
        self.assertEqual([c["start"] for c in found], sorted(c["start"] for c in found))
        self.assertEqual(len(found), 3)
        self.assertEqual([c["score"] for c in found], [0.0] * 3)

    def test_execution_has_measured_logs_without_provider_or_fake_costs(self):
        emitted = []
        rows = retrieval.execute(self.inputs, self.protocol, emitted.append)
        self.assertEqual(len(rows), 32)
        self.assertEqual(emitted, rows)
        for row in rows:
            self.assertEqual(row["case_origin"], "public_real_contract")
            self.assertEqual(row["execution_origin"], "local_algorithm")
            self.assertIsNone(row["api_cost_usd"])
            self.assertIsNone(row["input_tokens"])
            self.assertGreaterEqual(row["retrieval_latency_ms"], 0)
            self.assertEqual(row["status"], "ok")

    def test_gold_rejected_at_execution_boundary(self):
        self.inputs["tasks"][0]["answers"] = []
        with self.assertRaisesRegex(ValueError, "gold_in_execution"):
            self.execute()

    def test_index_failure_keeps_all_denominators_and_safe_error_type(self):
        with patch.object(retrieval, "Retriever", side_effect=RuntimeError("PRIVATE_SECRET")):
            rows = self.execute()
        self.assertEqual(len(rows), 32)
        self.assertTrue(all(r["status"] == "error" for r in rows))
        self.assertNotIn("PRIVATE_SECRET", json.dumps(rows))
        summary, scored = retrieval.evaluate(self.inputs, self.refs, rows, self.protocol)
        metrics = summary["evaluation"]["bm25-top3"]["overall"]
        self.assertEqual(metrics["failed_queries"], 8)
        self.assertEqual(metrics["macro_gold_character_recall"], 0)
        self.assertIsNone(metrics["query_latency_ms_p50"])

    def test_query_failure_is_logged_not_dropped(self):
        with patch.object(retrieval.Retriever, "retrieve", side_effect=ValueError("private")):
            rows = self.execute()
        self.assertEqual(len(rows), 32)
        self.assertTrue(all(r["error_type"] == "ValueError" for r in rows))

    def test_top5_contains_top3_for_same_query(self):
        rows = self.execute()
        for task in self.inputs["tasks"]:
            selected = {r["config_id"]: r for r in rows if r["case_id"] == task["case_id"]}
            self.assertEqual(selected["bm25-top3"]["chunks"], selected["bm25-top5"]["chunks"][:3])

    def test_overlapping_gold_and_chunks_count_characters_once(self):
        text = "0123456789abcdefghij"
        reference = {"is_impossible": False, "answers": [{"answer_start": 2, "text": "2345"},
                                                         {"answer_start": 4, "text": "4567"}]}
        prediction = {"status": "ok", "chunks": [{"start": 0, "end": 5}, {"start": 3, "end": 6}]}
        score = retrieval.score_case(text, reference, prediction)
        self.assertAlmostEqual(score["gold_character_recall"], 4 / 6)
        self.assertAlmostEqual(score["context_precision"], 4 / 6)
        self.assertEqual(score["retrieved_characters"], 6)
        self.assertEqual(score["retrieved_characters_with_overlap"], 8)

    def test_missing_and_unanswerable_cases_not_given_fake_accuracy(self):
        summary, scored = retrieval.evaluate(self.inputs, self.refs, [], self.protocol)
        metrics = summary["evaluation"]["bm25-top3"]["overall"]
        self.assertEqual(metrics["tasks"], 8)
        self.assertEqual(metrics["missing_queries"], 8)
        self.assertEqual(metrics["macro_gold_character_recall"], 0)
        self.assertEqual(metrics["unanswerable"], 4)
        self.assertIsNone(metrics["answer_accuracy"])
        self.assertIsNone(metrics["abstention_accuracy"])
        self.assertTrue(all(r["gold_character_recall"] is None for r in scored if r["is_impossible"]))

    def test_wrong_document_unknown_or_duplicate_prediction_rejected(self):
        original = self.execute()
        for edit in ("document", "duplicate", "unknown", "partition", "too_many_chunks"):
            rows = copy.deepcopy(original)
            if edit == "document":
                rows[0]["document_id"] = "wrong"
            elif edit == "duplicate":
                rows.append(copy.deepcopy(rows[0]))
            elif edit == "unknown":
                rows[0]["case_id"] = "unknown"
            elif edit == "partition":
                rows[0]["partition"] = "evaluation"
            else:
                rows[0]["chunks"] *= 10
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                retrieval.evaluate(self.inputs, self.refs, rows, self.protocol)

    def test_invalid_offsets_and_reference_membership_rejected(self):
        rows = self.execute()
        rows[0]["chunks"][0]["end"] = 999_999
        with self.assertRaisesRegex(ValueError, "offsets"):
            retrieval.evaluate(self.inputs, self.refs, rows, self.protocol)
        self.refs["references"].pop()
        with self.assertRaisesRegex(ValueError, "reconcile"):
            retrieval.evaluate(self.inputs, self.refs, [], self.protocol)


class StorageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.protocol = protocol()
        self.protocol_path = self.root / "protocol.json"
        self.protocol_path.write_text(json.dumps(self.protocol), encoding="utf-8")
        for target, name, value in ((intake, "ROOT", self.root), (experiment, "PROTOCOL", self.protocol_path),
                                    (experiment, "OUTPUT", self.root / "private/experiments")):
            mocked = patch.object(target, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(intake, "load_partitioned_documents",
                              return_value=(*documents(), self.protocol["archive_sha256"], []))
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(intake.urllib.request, "urlopen", side_effect=AssertionError("network forbidden"))
        self.network = mocked.start()
        self.addCleanup(mocked.stop)

    def test_prepare_reproducible_private_bundle(self):
        first = experiment.prepare()
        second = experiment.prepare()
        self.assertEqual(first, second)
        path = Path(first["bundle_directory"]) / "inputs.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.network.assert_not_called()

    def test_full_local_run_persists_events_and_marks_test_access(self):
        prepared = experiment.prepare()
        result = experiment.run(Path(prepared["bundle_directory"]))
        run = Path(result["run_directory"])
        summary = experiment.read_json(run / "summary.json", private=True)
        self.assertEqual(result["query_attempts"], 32)
        self.assertEqual(summary["execution"]["external_inference_calls"], 0)
        self.assertTrue(summary["execution"]["evaluation_labels_accessed"])
        self.assertFalse(summary["execution"]["is_llm_evaluation"])
        self.assertEqual(summary["execution"]["status"], "complete")
        self.assertEqual(len((run / "query_events.jsonl").read_text().splitlines()), 32)
        self.assertEqual(stat.S_IMODE((run / "query_events.jsonl").stat().st_mode), 0o600)
        self.network.assert_not_called()

    def test_modified_input_or_receipt_rejected(self):
        prepared = experiment.prepare()
        directory = Path(prepared["bundle_directory"])
        path = directory / "inputs.json"
        original = experiment.read_json(path, private=True)
        intake.write_json(path, original | {"tampered": True})
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            experiment.run(directory)
        with self.assertRaisesRegex(ValueError, "modified"):
            experiment.prepare()

    def test_public_or_symlink_output_rejected(self):
        with patch.object(experiment, "OUTPUT", self.root / "public"):
            with self.assertRaises(ValueError):
                experiment.prepare()
        (self.root / "private").mkdir()
        (self.root / "private/experiments").symlink_to(self.root)
        with self.assertRaises(ValueError):
            experiment.prepare()

    def test_blocked_offsets_prevent_run(self):
        full, train, test = documents()
        for doc in full:
            doc["paragraphs"][0]["qas"][0]["answers"][0]["answer_start"] = 0
        with patch.object(intake, "load_partitioned_documents",
                          return_value=(full, train, test, self.protocol["archive_sha256"], [])):
            prepared = experiment.prepare()
        self.assertEqual(prepared["status"], "blocked_offset_mismatch")
        with self.assertRaisesRegex(ValueError, "blocked"):
            experiment.run(Path(prepared["bundle_directory"]))

    def test_duplicate_json_keys_rejected(self):
        self.protocol_path.write_text('{"x":1,"x":2}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            experiment.read_json(self.protocol_path)

    def test_malformed_reference_hash_fails_after_predictions_persisted(self):
        prepared = experiment.prepare()
        bundle = Path(prepared["bundle_directory"])
        intake.write_json(bundle / "references.json", {"changed": True})
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            experiment.run(bundle)
        runs = list((experiment.OUTPUT / "runs").iterdir())
        self.assertEqual(len(runs), 1)
        self.assertTrue((runs[0] / "predictions.json").is_file())
        record = experiment.read_json(runs[0] / "execution.json", private=True)
        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["evaluation_labels_accessed"])


if __name__ == "__main__":
    unittest.main()
