import ast
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import export_alert_rules as catalogue


def results():
    return pd.DataFrame([
        {"screen": key, "list_size": 20, "true_positives": hits,
         "precision": hits / 20, "recall_of_cohort": hits / 16}
        for key, hits in zip(catalogue.SCREEN_NAMES, [13, 2, 4, 2])
    ])


class AlertCatalogueTests(unittest.TestCase):
    def test_current_top20_metrics(self):
        text = catalogue.screening_reference(results())
        self.assertIn("Mandatory review completion | 13/20 | 65% | 81%", text)
        self.assertIn("Restricted-tier session rate | 2/20 | 10% | 13%", text)
        self.assertIn("not from a separate test sample", text)

    def test_metrics_come_from_input(self):
        changed = results()
        changed.loc[0, ["true_positives", "precision"]] = [12, 0.6]
        text = catalogue.screening_reference(changed)
        self.assertIn("12/20 | 60%", text)
        self.assertNotIn("65%", text)

    def test_other_list_sizes_are_not_mixed(self):
        other = results().assign(list_size=10)
        self.assertEqual(catalogue.screening_reference(results()),
                         catalogue.screening_reference(pd.concat([results(), other])))

    def test_missing_or_duplicate_screen_rejected(self):
        for bad in [results().iloc[1:], pd.concat([results(), results().iloc[:1]])]:
            with self.subTest(rows=len(bad)), self.assertRaises(ValueError):
                catalogue.screening_reference(bad)

    def test_invalid_metrics_rejected(self):
        for column, value in [("precision", 0.55), ("precision", float("nan")),
                              ("recall_of_cohort", float("nan")),
                              ("true_positives", 13.5)]:
            bad = results()
            bad[column] = bad[column].astype(float)
            bad.loc[0, column] = value
            with self.subTest(column=column), self.assertRaises(ValueError):
                catalogue.screening_reference(bad)

    def test_generator_has_no_frozen_screening_percentages(self):
        tree = ast.parse((ROOT / "src/generate.py").read_text())
        rules = next(ast.literal_eval(node.value) for node in tree.body
                     if isinstance(node, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "ALERT_RULES"
                             for t in node.targets))
        for rule in rules[-2:]:
            self.assertIn("results/screening_comparison.csv", rule[-1])
            for stale in ["55%", "15%", "held-out", "no computable rate"]:
                self.assertNotIn(stale, rule[-1])

    def test_sync_sql_only_updates_rationale(self):
        rules = pd.DataFrame([
            {"rule_id": n, "rule_name": f"Rule {n}", "rationale": "Lawyer's review"}
            for n in (15, 16)
        ])
        text = catalogue.render_rule_sync(rules)
        self.assertIn("Lawyer''s review", text)
        self.assertIn("UPDATE r SET rationale = n.rationale", text)
        self.assertIn("BEGIN TRANSACTION", text)
        self.assertIn("ROLLBACK TRANSACTION", text)
        self.assertNotIn("SET threshold", text)

    def test_sync_sql_rejects_missing_rule(self):
        with self.assertRaises(ValueError):
            catalogue.render_rule_sync(pd.DataFrame([
                {"rule_id": 15, "rule_name": "Rule 15", "rationale": "Review"}
            ]))


if __name__ == "__main__":
    unittest.main()
