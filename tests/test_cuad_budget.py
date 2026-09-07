"""Budgeted retrieval invariants with invented source text only."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cuad_budget_experiment as budget
from observability import retrieval
from observability.evidence import union
from test_cuad_experiment import documents, protocol
import cuad_experiment as base


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.protocol = budget.load_protocol()
        self.protocol["character_budget"] = 80
        self.configs = [{"config_id": "baseline", "words": 8, "overlap_words": 2, "preamble_characters": 0},
                        {"config_id": "compact", "words": 4, "overlap_words": 1, "preamble_characters": 0},
                        {"config_id": "preamble", "words": 4, "overlap_words": 1, "preamble_characters": 20}]
        self.protocol["configurations"] = self.configs
        self.protocol["baseline_config_id"] = "baseline"
        self.settings = protocol()["retrieval"]
        self.text = "Opening term effective today. " + "Other fruit text. " * 30 + "Governing law is Exampleland. "

    def retrieve(self, config, text=None, category="Governing Law"):
        text = self.text if text is None else text
        index = retrieval.Retriever(text, {k: config[k] for k in ("words", "overlap_words")}, self.settings)
        return budget.budgeted_context(index, "governing law", category, len(text), config, self.protocol)

    def test_each_config_uses_exact_same_unique_character_budget(self):
        for config in self.configs:
            contexts = self.retrieve(config)
            spans = [(c["start"], c["end"]) for c in contexts]
            self.assertEqual(sum(e-s for s,e in spans), 80)
            self.assertEqual(sum(e-s for s,e in union(spans)), 80)

    def test_short_document_uses_full_text_not_padding(self):
        for config in self.configs:
            contexts = self.retrieve(config, "Short source.")
            self.assertEqual(union([(c["start"], c["end"]) for c in contexts]), [(0, 13)])

    def test_preamble_uses_budget_only_for_declared_categories(self):
        special = self.retrieve(self.configs[2], category="Effective Date")
        self.assertEqual(special[0], {"start": 0, "end": 20, "score": None})
        ordinary = self.retrieve(self.configs[2])
        self.assertIsNotNone(ordinary[0]["score"])
        self.assertEqual(sum(c["end"]-c["start"] for c in special), 80)

    def test_subtraction_handles_overlap_containment_and_touching(self):
        self.assertEqual(budget.uncovered(0, 20, [(2, 5), (4, 8), (10, 12)]), [(0, 2), (8, 10), (12, 20)])
        self.assertEqual(budget.uncovered(3, 6, [(0, 20)]), [])
        self.assertEqual(budget.uncovered(0, 2, [(2, 20)]), [(0, 2)])

    def test_reproducible_ranking(self):
        self.assertEqual(self.retrieve(self.configs[2]), self.retrieve(self.configs[2]))

    def test_no_gold_execution_and_failure_retention(self):
        p = protocol()
        files = base.build_bundle(p, *documents(), p["archive_sha256"])
        inputs = budget.subset(files["inputs.json"], "development")
        self.assertEqual(len(inputs["tasks"]), 8)
        with patch.object(retrieval, "Retriever", side_effect=RuntimeError("private source")):
            rows, _ = budget.execute(inputs, self.configs, self.protocol, self.settings, lambda r: None)
        self.assertEqual(len(rows), 24)
        self.assertTrue(all(r["status"] == "error" and r["error_type"] == "RuntimeError" for r in rows))
        result, scores = budget.evaluate(inputs, files["references.json"], rows, self.configs)
        self.assertEqual(result["baseline"]["overall"]["failed_queries"], 8)
        self.assertEqual(result["baseline"]["overall"]["macro_gold_character_recall"], 0)
        inputs["tasks"][0]["answers"] = []
        with self.assertRaisesRegex(ValueError, "gold_in_inputs"):
            budget.execute(inputs, self.configs, self.protocol, self.settings, lambda r: None)

    def test_candidate_selection_uses_declared_metrics_and_ties(self):
        result = {c["config_id"]: {"overall": {"failed_queries": 0, "missing_queries": 0,
                  "macro_gold_character_recall": .5, "all_gold_covered_rate": .4}} for c in self.configs}
        self.assertEqual(budget.choose_candidate(result, self.protocol), "compact")
        result["preamble"]["overall"]["macro_gold_character_recall"] = .6
        self.assertEqual(budget.choose_candidate(result, self.protocol), "preamble")
        result["baseline"]["overall"]["failed_queries"] = 1
        with self.assertRaises(ValueError):
            budget.choose_candidate(result, self.protocol)

    def test_missing_unknown_duplicate_and_wrong_document(self):
        p = protocol()
        files = base.build_bundle(p, *documents(), p["archive_sha256"])
        inputs = budget.subset(files["inputs.json"], "development")
        results, _ = budget.evaluate(inputs, files["references.json"], [], self.configs)
        self.assertEqual(results["baseline"]["overall"]["missing_queries"], 8)
        rows, _ = budget.execute(inputs, self.configs, self.protocol, self.settings, lambda r: None)
        for change in ("duplicate", "unknown", "wrong_document"):
            edited = copy.deepcopy(rows)
            if change == "duplicate":
                edited.append(copy.deepcopy(rows[0]))
            elif change == "unknown":
                edited[0]["config_id"] = "unknown"
            else:
                edited[0]["document_id"] = "wrong"
            with self.subTest(change=change), self.assertRaises(ValueError):
                budget.evaluate(inputs, files["references.json"], edited, self.configs)


if __name__ == "__main__":
    unittest.main()
