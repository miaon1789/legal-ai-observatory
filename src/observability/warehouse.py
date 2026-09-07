"""Small, restartable JSONL-to-SQL bridge using the existing Docker SQL Server."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .benchmark import ROOT
from .events import canonical, identifier, read_events, validate

MIGRATION = ROOT / "sql/06_observability/01_observed_runs.sql"
VIEWS = {"requests": "vw_requests", "attempts": "vw_attempts", "comparison": "vw_config_comparison",
         "alerts": "alert_state", "transitions": "alert_transition",
         "ingestion": "vw_ingestion_health"}


class WarehouseError(RuntimeError):
    pass


def literal(value: str) -> str:
    return "N'" + value.replace("'", "''") + "'"


class Warehouse:
    def __init__(self, database: str = "LegalAIObservatory", container: str | None = None):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", database):
            raise ValueError("Invalid database identifier")
        self.database = database
        self.container = container or os.environ.get("CONTAINER", "mssql-legalai")
        identifier(self.container, "container")

    def execute(self, sql: str) -> str:
        try:
            result = subprocess.run(
                ["docker", "exec", "-i", self.container, "sh", "-c",
                 'export SQLCMDPASSWORD="$MSSQL_SA_PASSWORD"; '
                 'exec /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -C '
                 '-b -r 1 -y 0 -w 65535 -t 30 -d "$1"', "sh", self.database],
                input=("SET NOCOUNT ON; SET QUOTED_IDENTIFIER ON; SET ANSI_NULLS ON; "
                       "SET ANSI_PADDING ON; SET ANSI_WARNINGS ON; SET ARITHABORT ON; "
                       "SET CONCAT_NULL_YIELDS_NULL ON; SET NUMERIC_ROUNDABORT OFF;\nGO\n" + sql),
                text=True, capture_output=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WarehouseError("SQL transport unavailable; raw JSONL is retained for replay") from exc
        if result.returncode:
            # SQL errors may echo data. Expose only server error numbers, never SQL text.
            codes = re.findall(r"Msg (\d+)", result.stdout + result.stderr)
            raise WarehouseError("SQL operation failed" + (" (" + ",".join(codes) + ")" if codes else "")
                                 + "; check Docker and SQL Server; raw JSONL is retained")
        return result.stdout.strip()

    def deploy(self) -> None:
        self.execute(MIGRATION.read_text())

    def ingest(self, events: list[dict]) -> dict:
        for event in events:
            validate(event)
        # One transaction for a pilot-sized file: no partially accepted batch.
        payload = canonical(events)
        # sqlcmd can insert newlines into long input lines, corrupting JSON strings.
        # Assemble bounded string fragments server-side, still in one transaction.
        fragments = ["DECLARE @payload NVARCHAR(MAX)=N'';"]
        fragments.extend("SET @payload+=" + literal(payload[i:i + 1500]) + ";"
                         for i in range(0, len(payload), 1500))
        fragments.append("EXEC telemetry.usp_ingest_events @events=@payload;")
        return json.loads(self.execute("\n".join(fragments)))

    def record_client_failure(self, code: str) -> None:
        identifier(code, "error_code", 64)
        self.execute("INSERT telemetry.ingestion_run "
                     "(ingestion_id,started_at,finished_at,status,event_count,inserted_count,error_code) "
                     "VALUES(NEWID(),SYSDATETIMEOFFSET(),SYSDATETIMEOFFSET(),'failed',0,0,"
                     + literal(code) + ");")

    def ingest_file(self, path: Path) -> dict:
        try:
            events, deferred = read_events(path)
        except (ValueError, OSError):
            try:
                self.record_client_failure("InvalidOrUnreadableJSONL")
            except WarehouseError:
                pass
            raise
        if deferred:
            # A torn write must not mark the pipeline healthy or import a partial file.
            self.record_client_failure("IncompleteJSONLTail")
            raise ValueError("Incomplete final JSONL line; file deferred without importing events")
        result = self.ingest(events)
        result["deferred_tail"] = False
        return result

    def rows(self, name: str) -> list[dict]:
        if name not in VIEWS:
            raise ValueError("Unknown export view")
        # One JSON object per SQL row avoids truncating a large FOR JSON document.
        output = self.execute("SELECT (SELECT v.* FOR JSON PATH, INCLUDE_NULL_VALUES, "
                              "WITHOUT_ARRAY_WRAPPER) FROM telemetry." + VIEWS[name] + " v;")
        return [json.loads(line) for line in output.splitlines() if line.strip()]

    def monitor(self, source: str, origin: str, kind: str, *, as_at: str | None = None,
                stale_minutes: int = 10) -> list[dict]:
        from .events import KINDS, ORIGINS, parse_time

        identifier(source, "source_id", 64)
        if origin not in ORIGINS or kind not in KINDS or not 1 <= stale_minutes <= 1440:
            raise ValueError("Invalid monitor scope or threshold")
        if as_at:
            parse_time(as_at)
        return json.loads(self.execute(
            "EXEC telemetry.usp_check_source @source_id=" + literal(source)
            + ",@data_origin=" + literal(origin) + ",@run_kind=" + literal(kind)
            + ",@as_at=" + (literal(as_at) if as_at else "NULL")
            + ",@stale_minutes=" + str(stale_minutes) + ";"))
