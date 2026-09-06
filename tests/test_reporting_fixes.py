"""Exercise the actual SQL views in a disposable database on the local container.

Run: .venv/bin/python -m unittest discover -s tests -v
Requires the running mssql-legalai container; does not rebuild project data.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
RULE = "Monthly cost 30% above trailing 3-month mean"


class ReportingFixTests(unittest.TestCase):
    @classmethod
    def sql(cls, statement: str, database: str | None = None) -> str:
        result = subprocess.run(
            ["docker", "exec", "-i", os.environ.get("CONTAINER", "mssql-legalai"),
             "sh", "-c",
             'export SQLCMDPASSWORD="$MSSQL_SA_PASSWORD"; '
             'exec /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C '
             '-b -r 1 -h -1 -W -s "|" -t 30 -d "$1"',
             "sh", database or cls.database],
            input="SET NOCOUNT ON;\nGO\n" + statement,
            text=True, capture_output=True, timeout=45,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout.strip()

    @classmethod
    def setUpClass(cls):
        cls.database = "LegalAIReportingTest_" + uuid.uuid4().hex[:12]
        cls.sql(f"CREATE DATABASE [{cls.database}];", "master")
        cls.addClassCleanup(cls.drop_database)
        schema = (ROOT / "sql/01_schema/01_create_tables.sql").read_text()
        cls.sql(schema.replace("LegalAIObservatory", cls.database))
        for path in sorted((ROOT / "sql/03_views").glob("*.sql")):
            cls.sql(path.read_text().replace("USE LegalAIObservatory;",
                                            f"USE [{cls.database}];"))
        cls.sql((ROOT / "tests/fixtures/reporting_dimensions.sql").read_text())

    @classmethod
    def drop_database(cls):
        if not cls.database.startswith("LegalAIReportingTest_"):
            raise RuntimeError("Refusing to drop a non-test database")
        cls.sql(f"ALTER DATABASE [{cls.database}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;\n"
                f"DROP DATABASE [{cls.database}];", "master")

    def setUp(self):
        self.sql("""
DELETE FROM dbo.fact_time_entry;
DELETE FROM dbo.fact_ai_session;
DELETE FROM dbo.fact_seat_month;
DELETE FROM dbo.fact_pipeline_run;
UPDATE dbo.dim_alert_rule SET threshold_value = 0.30, comparison = '>';
INSERT dbo.fact_ai_session
SELECT v.id, 1, 1, 1, 1, v.date_id, d.[date], 1, 1, v.cost, 100, 'Success', 0, NULL, 'firm_chat_log'
FROM (VALUES (1,20260430,5000),(2,20260531,10),(3,20260630,10),
             (4,20260731,10),(5,20260829,10)) v(id,date_id,cost)
JOIN dbo.dim_date d ON d.date_id = v.date_id;
INSERT dbo.fact_seat_month
SELECT id,1,1,date_id,1,90,1
FROM (VALUES (1,20260401),(2,20260501),(3,20260601),
             (4,20260701),(5,20260801)) v(id,date_id);
INSERT dbo.fact_pipeline_run VALUES
    (1,'firm_chat_log','20260831',20260831,1,0,0,0,'Success',1);
""")

    def cost_status(self):
        row = self.sql("SELECT current_value,is_breached,affected_count "
                       f"FROM dbo.vw_alert_status WHERE rule_name = '{RULE}';")
        return tuple(None if v == "NULL" else float(v) for v in row.split("|"))

    def test_historical_spike_does_not_keep_alert_open(self):
        self.assertEqual(self.cost_status(), (0, 0, 0))

    def test_licence_increase_triggers_and_recovers(self):
        self.sql("UPDATE dbo.fact_seat_month SET licence_cost_aud = 190 WHERE date_id = 20260801;")
        self.assertEqual(self.cost_status(), (1, 1, 1))
        self.sql("UPDATE dbo.fact_seat_month SET licence_cost_aud = 90 WHERE date_id = 20260801;")
        self.assertEqual(self.cost_status(), (0, 0, 0))

    def test_incomplete_month_is_excluded(self):
        self.sql("""
