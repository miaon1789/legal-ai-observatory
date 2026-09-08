"""Diagnostics use invented text, never distribute real contract fixtures."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cuad_failure_analysis as analysis
import cuad_budget_experiment as experiment


class FailureAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.text = "alpha beta gamma delta epsilon zeta"
        self.index = SimpleNamespace(chunks=[{"start": 0, "end": 17}, {"start": 11, "end": 28},
                                            {"start": 23, "end": len(self.text)}],
                                     index=SimpleNamespace(get_scores=lambda _: [2.0, 3.0, 1.0]))
        self.protocol = {"character_budget": 20, "preamble_categories": ["Date"]}
        self.config = {"preamble_characters": 5}

    def trace(self, category="Other"):
        return analysis.trace_context(self.index, "beta", category, len(self.text), self.config, self.protocol)

    def features(self, start, end, trace=None):
        reference = {"is_impossible": False, "answers": [{"answer_start": start, "text": self.text[start:end]}]}
        return analysis.evidence_diagnostics(self.text, reference, "beta", trace or self.trace(), self.protocol["character_budget"])

    def test_trace_matches_frozen_selector_with_overlap_and_preamble(self):
        for category in ("Other", "Date"):
            expected = experiment.budgeted_context(self.index, "beta", category, len(self.text), self.config, self.protocol)
            self.assertEqual(self.trace(category)["selected"], expected)

    def test_budget_cut_does_not_double_count_previously_selected_region(self):
        self.assertEqual(self.trace()["deferred"], [(3, 11)])
        features = self.features(0, 17)
        self.assertEqual(features["missing_gold_characters"], 8)
        self.assertEqual(features["missing_in_budget_cut_proposal"], 8)
        self.assertEqual(features["other_missing_gold_characters"], 0)

    def test_window_boundary_and_last_proposal_cut_are_distinct(self):
        features = self.features(23, 34)
        self.assertEqual(features["missing_in_budget_cut_proposal"], 0)
        self.assertTrue(features["incomplete_regions"][0]["shares_reached_window_boundary"])

    def test_exact_budget_at_proposal_boundary_has_no_deferred_tail(self):
        self.protocol["character_budget"] = 17
        self.assertEqual(self.trace()["deferred"], [])

    def test_short_document_fully_covered(self):
        self.protocol["character_budget"] = 100
        features = self.features(0, len(self.text))
        self.assertEqual(features["missing_gold_characters"], 0)
        self.assertEqual(features["incomplete_regions"], [])

    def test_negative_has_no_gold_not_a_retrieval_false_positive(self):
        result = analysis.evidence_diagnostics(self.text, {"answers": [], "is_impossible": True}, "beta", self.trace(), 20)
        self.assertFalse(result["is_answerable"])
        self.assertEqual(result["gold_characters"], 0)

    def test_overlapping_gold_is_merged_and_budget_limit_is_only_lower_bound(self):
        reference = {"is_impossible": False, "answers": [{"answer_start": 0, "text": self.text[:17]},
                                                        {"answer_start": 11, "text": self.text[11:28]}]}
        result = analysis.evidence_diagnostics(self.text, reference, "beta", self.trace(), 20)
        self.assertEqual(result["gold_characters"], 28)
        self.assertEqual(result["gold_regions"], 1)
        self.assertTrue(result["gold_exceeds_character_budget"])

    def test_evaluation_run_rejected_before_loading_source_text(self):
        with patch.object(analysis.base, "read_json", return_value={"format": "cuad-budget-results-v2",
                          "phase": "fresh_evaluation", "status": "complete"}) as reader:
            with self.assertRaisesRegex(ValueError, "completed_development_only"):
                analysis.analyze(Path("private/example"))
            self.assertEqual(reader.call_count, 1)


if __name__ == "__main__":
    unittest.main()
