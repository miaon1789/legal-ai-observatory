"""Estimate the effect of AI adoption on recorded hours, with confidence intervals.

A fixed-effects regression is not something a SQL view can compute, and a point
estimate without its control group and sample size is not auditable. The fit
therefore happens here and is written to a small result table,
``fact_billable_impact_estimate``, which is what Power BI reads. The control
definition and the sample sizes travel with the number.

Specification
-------------
    log(hours) ~ cell + treated:fee_arrangement

``cell`` is a fixed effect for every fee arrangement x practice area x level
combination, which absorbs the composition differences between the two groups.
``treated`` marks timesheet lines recorded after the lawyer's first AI session
on that matter. The control is lines recorded by lawyers who never adopted --
not the same lawyers' other matters, which are contaminated by the reinvested
capacity the treatment itself releases.

Standard errors are clustered on the lawyer. Timesheet lines within a lawyer are
not independent, and classical OLS errors here are optimistic by roughly an
order of magnitude.

Run:  .venv/bin/python src/billable_impact.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / C.OUT_DIR

MIN_CELL = 40          # per arm, per cell
NON_ADOPTER_MAX = 20   # sessions over the whole window


def load() -> pd.DataFrame:
    S = pd.read_csv(RAW / "fact_ai_session.csv")
    TE = pd.read_csv(RAW / "fact_time_entry.csv")
    M = pd.read_csv(RAW / "dim_matter.csv")
    L = pd.read_csv(RAW / "dim_lawyer.csv")

    use = (S.groupby(["lawyer_id", "matter_id"])
           .agg(n=("session_id", "size"), first=("date_id", "min")).reset_index())
    use = use[use["n"] >= C.AI_INTENSITY_THRESHOLD]

    te = (TE.merge(use, on=["lawyer_id", "matter_id"], how="left")
            .merge(M[["matter_id", "fee_arrangement", "practice_area"]], on="matter_id")
            .merge(L[["lawyer_id", "level"]], on="lawyer_id"))
    te["ai_matter"] = te["first"].notna()
    te["treated"] = te["ai_matter"] & (te["date_id"] >= te["first"].fillna(1e9))

    per_lawyer = S.groupby("lawyer_id").size()
    never = (set(per_lawyer[per_lawyer < NON_ADOPTER_MAX].index)
             | (set(L["lawyer_id"]) - set(per_lawyer.index)))
    te["grp"] = np.where(te["treated"], 1,
                         np.where(te["lawyer_id"].isin(never) & ~te["ai_matter"], 0, -1))
    te.attrs["n_never"] = len(never)
    return te


def fit(te: pd.DataFrame):
    d = te[(te["grp"] >= 0) & (te["hours"] > 0)].copy()
    d["cell"] = d["fee_arrangement"] + "|" + d["practice_area"] + "|" + d["level"]

    # A cell with a handful of control lines identifies its own contrast from
    # almost nothing while carrying full leverage in the fit.
    n = d.pivot_table(index="cell", columns="grp", values="hours", aggfunc="size").dropna()
    d = d[d["cell"].isin(n[(n >= MIN_CELL).all(axis=1)].index)]

    fees = sorted(d["fee_arrangement"].unique())
    cells = pd.get_dummies(d["cell"], dtype=float)
    treat = np.column_stack([(d["grp"].to_numpy() == 1)
                             & (d["fee_arrangement"].to_numpy() == f) for f in fees]) * 1.0
    X = np.hstack([cells.to_numpy(), treat])
    y = np.log(d["hours"].to_numpy())

    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta

    # cluster-robust (CR1) variance, clustered on the lawyer
    codes, uniq = pd.factorize(d["lawyer_id"].to_numpy())
    G, N, K = len(uniq), X.shape[0], X.shape[1]
    Su = np.zeros((G, K))
    np.add.at(Su, codes, X * resid[:, None])
    XtX_inv = np.linalg.pinv(X.T @ X)
    V = XtX_inv @ (Su.T @ Su) @ XtX_inv
    V *= (G / (G - 1)) * ((N - 1) / (N - K))
    se = np.sqrt(np.diag(V))

    k = len(fees)
    rows = []
    for i, f in enumerate(fees):
        b, s = beta[-k + i], se[-k + i]
        rows.append({
            "fee_arrangement": f,
            "estimate_pct": (np.exp(b) - 1) * 100,
            "ci_low_pct": (np.exp(b - 1.96 * s) - 1) * 100,
            "ci_high_pct": (np.exp(b + 1.96 * s) - 1) * 100,
            "se_pct": s * 100,
            "treated_entries": int(((d["grp"] == 1)
                                    & (d["fee_arrangement"] == f)).sum()),
            "control_entries": int(((d["grp"] == 0)
                                    & (d["fee_arrangement"] == f)).sum()),
        })
    out = pd.DataFrame(rows)
    meta = {"cells": int(cells.shape[1]), "rows": N, "clusters": G,
            "never": te.attrs.get("n_never", 0)}
    return out, meta, (beta, V, k, fees)


def contrast(bundle, a: str, b: str) -> tuple[float, float]:
    """Difference between two fee arrangements, in log points, with its SE."""
    beta, V, k, fees = bundle
    ia, ib = len(beta) - k + fees.index(a), len(beta) - k + fees.index(b)
    diff = beta[ia] - beta[ib]
    var = V[ia, ia] + V[ib, ib] - 2 * V[ia, ib]
    return diff, float(np.sqrt(var))


def main() -> None:
    te = load()
    est, meta, bundle = fit(te)

    est.insert(0, "estimate_id", np.arange(1, len(est) + 1))
    est["metric"] = "Hours per lawyer-matter-day"
    est["method"] = "OLS on log(hours), lawyer-clustered SE"
    est["fixed_effects"] = "fee_arrangement x practice_area x level"
    est["control_definition"] = (
        f"Timesheet lines of the {meta['never']} lawyers with fewer than "
        f"{NON_ADOPTER_MAX} AI sessions across the window")
    est["n_cells"] = meta["cells"]
    est["n_clusters"] = meta["clusters"]
    est["generated_on"] = dt.date.today().isoformat()
    for c in ["estimate_pct", "ci_low_pct", "ci_high_pct", "se_pct"]:
        est[c] = est[c].round(2)

    est.to_csv(RAW / "fact_billable_impact_estimate.csv", index=False,
               float_format="%.6f")
    (ROOT / "results").mkdir(exist_ok=True)
    est.to_csv(ROOT / "results" / "billable_impact_estimate.csv", index=False)

    print(f"log(hours) ~ cell + treated:fee   |   {meta['rows']:,} timesheet lines, "
          f"{meta['cells']} cells, {meta['clusters']} lawyer clusters")
    print(f"control: {est['control_definition'].iat[0]}\n")
    show = est[["fee_arrangement", "estimate_pct", "ci_low_pct", "ci_high_pct",
                "se_pct", "treated_entries", "control_entries"]]
    print(show.to_string(index=False))

    print("\npairwise contrasts (log points, lawyer-clustered):")
    fees = est["fee_arrangement"].tolist()
    for i in range(len(fees)):
        for j in range(i + 1, len(fees)):
            d, s = contrast(bundle, fees[i], fees[j])
            lo, hi = (np.exp(d - 1.96 * s) - 1) * 100, (np.exp(d + 1.96 * s) - 1) * 100
            sig = "distinguishable" if lo * hi > 0 else "not distinguishable"
            print(f"  {fees[i]:>10s} vs {fees[j]:<10s}  "
                  f"{(np.exp(d)-1)*100:+6.1f}%  [{lo:+6.1f}, {hi:+6.1f}]   {sig}")


if __name__ == "__main__":
    main()
