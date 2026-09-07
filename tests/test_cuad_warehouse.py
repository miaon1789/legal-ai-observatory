import copy
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cuad_warehouse import validate


def packet():
    result = {"format": "cuad-sql-packet-v1", "run": {
        "run_id": uuid.uuid4().hex, "run_label": "CUAD v2 | fresh evaluation", "phase": "fresh_evaluation",
        "protocol_sha256": "a" * 64, "started_at": "2026-09-07T06:00:00+00:00",
        "case_origin": "public_real_contract", "execution_origin": "local_algorithm", "character_budget": 10,
        "expected_rows": 4}, "configurations": [{"config_id": "a", "config_role": "baseline"},
                                                 {"config_id": "b", "config_role": "candidate"}], "cases": []}
    for case, positive in (("positive", 1), ("negative", 0)):
        for config in ("a", "b"):
            result["cases"].append({"case_id": case, "config_id": config, "document_id": "invented",
                "dataset_partition": "evaluation", "category": "Governing Law", "length_quartile": 1,
                "document_characters": 100, "response_state": "ok", "is_answerable": positive,
                "gold_character_recall": .5 if positive else None, "any_gold_hit": 1 if positive else None,
                "all_gold_covered": 0 if positive else None, "context_precision": .4 if positive else None,
                "retrieved_characters": 10, "retrieval_latency_ms": .25})
    return result


class PacketTests(unittest.TestCase):
    def test_valid_packet(self):
        validate(packet())

    def test_unexpected_source_fields_rejected(self):
        for section in ("top", "run", "case"):
            value = packet()
            target = value if section == "top" else value["run"] if section == "run" else value["cases"][0]
            target["quote"] = "must never enter SQL"
            with self.subTest(section=section), self.assertRaises(ValueError):
                validate(value)

    def test_provenance_and_phase_not_relabelled(self):
        for field, content in (("case_origin", "self_authored_synthetic"), ("execution_origin", "observed"),
                               ("phase", "production"), ("protocol_sha256", "bad")):
            value = packet()
            value["run"][field] = content
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(value)

    def test_negatives_stay_null(self):
        value = packet()
        value["cases"][2]["gold_character_recall"] = 0
        with self.assertRaisesRegex(ValueError, "null"):
            validate(value)

    def test_unpaired_duplicate_and_changed_identity(self):
        for mode in ("unpaired", "duplicate", "identity"):
            value = packet()
            if mode == "unpaired":
                value["cases"].pop()
                value["run"]["expected_rows"] = 3
            elif mode == "duplicate":
                value["cases"][1] = copy.deepcopy(value["cases"][0])
            else:
                value["cases"][1]["document_id"] = "different"
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate(value)

    def test_invalid_metrics_budget_nan_and_failure(self):
        for field, content in (("gold_character_recall", 1.1), ("context_precision", float("nan")),
                               ("retrieved_characters", 11), ("retrieval_latency_ms", None),
                               ("all_gold_covered", 1), ("response_state", "error")):
            value = packet()
            value["cases"][0][field] = content
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(value)

    def test_failed_query_remains_in_denominator(self):
        value = packet()
        value["cases"][0].update(response_state="error", gold_character_recall=0.0, any_gold_hit=0,
                                 all_gold_covered=0, context_precision=0.0, retrieved_characters=0)
        validate(value)


if __name__ == "__main__":
    unittest.main()
