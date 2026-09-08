"""Sentence-unit experiments are exercised with invented source text only."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cuad_boundary_experiment as boundaries
import cuad_budget_experiment as budget
from observability import retrieval


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.protocol = boundaries.load_protocol()
        self.protocol["character_budget"] = 65
        self.old = budget.load_protocol()
        self.old["character_budget"] = 65
        self.config = next(c for c in self.old["configurations"] if c["config_id"] == self.protocol["parent_candidate_id"])
        self.config.update(words=8, overlap_words=2, preamble_characters=16)
        self.text = "Opening sentence. Governing law is Exampleland. Other information. Another sentence."
        self.settings = {"k1": 1.5, "b": .75, "epsilon": .25}
        self.inputs = {"documents": [{"document_id": "invented", "partition": "development", "length_quartile": 1, "text": self.text}],
                       "tasks": [{"case_id": "law", "document_id": "invented", "category": "Governing Law", "query": "governing law"},
                                 {"case_id": "date", "document_id": "invented", "category": "Effective Date", "query": "effective date"}]}

    def test_source_partition_preserves_whitespace_abbreviations_and_decimals(self):
        text = "  Example Corp. paid $2.50.\n\nThe term is 10 years.  Next sentence.  "
        units = boundaries.sentence_units(text)
        self.assertEqual(len(units), 3)
        self.assertEqual("".join(text[a:b] for a, b in units), text)
        self.assertEqual(units[0], (0, 29))
        self.assertTrue(all(a < b for a, b in units))

    def test_invalid_offsets_and_omitted_nonwhitespace_rejected(self):
        with self.assertRaises(ValueError):
            boundaries.source_units("source", [SimpleNamespace(start=1, end=6, sent="ource")])
        with self.assertRaises(ValueError):
            boundaries.source_units("source", [SimpleNamespace(start=0, end=3, sent="sou")])
        with self.assertRaises(ValueError):
            boundaries.source_units("source", [SimpleNamespace(start=0, end=6, sent="changed")])
        with self.assertRaises(ValueError):
            boundaries.source_units("source", [])

    def test_whole_units_deduplicated_and_no_clipping(self):
        units = boundaries.sentence_units(self.text)
        index = retrieval.Retriever(self.text, {k: self.config[k] for k in ("words", "overlap_words")}, self.settings)
        selected = boundaries.select_units(index, "governing law", "Governing Law", len(self.text), units, self.config, self.old, 65)
        spans = [(c["start"], c["end"]) for c in selected]
        self.assertEqual(len(spans), len(set(spans)))
        self.assertTrue(set(spans) <= set(units))
        self.assertLessEqual(boundaries.context_size(selected), 65)
        self.assertEqual(boundaries.context_size(selected), sum(b-a for a,b in spans))

    def test_units_too_long_skipped_then_later_short_unit_can_fit(self):
        index = SimpleNamespace(chunks=[{"start": 0, "end": 100}, {"start": 100, "end": 110}],
                                index=SimpleNamespace(get_scores=lambda _: [2., 1.]))
        selected = boundaries.select_units(index, "query", "Other", 110, [(0,100), (100,110)], self.config, self.old, 20)
        self.assertEqual(selected, [{"start": 100, "end": 110, "score": 1.}])

    def test_zero_budget_and_all_oversized_units_give_empty_context(self):
        index = SimpleNamespace(chunks=[{"start": 0, "end": 100}], index=SimpleNamespace(get_scores=lambda _: [1.]))
        for limit in (0, 20):
            self.assertEqual(boundaries.select_units(index, "query", "Other", 100, [(0,100)], self.config, self.old, limit), [])

    def test_header_seed_expands_to_full_unit_and_counts_toward_budget(self):
        index = retrieval.Retriever(self.text, {k: self.config[k] for k in ("words", "overlap_words")}, self.settings)
        units = boundaries.sentence_units(self.text)
        selected = boundaries.select_units(index, "date", "Effective Date", len(self.text), units, self.config, self.old, 65)
        self.assertEqual((selected[0]["start"], selected[0]["end"]), units[0])
        self.assertIsNone(selected[0]["score"])
        self.assertLessEqual(boundaries.context_size(selected), 65)

    def test_nonfinite_ranking_rejected(self):
        index = SimpleNamespace(chunks=[{"start": 0, "end": 10}], index=SimpleNamespace(get_scores=lambda _: [float("nan")]))
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            boundaries.select_units(index, "query", "Other", 10, [(0,10)], self.config, self.old, 20)

    def test_all_three_arms_execute_and_control_matches_each_question_size(self):
        emitted = []
        rows, prep = boundaries.execute(self.inputs, self.protocol, self.old, self.settings, emitted.append)
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows, emitted)
        self.assertEqual(len(prep), 1)
        for case_id in ("law", "date"):
            pair = {r["config_id"]:r for r in rows if r["case_id"] == case_id}
            self.assertTrue(all(r["status"] == "ok" for r in pair.values()))
            sizes = [boundaries.context_size(pair[c]["chunks"]) for c in boundaries.CONFIG_IDS]
            self.assertEqual(sizes[0], 65)
            self.assertEqual(sizes[1], sizes[2])

    def test_segmentation_failure_does_not_remove_baseline_or_become_zero_length_control(self):
        with patch.object(boundaries, "sentence_units", side_effect=ValueError("invented error")):
            rows, prep = boundaries.execute(self.inputs, self.protocol, self.old, self.settings, lambda _: None)
        for row in rows:
            expected = "ok" if row["config_id"] == boundaries.CONFIG_IDS[0] else "error"
            self.assertEqual(row["status"], expected)
            if expected == "error":
                self.assertEqual(row["chunks"], [])
                self.assertEqual(row["error_type"], "ValueError")
        self.assertEqual(prep[0]["segmentation_error"], "ValueError")

    def test_gold_and_evaluation_inputs_rejected(self):
        for mode in ("gold", "evaluation"):
            inputs = copy.deepcopy(self.inputs)
            if mode == "gold":
                inputs["tasks"][0]["answers"] = []
            else:
                inputs["documents"][0]["partition"] = "evaluation"
            with self.assertRaises(ValueError):
                boundaries.execute(inputs, self.protocol, self.old, self.settings, lambda _: None)

    def test_parent_evaluation_rejected_before_source_reads(self):
        with patch.object(boundaries.base, "read_json", return_value={"phase": "fresh_evaluation", "status": "complete"}) as read:
            with self.assertRaisesRegex(ValueError, "completed_development_only"):
                boundaries.load_development(self.protocol)
            self.assertEqual(read.call_count, 1)

    def test_paired_regressions_and_failures_included_negatives_not_quality_scored(self):
        refs = {"references": [{"case_id": "law", "document_id": "invented", "answers": [{"answer_start": 18, "text": "Governing law"}], "is_impossible": False},
                               {"case_id": "date", "document_id": "invented", "answers": [], "is_impossible": True}]}
        rows, _ = boundaries.execute(self.inputs, self.protocol, self.old, self.settings, lambda _: None)
        for row in rows:
            if row["case_id"] == "law" and row["config_id"] == boundaries.CONFIG_IDS[1]:
                row.update(status="error", error_type="ValueError", chunks=[])
        results, scores = budget.evaluate(self.inputs, refs, rows, self.protocol["configurations"])
        paired, cases = boundaries.paired_changes(scores, *boundaries.CONFIG_IDS[:2])
        self.assertEqual(paired["positive_tasks"], 1)
        self.assertEqual(paired["fully_covered_lost"], 1)
        self.assertEqual(cases[0]["after"], 0)
        self.assertFalse(boundaries.decision(results, paired)["qualifies_for_future_frozen_evaluation"])

    def test_strict_guardrails_cannot_pass_only_on_mean_recall(self):
        results = {c: {"overall": {"all_gold_covered_rate": .8, "macro_gold_character_recall": .9,
                                   "failed_queries": 0, "missing_queries": 0}} for c in boundaries.CONFIG_IDS}
        results[boundaries.CONFIG_IDS[1]]["overall"].update(all_gold_covered_rate=.85, macro_gold_character_recall=.95)
        self.assertTrue(boundaries.decision(results, {"fully_covered_lost": 0})["qualifies_for_future_frozen_evaluation"])
        self.assertFalse(boundaries.decision(results, {"fully_covered_lost": 1})["qualifies_for_future_frozen_evaluation"])
        results[boundaries.CONFIG_IDS[2]]["overall"]["failed_queries"] = 1
        self.assertFalse(boundaries.decision(results, {"fully_covered_lost": 0})["qualifies_for_future_frozen_evaluation"])


if __name__ == "__main__":
    unittest.main()