UPDATE dbo.fact_pipeline_run SET run_date = '20260815', date_id = 20260815;
UPDATE dbo.fact_ai_session SET cost_aud = 10 WHERE date_id = 20260430;
UPDATE dbo.fact_seat_month SET licence_cost_aud = 9999 WHERE date_id = 20260801;
""")
        self.assertEqual(self.cost_status(), (0, 0, 0))

    def test_missing_calendar_month_is_unevaluated(self):
        self.sql("""
DELETE FROM dbo.fact_ai_session WHERE date_id = 20260731;
DELETE FROM dbo.fact_seat_month WHERE date_id = 20260701;
""")
        self.assertEqual(self.cost_status(), (None, None, None))

    def test_zero_baseline_is_unevaluated(self):
        self.sql("""
UPDATE dbo.fact_ai_session SET cost_aud = 0 WHERE date_id < 20260801;
UPDATE dbo.fact_seat_month SET licence_cost_aud = 0 WHERE date_id < 20260801;
""")
        self.assertEqual(self.cost_status(), (None, None, None))

    def test_licence_month_without_sessions_still_counts(self):
        self.sql("""
DELETE FROM dbo.fact_ai_session WHERE date_id = 20260731;
UPDATE dbo.fact_seat_month SET licence_cost_aud = 100 WHERE date_id = 20260701;
""")
        self.assertEqual(self.cost_status(), (0, 0, 0))

    def test_no_watermark_is_unevaluated(self):
        self.sql("DELETE FROM dbo.fact_pipeline_run;")
        self.assertEqual(self.cost_status(), (None, None, None))

    def test_threshold_and_operator_are_read_from_rule(self):
        self.sql("UPDATE dbo.fact_seat_month SET licence_cost_aud = 120 WHERE date_id = 20260801;")
        self.assertEqual(self.cost_status(), (0.3, 0, 0))
        self.sql("UPDATE dbo.dim_alert_rule SET comparison = '>=';")
        self.assertEqual(self.cost_status(), (0.3, 1, 1))
        self.sql("UPDATE dbo.dim_alert_rule SET threshold_value = 0.5;")
        self.assertEqual(self.cost_status(), (0.3, 0, 0))

    def test_revenue_excludes_nonbillable_and_pre_adoption_hours(self):
        self.sql("""
INSERT dbo.fact_time_entry VALUES
 (1,1,1,20260801,90,1,'test',1),
 (2,1,1,20260801,90,0,'test',1),
 (3,1,1,20260801,900,1,'test',0),
 (4,2,1,20260801,90,1,'test',1),
 (5,2,1,20260801,90,0,'test',1),
 (6,1,2,20260801,90,1,'test',1);
""")
        row = self.sql("SELECT SUM(hourly_revenue_exposure_aud), "
                       "SUM(notional_capacity_released_aud), SUM(post_adoption_billable_hours) "
                       "FROM dbo.vw_billable_impact WHERE fee_arrangement = 'Hourly';")
        revenue, capacity, billable = map(float, row.split("|"))
        self.assertAlmostEqual(revenue, 3000, delta=0.05)
        self.assertAlmostEqual(capacity, 6000, delta=0.05)
        self.assertEqual(billable, 180)
        self.sql("UPDATE dbo.fact_time_entry SET hours = 990 WHERE billable = 0;")
        row = self.sql("SELECT SUM(hourly_revenue_exposure_aud) FROM dbo.vw_billable_impact;")
        self.assertAlmostEqual(float(row), 3000, delta=0.05)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM dbo.vw_billable_impact "
                                  "WHERE fee_arrangement = 'Fixed Fee' "
                                  "AND hourly_revenue_exposure_aud IS NOT NULL;"), "0")


if __name__ == "__main__":
    unittest.main()
