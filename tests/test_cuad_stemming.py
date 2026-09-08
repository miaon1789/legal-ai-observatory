"""English normalization and experiment isolation, using invented documents only."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import cuad_stemming_experiment as stemming
import cuad_budget_experiment as budget
from observability import retrieval


class StemmingTests(unittest.TestCase):
    def setUp(self):
        self.protocol = stemming.load_protocol()
        self.protocol["character_budget"] = 80
        self.old = budget.load_protocol()
        self.old["character_budget"] = 80
        self.config = next(c for c in self.old["configurations"] if c["config_id"] == self.protocol["parent_candidate_id"])
        self.config.update(words=8, overlap_words=2, preamble_characters=16)
        self.settings = {"k1": 1.5, "b": .75, "epsilon": .25}
        self.text = "Opening sentence. Audited records are retained. No assignment is permitted. Other words. " * 3
        self.inputs = {"documents": [{"document_id": "invented", "partition": "development", "length_quartile": 1, "text": self.text}],
                       "tasks": [{"case_id": "audit", "document_id": "invented", "category": "Audit Rights", "query": "audit records"},
                                 {"case_id": "date", "document_id": "invented", "category": "Effective Date", "query": "effective date"}]}

    def test_expected_morphological_groups_and_unchanged_negation_numbers(self):
        index = stemming.StemmedScores([["audit"], ["record"], ["other"]], self.settings)
        for group in (["audit", "audited", "audits"], ["assign", "assignment", "assigned", "assigning"],
                      ["expire", "expires", "expiration"]):
            self.assertEqual(len(set(index.stemmer.stemWords(group))), 1)
        self.assertEqual(index.stemmer.stemWords(["not", "no", "2026", "1433"]), ["not", "no", "2026", "1433"])

    def test_query_and_document_are_normalized_once_consistently(self):
        scores = stemming.StemmedScores([["audited", "records"], ["something"], ["unrelated"]], self.settings)
        self.assertIn("audit", scores.bm25.idf)
        self.assertNotIn("audited", scores.bm25.idf)
        self.assertEqual(scores.get_scores(["audit"]).tolist(), scores.get_scores(["audited"]).tolist())

    def test_query_order_and_collapsed_multiplicity_preserved(self):
        scores = stemming.StemmedScores([["audit"], ["other"]], self.settings)
        with patch.object(scores.bm25, "get_scores", return_value=[1]) as query:
            scores.get_scores(["audit", "audited", "audits", "records"])
        self.assertEqual(query.call_args.args[0], ["audit", "audit", "audit", "record"])

    def test_raw_text_window_positions_and_unicode_preserved(self):
        text = "Caf\u00e9 reports were audited.\n\n" + self.text
        chunking = {k: self.config[k] for k in ("words", "overlap_words")}
        raw = retrieval.Retriever(text, chunking, self.settings)
        stem = stemming.StemmedRetriever(text, chunking, self.settings)
        self.assertEqual(raw.chunks, stem.chunks)
        self.assertIn("audited", text[stem.chunks[0]["start"]:stem.chunks[0]["end"]])

    def test_both_arms_exact_budget_and_header_seed_unchanged(self):
        emitted = []
        rows, preprocessing = stemming.execute(self.inputs, self.protocol, self.old, self.settings, emitted.append)
        self.assertEqual(rows, emitted)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(preprocessing), 2)
        for row in rows:
            self.assertEqual(row["status"], "ok")
            self.assertEqual(stemming.shared.context_size(row["chunks"]), 80)
            if row["category"] == "Effective Date":
                self.assertEqual(row["chunks"][0], {"start": 0, "end": 16, "score": None})

    def test_short_source_not_padded(self):
        inputs = copy.deepcopy(self.inputs)
        inputs["documents"][0]["text"] = "Audited records."
        rows, _ = stemming.execute(inputs, self.protocol, self.old, self.settings, lambda _: None)
        self.assertTrue(all(stemming.shared.context_size(r["chunks"]) == 16 for r in rows))

    def test_gold_or_evaluation_inputs_rejected(self):
        for mode in ("gold", "evaluation"):
            inputs = copy.deepcopy(self.inputs)
            if mode == "gold":
                inputs["tasks"][0]["is_impossible"] = True
            else:
                inputs["documents"][0]["partition"] = "evaluation"
            with self.assertRaises(ValueError):
                stemming.execute(inputs, self.protocol, self.old, self.settings, lambda _: None)

    def test_candidate_index_failure_keeps_baseline_and_all_attempts(self):
        with patch.object(stemming, "StemmedRetriever", side_effect=RuntimeError("do not leak source")):
            rows, _ = stemming.execute(self.inputs, self.protocol, self.old, self.settings, lambda _: None)
        self.assertEqual(len(rows), 4)
        for row in rows:
            if row["config_id"] == stemming.CONFIG_IDS[1]:
                self.assertEqual(row["status"], "error")
                self.assertEqual(row["error_type"], "RuntimeError")
                self.assertEqual(row["chunks"], [])
            else:
                self.assertEqual(row["status"], "ok")

    def test_candidate_errors_count_as_zero_for_positive_not_for_negative(self):
        refs = {"references": [{"case_id": "audit", "document_id": "invented", "is_impossible": False,
                                "answers": [{"answer_start": 18, "text": "Audited records"}]},
                               {"case_id": "date", "document_id": "invented", "is_impossible": True, "answers": []}]}
        with patch.object(stemming, "StemmedRetriever", side_effect=ValueError("fixture failure")):
            rows, _ = stemming.execute(self.inputs, self.protocol, self.old, self.settings, lambda _: None)
        result, scores = budget.evaluate(self.inputs, refs, rows, self.protocol["configurations"])
        candidate = result[stemming.CONFIG_IDS[1]]["overall"]
        self.assertEqual(candidate["failed_queries"], 2)
        self.assertEqual(candidate["macro_gold_character_recall"], 0)
        self.assertTrue(all(r["gold_character_recall"] is None for r in scores if r["is_impossible"]))

    def test_repeated_execution_has_identical_contexts(self):
        first, _ = stemming.execute(self.inputs, self.protocol, self.old, self.settings, lambda _: None)
        second, _ = stemming.execute(self.inputs, self.protocol, self.old, self.settings, lambda _: None)
        self.assertEqual([r["chunks"] for r in first], [r["chunks"] for r in second])

    def test_protocol_version_is_pinned(self):
        with patch.object(stemming.importlib.metadata, "version", return_value="wrong"):
            with self.assertRaisesRegex(ValueError, "unsupported_stemming_protocol"):
                stemming.load_protocol()

    def test_guardrails_require_no_lost_complete_cases_and_no_errors(self):
        results = {c: {"overall": {"all_gold_covered_rate": .8, "macro_gold_character_recall": .9,
                                   "failed_queries": 0, "missing_queries": 0}} for c in stemming.CONFIG_IDS}
        results[stemming.CONFIG_IDS[1]]["overall"].update(all_gold_covered_rate=.85, macro_gold_character_recall=.95)
        self.assertTrue(stemming.decision(results, {"fully_covered_lost": 0})["qualifies_for_future_frozen_evaluation"])
        self.assertFalse(stemming.decision(results, {"fully_covered_lost": 1})["qualifies_for_future_frozen_evaluation"])
        results[stemming.CONFIG_IDS[1]]["overall"]["missing_queries"] = 1
        self.assertFalse(stemming.decision(results, {"fully_covered_lost": 0})["qualifies_for_future_frozen_evaluation"])


if __name__ == "__main__":
    unittest.main()
