"""Cross-checks on the generated warehouse, run before anything is loaded.

Embedded patterns interfere with each other. This script verifies that each one
is present at roughly the intended magnitude, and that the four failure modes
listed in the decisions document did not occur.

Run:  .venv/bin/python src/consistency_checks.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C
import billable_impact as BI

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / C.OUT_DIR
GT = ROOT / C.GROUND_TRUTH_DIR

pd.set_option("display.width", 150)
pd.set_option("display.max_columns", 30)

L = pd.read_csv(RAW / "dim_lawyer.csv")
M = pd.read_csv(RAW / "dim_matter.csv")
T = pd.read_csv(RAW / "dim_task_type.csv")
TOOL = pd.read_csv(RAW / "dim_tool.csv")
S = pd.read_csv(RAW / "fact_ai_session.csv")
O = pd.read_csv(RAW / "fact_ai_output.csv")
TE = pd.read_csv(RAW / "fact_time_entry.csv")
INC = pd.read_csv(RAW / "fact_incident.csv")
PR = pd.read_csv(RAW / "fact_pipeline_run.csv")
GTL = pd.read_csv(GT / "_ground_truth_lawyer.csv")

S["month"] = S["date_id"] // 100
LM = L.merge(GTL, on="lawyer_id")

FAILS: list[str] = []


def head(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def check(ok: bool, msg: str):
    print(("  PASS  " if ok else "  FAIL  ") + msg)
    if not ok:
        FAILS.append(msg)


# --------------------------------------------------------- guard 1 & 3
head("GUARD 1 — level and practice group must be near-independent")
ct = pd.crosstab(L["level"], L["practice_group"])
exp = np.outer(ct.sum(1), ct.sum(0)) / ct.to_numpy().sum()
chi2 = float(((ct.to_numpy() - exp) ** 2 / exp).sum())
print(ct.to_string())
print(f"\n  chi-square = {chi2:.1f}  (12 df, 5% critical = 21.0)")
check(chi2 < 21.0, "level x practice_group independence "
      "(otherwise Page 2 cannot separate the seniority effect from the practice effect)")

head("GUARD 3 — the non-compliant cohort must be spread, not clustered")
nc = LM[LM["gen_cohort"] == "Non-compliant"]
print(f"  cohort size = {len(nc)} of {len(L)} ({len(nc)/len(L):.1%})\n")
print(pd.crosstab(nc["level"], nc["practice_group"]).to_string())
check(nc["practice_group"].nunique() >= 4 and nc["level"].nunique() == 4,
      "cohort spans all 4 levels and at least 4 practice groups")
check(nc["practice_group"].value_counts().max() <= max(3, len(nc) * 0.40),
      "no practice group holds a disproportionate share of the cohort")

head("GUARD 4 — the two failure windows must not overlap")
print(f"  service outage   {C.OUTAGE_START} -> {C.OUTAGE_END}")
print(f"  pipeline outage  {C.PIPELINE_GAP_START} -> {C.PIPELINE_GAP_END}")
check(C.OUTAGE_END < C.PIPELINE_GAP_START or C.PIPELINE_GAP_END < C.OUTAGE_START,
      "windows are disjoint")
check((C.OUTAGE_START.year, C.OUTAGE_START.month)
      != (C.PIPELINE_GAP_START.year, C.PIPELINE_GAP_START.month),
      "windows fall in different months")


# --------------------------------------------------------- patterns 1 & 2
head("PATTERN 1 & 2 — adoption by level and practice area (plateau months only)")
plateau = S[S["month"] >= 202512]
n_months = plateau["month"].nunique()
act = plateau.merge(L[["lawyer_id", "level", "practice_group"]], on="lawyer_id")

pop_l = L[L["is_active"] == 1].groupby("level").size()
mau_l = (act.groupby(["month", "level"])["lawyer_id"].nunique()
         .groupby("level").mean())
tbl = pd.DataFrame({"lawyers": pop_l, "mean monthly active": mau_l.round(1)})
tbl["adoption"] = (tbl["mean monthly active"] / tbl["lawyers"]).round(3)
tbl["target"] = pd.Series(C.ADOPTION_BY_LEVEL)
print(tbl.to_string())

pop_a = L[L["is_active"] == 1].groupby("practice_group").size()
mau_a = (act.groupby(["month", "practice_group"])["lawyer_id"].nunique()
         .groupby("practice_group").mean())
tbl2 = pd.DataFrame({"lawyers": pop_a, "mean monthly active": mau_a.round(1)})
tbl2["adoption"] = (tbl2["mean monthly active"] / tbl2["lawyers"]).round(3)
print("\n" + tbl2.to_string())
ratio = tbl2.loc["M&A", "adoption"] / tbl2.loc["Litigation", "adoption"]
print(f"\n  M&A / Litigation adoption ratio = {ratio:.2f}  (target 2.5)")
check(2.0 <= ratio <= 3.1, "transactional/contentious adoption ratio near 2.5x")


# --------------------------------------------------------- guard 2 & pattern 3
head("GUARD 2 & PATTERN 3 — Litigation must have enough volume to measure quality")
oo = (O.merge(M[["matter_id", "practice_area", "confidentiality_tier",
                 "fee_arrangement"]], on="matter_id")
        .merge(T[["task_type_id", "task_name", "review_policy_base", "review_type"]],
               on="task_type_id")
        .merge(L[["lawyer_id", "level", "practice_group"]], on="lawyer_id"))
q = oo.groupby("practice_area").agg(
    outputs=("output_id", "size"),
    median_edit=("edit_distance_pct", "median"),
    accept_rate=("accepted", "mean")).round(3)
q["target_median"] = pd.Series(C.EDIT_MEDIAN_BY_AREA) * 100
print(q.to_string())
lit_n = int(q.loc["Litigation", "outputs"])
check(lit_n >= 5000, f"Litigation output count = {lit_n:,} (needs to be thick enough "
                     "that the edit-distance gap is measurable, not noise)")
gap = q.loc["Litigation", "median_edit"] - q.loc["M&A", "median_edit"]
check(gap > 12, f"Litigation median edit distance exceeds M&A by {gap:.1f}pp")


# --------------------------------------------------------- pattern 4
head("PATTERN 4 — fee-arrangement asymmetry: four estimators, one survives")
use = (S.groupby(["lawyer_id", "matter_id"])
       .agg(n=("session_id", "size"), first=("date_id", "min")).reset_index())
use = use[use["n"] >= C.AI_INTENSITY_THRESHOLD]
te = (TE.merge(use, on=["lawyer_id", "matter_id"], how="left")
        .merge(M[["matter_id", "fee_arrangement", "practice_area"]], on="matter_id")
        .merge(L[["lawyer_id", "level"]], on="lawyer_id")
        .merge(GTL[["lawyer_id", "gen_reinvests"]], on="lawyer_id"))
te["ai_matter"] = te["first"].notna()
te["post"] = te["ai_matter"] & (te["date_id"] >= te["first"].fillna(1e9))
sess_per_lawyer = S.groupby("lawyer_id").size()
never = (set(sess_per_lawyer[sess_per_lawyer < 20].index)
         | (set(L["lawyer_id"]) - set(sess_per_lawyer.index)))
TARGET = pd.Series(C.HOURS_EFFECT_BY_FEE) * 100


print("  (a) NAIVE — total hours per matter, AI matters vs the rest")
per = te.groupby(["lawyer_id", "matter_id", "ai_matter"])["hours"].sum().reset_index()
nv = per.groupby("ai_matter")["hours"].mean()
print(f"      AI {nv[True]:.0f}h vs non-AI {nv[False]:.0f}h  ({nv[True]/nv[False]-1:+.0%})"
      "  <-- wrong sign")
print("      Matters that attract AI are simply bigger. This is selection.\n")

print("  (b) WITHIN lawyer-matter, before vs after the first session")
n_pre = (te[te["ai_matter"]].groupby(["lawyer_id", "matter_id"])["post"]
         .agg(entries="size", post="sum"))
no_pre = float((n_pre["post"] == n_pre["entries"]).mean())
print(f"      unusable: {no_pre:.0%} of AI lawyer-matters have no pre-period at all, "
      f"and the\n      median lawyer-matter has "
      f"{int((n_pre['entries'] - n_pre['post']).median())} timesheet entry before "
      "first use.")
print("      Lawyers start using AI as soon as they pick up a matter, so there is\n"
      "      no before to compare against.\n")

print("  (c) CONTROL = the same lawyers' other matters")
te["grp"] = np.where(te["post"], 1, np.where(te["ai_matter"], -1, 0))
eff_c, _, _ = BI.fit(te)
print(pd.DataFrame({"effect %": eff_c.set_index("fee_arrangement")["estimate_pct"],
                    "target %": TARGET}).to_string())
print("      Contaminated: reinvested capacity lands on exactly those matters, so\n"
      "      the treatment lifts its own control.\n")

print("  (d) CONTROL = matters of lawyers who never adopted")
te["grp"] = np.where(te["post"], 1,
                     np.where(te["lawyer_id"].isin(never) & ~te["ai_matter"], 0, -1))
est, meta, bundle = BI.fit(te)
est = est.set_index("fee_arrangement")
show = est[["estimate_pct", "ci_low_pct", "ci_high_pct", "treated_entries"]].copy()
show["target %"] = TARGET
print(show.round(1).to_string())
print(f"      {len(never)} lawyers never adopted. log(hours) on {meta['cells']} "
      f"fee x practice area x level\n      fixed effects, {meta['rows']:,} timesheet "
      f"lines, SEs clustered on {meta['clusters']} lawyers.")

eff_d = est["estimate_pct"]
check(bool((eff_d < 0).all()), "the effect is negative in every fee arrangement")
check(bool((est["ci_high_pct"] < 0).all()),
      "every confidence interval excludes zero")
print("\n      pairwise contrasts:")
fees = list(est.index)
for a in range(len(fees)):
    for b in range(a + 1, len(fees)):
        dd, sd = BI.contrast(bundle, fees[a], fees[b])
        lo, hi = (np.exp(dd - 1.96 * sd) - 1) * 100, (np.exp(dd + 1.96 * sd) - 1) * 100
        verdict = "distinguishable" if lo * hi > 0 else "not distinguishable"
        print(f"        {fees[a]:>10s} vs {fees[b]:<10s} "
              f"{(np.exp(dd)-1)*100:+6.1f}%  [{lo:+6.1f}, {hi:+6.1f}]  {verdict}")
d_ch, s_ch = BI.contrast(bundle, "Capped", "Hourly")
check(abs(np.exp(d_ch) - 1) * 100 < 3,
      "Capped and Hourly are not separable — a 1pp designed difference is below "
      "what this design resolves, so Page 3 must not rank them")
check(int(est["treated_entries"].min()) >= 5000,
      f"every fee arrangement has enough treated entries "
      f"(min {int(est['treated_entries'].min()):,})")


head("PATTERN 4b — where did the released capacity go?")
CAP = pd.read_csv(GT / "_ground_truth_capacity.csv").merge(GTL, on="lawyer_id")
agg = CAP.groupby("gen_reinvests").agg(
    lawyers=("lawyer_id", "size"), freed=("freed", "sum"),
    returned=("returned", "sum")).round(0)
agg["returned / freed"] = (agg["returned"] / agg["freed"]).round(3)
agg["net hours lost per lawyer"] = ((agg["freed"] - agg["returned"]) / agg["lawyers"]).round(0)
print(agg.to_string())
r_yes = agg.loc[True, "returned / freed"]
r_no = agg.loc[False, "returned / freed"]
print(f"\n  reinvesting lawyers put back {r_yes:.0%} of freed hours; the rest put back "
      f"{r_no:.0%}")
print("  The shortfall is not a bug: a heavy adopter can have every matter they")
print("  touched in a quarter under AI, leaving the freed time nowhere to land.")
check(r_yes > 0.55 and r_no < 0.02,
      "the two populations behave as designed — capacity moved for one, lost for the other")

ratio_pop = CAP["returned"].sum() / CAP["freed"].sum()
print(f"\n  firm-wide, {ratio_pop:.0%} of released capacity reappears as recorded time.")
print("  This is the number Page 3 must show beside Notional Capacity Released;")
print("  without it the released hours read as profit.")


# --------------------------------------------------------- pattern 5
head("PATTERN 5 — governance: one cause, two symptoms")
ss = S.merge(M[["matter_id", "confidentiality_tier"]], on="matter_id")
ss = ss.merge(LM[["lawyer_id", "gen_cohort"]], on="lawyer_id")
share_m = M["confidentiality_tier"].value_counts(normalize=True)
share_s = ss["confidentiality_tier"].value_counts(normalize=True)
print(pd.DataFrame({"share of matters": share_m.round(4),
                    "share of sessions": share_s.round(4)}).to_string())
exposure = share_s.get("Restricted", 0) + share_s.get("Barrier", 0)
print(f"\n  total exposure (sessions on Restricted or Barrier) = {exposure:.2%}")
check(share_s["Standard"] > share_m["Standard"],
      "sensitive tiers are under-used relative to their share of matters")

oo["mandatory"] = ((oo["review_policy_base"] == "Mandatory")
                   | oo["confidentiality_tier"].isin(["Restricted", "Barrier"])
                   | (oo["is_client_facing"] == 1))
mand = oo[oo["mandatory"]].merge(LM[["lawyer_id", "gen_cohort"]], on="lawyer_id")
print("\n  mandatory review coverage")
by_tier = mand.groupby("confidentiality_tier")["reviewed"].agg(["mean", "size"]).round(3)
print(by_tier.to_string())
by_cohort = mand.groupby("gen_cohort")["reviewed"].agg(["mean", "size"]).round(3)
print("\n" + by_cohort.to_string())
print("\n  coverage by review type (Page 4 reports these separately)")
print(mand.groupby("review_type")["reviewed"].agg(["mean", "size"]).round(3).to_string())

print("\n  per-lawyer screening — which signal actually finds these people?")
n_sens = ss[ss["confidentiality_tier"].isin(["Restricted", "Barrier"])] \
    .groupby("lawyer_id").size().rename("sensitive")
n_all = ss.groupby("lawyer_id").size().rename("sessions")
cov_l = mand.groupby("lawyer_id").agg(coverage=("reviewed", "mean"),
                                      mandatory=("reviewed", "size"))
scr = (pd.concat([n_all, n_sens], axis=1).fillna({"sensitive": 0})
       .join(cov_l).join(LM.set_index("lawyer_id")["gen_cohort"]).dropna())
scr = scr[scr["mandatory"] >= 25]
scr["sensitive_rate"] = scr["sensitive"] / scr["sessions"]
n_cohort = int((scr["gen_cohort"] == "Non-compliant").sum())
print(f"    {len(scr)} lawyers with enough mandatory-review volume to screen; "
      f"{n_cohort} are in the cohort")
for label, ranked in [
        ("lowest mandatory review coverage", scr.nsmallest(20, "coverage")),
        ("highest sensitive-session rate", scr.nlargest(20, "sensitive_rate"))]:
    hit = int((ranked["gen_cohort"] == "Non-compliant").sum())
    print(f"      20 names by {label:34s} -> finds {hit}/{n_cohort}")
print("\n    Exposure rate barely separates them: a lawyer staffed on more sensitive")
print("    matters is not the same as a lawyer ignoring the restriction. Review")
print("    completion is the signal; exposure is only the context.")
print("\n" + scr.groupby("gen_cohort")["coverage"]
      .describe()[["min", "25%", "50%", "max"]].round(3).to_string())
best = int((scr.nsmallest(20, "coverage")["gen_cohort"] == "Non-compliant").sum())
check(best >= n_cohort * 0.7,
      f"a 20-name review list built on review coverage finds {best} of {n_cohort} "
      "cohort members")
overlap = (scr.loc[scr["gen_cohort"] == "Compliant", "coverage"].min()
           < scr.loc[scr["gen_cohort"] == "Non-compliant", "coverage"].max())
check(bool(overlap), "the two populations overlap — the screen produces a list to check "
                     "by hand, not a filter that returns the answer")


# --------------------------------------------------------- pattern 6
head("PATTERN 6 — incidents concentrate where review coverage is poor")
cov = mand.groupby("lawyer_id")["reviewed"].mean().rename("coverage")
inc_n = INC.groupby("lawyer_id").size().rename("incidents")
z = pd.DataFrame(cov).join(inc_n).fillna({"incidents": 0})
z["quartile"] = pd.qcut(z["coverage"], 4, labels=["Q1 worst", "Q2", "Q3", "Q4 best"])
agg = z.groupby("quartile", observed=True).agg(
    lawyers=("incidents", "size"), incidents=("incidents", "sum"),
    mean_coverage=("coverage", "mean")).round(3)
agg["incidents per lawyer"] = (agg["incidents"] / agg["lawyers"]).round(2)
print(agg.to_string())
r = agg.loc["Q1 worst", "incidents per lawyer"] / max(agg.loc["Q4 best", "incidents per lawyer"], .01)
print(f"\n  worst-quartile / best-quartile incident rate = {r:.1f}x")
check(r >= 1.8, "incidents track poor review coverage")


# --------------------------------------------------------- pattern 9
head("PATTERN 9 — licence cost, and the seats nobody opened")
SM = pd.read_csv(RAW / "fact_seat_month.csv")
sm = SM.merge(TOOL[["tool_id", "tool_name", "licence_cost_per_seat_month"]], on="tool_id")
by_tool = sm.groupby("tool_name").agg(
    seat_months=("seats", "sum"),
    idle_share=("used_in_month", lambda x: 1 - x.mean()),
    licence_aud=("licence_cost_aud", "sum"),
    idle_aud=("licence_cost_aud", lambda x: 0)).round(3)
by_tool["idle_aud"] = (sm[sm["used_in_month"] == 0].groupby("tool_name")["licence_cost_aud"]
                       .sum().round(0))
print(by_tool.to_string())

licence = sm["licence_cost_aud"].sum()
token = S["cost_aud"].sum()
print(f"\n  licence ${licence:,.0f} vs token ${token:,.0f} — "
      f"tokens are {token / (licence + token):.1%} of what the firm pays")
print(f"  idle licence spend ${by_tool['idle_aud'].sum():,.0f} "
      f"({by_tool['idle_aud'].sum() / licence:.0%} of the licence bill)")
check(token / (licence + token) < 0.05,
      "token spend is a rounding error against licences — a cost page built from "
      "the session table alone would report the wrong order of magnitude")

# allocation must not be a copy of usage, or idle seats cannot exist to be found
harvey = sm[sm["tool_name"] == "Harvey"]
holders = set(harvey["lawyer_id"])
users = set(S.merge(TOOL[["tool_id", "tool_name"]], on="tool_id")
            .query("tool_name == 'Harvey'")["lawyer_id"])
print(f"\n  Harvey: {len(holders)} seats allocated, {len(users)} lawyers ever used it, "
      f"{len(holders - users)} seats never opened at all")
check(len(holders - users) >= 20,
      "seats were allocated to people who never used the tool — allocation is a "
      "decision made at rollout, not a record of behaviour")
check(0.30 < (1 - sm["used_in_month"].mean()) < 0.75,
      "idle share is consistent with the adoption rate rather than assumed")


# --------------------------------------------------------- pattern 7
head("PATTERN 7 — latency and the service failure window")
st = S.merge(TOOL[["tool_id", "tool_name"]], on="tool_id")
st = st.merge(T[["task_type_id", "task_name"]], on="task_type_id")
ok = st[st["status"] == "Success"]
print(ok.groupby("tool_name")["latency_ms"].quantile(0.95).round(0).to_string())
longdoc = ok[ok["task_name"].isin(C.LONG_DOC_TASKS)]
h = longdoc[longdoc["tool_name"] == "Harvey"]["latency_ms"].quantile(0.95)
o_ = longdoc[longdoc["tool_name"] != "Harvey"]["latency_ms"].quantile(0.95)
print(f"\n  p95 on long-document tasks: Harvey {h:.0f}ms vs others {o_:.0f}ms")
check(h > o_ * 1.2, "Harvey is measurably slower on long-document work")

S["d"] = pd.to_datetime(S["date_id"], format="%Y%m%d")
err = S.assign(bad=(S["status"] != "Success").astype(int)).groupby("d")["bad"].mean()
win = err[(err.index.date >= C.OUTAGE_START) & (err.index.date <= C.OUTAGE_END)]
base = err[(err.index.date < C.OUTAGE_START) | (err.index.date > C.OUTAGE_END)]
print(f"\n  error rate: baseline {base.mean():.2%}, outage window {win.mean():.2%} "
      f"over {len(win)} days")
et = S[(S["d"].dt.date >= C.OUTAGE_START) & (S["d"].dt.date <= C.OUTAGE_END)]
print("  error types during the window:")
print("   ", et["error_type"].value_counts(normalize=True).round(3).to_dict())
check(win.mean() > base.mean() * 4, "the outage is unmistakable against baseline")


# --------------------------------------------------------- pattern 8
head("PATTERN 8 — the planted pipeline failure must be catchable")
PR["d"] = pd.to_datetime(PR["run_date"])
gapwin = PR[(PR["source_system"] == C.PIPELINE_GAP_SOURCE)
            & (PR["d"].dt.date >= C.PIPELINE_GAP_START)
            & (PR["d"].dt.date <= C.PIPELINE_GAP_END)]
print(gapwin[["source_system", "run_date", "rows_loaded", "run_status"]].to_string(index=False))
check((gapwin["rows_loaded"] == 0).all() and len(gapwin) == 3,
      "three consecutive zero-row loads on the Copilot feed")

sess_in_gap = S[(S["source_system"] == C.PIPELINE_GAP_SOURCE)
                & (S["d"].dt.date >= C.PIPELINE_GAP_START)
                & (S["d"].dt.date <= C.PIPELINE_GAP_END)]
check(len(sess_in_gap) == 0,
      "the rows the pipeline says it lost are actually absent from fact_ai_session")

ref = PR[PR["source_system"] == C.REFERENTIAL_FAIL_SOURCE]
rate = ref["referential_failures"].sum() / max(ref["rows_loaded"].sum(), 1)
busy = ref[ref["rows_loaded"] >= 50]        # a day with a handful of rows is noise
worst_day = (busy["referential_failures"] / busy["rows_loaded"]).max()
print(f"\n  chronic mismatch on {C.REFERENTIAL_FAIL_SOURCE}: {rate:.2%} overall, "
      f"worst day with real volume {worst_day:.2%} (of {len(busy)} such days)")
check(0.012 < rate < 0.03, "chronic referential failure is present at roughly 2%")
check(worst_day < 0.10, "no single day is bad enough to trip a status light — "
                        "it has to be found as a trend")

lost = pd.read_csv(GT / "_ground_truth_pipeline_loss.csv").iloc[0]
print(f"\n  generated {lost.sessions_generated:,} sessions, "
      f"pipeline lost {lost.sessions_lost_to_pipeline:,}, "
      f"published {lost.sessions_published:,}")


# --------------------------------------------------------------- summary
head("SUMMARY")
if FAILS:
    print(f"  {len(FAILS)} check(s) FAILED:")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("  all checks passed")
