"""Local CUAD preparation tests; all fixtures are invented, with no real contracts."""
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
import cuad_intake as intake
import cuad_prepare as prep


def fixture():
    text = "Payment is due in 30 days.\nSigned by: An Example"
    name_start = text.index("An Example")
    document = {"title": "invented-preparation-fixture", "paragraphs": [{"context": text, "qas": [
        {"id": "fixture__Period", "question": "What is the payment period?", "is_impossible": False,
         "answers": [{"text": "30 days", "answer_start": text.index("30 days")},
                     {"text": "30", "answer_start": text.index("30 days")}]},
        {"id": "fixture__Renewal", "question": "What is the renewal period?", "is_impossible": True,
         "answers": []},
    ]}]}
    record = {"sample_id": "cuad-" + prep.sha(document["title"])[:12], "text_sha256": prep.sha(text),
              "assistant_full_text_review": "complete", "disposition": "prepare_locally",
              "reason": "Invented test only", "scope_note": "Only the supplied document.",
              "source": {"kind": "original_exhibit", "identity_status": "corroborated",
                         "url": "https://www.sec.gov/Archives/edgar/data/0/invented-fixture.htm"},
              "masks": [{"start": name_start, "end": len(text), "reason": "invented_name",
                         "segment_sha256": prep.sha(text[name_start:])}]}
    review = {"schema_version": 1, "dataset": "CUAD", "revision": intake.REVISION,
              "purpose": "private_preparation_only", "reviewer_kind": "coding_assistant",
              "limits": copy.deepcopy(prep.LIMITS), "documents": [record]}
    return document, review


