"""Intake safety and reproducibility using invented fixtures, never CUAD text."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cuad_intake as intake


def document(index):
    return {"title": f"invented-fixture-{index}", "paragraphs": [{
        "context": "Payment is due in 30 days. Contact demo@example.invalid.",
        "qas": [
            {"id": f"q-{index}-a", "question": "What is the payment period?",
             "answers": [{"text": "30 days", "answer_start": 18}], "is_impossible": False},
            {"id": f"q-{index}-b", "question": "What is the renewal period?",
             "answers": [], "is_impossible": True},
        ]}]}


class CuadIntakeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.directory = self.root / "private/datasets/cuad/fixture"
        for key, value in (("ROOT", self.root), ("DIRECTORY", self.directory)):
            patcher = patch.object(intake, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        network = patch.object(intake.urllib.request, "urlopen", side_effect=AssertionError("Network forbidden"))
        self.network = network.start()
        self.addCleanup(network.stop)

    def pin(self, raw):
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        for key, value in (("ARCHIVE_BYTES", len(raw)), ("BLOB_SHA1", blob)):
            patcher = patch.object(intake, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return raw

    def fixture(self, change=None):
        docs = [document(i) for i in range(8)]
        payloads = {"CUADv1.json": {"data": docs},
                    "train_separate_questions.json": {"data": copy.deepcopy(docs[:6])},
                    "test.json": {"data": copy.deepcopy(docs[6:])}}
        if change:
            change(payloads)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in payloads.items():
                archive.writestr(name, json.dumps(payload))
        raw = self.pin(stream.getvalue())
        intake.write_private(self.directory / "data.zip", raw)
        return raw

    def test_archive_size_and_git_identity(self):
        raw = self.pin(b"invented archive bytes")
        self.assertEqual(intake.verify_archive(raw), hashlib.sha256(raw).hexdigest())
        for changed in (raw + b"!", b"X" + raw[1:]):
            with self.assertRaises(ValueError):
                intake.verify_archive(changed)

    def test_acquisition_requires_acknowledgement(self):
        with self.assertRaises(ValueError):
            intake.acquire(False)
        self.network.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_cached_acquisition_preserves_original_record(self):
        self.fixture()
        first = intake.acquire(True)
        manifest = self.directory / "acquisition.json"
        original = manifest.read_bytes()
        second = intake.acquire(True)
        self.assertEqual(first, second)
        self.assertEqual(manifest.read_bytes(), original)
        self.assertEqual(json.loads(original)["external_inference"], "not_approved")
        self.network.assert_not_called()

    def test_download_is_bounded_and_verified(self):
        raw = self.fixture()
        (self.directory / "data.zip").unlink()
        response = SimpleNamespace(url=intake.SOURCE, read=Mock(return_value=raw))
        self.network.side_effect = None
        self.network.return_value.__enter__.return_value = response
        result = intake.acquire(True)
        self.assertEqual(result["archive_sha256"], hashlib.sha256(raw).hexdigest())
        response.read.assert_called_once_with(len(raw) + 1)

    def test_redirect_or_corrupted_download_not_saved(self):
        raw = self.fixture()
        path = self.directory / "data.zip"
        path.unlink()
        self.network.side_effect = None
        for url, body in (("https://example.invalid/new.zip", raw), (intake.SOURCE, b"X" + raw[1:])):
            self.network.return_value.__enter__.return_value = SimpleNamespace(url=url, read=Mock(return_value=body))
            with self.assertRaises(ValueError):
                intake.acquire(True)
            self.assertFalse(path.exists())

    def test_private_path_and_symlink_parents(self):
        with self.assertRaises(ValueError):
            intake.write_private(self.root / "public.txt", b"private")
        self.directory.mkdir(parents=True)
        (self.directory / "samples").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            intake.write_private(self.directory / "samples/escaped.txt", b"private")
        (self.root / "private/alias").symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            intake.write_private(self.root / "private/alias/escaped.txt", b"private")
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_cached_symlink_and_corruption_stop_inspection(self):
        raw = self.fixture()
        path = self.directory / "data.zip"
        path.unlink()
        path.symlink_to(self.directory / "absent.zip")
        with self.assertRaises(ValueError):
            intake.inspect_samples(5)
        path.unlink()
        path.write_bytes(b"X" + raw[1:])
        with self.assertRaises(ValueError):
            intake.acquire(True)
        self.network.assert_not_called()

    def test_unsafe_and_duplicate_zip_members(self):
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for members in (("../escape",), ("/absolute",), ("C:/drive",), ("a\\b",), ("same", "same"), (link,)):
            with self.subTest(members=members), warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, "w") as archive:
                    for member in members:
                        archive.writestr(member, "invented")
                path = self.directory / "unsafe.zip"
                intake.write_private(path, stream.getvalue())
                with self.assertRaises(ValueError):
                    intake.archive_inventory(path)

    def test_parser_accepts_invented_canonical_document(self):
        self.assertEqual(intake.parse_documents({"data": [document(1)]}), [document(1)])

    def test_parser_rejects_non_objects_at_each_level(self):
        cases = [None, {"data": [None]}, {"data": [{"title": "a", "paragraphs": [None]}]}]
        for field in ("qas", "answers"):
            doc = document(1)
            target = doc["paragraphs"][0] if field == "qas" else doc["paragraphs"][0]["qas"][0]
            target[field] = [None]
            cases.append({"data": [doc]})
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                intake.parse_documents(payload)

    def test_invalid_annotations_and_duplicate_titles_rejected(self):
        for start in (-1, True, "18"):
            doc = document(1)
            doc["paragraphs"][0]["qas"][0]["answers"][0]["answer_start"] = start
            with self.assertRaises(ValueError):
                intake.parse_documents({"data": [doc]})
        doc = document(1)
        doc["paragraphs"][0]["qas"][0]["is_impossible"] = True
        with self.assertRaises(ValueError):
            intake.parse_documents({"data": [doc]})
        with self.assertRaises(ValueError):
            intake.parse_documents({"data": [document(1), document(1)]})

    def test_marker_preview_masks_obvious_contact_patterns(self):
        content = "demo@example.invalid (212) 555-0100 123-45-6789 confidential treatment [***]"
        flagged = intake.markers(content)
        self.assertTrue(flagged["email"] and flagged["confidential_treatment"])
        preview = intake.mask_preview(content)
        for value in ("demo@example.invalid", "(212) 555-0100", "123-45-6789"):
            self.assertNotIn(value, preview)

    def test_inspection_is_train_only_repeatable_private_and_not_clearance(self):
        self.fixture()
        first = intake.inspect_samples(5)
        second = intake.inspect_samples(5)
        self.assertEqual(first["samples"], second["samples"])
        self.assertEqual(first["inspection_count"], 5)
        self.assertEqual(first["source_urls_verified"], 0)
        self.assertEqual(sum(s["offset_mismatches"] for s in first["samples"]), 0)
        allowed = {f"invented-fixture-{i}" for i in range(6)}
        for path in (self.directory / "reviews").glob("*.json"):
            report = json.loads(path.read_text())
            self.assertIn(report["original_title"], allowed)
            self.assertEqual(report["decision"], "local_review_only")
            self.assertEqual(report["full_text_human_review"], "pending")
            self.assertEqual(report["external_inference"], "not_approved")
            self.assertEqual(report["publication_of_source_text"], "not_approved")
            self.assertNotIn(report["original_title"], json.dumps(first))
        for path in self.directory.rglob("*"):
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.network.assert_not_called()

    def test_offset_mismatch_is_reported_not_silently_fixed(self):
        def change(payloads):
            for doc in payloads["CUADv1.json"]["data"]:
                doc["paragraphs"][0]["qas"][0]["answers"][0]["answer_start"] = 0
        self.fixture(change)
        summary = intake.inspect_samples(5)
        self.assertEqual(sum(s["offset_mismatches"] for s in summary["samples"]), 5)

    def test_partition_overlap_or_missing_membership_stops_inspection(self):
        for replacement in ([document(0)], [document(6)]):
            self.fixture(lambda p: p["test.json"].update(data=replacement))
            with self.assertRaises(ValueError):
                intake.inspect_samples(5)

    def test_canonical_training_text_must_match(self):
        def change(payloads):
            for doc in payloads["train_separate_questions.json"]["data"]:
                doc["paragraphs"][0]["context"] += " altered"
        self.fixture(change)
        with self.assertRaises(ValueError):
            intake.inspect_samples(5)

    def test_sample_bounds_and_insufficient_training_documents(self):
        for count in (4, 11, True, 5.0):
            with self.assertRaises(ValueError):
                intake.inspect_samples(count)
        self.fixture()
        with self.assertRaises(ValueError):
            intake.inspect_samples(7)

    def test_missing_required_archive_member_stops_inspection(self):
        self.fixture(lambda p: p.pop("test.json"))
        with self.assertRaises(ValueError):
            intake.inspect_samples(5)


if __name__ == "__main__":
    unittest.main()
