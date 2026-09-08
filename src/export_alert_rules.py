"""Render docs/ALERT_RULES.md from dim_alert_rule.

The catalogue is generated, not written. A hand-maintained copy of a table
drifts from it within a fortnight, and a governance document that disagrees with
the system it documents is worse than none.

Run:  .venv/bin/python src/export_alert_rules.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C

ROOT = Path(__file__).resolve().parent.parent
ORDER = ["Governance", "Operational", "Quality", "Cost"]

PREAMBLE = """# Alert rules

Generated from `dim_alert_rule` and `results/screening_comparison.csv` by
`src/export_alert_rules.py`. Edit the source data, not this file.

The catalogue defines conditions, thresholds, owners and reasons for follow-up.
It supports the synthetic operations report. It does not send notifications.

**Thresholds are data.** `vw_alert_status` computes a current value for each
rule and applies the `threshold_value` and `comparison` stored here, so changing
a threshold is a row update rather than an edit to SQL that hard-coded it. The
`rationale` travels with the breach.

Governance and operational rules use different thresholds because their risks
differ. These are scenario assumptions. A real deployment would need its own
policy owners to agree the thresholds and response process.
"""

BLURB = {
    "Governance": "Tight thresholds, set against the cost of the failure they "
                  "guard rather than against a service level.",
    "Operational": "Set at the point where a lawyer abandons the tool, or where "
                   "a feed has stopped telling the truth.",
    "Quality": "Aimed at the output rather than the platform: a tool can be fast, "
               "cheap and available while producing work nobody keeps.",
    "Cost": "Unit economics, not spend. Spend rises with adoption and that is "
            "the plan working.",
}


SCREEN_NAMES = {
    "Mandatory review completion (lowest first)": "Mandatory review completion",
    "Restricted-tier session rate (highest first)": "Restricted-tier session rate",
    "Restricted-tier session count (highest first)": "Restricted-tier session count",
    "Both: rank sum of the two above": "Rank sum of completion and exposure rate",
}


def screening_reference(results: pd.DataFrame) -> str:
    top = results[results["list_size"] == 20].set_index("screen")
    if not top.index.is_unique or set(top.index) != set(SCREEN_NAMES):
        raise ValueError("Expected one Top 20 result for each of the four screens")
    out = [
        "\n## Screening Reference\n\n",
        "Full synthetic window, March 2025 to August 2026. Each screen ranks the "
        "same eligible population with at least 25 mandatory outputs. Ground "
        "truth is withheld from the report, not from a separate test sample.\n\n",
        "| Screen, 20 names | True positives | Precision | Recall of full cohort |\n",
        "|---|---:|---:|---:|\n",
    ]
    for key, label in SCREEN_NAMES.items():
        row = top.loc[key]
        hits, precision, recall = (float(row[c]) for c in
                                   ("true_positives", "precision", "recall_of_cohort"))
        if (not all(math.isfinite(v) for v in (hits, precision, recall))
                or not hits.is_integer() or not 0 <= hits <= 20
                or abs(precision - hits / 20) > 1e-9 or not 0 <= recall <= 1):
            raise ValueError("Invalid Top 20 screening metrics")
        recall_percent = int(recall * 100 + 0.5)
        out.append(f"| {label} | {int(hits)}/20 | {precision:.0%} | {recall_percent}% |\n")
    out.append(
        "\nResults come from `results/screening_comparison.csv`. Recall is rounded "
        "to whole percentages here. These results do not describe the quarterly "
        "alert threshold or arbitrary report filters. Low-volume users are "
        "excluded by the minimum-volume rule, not because every such rate is "
        "mathematically undefined. The list supports review, not a verdict.\n"
    )
    return "".join(out)


def render(rules: pd.DataFrame, results: pd.DataFrame) -> str:
    out = [PREAMBLE]
    out.append(f"\n{len(rules)} rules: "
               + ", ".join(f"{n} {c.lower()}" for c, n in
                           rules["category"].value_counts().reindex(ORDER).items())
               + ".\n")
    out.append(screening_reference(results))

    for cat in ORDER:
        block = rules[rules["category"] == cat]
        if block.empty:
            continue
        out.append(f"\n## {cat}\n\n{BLURB[cat]}\n")
        for r in block.itertuples():
            out.append(f"\n### {r.rule_id}. {r.rule_name}\n")
            out.append(f"| | |\n|---|---|\n"
                       f"| **Condition** | `{r.condition_text}` |\n"
                       f"| **Fires when** | value `{r.comparison}` "
                       f"{r.threshold_value:g} |\n"
                       f"| **Severity** | {r.severity} |\n"
                       f"| **Owner** | {r.owner_role} |\n")
            out.append(f"\n{r.rationale}\n")

    return "".join(out)


def render_rule_sync(rules: pd.DataFrame) -> str:
    selected = rules[rules["rule_id"].isin([15, 16])].sort_values("rule_id")
    if selected["rule_id"].tolist() != [15, 16]:
        raise ValueError("Expected screening rules 15 and 16")
    def quote(value):
        return "N'" + str(value).replace("'", "''") + "'"
    values = ",\n".join(
        f"({r.rule_id}, {quote(r.rule_name)}, {quote(r.rationale)})"
        for r in selected.itertuples()
    )
    return """-- Generated by src/export_alert_rules.py from dim_alert_rule.csv.
-- Updates explanation text only. Thresholds and metrics are unchanged.
USE LegalAIObservatory;
GO
SET NOCOUNT ON;
SET XACT_ABORT ON;
DECLARE @rules TABLE (rule_id INT PRIMARY KEY, rule_name NVARCHAR(200), rationale NVARCHAR(MAX));
INSERT @rules VALUES
""" + values + """;
BEGIN TRY
    BEGIN TRANSACTION;
    IF (SELECT COUNT(*) FROM dbo.dim_alert_rule AS r JOIN @rules AS n
        ON r.rule_id = n.rule_id AND r.rule_name = n.rule_name) <> 2
        THROW 51000, 'Expected rule IDs and names were not found. No changes applied.', 1;
    UPDATE r SET rationale = n.rationale
    FROM dbo.dim_alert_rule AS r JOIN @rules AS n ON r.rule_id = n.rule_id
    WHERE r.rationale <> n.rationale OR r.rationale IS NULL;
    SELECT @@ROWCOUNT AS updated_rationales;
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
"""


def main() -> None:
    rules = pd.read_csv(ROOT / C.OUT_DIR / "dim_alert_rule.csv")
    results = pd.read_csv(ROOT / "results" / "screening_comparison.csv")
    path = ROOT / "docs" / "ALERT_RULES.md"
    path.write_text(render(rules, results), encoding="utf-8")
    (ROOT / "sql/05_validation/03_rule_catalogue_sync.sql").write_text(
        render_rule_sync(rules), encoding="utf-8")
    print(f"wrote {path}: {len(rules)} rules across "
          f"{rules['category'].nunique()} categories")


if __name__ == "__main__":
    main()
