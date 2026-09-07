import copy
import json
import os
import unittest
import uuid

from test_cuad_warehouse import packet
from cuad_warehouse import EvaluationWarehouse
from observability.events import canonical
from observability.warehouse import Warehouse, WarehouseError, literal


@unittest.skipUnless(os.environ.get("RUN_SQL_TESTS") == "1", "Requires disposable SQL database")
class EvaluationSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = "LegalAIEvaluationTest_" + uuid.uuid4().hex[:10]
        cls.admin = Warehouse("master")
        cls.admin.execute(f"CREATE DATABASE [{cls.database}];")
        cls.addClassCleanup(cls.cleanup)
        cls.db = EvaluationWarehouse(cls.database)
        cls.db.deploy()
        cls.db.deploy()

    @classmethod
    def cleanup(cls):
        if not cls.database.startswith("LegalAIEvaluationTest_"):
            raise ValueError("non_test_database")
        cls.admin.execute(f"ALTER DATABASE [{cls.database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;\n"
                          f"DROP DATABASE [{cls.database}];")

    def test_idempotent_import_and_every_row_reconciles(self):
        value = packet()
        self.assertEqual(self.db.ingest(value)["inserted_rows"], 4)
        self.assertEqual(self.db.ingest(value)["inserted_rows"], 0)
        self.assertEqual(self.db.verify(value)["verified_rows"], 4)

    def test_conflict_does_not_replace_earlier_run(self):
        value = packet()
        self.db.ingest(value)
        changed = copy.deepcopy(value)
        changed["cases"][0]["retrieval_latency_ms"] = 2.0
        with self.assertRaises(WarehouseError):
            self.db.ingest(changed)
        self.db.verify(value)

    def test_missing_and_error_denominators_and_nulls(self):
        value = packet()
        value["cases"][0].update(response_state="error", gold_character_recall=0.0, any_gold_hit=0,
                                 all_gold_covered=0, context_precision=0.0, retrieved_characters=0)
        value["cases"][2].update(response_state="missing", retrieved_characters=0, retrieval_latency_ms=None)
        self.db.ingest(value)
        rows = {r["config_id"]: r for r in self.db.verify(value)["summary"]}
        self.assertEqual(rows["a"]["questions"], 2)
        self.assertEqual(rows["a"]["answerable"], 1)
        self.assertEqual(rows["a"]["failed_queries"], 1)
        self.assertEqual(rows["a"]["missing_queries"], 1)
        self.assertEqual(rows["a"]["macro_gold_character_recall"], 0)
        self.assertEqual(rows["b"]["macro_gold_character_recall"], .5)

    def test_long_json_transport_and_isolated_runs(self):
        value = packet()
        prototype = copy.deepcopy(value["cases"])
        value["cases"] = [row | {"case_id": row["case_id"] + str(i)} for i in range(100) for row in prototype]
        value["run"]["expected_rows"] = len(value["cases"])
        self.assertEqual(self.db.ingest(value)["inserted_rows"], 400)
        self.assertEqual(self.db.verify(value)["verified_rows"], 400)
        other = packet()
        self.db.ingest(other)
        self.assertEqual(self.db.verify(other)["verified_rows"], 4)

    def test_sql_transaction_rolls_back_invalid_budget_even_without_python(self):
        value = packet()
        value["cases"][0]["retrieved_characters"] = 9
        with self.assertRaises(WarehouseError):
            self.db.transport.execute("EXEC evaluation.usp_ingest @packet=" + literal(canonical(value)) + ";")
        rows = self.db.transport.execute("SELECT COUNT(*) AS n FROM evaluation.experiment_run WHERE run_id="
                                         + literal(value["run"]["run_id"]) + " FOR JSON PATH;")
        self.assertEqual(json.loads(rows)[0]["n"], 0)

    def test_negative_only_subset_average_is_null(self):
        value = packet()
        value["cases"] = [r for r in value["cases"] if not r["is_answerable"]]
        value["run"]["expected_rows"] = 2
        self.db.ingest(value)
        self.assertTrue(all(r["macro_gold_character_recall"] is None for r in self.db.verify(value)["summary"]))


if __name__ == "__main__":
    unittest.main()