class CuadPreparationTests(unittest.TestCase):
    def setUp(self):
        self.doc, self.review = fixture()
        self.record = self.review["documents"][0]
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.directory = self.root / "private/datasets/cuad/test"
        for key, value in (("ROOT", self.root), ("DIRECTORY", self.directory)):
            patcher = patch.object(intake, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        network = patch.object(intake.urllib.request, "urlopen", side_effect=AssertionError("Network forbidden"))
        self.network = network.start()
        self.addCleanup(network.stop)

    def build(self):
        return prep.build_bundle(self.review, [self.doc], [copy.deepcopy(self.doc)], "a" * 64)

    def test_inputs_have_no_gold_fields_and_annotations_are_retained(self):
        files = self.build()
        inputs = json.loads(files["inputs.json"])
        refs = json.loads(files["references.json"])["references"]
        self.assertEqual(len(inputs["tasks"]), 2)
        self.assertEqual(set(inputs["tasks"][0]), {"case_id", "document_id", "question"})
        self.assertEqual(set(inputs["documents"][0]), {"document_id", "text", "scope_note"})
        self.assertNotIn("is_impossible", json.dumps(inputs))
        self.assertNotIn("source_qa_id", json.dumps(inputs))
        self.assertEqual(refs[0]["answers"], self.doc["paragraphs"][0]["qas"][0]["answers"])
        self.assertTrue(refs[1]["is_impossible"])
        self.assertEqual(refs[1]["answers"], [])
        self.assertEqual(inputs["limits"], prep.LIMITS)

    def test_mask_preserves_unicode_codepoints_whitespace_and_evidence(self):
        text = "Before: \U0001f600 Ren\u00e9e\nAfter"
        start, end = text.index("\U0001f600"), text.index("\nAfter")
        mask = {"start": start, "end": end, "reason": "invented_name", "segment_sha256": prep.sha(text[start:end])}
        result = prep.mask_text(text, [mask])
        self.assertEqual(len(result), len(text))
        self.assertEqual(result, "Before: X XXXXX\nAfter")
        files = self.build()
        transformed = json.loads(files["inputs.json"])["documents"][0]["text"]
        self.assertNotIn("An Example", transformed)
        self.assertIn("30 days", transformed)

    def test_bad_mask_bounds_hash_or_order_rejected(self):
        mask = copy.deepcopy(self.record["masks"][0])
        for updates in ({"start": -1}, {"end": 10_000}, {"start": True}, {"end": mask["start"]},
                        {"segment_sha256": "0" * 64}):
            self.record["masks"] = [mask | updates]
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.build()
        self.record["masks"] = [mask, mask]
        with self.assertRaises(ValueError):
            self.build()

    def test_mask_overlapping_gold_is_rejected_without_changing_rubric(self):
        text = self.doc["paragraphs"][0]["context"]
        start = text.index("30 days")
        self.record["masks"] = [{"start": start, "end": start + 7, "reason": "bad_selection",
                                  "segment_sha256": prep.sha("30 days")}]
        with self.assertRaisesRegex(ValueError, "overlaps gold"):
            self.build()
        self.assertEqual(self.doc["paragraphs"][0]["qas"][0]["answers"][0]["text"], "30 days")

    def test_bad_original_offsets_are_not_repaired(self):
        self.doc["paragraphs"][0]["qas"][0]["answers"][0]["answer_start"] = 0
        with self.assertRaisesRegex(ValueError, "Original annotation"):
            self.build()

    def test_changed_source_review_or_revision_rejected(self):
        self.record["text_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.build()
        self.review["revision"] = "stale"
        with self.assertRaises(ValueError):
            self.build()

    def test_missing_review_and_changed_use_not_accepted(self):
        for limits in ({}, prep.LIMITS | {"external_inference": "approved"},
                       prep.LIMITS | {"publication_of_source_text": "approved"},
                       prep.LIMITS | {"legal_clearance": "approved"}):
            self.review["limits"] = limits
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.build()
        self.review["limits"] = copy.deepcopy(prep.LIMITS)
        self.record["assistant_full_text_review"] = "pending"
        with self.assertRaises(ValueError):
            self.build()

    def test_partial_or_unexpected_source_cannot_be_prepared(self):
        original = copy.deepcopy(self.record["source"])
        for updates in ({"kind": "filing_cover_only"}, {"identity_status": "partial"},
                        {"url": "https://example.invalid/contract"},
                        {"url": "https://www.sec.gov.example.invalid/Archives/edgar/data/a"},
                        {"url": "http://www.sec.gov/Archives/edgar/data/a"}):
            self.record["source"] = original | updates
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.build()

    def test_held_candidate_is_not_written_to_inputs_or_gold(self):
        held_doc = copy.deepcopy(self.doc)
        held_doc["title"] = "invented-held-fixture"
        held = copy.deepcopy(self.record)
        held["sample_id"] = "cuad-" + prep.sha(held_doc["title"])[:12]
        held["disposition"] = "hold"
        held["source"]["kind"] = "filing_cover_only"
        self.review["documents"].append(held)
        files = prep.build_bundle(self.review, [self.doc, held_doc], [self.doc, held_doc], "a" * 64)
        self.assertNotIn(held["sample_id"], files["inputs.json"].decode())
        self.assertNotIn(held["sample_id"], files["references.json"].decode())
        receipt = json.loads(files["receipt.json"])
        self.assertEqual(receipt["prepared_documents"], 1)
        self.assertEqual(receipt["held_documents"][0]["sample_id"], held["sample_id"])

    def test_all_held_or_duplicate_candidates_fail(self):
        self.record["disposition"] = "hold"
        with self.assertRaises(ValueError):
            self.build()
        self.record["disposition"] = "prepare_locally"
        self.review["documents"].append(copy.deepcopy(self.record))
        with self.assertRaises(ValueError):
            self.build()

    def test_official_test_documents_and_changed_train_text_rejected(self):
        with self.assertRaises(ValueError):
            prep.build_bundle(self.review, [self.doc], [], "a" * 64)
        train_doc = copy.deepcopy(self.doc)
        train_doc["paragraphs"][0]["context"] += " altered"
        with self.assertRaises(ValueError):
            prep.build_bundle(self.review, [self.doc], [train_doc], "a" * 64)

    def test_oversized_document_is_not_silently_truncated(self):
        self.doc["paragraphs"][0]["context"] += "X" * 100_001
        self.record["text_sha256"] = prep.sha(self.doc["paragraphs"][0]["context"])
        with self.assertRaisesRegex(ValueError, "100,000"):
            self.build()

    def test_repeatable_bundle_identity_receipt_checksums_and_permissions(self):
        path = self.directory / "candidate_review.json"
        intake.write_json(path, self.review)
        loaded = ([self.doc], [self.doc], [], "a" * 64, [])
        with patch.object(intake, "load_partitioned_documents", return_value=loaded):
            first = prep.prepare(path)
            second = prep.prepare(path)
        self.assertEqual(first, second)
        self.assertEqual(first["tasks"], 2)
        self.assertEqual(first["answerable_tasks"], 1)
        self.assertEqual(first["unanswerable_tasks"], 1)
        self.assertEqual(first["answer_spans"], 2)
        directory = Path(first["directory"])
        receipt = json.loads((directory / "receipt.json").read_text())
        for name in ("inputs", "references"):
            content = (directory / f"{name}.json").read_text()
            self.assertEqual(prep.sha(content), receipt[f"{name}_sha256"])
        for file in directory.iterdir():
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        self.network.assert_not_called()

    def test_failed_preparation_leaves_no_bundle(self):
        self.record["masks"][0]["segment_sha256"] = "0" * 64
        path = self.directory / "candidate_review.json"
        intake.write_json(path, self.review)
        with patch.object(intake, "load_partitioned_documents", return_value=([self.doc], [self.doc], [], "a" * 64, [])):
            with self.assertRaises(ValueError):
                prep.prepare(path)
        self.assertFalse((self.directory / "prepared").exists())

    def test_review_outside_private_or_symlinked_is_rejected(self):
        public = self.root / "review.json"
        public.write_bytes(prep.encoded(self.review))
        with self.assertRaises(ValueError):
            prep.prepare(public)
        self.directory.mkdir(parents=True)
        link = self.directory / "review.json"
        link.symlink_to(public)
        with self.assertRaises(ValueError):
            prep.prepare(link)


if __name__ == "__main__":
    unittest.main()
