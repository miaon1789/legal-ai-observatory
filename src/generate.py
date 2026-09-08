"""Build the synthetic legal-AI operations warehouse as CSV extracts.

Run:  .venv/bin/python src/generate.py

The generator produces the *observed* warehouse, not the ground truth. Two
things are deliberately lost on the way in — a three-day feed outage and a
chronic referential mismatch — so that the data-quality monitoring has a real
failure to catch rather than a cosmetic one. See docs/DATA_MODEL.md section 5.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C

RNG = np.random.default_rng(C.SEED)
ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- dim_date
def build_dim_date() -> pd.DataFrame:
    days = pd.date_range(C.CALENDAR_START, C.CALENDAR_END, freq="D")
    d = pd.DataFrame({"date": days})
    d["date_id"] = d["date"].dt.strftime("%Y%m%d").astype(int)
    d["year"] = d["date"].dt.year
    d["quarter"] = d["date"].dt.quarter
    d["month"] = d["date"].dt.month
    d["month_name"] = d["date"].dt.strftime("%b")
    d["iso_year_week"] = d["date"].dt.strftime("%G-W%V")
    d["day_of_week"] = d["date"].dt.dayofweek + 1
    d["is_business_day"] = (d["date"].dt.dayofweek < 5).astype(int)
    # Australian financial year: 1 July - 30 June
    d["fy_year"] = np.where(d["month"] >= 7, d["year"] + 1, d["year"])
    d["fy_quarter"] = ((d["month"] - 7) % 12) // 3 + 1
    return d[["date_id", "date", "year", "quarter", "month", "month_name",
              "iso_year_week", "day_of_week", "is_business_day", "fy_year", "fy_quarter"]]


# -------------------------------------------------------------- dim_lawyer
FIRST = ["Alice", "Ben", "Chloe", "Daniel", "Ella", "Finn", "Grace", "Henry", "Isla",
         "James", "Kate", "Liam", "Mia", "Noah", "Olivia", "Patrick", "Quinn", "Ruby",
         "Sam", "Tessa", "Uma", "Victor", "Wren", "Xavier", "Yasmin", "Zach", "Amara",
         "Bodhi", "Cara", "Dev", "Elena", "Farid", "Gia", "Hugo", "Ines", "Jun",
         "Kiran", "Lena", "Mateo", "Nadia", "Omar", "Priya", "Rafa", "Sana", "Tomas"]
LAST = ["Alderton", "Beaumont", "Cavanagh", "Delaney", "Ellery", "Fairbairn", "Gallagher",
        "Hollingsworth", "Ingram", "Jarrett", "Kensington", "Lockhart", "Merriweather",
        "Northcote", "Ormsby", "Pemberton", "Quarrier", "Ravenscroft", "Sinclair",
        "Thackeray", "Underwood", "Vandermeer", "Whitlock", "Yardley", "Zeller",
        "Bassett", "Corrigan", "Duffield", "Everleigh", "Fitzsimmons", "Grantham",
        "Havelock", "Iversen", "Jephson", "Kilbride", "Larkspur", "Mowbray", "Nakamura",
        "Oyelaran", "Prendergast", "Rutherford", "Stavros", "Tremayne", "Vasquez"]


def build_dim_lawyer() -> pd.DataFrame:
    n = C.N_LAWYERS
    level = RNG.choice(C.LEVELS, n, p=C.LEVEL_MIX)
    group = RNG.choice(C.PRACTICE_AREAS, n, p=C.AREA_MIX)   # independent of level
    office = RNG.choice(C.OFFICES, n, p=C.OFFICE_MIX)

    rate = np.array([C.CHARGE_RATE[l] for l in level], dtype=float)
    rate *= 1 + RNG.uniform(-C.RATE_JITTER, C.RATE_JITTER, n)
    rate = np.round(rate / 10) * 10

    admitted = np.array([2026 - RNG.integers(*C.YEARS_ADMITTED[l], endpoint=True)
                         for l in level])

    names = [f"{FIRST[RNG.integers(len(FIRST))]} {LAST[RNG.integers(len(LAST))]}"
             for _ in range(n)]

    df = pd.DataFrame({
        "lawyer_id": np.arange(1, n + 1),
        "display_name": names,
        "level": level,
        "practice_group": group,
        "office": office,
        "admitted_year": admitted,
        "standard_charge_rate_aud": rate,
        "is_active": (RNG.random(n) > C.INACTIVE_RATE).astype(int),
    })

    # --- generator-only attributes, stripped before dim_lawyer is written ---
    # policy_cohort is stratified by level and spread across practice groups so
    # that Page 5 reads as individual behaviour, not a departmental problem.
    cohort = np.array(["Compliant"] * n, dtype=object)
    for lv in C.LEVELS:
        idx = np.flatnonzero(df["level"].to_numpy() == lv)
        k = int(round(len(idx) * C.NONCOMPLIANT_SHARE))
        if k == 0:
            continue
        # one per practice group first, so the cohort cannot pile into one team
        picked, groups_used = [], set()
        for i in RNG.permutation(idx):
            g = df.at[i, "practice_group"]
            if g not in groups_used:
                picked.append(i)
                groups_used.add(g)
            if len(picked) == k:
                break
        remaining = [i for i in RNG.permutation(idx) if i not in picked]
        picked += remaining[: k - len(picked)]
        cohort[picked] = "Non-compliant"

    df["gen_cohort"] = cohort
    # A latent adoption propensity, so adoption is sticky rather than a fresh
    # coin flip each month -- otherwise week-4 retention is meaningless.
    # Assigned as a stratified uniform within each level x practice group cell:
    # with 80-odd lawyers per level, an unstratified draw leaves the realised
    # adoption rate several points away from the designed one, and in the
    # low-adoption cells that noise is larger than the effect being modelled.
    # The pattern should be a property of the design, not of the seed.
    prop = np.empty(n)
    for _, cell in df.groupby(["level", "practice_group"], sort=False):
        idx = cell.index.to_numpy()
        prop[idx] = (RNG.permutation(len(idx)) + 0.5) / len(idx)
    df["gen_propensity"] = prop
    df["gen_churns"] = RNG.random(n) < 0.08
    df["gen_churn_month"] = RNG.integers(4, 18, n)
    # per-lawyer variation in review diligence, so teams differ (pattern 6)
    dil = np.clip(RNG.normal(1.0, C.REVIEW_DILIGENCE_SD, n), *C.DILIGENCE_CLIP)
    careless = (RNG.random(n) < C.CARELESS_SHARE) & (cohort == "Compliant")
    dil[careless] *= RNG.uniform(*C.CARELESS_FACTOR, careless.sum())
    df["gen_diligence"] = dil
    df["gen_careless"] = careless
    df["gen_reinvests"] = RNG.random(n) < C.REINVEST_SHARE
    return df


# ---------------------------------------------------------------- dim_tool
def build_dim_tool() -> pd.DataFrame:
    return pd.DataFrame(
        [{"tool_id": i + 1, "tool_name": t[0], "deployment_date": t[1],
          "cost_per_1k_input": t[2], "cost_per_1k_output": t[3],
          "licence_cost_per_seat_month": C.LICENCE_COST_PER_SEAT_MONTH[t[0]],
          "source_system": t[4]}
         for i, t in enumerate(C.TOOLS)])


def build_dim_task_type() -> pd.DataFrame:
    return pd.DataFrame(
        [{"task_type_id": i + 1, "task_name": t[0], "risk_class": t[1],
          "review_policy_base": t[2], "review_type": t[3]}
         for i, t in enumerate(C.TASK_TYPES)])


# -------------------------------------------------------------- dim_matter
CODENAMES = ["Alderman", "Bellwether", "Corvus", "Draco", "Elmwood", "Fathom", "Granite",
             "Harbour", "Ironbark", "Juniper", "Kestrel", "Lantern", "Meridian", "Nimbus",
             "Onyx", "Pinnacle", "Quarry", "Redgum", "Sandstone", "Talisman", "Umbra",
             "Verdant", "Wattle", "Yarra", "Zephyr", "Ardent", "Basalt", "Cascade"]
STREETS = ["Collins", "Bourke", "Pitt", "George", "Adelaide", "St Georges", "Queen",
           "William", "Flinders", "Hunter", "Eagle", "Murray"]


def _matter_name(area: str, i: int) -> str:
    cn = CODENAMES[RNG.integers(len(CODENAMES))]
    if area == "Litigation":
        return f"{LAST[RNG.integers(len(LAST))]} v {LAST[RNG.integers(len(LAST))]}"
    if area == "Real Estate":
        return f"{STREETS[RNG.integers(len(STREETS))]} Street Precinct — {cn}"
    if area == "Employment":
        return f"{cn} Restructure Advice"
    return f"Project {cn} {i % 97:02d}"


def build_dim_matter(lawyers: pd.DataFrame) -> pd.DataFrame:
    n = C.N_MATTERS
    area = RNG.choice(C.PRACTICE_AREAS, n, p=C.AREA_MIX)
    fee = np.array([RNG.choice(C.FEE_ARRANGEMENTS, p=C.FEE_BY_AREA[a]) for a in area])
    tier = RNG.choice(C.TIERS, n, p=C.TIER_MIX)   # independent of practice area
    barrier = np.where(tier == "Barrier",
                       RNG.integers(1, C.N_BARRIER_GROUPS + 1, n).astype(object), None)

    # matters open from before the window so the early months are not empty
    span_start = C.WINDOW_START - dt.timedelta(days=150)
    span_days = (C.WINDOW_END - span_start).days
    opened = [span_start + dt.timedelta(days=int(x))
              for x in RNG.integers(0, span_days, n)]
    months = np.array([RNG.integers(*C.MATTER_MONTHS[a], endpoint=True) for a in area])
    closed = [o + dt.timedelta(days=int(m * 30.4)) for o, m in zip(opened, months)]
    closed = [None if c > C.WINDOW_END else c for c in closed]

    seniors = lawyers[lawyers["level"].isin(["Partner", "Senior Associate"])]
    lead = []
    for a in area:
        pool = seniors[seniors["practice_group"] == a]
        pool = pool if len(pool) else seniors
        lead.append(int(pool["lawyer_id"].to_numpy()[RNG.integers(len(pool))]))

    return pd.DataFrame({
        "matter_id": np.arange(1, n + 1),
        "matter_name": [_matter_name(a, i) for i, a in enumerate(area)],
        "practice_area": area,
        "client_sector": RNG.choice(C.CLIENT_SECTORS, n),
        "fee_arrangement": fee,
        "confidentiality_tier": tier,
        "barrier_group_id": barrier,
        "lead_lawyer_id": lead,
        "opened_date": opened,
        "closed_date": closed,
    })


def build_assignments(lawyers: pd.DataFrame, matters: pd.DataFrame) -> pd.DataFrame:
    """Which lawyers work on which matters. Mostly same practice group."""
    rows = []
    by_group = {g: lawyers.loc[lawyers["practice_group"] == g, "lawyer_id"].to_numpy()
                for g in C.PRACTICE_AREAS}
    all_ids = lawyers["lawyer_id"].to_numpy()
    for m in matters.itertuples():
        size = int(RNG.integers(*C.TEAM_SIZE, endpoint=True))
        pool = by_group.get(m.practice_area, all_ids)
        team = set(RNG.choice(pool, min(size, len(pool)), replace=False).tolist())
        team.add(int(m.lead_lawyer_id))
        if RNG.random() < 0.25:                    # occasional cross-group secondee
            team.add(int(all_ids[RNG.integers(len(all_ids))]))
        for lid in team:
            rows.append((int(lid), int(m.matter_id)))
    return pd.DataFrame(rows, columns=["lawyer_id", "matter_id"]).drop_duplicates()


def allocate_seats(lawyers: pd.DataFrame) -> dict[str, set[int]]:
    """Who holds a licence for each tool.

    Allocated at rollout, before any usage exists, because that is the order it
    happens in: a firm decides who gets a seat and finds out afterwards who
    used it. Generating usage first and fitting seats around it would make idle
    seats an artefact instead of a consequence.
    """
    active = lawyers[lawyers["is_active"] == 1]
    everyone = set(active["lawyer_id"].tolist())
    w = active["practice_group"].map(C.HARVEY_SEAT_AREA_WEIGHT).to_numpy(dtype=float)
    n = int(round(len(active) * C.HARVEY_SEAT_SHARE))
    harvey = set(RNG.choice(active["lawyer_id"].to_numpy(), n,
                            replace=False, p=w / w.sum()).tolist())
    return {"Copilot": everyone, "Firm Chat": everyone, "Harvey": harvey}


print("building dimensions ...", flush=True)
dim_date = build_dim_date()
dim_lawyer = build_dim_lawyer()
dim_tool = build_dim_tool()
dim_task_type = build_dim_task_type()
dim_matter = build_dim_matter(dim_lawyer)
assignments = build_assignments(dim_lawyer, dim_matter)
seat_holders = allocate_seats(dim_lawyer)
print(f"  lawyers={len(dim_lawyer)} matters={len(dim_matter)} "
      f"assignments={len(assignments)} "
      f"non-compliant={(dim_lawyer['gen_cohort'] == 'Non-compliant').sum()} "
      f"harvey_seats={len(seat_holders['Harvey'])}", flush=True)


# ------------------------------------------------------------ fact_ai_session
def _month_starts() -> list[dt.date]:
    out, d = [], C.WINDOW_START.replace(day=1)
    while d <= C.WINDOW_END:
        out.append(d)
        d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


MONTHS = _month_starts()
BDAYS_BY_MONTH: dict[dt.date, np.ndarray] = {}
for _m in MONTHS:
    _end = min((_m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
               - dt.timedelta(days=1), C.WINDOW_END)
    _rng_days = pd.bdate_range(max(_m, C.WINDOW_START), _end)
    BDAYS_BY_MONTH[_m] = _rng_days.map(dt.date.toordinal).to_numpy()


def build_sessions(lawyers, matters, assignments, tools, seats) -> pd.DataFrame:
    m_idx = matters.set_index("matter_id")
    tool_by_name = dict(zip(tools["tool_name"], tools["tool_id"]))
    tool_src = dict(zip(tools["tool_name"], tools["source_system"]))
    tool_cost = {r.tool_name: (r.cost_per_1k_input, r.cost_per_1k_output)
                 for r in tools.itertuples()}
    harvey_live = dict(zip(tools["tool_name"], tools["deployment_date"]))
    task_ids = dict(zip(dim_task_type["task_name"], dim_task_type["task_type_id"]))

    lawyer_matters = {lid: g["matter_id"].to_numpy()
                      for lid, g in assignments.groupby("lawyer_id")}
    opened_ord = m_idx["opened_date"].map(dt.date.toordinal)
    closed_ord = m_idx["closed_date"].map(
        lambda c: c.toordinal() if c is not None else 10**7)

    rows = []
    for law in lawyers.itertuples():
        # A lawyer who has left the firm holds no tool seat and starts no new
        # sessions. Their historical time entries stay, because a practice
        # management system does not delete recorded time. Excluding them here
        # also keeps the adoption numerator and denominator on the same
        # population -- dim_lawyer[is_active] filters both.
        if law.is_active == 0:
            continue
        mids = lawyer_matters.get(law.lawyer_id)
        if mids is None or len(mids) == 0:
            continue
        p_plateau = min(C.ADOPTION_BY_LEVEL[law.level]
                        * C.ADOPTION_BY_AREA[law.practice_group]
                        * C.ADOPTION_CALIBRATION, C.ADOPTION_CEILING)
        intensity = C.INTENSITY_BY_LEVEL[law.level] * C.INTENSITY_BY_AREA[law.practice_group]
        tier_w = C.TIER_USE_MULT[law.gen_cohort]
        m_open = opened_ord.loc[mids].to_numpy()
        m_close = closed_ord.loc[mids].to_numpy()
        m_tier = m_idx.loc[mids, "confidentiality_tier"].to_numpy()
        m_area = m_idx.loc[mids, "practice_area"].to_numpy()
        base_w = np.array([tier_w[t] for t in m_tier], dtype=float)

        churn_month = int(law.gen_churn_month) if law.gen_churns else 999

        for i, mth in enumerate(MONTHS):
            prog = min(1.0, i / C.RAMP_MONTHS)
            ramp = C.RAMP_FLOOR + (1 - C.RAMP_FLOOR) * prog
            ramp_i = C.INTENSITY_RAMP_FLOOR + (1 - C.INTENSITY_RAMP_FLOOR) * prog
            if law.gen_propensity >= p_plateau * ramp:
                continue
            if i >= churn_month:
                continue
            if RNG.random() < C.QUIET_MONTH_P:
                continue

            n = RNG.poisson(intensity * ramp_i)
            if n == 0:
                continue
            days = BDAYS_BY_MONTH[mth]
            if len(days) == 0:
                continue
            picked_days = RNG.choice(days, n)
            live = (m_open <= picked_days[:, None]) & (m_close >= picked_days[:, None])
            w = live * base_w
            tot = w.sum(axis=1)
            keep = tot > 0
            if not keep.any():
                continue
            picked_days, w, tot = picked_days[keep], w[keep], tot[keep]
            u = RNG.random(len(tot))[:, None]
            choice = (np.cumsum(w / tot[:, None], axis=1) > u).argmax(axis=1)

            for day_ord, ci in zip(picked_days, choice):
                d = dt.date.fromordinal(int(day_ord))
                area = m_area[ci]
                mix = C.TASK_MIX_BY_AREA[area]
                task = RNG.choice(list(mix), p=list(mix.values()))
                avail = [t for t in tool_by_name
                         if harvey_live[t] <= d and law.lawyer_id in seats[t]]
                share = (C.TOOL_SHARE_AFTER_HARVEY if "Harvey" in avail
                         else C.TOOL_SHARE_BEFORE_HARVEY)
                p = np.array([share[t] for t in avail], dtype=float)
                tool = RNG.choice(avail, p=p / p.sum())

                ti, to = C.TOKENS_BY_TASK[task]
                tin = max(50, int(RNG.lognormal(np.log(ti), 0.45)))
                tout = max(20, int(RNG.lognormal(np.log(to), 0.50)))
                cin, cout = tool_cost[tool]
                cost = round(tin / 1000 * cin + tout / 1000 * cout, 4)

                mu = np.log(C.LATENCY_P95_MS) - 1.645 * C.LATENCY_LOG_SD
                lat = RNG.lognormal(mu, C.LATENCY_LOG_SD)
                if tool == "Harvey" and task in C.LONG_DOC_TASKS:
                    lat *= C.HARVEY_LONGDOC_MULT

                in_outage = C.OUTAGE_START <= d <= C.OUTAGE_END
                err_p = C.ERROR_RATE_OUTAGE if in_outage else C.ERROR_RATE_BASE
                if RNG.random() < err_p:
                    mix_e = C.ERROR_MIX_OUTAGE if in_outage else C.ERROR_MIX_BASE
                    etype = RNG.choice(C.ERROR_TYPES, p=mix_e)
                    status = "Timeout" if etype == "Timeout" else "Error"
                    retry = int(RNG.integers(1, 4))
                    if etype == "Timeout":
                        lat = max(lat, 30000 * RNG.uniform(0.9, 1.1))
                else:
                    etype, status, retry = None, "Success", int(RNG.random() < 0.05)

                rows.append((law.lawyer_id, int(mids[ci]), tool_by_name[tool],
                             task_ids[task], int(d.strftime("%Y%m%d")),
                             dt.datetime.combine(d, dt.time(0)) + dt.timedelta(
                                 minutes=int(RNG.integers(8 * 60, 20 * 60))),
                             tin, tout, cost, int(lat), status, retry, etype,
                             tool_src[tool]))

    s = pd.DataFrame(rows, columns=[
        "lawyer_id", "matter_id", "tool_id", "task_type_id", "date_id", "started_at",
        "tokens_in", "tokens_out", "cost_aud", "latency_ms", "status", "retry_count",
        "error_type", "source_system"])
    s.insert(0, "session_id", np.arange(1, len(s) + 1))
    return s


print("building sessions ...", flush=True)
sessions_truth = build_sessions(dim_lawyer, dim_matter, assignments, dim_tool,
                                seat_holders)
print(f"  sessions (before pipeline loss) = {len(sessions_truth):,}", flush=True)


# --------------------------------------------- fact_pipeline_run + the losses
def build_pipeline_runs(sessions: pd.DataFrame, tools: pd.DataFrame):
    """Model the ingest layer, and actually lose the rows it says it lost.

    A monitoring table that reports a failure while every downstream fact
    remains intact is theatre. The rows the pipeline drops are removed from
    fact_ai_session, so the Page 1 health strip explains a real dip.
    """
    days = pd.date_range(C.WINDOW_START, C.WINDOW_END, freq="D").date
    counts = (sessions.groupby(["source_system", "date_id"]).size()
              .rename("n").reset_index())
    lookup = {(r.source_system, r.date_id): r.n for r in counts.itertuples()}

    runs, drop_ids = [], []
    rid = 0
    for src in tools["source_system"]:
        for d in days:
            did = int(d.strftime("%Y%m%d"))
            true_rows = int(lookup.get((src, did), 0))
            ref_fail = 0
            rejected = 0

            in_gap = (src == C.PIPELINE_GAP_SOURCE
                      and C.PIPELINE_GAP_START <= d <= C.PIPELINE_GAP_END)
            if in_gap:
                loaded = 0
                status = "Failed"
                day_ids = sessions.loc[(sessions["source_system"] == src)
                                       & (sessions["date_id"] == did), "session_id"]
                drop_ids.extend(day_ids.tolist())
            else:
                if src == C.REFERENTIAL_FAIL_SOURCE and true_rows:
                    ref_fail = int(RNG.binomial(true_rows, C.REFERENTIAL_FAIL_RATE))
                    rejected = ref_fail
                    if ref_fail:
                        pool = sessions.loc[(sessions["source_system"] == src)
                                            & (sessions["date_id"] == did), "session_id"]
                        drop_ids.extend(RNG.choice(pool.to_numpy(), ref_fail,
                                                   replace=False).tolist())
                loaded = true_rows - rejected
                status = "Warning" if ref_fail else "Success"

            rid += 1
            runs.append((rid, src, d, did, loaded, rejected,
                         round(float(RNG.uniform(0, 0.004)), 5), ref_fail, status,
                         int(max(20, RNG.normal(45 + loaded * 0.012, 12)))))

    runs_df = pd.DataFrame(runs, columns=[
        "run_id", "source_system", "run_date", "date_id", "rows_loaded",
        "rows_rejected", "null_rate_key_fields", "referential_failures",
        "run_status", "duration_seconds"])
    return runs_df, set(drop_ids)


print("building pipeline runs ...", flush=True)
pipeline_runs, lost_ids = build_pipeline_runs(sessions_truth, dim_tool)
fact_ai_session = sessions_truth[~sessions_truth["session_id"].isin(lost_ids)].copy()
print(f"  pipeline dropped {len(lost_ids):,} rows; "
      f"observed sessions = {len(fact_ai_session):,}", flush=True)


# ------------------------------------------------------------- fact_ai_output
LEVEL_UP = {"Graduate": "Associate", "Associate": "Senior Associate",
            "Senior Associate": "Partner", "Partner": "Partner"}
REVIEW_MINUTES = {"Research": 25, "Drafting": 40, "Due Diligence Review": 55,
                  "Summarisation": 15, "Translation": 12, "Correspondence": 8}


def build_outputs(sessions, lawyers, matters, tasks) -> pd.DataFrame:
    s = sessions[sessions["status"] == "Success"].copy()
    s = s[RNG.random(len(s)) < 0.95]                 # not every call yields an output

    s = s.merge(tasks[["task_type_id", "task_name", "review_policy_base"]],
                on="task_type_id", how="left")
    s = s.merge(matters[["matter_id", "practice_area", "confidentiality_tier"]],
                on="matter_id", how="left")
    s = s.merge(lawyers[["lawyer_id", "level", "gen_cohort", "gen_diligence"]],
                on="lawyer_id", how="left")

    n = len(s)
    task = s["task_name"].to_numpy()
    area = s["practice_area"].to_numpy()
    tier = s["confidentiality_tier"].to_numpy()

    client_facing = (RNG.random(n) < np.array([C.CLIENT_FACING_P[t] for t in task])).astype(int)

    # mirror of vw_effective_review_policy -- the view is authoritative, this is
    # only how the observed `reviewed` flag was produced
    mandatory = ((s["review_policy_base"].to_numpy() == "Mandatory")
                 | np.isin(tier, ["Restricted", "Barrier"])
                 | (client_facing == 1))

    med = np.array([C.EDIT_MEDIAN_BY_AREA[a] for a in area]) * \
          np.array([C.EDIT_TASK_ADJ[t] for t in task])
    edit = np.clip(med * np.exp(RNG.normal(0, 0.55, n)), 0.01, 0.95)

    p_acc = np.clip(C.ACCEPT_BASE + C.ACCEPT_EDIT_PENALTY * (0.20 - edit), 0.15, 0.95)
    accepted = (RNG.random(n) < p_acc).astype(int)

    cohort = s["gen_cohort"].to_numpy()
    dil = s["gen_diligence"].to_numpy()
    base_rate = np.where(mandatory,
                         np.array([C.MANDATORY_REVIEW_RATE[c] for c in cohort]),
                         np.array([C.OPTIONAL_REVIEW_RATE[c] for c in cohort]))
    reviewed = (RNG.random(n) < np.clip(base_rate * dil, 0.02, 0.99)).astype(int)

    rev_level = np.where(reviewed == 1,
                         np.array([LEVEL_UP[l] for l in s["level"].to_numpy()]), None)
    minutes = np.where(
        reviewed == 1,
        np.round(np.array([REVIEW_MINUTES[t] for t in task]) * (1 + edit)
                 * RNG.uniform(0.7, 1.4, n)).astype(int), None)

    out = pd.DataFrame({
        "session_id": s["session_id"].to_numpy(),
        "lawyer_id": s["lawyer_id"].to_numpy(),
        "matter_id": s["matter_id"].to_numpy(),
        "tool_id": s["tool_id"].to_numpy(),
        "task_type_id": s["task_type_id"].to_numpy(),
        "date_id": s["date_id"].to_numpy(),
        "accepted": accepted,
        "edit_distance_pct": np.round(edit * 100, 1),
        "is_client_facing": client_facing,
        "reviewed": reviewed,
        "reviewed_by_level": rev_level,
        "minutes_to_review": minutes,
    })
    out.insert(0, "output_id", np.arange(1, len(out) + 1))
    return out


print("building outputs ...", flush=True)
fact_ai_output = build_outputs(fact_ai_session, dim_lawyer, dim_matter, dim_task_type)
print(f"  outputs = {len(fact_ai_output):,}", flush=True)


# ----------------------------------------------------------- fact_time_entry
def build_time_entries(lawyers, matters, assignments, sessions_truth,
                       sessions_observed) -> pd.DataFrame:
    """Daily timesheet lines, then the AI effect applied within matter x level.

    The hours reduction is applied per lawyer-matter, after that lawyer's first
    session on the matter, and only where use is sustained. Freed hours are then
    either reinvested on that lawyer's other matters within 30 days, or lost.
    """
    bdays = pd.bdate_range(C.WINDOW_START, C.WINDOW_END)
    bord = bdays.map(dt.date.toordinal).to_numpy()
    m_idx = matters.set_index("matter_id")
    open_ord = m_idx["opened_date"].map(dt.date.toordinal)
    close_ord = m_idx["closed_date"].map(lambda c: c.toordinal() if c is not None else 10**7)
    lawyer_matters = {lid: g["matter_id"].to_numpy() for lid, g in assignments.groupby("lawyer_id")}

    frames = []
    for law in lawyers.itertuples():
        mids = lawyer_matters.get(law.lawyer_id)
        if mids is None or len(mids) == 0:
            continue
        active = ((open_ord.loc[mids].to_numpy()[None, :] <= bord[:, None])
                  & (close_ord.loc[mids].to_numpy()[None, :] >= bord[:, None]))
        worked = (RNG.random(len(bord)) > C.LEAVE_RATE) & active.any(axis=1)
        if not worked.any():
            continue
        d_idx = np.flatnonzero(worked)
        act = active[d_idx]

        want = RNG.choice([1, 2, 3], len(d_idx), p=C.MATTERS_PER_DAY)
        n_lines = np.minimum(want, act.sum(axis=1))

        scores = RNG.random(act.shape) * act
        order = np.argsort(-scores, axis=1)

        mu, sd = C.DAILY_HOURS[law.level]
        day_hours = np.clip(RNG.normal(mu, sd, len(d_idx)), 1.5, 14.0)

        rows_day, rows_matter, rows_hours = [], [], []
        for k in range(len(d_idx)):
            nl = int(n_lines[k])
            if nl == 0:
                continue
            picks = order[k, :nl]
            w = RNG.random(nl) + 0.25
            w = w / w.sum()
            for j, mi in enumerate(picks):
                rows_day.append(bord[d_idx[k]])
                rows_matter.append(int(mids[mi]))
                rows_hours.append(day_hours[k] * w[j])

        if not rows_day:
            continue
        frames.append(pd.DataFrame({
            "lawyer_id": law.lawyer_id,
            "date_ord": rows_day,
            "matter_id": rows_matter,
            "hours": rows_hours,
        }))

    te = pd.concat(frames, ignore_index=True)
    te["billable"] = (RNG.random(len(te))
                      < te["lawyer_id"].map(dict(zip(lawyers["lawyer_id"],
                          lawyers["level"].map(C.BILLABLE_SHARE))))).astype(int)
    te["task_code"] = RNG.choice(C.TASK_CODES, len(te))

    # --- the AI effect, applied within lawyer x matter ---------------------
    use = (sessions_truth.groupby(["lawyer_id", "matter_id"])
           .agg(n=("session_id", "size"), first_id=("date_id", "min")).reset_index())
    use = use[use["n"] >= C.AI_INTENSITY_THRESHOLD]
    use["first_ord"] = use["first_id"].map(
        lambda x: dt.date(x // 10000, x // 100 % 100, x % 100).toordinal())

    te = te.merge(use[["lawyer_id", "matter_id", "first_ord"]],
                  on=["lawyer_id", "matter_id"], how="left")
    te = te.merge(matters[["matter_id", "fee_arrangement"]], on="matter_id", how="left")

    affected = te["first_ord"].notna() & (te["date_ord"] >= te["first_ord"].fillna(10**9))
    factor = 1 + te["fee_arrangement"].map(C.HOURS_EFFECT_BY_FEE).astype(float)
    te["hours_base"] = te["hours"].copy()
    te.loc[affected, "hours"] = te.loc[affected, "hours"] * factor[affected]
    te["freed"] = te["hours_base"] - te["hours"]

    # --- reallocation: freed hours reappear elsewhere, or they do not ------
    # A heavy adopter can have every matter they touched in a 30-day window
    # under AI, leaving nowhere inside that window for the freed hours to land.
    # Those spill into the quarter. What still has nowhere to go is capacity
    # genuinely lost, which is the honest outcome for that lawyer.
    reinvests = dict(zip(lawyers["lawyer_id"], lawyers["gen_reinvests"]))
    origin = te["date_ord"].min()
    te["bucket"] = (te["date_ord"] - origin) // C.REINVEST_WINDOW_DAYS
    te["quarter"] = te["bucket"] // 3      # buckets nest exactly into quarters
    te["reinvests"] = te["lawyer_id"].map(reinvests).fillna(False).astype(bool)

    open_row = (~affected) & te["reinvests"]
    te["w"] = te["hours"].where(open_row, 0.0)
    te["cap_b"] = te.groupby(["lawyer_id", "bucket"])["w"].transform("sum")
    te["cap_q"] = te.groupby(["lawyer_id", "quarter"])["w"].transform("sum")

    pool = (te.loc[affected & te["reinvests"]]
              .groupby(["lawyer_id", "bucket"])["freed"].sum()
              .rename("pool").reset_index())
    pool["quarter"] = pool["bucket"] // 3
    cap_lookup = te[["lawyer_id", "bucket", "cap_b"]].drop_duplicates(["lawyer_id", "bucket"])
    pool = pool.merge(cap_lookup, on=["lawyer_id", "bucket"], how="left").fillna({"cap_b": 0})
    direct = pool.loc[pool["cap_b"] > 0, ["lawyer_id", "bucket", "pool"]]
    spill = (pool.loc[pool["cap_b"] <= 0].groupby(["lawyer_id", "quarter"])["pool"]
             .sum().rename("pool_q").reset_index())

    te = te.merge(direct, on=["lawyer_id", "bucket"], how="left")
    te = te.merge(spill, on=["lawyer_id", "quarter"], how="left")
    add = (te["pool"].fillna(0) * (te["w"] / te["cap_b"].replace(0, np.nan)).fillna(0)
           + te["pool_q"].fillna(0) * (te["w"] / te["cap_q"].replace(0, np.nan)).fillna(0))
    te["hours"] = te["hours"] + add
    te.loc[add > 0, "billable"] = 1

    cap = te.groupby("lawyer_id").agg(
        hours_base=("hours_base", "sum"), hours_final=("hours", "sum"),
        freed=("freed", "sum")).reset_index()
    cap["returned"] = te.assign(add=add).groupby("lawyer_id")["add"].sum().to_numpy()
    cap.to_csv(ROOT / C.GROUND_TRUTH_DIR / "_ground_truth_capacity.csv", index=False)

    print(f"    baseline hours {te['hours_base'].sum():,.0f}; "
          f"freed {te['freed'].sum():,.0f}; "
          f"returned {add.sum():,.0f} "
          f"({add.sum() / te['freed'].sum():.0%} of freed, "
          f"{add.sum() / (te['freed'] * te['reinvests']).sum():.0%} of the reinvesting share); "
          f"final {te['hours'].sum():,.0f}")
    te["date_id"] = te["date_ord"].map(lambda o: int(dt.date.fromordinal(o).strftime("%Y%m%d")))
    te["hours"] = te["hours"].round(2)
    # Carried on the fact so Power BI can build Page 3 from a clean star. The
    # flag depends on the session table, so without it the join lives only in a
    # view and the measure would need a fact-to-fact hop to reach it.
    # Derived from the sessions that reached the warehouse, not from the ones
    # that happened. The effect above is applied on real behaviour, because the
    # lawyer really did use the tool; this flag is what an analyst could
    # actually compute, so the rows the pipeline lost are missing from it too.
    seen = (sessions_observed.groupby(["lawyer_id", "matter_id"])
            .agg(n=("session_id", "size"), first_id=("date_id", "min")).reset_index())
    seen = seen[seen["n"] >= C.AI_INTENSITY_THRESHOLD]
    seen["seen_ord"] = seen["first_id"].map(
        lambda x: dt.date(x // 10000, x // 100 % 100, x % 100).toordinal())
    te = te.merge(seen[["lawyer_id", "matter_id", "seen_ord"]],
                  on=["lawyer_id", "matter_id"], how="left")
    te["is_post_adoption"] = (te["seen_ord"].notna()
                              & (te["date_ord"] >= te["seen_ord"].fillna(10**9))).astype(int)
    out = te[["lawyer_id", "matter_id", "date_id", "hours", "billable", "task_code",
              "is_post_adoption"]].copy()
    out.insert(0, "time_entry_id", np.arange(1, len(out) + 1))
    return out, te


print("building time entries ...", flush=True)
fact_time_entry, _te_full = build_time_entries(
    dim_lawyer, dim_matter, assignments, sessions_truth, fact_ai_session)
print(f"  time entries = {len(fact_time_entry):,}", flush=True)


# ---------------------------------------------------- effective review policy
def effective_mandatory(outputs, matters, tasks) -> np.ndarray:
    """Python mirror of vw_effective_review_policy. The SQL view is
    authoritative; this exists so the generator and the checks can reason about
    the same rule without querying the database."""
    o = outputs.merge(tasks[["task_type_id", "review_policy_base"]], on="task_type_id",
                      how="left")
    o = o.merge(matters[["matter_id", "confidentiality_tier"]], on="matter_id", how="left")
    return ((o["review_policy_base"].to_numpy() == "Mandatory")
            | np.isin(o["confidentiality_tier"].to_numpy(), ["Restricted", "Barrier"])
            | (o["is_client_facing"].to_numpy() == 1))


# ------------------------------------------------------------ fact_incident
def build_incidents(sessions, outputs, lawyers, matters, tasks) -> pd.DataFrame:
    mand = effective_mandatory(outputs, matters, tasks)
    o = outputs.copy()
    o["mandatory"] = mand
    completion = (o[o["mandatory"]].groupby("lawyer_id")["reviewed"].mean()
                  .rename("completion"))

    s = sessions.merge(matters[["matter_id", "confidentiality_tier"]], on="matter_id",
                       how="left")
    s = s.merge(lawyers[["lawyer_id", "gen_cohort"]], on="lawyer_id", how="left")
    s = s.merge(completion, on="lawyer_id", how="left")
    s["completion"] = s["completion"].fillna(0.9)

    def pick(frame, k, weights=None):
        if len(frame) == 0 or k == 0:
            return frame.head(0)
        w = None if weights is None else np.asarray(weights, dtype=float)
        if w is not None:
            w = np.clip(w, 1e-6, None)
            w = w / w.sum()
        idx = RNG.choice(len(frame), min(k, len(frame)), replace=False, p=w)
        return frame.iloc[idx]

    # pattern 6: incidents concentrate where review coverage is poor
    unreviewed = o[(o["mandatory"]) & (o["reviewed"] == 0) & (o["edit_distance_pct"] > 35)]
    heavy_edit = o[o["edit_distance_pct"] > 35]
    hall = pd.concat([
        pick(s[s["session_id"].isin(unreviewed["session_id"])], 110),
        pick(s[s["session_id"].isin(heavy_edit["session_id"])], 80),
    ]).drop_duplicates("session_id")

    sens = s[s["confidentiality_tier"].isin(["Restricted", "Barrier"])]
    conf = pick(sens, 120)
    breach_src = sens[sens["gen_cohort"] == "Non-compliant"]
    breach = pick(breach_src, 70)

    outage = s[(s["status"] != "Success")
               & (s["date_id"] >= int(C.OUTAGE_START.strftime("%Y%m%d")))
               & (s["date_id"] <= int(C.OUTAGE_END.strftime("%Y%m%d")))]
    svc = pick(outage, 70)

    parts = []
    for frame, itype, sev_p in [
        (hall, "Hallucination", [0.15, 0.45, 0.30, 0.10]),
        (conf, "Confidentiality flag", [0.10, 0.35, 0.40, 0.15]),
        (breach, "Policy breach", [0.05, 0.25, 0.45, 0.25]),
        (svc, "Service outage", [0.30, 0.45, 0.20, 0.05]),
    ]:
        if len(frame) == 0:
            continue
        n = len(frame)
        resolved = (RNG.random(n) < C.INCIDENT_RESOLVED_RATE).astype(int)
        parts.append(pd.DataFrame({
            "session_id": frame["session_id"].to_numpy(),
            "matter_id": frame["matter_id"].to_numpy(),
            "lawyer_id": frame["lawyer_id"].to_numpy(),
            "date_id": frame["date_id"].to_numpy(),
            "incident_type": itype,
            "severity": RNG.choice([1, 2, 3, 4], n, p=sev_p),
            "days_to_resolve": np.where(resolved == 1,
                                        np.round(RNG.gamma(2.2, 4.0, n)).astype(int), None),
            "resolved": resolved,
        }))
    inc = pd.concat(parts, ignore_index=True)
    inc.insert(0, "incident_id", np.arange(1, len(inc) + 1))
    return inc


print("building incidents ...", flush=True)
fact_incident = build_incidents(fact_ai_session, fact_ai_output, dim_lawyer,
                                dim_matter, dim_task_type)
print(f"  incidents = {len(fact_incident):,}", flush=True)


# --------------------------------------------------------- fact_seat_month
def build_seat_months(lawyers, tools, sessions, seats) -> pd.DataFrame:
    """One row per licensed seat per month, with whether it was used.

    Seats are allocated at rollout and held; usage is whatever happened. The
    difference between the two is what a firm is paying for nothing, and it is
    invisible on any dashboard that only counts sessions.
    """
    used = set(zip(sessions["lawyer_id"], sessions["tool_id"],
                   sessions["date_id"] // 100))

    rows = []
    for t in tools.itertuples():
        holders = np.array(sorted(seats[t.tool_name]))
        for mth in MONTHS:
            if mth < t.deployment_date.replace(day=1):
                continue
            ym = mth.year * 100 + mth.month
            did = int(mth.strftime("%Y%m%d"))
            for lid in holders:
                rows.append((int(lid), t.tool_id, did, 1,
                             t.licence_cost_per_seat_month,
                             int((int(lid), t.tool_id, ym) in used)))

    df = pd.DataFrame(rows, columns=["lawyer_id", "tool_id", "date_id", "seats",
                                     "licence_cost_aud", "used_in_month"])
    df.insert(0, "seat_month_id", np.arange(1, len(df) + 1))
    return df


print("building seat months ...", flush=True)
fact_seat_month = build_seat_months(dim_lawyer, dim_tool, fact_ai_session, seat_holders)
_idle = 1 - fact_seat_month["used_in_month"].mean()
print(f"  seat-months = {len(fact_seat_month):,}; "
      f"licence spend = ${fact_seat_month['licence_cost_aud'].sum():,.0f}; "
      f"idle {_idle:.1%}", flush=True)


# --------------------------------------------------------- dim_alert_rule
ALERT_RULES = [
    # Operational -- thresholds set by what keeps a tool usable
    ("p95 latency above 8s", "Operational", "p95 latency over trailing 24h", 8000, ">",
     "High", "IT Operations",
     "Past roughly eight seconds a lawyer stops waiting and goes back to doing it by "
     "hand. The threshold is set at the point of abandonment, not at a system limit."),
    ("Error rate above 5% over 24h", "Operational", "failed or timed-out sessions / all sessions",
     0.05, ">", "High", "IT Operations",
     "Below 5% users retry and carry on; above it they stop trusting the tool, and "
     "trust is far slower to rebuild than uptime."),
    ("Feed loaded zero rows", "Operational", "rows_loaded for any source on any day", 0, "=",
     "Critical", "Data Engineering",
     "A silent zero-row load is indistinguishable on every other page from a genuine "
     "drop in usage. This is the only rule that catches it."),
    ("Feed stale beyond 36 hours", "Operational", "hours since last successful run", 36, ">",
     "High", "Data Engineering",
     "A dashboard built on a stale extract looks entirely healthy. Staleness is "
     "invisible everywhere except here."),
    ("Referential failures above 1% of a source", "Operational",
     "referential_failures / rows_loaded, 7-day", 0.01, ">", "Medium", "Data Engineering",
     "A chronic low-level mismatch never trips a daily threshold, yet every metric "
     "downstream is quietly understated. Trend, not status light."),
    # Quality
    ("Median edit distance above 45%", "Quality", "median edit_distance_pct by task type",
     45, ">", "Medium", "AI Operations",
     "Beyond roughly half rewritten, the tool is costing more time than it saves for "
     "that task, whatever the acceptance rate says."),
    ("Acceptance rate falls 15pp month on month", "Quality", "change in acceptance rate",
     -0.15, "<", "Medium", "AI Operations",
     "A sharp drop usually means a model or prompt change reached production without "
     "anyone telling the people who rely on it."),
    # Cost
    ("Cost per accepted output above $12", "Cost",
     "(licence + token cost) / accepted outputs", 12, ">", "Medium", "Finance",
     "Cost per call rewards a tool that is cheap and useless. Only cost per output a "
     "lawyer actually kept is a real unit cost, and it has to include licences: token "
     "spend is under 1% of what the firm pays. Counting tokens alone gives about 10 "
     "cents an output and makes the whole programme look free. The real figure is "
     "around $10.60, which is what the threshold is set against. Review time is still "
     "excluded, so this remains an understatement."),
    ("Idle seat share above 40% for a paid tool", "Cost",
     "seat-months with no session / seat-months, per tool per quarter", 0.40, ">",
     "Medium", "Finance",
     "Seats are bought at rollout and renewed annually; usage is checked, if at all, "
     "by someone looking at a session count. The gap between the two is money already "
     "spent. It is also the cheapest thing on this list to fix, because unlike adoption "
     "it needs no behaviour change from anyone -- just a reconciliation before renewal. "
     "The threshold is loose on purpose: some idle share is the cost of keeping a seat "
     "open for occasional need."),
    ("Monthly cost 30% above trailing 3-month mean", "Cost",
     "latest complete month (licence + token cost) / previous 3 calendar months mean - 1", 0.30, ">",
     "Low", "Finance",
     "Compares licence plus token cost in the latest complete calendar month with "
     "the previous three consecutive calendar months. Completeness uses the latest "
     "pipeline date, including non-working days. Missing months or a zero baseline "
     "are unevaluated, not healthy. Historical peaks do not keep the current alert "
     "open. Planned rollout or renewal changes still need Finance review."),
    # Governance -- deliberately tighter, and the rationale says why
    ("Review coverage below 95% on Barrier matters", "Governance",
     "review coverage where confidentiality_tier = Barrier", 0.95, "<", "Critical",
     "General Counsel",
     "The operational rules above tolerate a few percent of failure because the cost of "
     "a failure is a lawyer's wasted minute. Here the cost of a failure is privileged "
     "information crossing an information barrier, which is not recoverable and is "
     "reportable. A tolerance that is sensible for latency is negligent for a barrier."),
    ("Any unreviewed output where review is Mandatory", "Governance",
     "count of outputs, effective policy Mandatory and reviewed = 0", 0, ">", "Critical",
     "Risk & Compliance",
     "Expressed as a count, not a rate. A rate invites a tolerance, and the acceptable "
     "number of unverified citations relied on in client work is zero."),
    ("First use of a newly deployed tool on a Restricted matter", "Governance",
     "first session for a tool where tier in (Restricted, Barrier)", 1, ">=", "High",
     "General Counsel",
     "New tools reach sensitive work before the policy that governs them does. This "
     "fires on the first occurrence, not on a volume threshold, because the point is "
     "to review the decision before it becomes a pattern."),
    ("Lawyer active across two barrier groups in 90 days", "Governance",
     "distinct barrier_group_id per lawyer, rolling 90 days", 2, ">=", "Critical",
     "General Counsel",
     "A screening signal, not a finding: barriers lift, people get reassigned, groups "
     "close. It is deliberately sensitive because the cost of one missed crossing "
     "outweighs the cost of checking a short list by hand."),
    ("Lawyer mandatory review completion below 70%", "Governance",
     "reviewed / mandatory outputs per lawyer per quarter, minimum 25 outputs", 0.70,
     "<", "Critical", "Risk & Compliance",
     "Low mandatory review completion identifies cases for follow-up, not a finding "
     "of misconduct. The full-window Top 20 comparison is recorded in "
     "results/screening_comparison.csv and rendered in the rule catalogue. It uses "
     "synthetic ground truth withheld from the report, not an independent test "
     "sample. Lawyers with fewer than 25 mandatory outputs are excluded by the "
     "eligibility rule. A quarterly alert or a different filter requires its own "
     "evaluation. Incomplete review can also reflect delay rather than deliberate "
     "non-compliance."),
    ("Restricted-tier session rate above 3x the firm median", "Governance",
     "share of a lawyer's sessions on Restricted or Barrier matters, per quarter", 3, ">",
     "Low", "Risk & Compliance",
     "Sensitive-session exposure provides context for the review list. Assignment "
     "to sensitive matters can drive this rate without implying misconduct. The "
     "full-window Top 20 comparison in results/screening_comparison.csv shows why "
     "exposure alone is a weaker screen in this synthetic scenario. The catalogue "
     "renders those results separately from this quarterly alert threshold."),
]

dim_alert_rule = pd.DataFrame(ALERT_RULES, columns=[
    "rule_name", "category", "condition_text", "threshold_value", "comparison",
    "severity", "owner_role", "rationale"])
dim_alert_rule.insert(0, "rule_id", np.arange(1, len(dim_alert_rule) + 1))


# ------------------------------------------------------------------- write
def write_all():
    raw = ROOT / C.OUT_DIR
    gt = ROOT / C.GROUND_TRUTH_DIR
    raw.mkdir(parents=True, exist_ok=True)
    gt.mkdir(parents=True, exist_ok=True)

    gen_cols = [c for c in dim_lawyer.columns if c.startswith("gen_")]
    published_lawyer = dim_lawyer.drop(columns=gen_cols)

    tables = {
        "dim_date": dim_date,
        "dim_lawyer": published_lawyer,
        "dim_matter": dim_matter,
        "dim_tool": dim_tool.drop(columns=["source_system"]),
        "dim_task_type": dim_task_type,
        "dim_alert_rule": dim_alert_rule,
        "fact_ai_session": fact_ai_session,
        "fact_ai_output": fact_ai_output,
        "fact_time_entry": fact_time_entry,
        "fact_incident": fact_incident,
        "fact_pipeline_run": pipeline_runs,
        "fact_seat_month": fact_seat_month,
    }
    for name, df in tables.items():
        # Fixed-point, not the default repr: pandas writes small floats as
        # "4e-05", which BULK INSERT cannot parse into DECIMAL.
        df.to_csv(raw / f"{name}.csv", index=False, date_format="%Y-%m-%d",
                  float_format="%.6f")

    # Held out on purpose. policy_cohort is NOT a column in dim_lawyer: shipping
    # it would let Page 5 filter on the answer instead of finding it. This file
    # exists only so the consistency checks can confirm the intended effect was
    # produced. See docs/DATA_MODEL.md section 5.
    dim_lawyer[["lawyer_id"] + gen_cols].to_csv(
        gt / "_ground_truth_lawyer.csv", index=False)
    pd.DataFrame([{"sessions_generated": len(sessions_truth),
                   "sessions_lost_to_pipeline": len(lost_ids),
                   "sessions_published": len(fact_ai_session)}]).to_csv(
        gt / "_ground_truth_pipeline_loss.csv", index=False)

    total = sum(len(d) for d in tables.values())
    print(f"\nwrote {len(tables)} tables to {C.OUT_DIR}/  ({total:,} rows)")
    for name, df in tables.items():
        print(f"  {name:22s} {len(df):>9,}")


write_all()
