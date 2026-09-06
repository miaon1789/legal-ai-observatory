"""Render docs/ALERT_RULES.md from dim_alert_rule.

The catalogue is generated, not written. A hand-maintained copy of a table
drifts from it within a fortnight, and a governance document that disagrees with
the system it documents is worse than none.

Run:  .venv/bin/python src/export_alert_rules.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C

ROOT = Path(__file__).resolve().parent.parent
ORDER = ["Governance", "Operational", "Quality", "Cost"]

PREAMBLE = """# Alert rules

Generated from `dim_alert_rule` by `src/export_alert_rules.py` — edit the rule
data, not this file.

This is a rule catalogue, not an alerting system. Building the delivery
machinery would have been the easy half and the less useful one: what a firm
lacks at this stage is not a way to send emails, it is agreement on what should
trigger one and why.

**Thresholds are data.** `vw_alert_status` computes a current value for each
rule and applies the `threshold_value` and `comparison` stored here, so changing
a threshold is a row update rather than an edit to SQL that hard-coded it. The
`rationale` travels with the breach.

**Governance thresholds are tighter, deliberately.** An operational rule
tolerates a few percent of failure because the cost of a failure is a lawyer's
wasted minute. A barrier-coverage rule does not, because the cost of a failure
is privileged information crossing an information barrier — not recoverable, and
reportable. A tolerance that is sensible for latency is negligent for a barrier.
Being able to hold both kinds of threshold in the same catalogue, and to say why
they differ, is the point of the document.
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


def main() -> None:
    rules = pd.read_csv(ROOT / C.OUT_DIR / "dim_alert_rule.csv")
    out = [PREAMBLE]
    out.append(f"\n{len(rules)} rules: "
               + ", ".join(f"{n} {c.lower()}" for c, n in
                           rules["category"].value_counts().reindex(ORDER).items())
               + ".\n")

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

    path = ROOT / "docs" / "ALERT_RULES.md"
    path.write_text("".join(out))
    print(f"wrote {path} — {len(rules)} rules across "
          f"{rules['category'].nunique()} categories")


if __name__ == "__main__":
    main()
