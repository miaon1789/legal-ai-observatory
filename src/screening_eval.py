"""Evaluate the Page 5 screening rules against held-out ground truth.

The non-compliant cohort is generated but never published: `policy_cohort` is
not a column in `dim_lawyer`, so the dashboard has to find these people from
behaviour. That same held-out labelling makes the screening rule itself
measurable -- which candidate signal actually recovers them, at what precision,
and how many escape any screen at all.

This is the evidence behind the claim that Page 5 produces "a short list for a
conversation, not a verdict": the list is about half true positives by design.

Run:  .venv/bin/python src/screening_eval.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C

ROOT = Path(__file__).resolve().parent.parent
RAW, GT, RES = ROOT / C.OUT_DIR, ROOT / C.GROUND_TRUTH_DIR, ROOT / "results"
MIN_MANDATORY = 25          # below this a coverage rate is noise
LIST_SIZES = [10, 20, 30, 50]


def build() -> tuple[pd.DataFrame, int, int]:
    S = pd.read_csv(RAW / "fact_ai_session.csv")
    O = pd.read_csv(RAW / "fact_ai_output.csv")
    M = pd.read_csv(RAW / "dim_matter.csv")
    T = pd.read_csv(RAW / "dim_task_type.csv")
    L = pd.read_csv(RAW / "dim_lawyer.csv")
    truth = pd.read_csv(GT / "_ground_truth_lawyer.csv")

    o = (O.merge(T[["task_type_id", "review_policy_base"]], on="task_type_id")
          .merge(M[["matter_id", "confidentiality_tier"]], on="matter_id"))
    o["mandatory"] = ((o["review_policy_base"] == "Mandatory")
                      | o["confidentiality_tier"].isin(["Restricted", "Barrier"])
                      | (o["is_client_facing"] == 1))
    cov = (o[o["mandatory"]].groupby("lawyer_id")
           .agg(coverage=("reviewed", "mean"), mandatory=("reviewed", "size")))

    ss = S.merge(M[["matter_id", "confidentiality_tier"]], on="matter_id")
    ss["sens"] = ss["confidentiality_tier"].isin(["Restricted", "Barrier"]).astype(int)
    expo = ss.groupby("lawyer_id").agg(sensitive=("sens", "sum"), sessions=("sens", "size"))

    df = (L[["lawyer_id"]].set_index("lawyer_id").join(cov).join(expo)
          .join(truth.set_index("lawyer_id")[["gen_cohort", "gen_careless"]]))
    df["is_cohort"] = (df["gen_cohort"] == "Non-compliant").astype(int)
    cohort_total = int(df["is_cohort"].sum())

    df = df[df["mandatory"].fillna(0) >= MIN_MANDATORY].copy()
    df["sensitive_rate"] = df["sensitive"] / df["sessions"]
    df["sensitive_count"] = df["sensitive"]
    return df, cohort_total, int(df["is_cohort"].sum())


def evaluate(df: pd.DataFrame, cohort_total: int) -> pd.DataFrame:
    screens = {
        "Mandatory review completion (lowest first)": -df["coverage"],
        "Restricted-tier session rate (highest first)": df["sensitive_rate"],
        "Restricted-tier session count (highest first)": df["sensitive_count"],
        "Both: rank sum of the two above": (df["coverage"].rank()
                                            + (-df["sensitive_rate"]).rank()).rpow(-1),
    }
    y = df["is_cohort"].to_numpy()
    rows = []
    for name, score in screens.items():
        s = np.asarray(score, dtype=float)
        order = np.argsort(-s, kind="stable")
        hits = np.cumsum(y[order])
        ap = float(np.mean([hits[i] / (i + 1) for i in range(len(y)) if y[order][i] == 1]))
        for k in LIST_SIZES:
            rows.append({
                "screen": name,
                "list_size": k,
                "true_positives": int(hits[k - 1]),
                "precision": round(hits[k - 1] / k, 3),
                "recall_of_screenable": round(hits[k - 1] / y.sum(), 3),
                "recall_of_cohort": round(hits[k - 1] / cohort_total, 3),
                "average_precision": round(ap, 3),
            })
    return pd.DataFrame(rows)


def main() -> None:
    RES.mkdir(exist_ok=True)
    df, cohort_total, cohort_screenable = build()
    res = evaluate(df, cohort_total)
    res.to_csv(RES / "screening_comparison.csv", index=False)

    print(f"population: {len(df)} lawyers with at least {MIN_MANDATORY} outputs "
          f"requiring review")
    print(f"cohort:     {cohort_total} non-compliant lawyers in the firm, "
          f"{cohort_screenable} of them screenable\n")

    wide = res[res["list_size"] == 20].set_index("screen")
    print("At a 20-name review list:")
    print(wide[["true_positives", "precision", "recall_of_cohort",
                "average_precision"]].to_string())

    print("\nPrecision by list size:")
    print(res.pivot(index="screen", columns="list_size", values="precision").to_string())
    print("\nRecall of the full cohort by list size:")
    print(res.pivot(index="screen", columns="list_size",
                    values="recall_of_cohort").to_string())

    miss = cohort_total - cohort_screenable
    print(f"\nCeiling: {miss} of {cohort_total} cohort members never reach any list — "
          f"they record\nfewer than {MIN_MANDATORY} outputs requiring review, so no "
          "coverage rate can be computed\nfor them. Light users are invisible to a "
          "rate-based screen. That caps recall at\n"
          f"{cohort_screenable / cohort_total:.0%} however good the ranking is, and it "
          "is a property of the rule, not of\nthe data.")

    careless = df[(df["gen_careless"]) & (df["is_cohort"] == 0)]
    top = df.nsmallest(20, "coverage")
    print(f"\nComposition of the 20-name list from the best screen: "
          f"{int(top['is_cohort'].sum())} non-compliant, "
          f"{int(top['gen_careless'].sum() - (top['is_cohort'] & top['gen_careless']).sum())} "
          f"merely careless, {int(20 - top['is_cohort'].sum() - top['gen_careless'].sum())} "
          "neither.")
    print(f"({len(careless)} careless lawyers exist firm-wide, and no measure in the "
          "model separates\nthem from the deliberate ones.)")
    print(f"\nwrote {RES / 'screening_comparison.csv'}")


if __name__ == "__main__":
    main()
