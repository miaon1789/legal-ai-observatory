"""Validated numeric CUAD results -> additive SQL evaluation schema, never source text."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re

import cuad_experiment as base
import cuad_intake as intake
from observability.events import canonical, digest, identifier, parse_time
from observability.warehouse import Warehouse, literal

ROOT = base.ROOT
MIGRATION = ROOT / "sql/07_evaluation/01_retrieval_results.sql"
CASE_FIELDS = {"case_id", "config_id", "document_id", "dataset_partition", "category", "length_quartile",
               "document_characters", "response_state", "is_answerable", "gold_character_recall", "any_gold_hit",
               "all_gold_covered", "context_precision", "retrieved_characters", "retrieval_latency_ms"}
RUN_FIELDS = {"run_id", "run_label", "phase", "protocol_sha256", "started_at", "case_origin",
              "execution_origin", "character_budget", "expected_rows"}


def validate(packet: dict) -> None:
    if set(packet) != {"format", "run", "configurations", "cases"} or packet["format"] != "cuad-sql-packet-v1":
        raise ValueError("invalid_packet_fields")
    run = packet["run"]
    if set(run) != RUN_FIELDS or not re.fullmatch(r"[0-9a-f]{32}", run["run_id"]):
        raise ValueError("invalid_run")
    labels = {"baseline": "CUAD v1 | Top 3 vs Top 5", "development": "CUAD v2 | development",
              "fresh_evaluation": "CUAD v2 | fresh evaluation"}
    if (run["phase"] not in labels or run["run_label"] != labels[run["phase"]]
            or run["case_origin"] != "public_real_contract" or run["execution_origin"] != "local_algorithm"
            or not re.fullmatch(r"[0-9a-f]{64}", run["protocol_sha256"])):
        raise ValueError("invalid_provenance")
    parse_time(run["started_at"])
    budget = run["character_budget"]
    if budget is not None and (type(budget) is not int or not 1 <= budget <= 20_000):
        raise ValueError("invalid_budget")
    if type(run["expected_rows"]) is not int or not 1 <= run["expected_rows"] == len(packet["cases"]) <= 5000:
        raise ValueError("row_count_mismatch")
    configs = {}
    for c in packet["configurations"]:
        if (set(c) != {"config_id", "config_role"} or c["config_id"] in configs
                or c["config_role"] not in {"baseline", "candidate", "development_only"}):
            raise ValueError("invalid_configuration")
        identifier(c["config_id"], "config_id", 64)
        configs[c["config_id"]] = c["config_role"]
    if not 2 <= len(configs) <= 3 or list(configs.values()).count("baseline") != 1 or list(configs.values()).count("candidate") != 1:
        raise ValueError("missing_paired_configuration")
    categories = {c["category"] for c in base.read_json(base.PROTOCOL)["categories"]}
    keys, tasks = set(), {}
    for row in packet["cases"]:
        if set(row) != CASE_FIELDS:
            raise ValueError("unexpected_case_fields")
        for key, limit in (("case_id", 100), ("config_id", 64), ("document_id", 32)):
            identifier(row[key], key, limit)
        key = row["case_id"], row["config_id"]
        if key in keys or row["config_id"] not in configs:
            raise ValueError("duplicate_or_unknown_case")
        keys.add(key)
        if (row["dataset_partition"] not in {"development", "evaluation"} or row["category"] not in categories
                or type(row["length_quartile"]) is not int or not 1 <= row["length_quartile"] <= 4
                or type(row["document_characters"]) is not int or row["document_characters"] <= 0
                or row["response_state"] not in {"ok", "error", "missing"}
                or type(row["is_answerable"]) is not int or row["is_answerable"] not in (0, 1)):
            raise ValueError("invalid_case_metadata")
        if (run["phase"] == "development" and row["dataset_partition"] != "development") or (
                run["phase"] == "fresh_evaluation" and row["dataset_partition"] != "evaluation"):
            raise ValueError("phase_partition_mismatch")
        identity = tuple(row[k] for k in ("document_id", "dataset_partition", "category", "length_quartile",
                                          "document_characters", "is_answerable"))
        if row["case_id"] in tasks and tasks[row["case_id"]] != identity:
            raise ValueError("case_identity_changed_between_configs")
        tasks[row["case_id"]] = identity
        for field in ("gold_character_recall", "context_precision", "retrieval_latency_ms"):
            value = row[field]
            if value is not None and (type(value) not in (float, int) or not math.isfinite(value) or value < 0):
                raise ValueError("invalid_numeric_value")
        gold = [row[k] for k in ("gold_character_recall", "context_precision", "any_gold_hit", "all_gold_covered")]
        if row["is_answerable"]:
            if (any(v is None for v in gold) or not 0 <= gold[0] <= 1 or not 0 <= gold[1] <= 1
                    or any(type(row[k]) is not int or row[k] not in (0, 1) for k in ("any_gold_hit", "all_gold_covered"))
                    or row["any_gold_hit"] != int(gold[0] > 0) or row["all_gold_covered"] != int(gold[0] == 1)):
                raise ValueError("invalid_positive_metrics")
        elif any(v is not None for v in gold):
            raise ValueError("negative_metrics_must_be_null")
        count = row["retrieved_characters"]
        if type(count) is not int or not 0 <= count <= row["document_characters"]:
            raise ValueError("invalid_context_size")
        if row["response_state"] == "ok":
            if row["retrieval_latency_ms"] is None or (budget is not None and count != min(budget, row["document_characters"])):
                raise ValueError("budget_or_timing_mismatch")
        elif count or (row["is_answerable"] and any(gold)):
            raise ValueError("failure_cannot_have_success_metrics")
    if keys != {(case, config) for case in tasks for config in configs}:
        raise ValueError("unpaired_tasks")


def prepare(run_directory: Path) -> tuple[Path, dict]:
    summary = base.read_json(run_directory / "summary.json", private=True)
    scores = base.read_json(run_directory / "case_scores.json", private=True)["cases"]
    if summary["format"] == "cuad-retrieval-results-v1":
        record = summary["execution"]
        phase, baseline, candidate = "baseline", "bm25-top3", "bm25-top5"
        directory = base.OUTPUT / "bundles" / record["bundle_id"]
        receipt = base.read_json(directory / "receipt.json", private=True)
        inputs = base.read_bundle_file(directory, "inputs.json", receipt)
        config_ids = [baseline, candidate]
        budget = None
    elif summary["format"] == "cuad-budget-results-v2":
        record, phase, baseline = summary, summary["phase"], "bm25-250-budget"
        inputs = base.read_json(run_directory / "inputs.json", private=True)
        if digest(inputs) != summary["inputs_sha256"] or digest(scores) != summary["case_scores_sha256"]:
            raise ValueError("run_files_changed")
        if phase == "development":
            selection = base.read_json(run_directory / "selection.json", private=True)
            if selection["summary_sha256"] != digest(summary):
                raise ValueError("selection_changed")
            candidate = selection["selected_candidate"]
        else:
            candidate = summary["provenance"]["selected_candidate"]
        config_ids = [c["config_id"] for c in summary["configurations"]]
        budget = summary["character_budget"]
    else:
        raise ValueError("unsupported_run_format")
    if record["status"] != "complete" or record["external_inference_calls"] != 0 or record["is_llm_evaluation"]:
        raise ValueError("not_a_completed_local_retrieval_run")
    docs = {d["document_id"]: d for d in inputs["documents"]}
    tasks = {t["case_id"]: t for t in inputs["tasks"]}
    packet = {"format": "cuad-sql-packet-v1", "run": {
        "run_id": record["run_id"], "run_label": {"baseline": "CUAD v1 | Top 3 vs Top 5",
            "development": "CUAD v2 | development", "fresh_evaluation": "CUAD v2 | fresh evaluation"}[phase],
        "phase": phase, "protocol_sha256": record["protocol_sha256"], "started_at": record["started_at"],
        "case_origin": record["case_origin"], "execution_origin": record["execution_origin"],
        "character_budget": budget, "expected_rows": len(tasks) * len(config_ids)},
        "configurations": [{"config_id": c, "config_role": "baseline" if c == baseline else
                             "candidate" if c == candidate else "development_only"} for c in config_ids], "cases": []}
    for row in scores:
        task = tasks[row["case_id"]]
        doc = docs[task["document_id"]]
        if (row["document_id"] != doc["document_id"] or row["category"] != task["category"]
                or row["partition"] != doc["partition"] or row["length_quartile"] != doc["length_quartile"]
                or type(row["is_impossible"]) is not bool
                or row["answer_accuracy"] is not None or row["abstention_accuracy"] is not None):
            raise ValueError("score_identity_or_semantics_mismatch")
        packet["cases"].append({
            "case_id": row["case_id"], "config_id": row["config_id"], "document_id": row["document_id"],
            "dataset_partition": row["partition"], "category": row["category"],
            "length_quartile": row["length_quartile"], "document_characters": len(doc["text"]),
            "response_state": row["response_state"], "is_answerable": int(not row["is_impossible"]),
            "gold_character_recall": row["gold_character_recall"], "context_precision": row["context_precision"],
            "any_gold_hit": None if row["any_gold_hit"] is None else int(row["any_gold_hit"]),
            "all_gold_covered": None if row["all_gold_covered"] is None else int(row["all_gold_covered"]),
            "retrieved_characters": row["retrieved_characters"], "retrieval_latency_ms": row["retrieval_latency_ms"]})
    packet["cases"].sort(key=lambda r: (r["case_id"], r["config_id"]))
    validate(packet)
    output = ROOT / "private/cuad_warehouse" / (record["run_id"] + ".json")
    if output.exists() and digest(base.read_json(output, private=True)) != digest(packet):
        raise ValueError("prepared_packet_conflict")
    intake.write_json(output, packet)
    return output, packet


class EvaluationWarehouse:
    def __init__(self, database="LegalAIObservatory"):
        self.transport = Warehouse(database)

    def deploy(self):
        self.transport.execute(MIGRATION.read_text())

    def ingest(self, packet):
        validate(packet)
        payload = canonical(packet)
        sql = ["DECLARE @packet NVARCHAR(MAX)=N'';"]
        sql += ["SET @packet+=" + literal(payload[i:i+1500]) + ";" for i in range(0, len(payload), 1500)]
        sql.append("EXEC evaluation.usp_ingest @packet=@packet;")
        return json.loads(self.transport.execute("\n".join(sql)))

    def rows(self, view: str, run_id: str):
        if view not in {"vw_case_results", "vw_config_summary"} or not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("invalid_export_scope")
        output = self.transport.execute("SELECT (SELECT v.* FOR JSON PATH,INCLUDE_NULL_VALUES,WITHOUT_ARRAY_WRAPPER) "
            "FROM evaluation." + view + " v WHERE run_id=" + literal(run_id) + ";")
        return [json.loads(line) for line in output.splitlines() if line.strip()]

    def verify(self, packet):
        validate(packet)
        rows = self.rows("vw_case_results", packet["run"]["run_id"])
        actual = {(r["case_id"], r["config_id"]): r for r in rows}
        if len(rows) != len(packet["cases"]) or len(actual) != len(rows):
            raise ValueError("sql_row_count_mismatch")
        for expected in packet["cases"]:
            got = actual[(expected["case_id"], expected["config_id"])]
            for key, value in expected.items():
                if type(value) is float:
                    if got[key] is None or not math.isclose(value, got[key], rel_tol=1e-10, abs_tol=1e-10):
                        raise ValueError("sql_numeric_mismatch")
                elif got[key] != value:
                    raise ValueError("sql_value_mismatch")
        return {"run_id": packet["run"]["run_id"], "verified_rows": len(rows),
                "summary": self.rows("vw_config_summary", packet["run"]["run_id"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="LegalAIObservatory")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("deploy")
    sub.add_parser("prepare").add_argument("--run", type=Path, required=True)
    for name in ("ingest", "verify"):
        sub.add_parser(name).add_argument("--packet", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        path, packet = prepare(args.run)
        result = {"packet": str(path), "rows": len(packet["cases"]), "run": packet["run"]}
    else:
        warehouse = EvaluationWarehouse(args.database)
        if args.command == "deploy":
            warehouse.deploy()
            result = {"schema": "evaluation", "status": "deployed"}
        else:
            packet = base.read_json(args.packet, private=True)
            result = warehouse.ingest(packet) if args.command == "ingest" else warehouse.verify(packet)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
