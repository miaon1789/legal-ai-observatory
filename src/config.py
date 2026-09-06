"""Generator parameters for the synthetic legal-AI operations warehouse.

Every number here is an assumption, not an observation. The ones that rest on
practitioner judgement rather than arithmetic are marked JUDGEMENT and are
listed in docs/DATA_MODEL.md section 6.
"""
from __future__ import annotations

import datetime as dt

SEED = 20260903

# --- window -------------------------------------------------------------
WINDOW_START = dt.date(2025, 3, 1)
WINDOW_END = dt.date(2026, 8, 31)
CALENDAR_START = dt.date(2025, 1, 1)   # dim_date is padded to whole years so
CALENDAR_END = dt.date(2026, 12, 31)   # Power BI time intelligence stays clean

N_LAWYERS = 400
N_MATTERS = 1100
SESSION_CAP = 130_000

# --- lawyers ------------------------------------------------------------
LEVELS = ["Partner", "Senior Associate", "Associate", "Graduate"]
LEVEL_MIX = [0.20, 0.25, 0.35, 0.20]

# JUDGEMENT: notional list charge-out rates, AUD/hr, GST-exclusive.
# Top-tier list rates are not published; see docs/DATA_MODEL.md section 6.
CHARGE_RATE = {"Partner": 1150, "Senior Associate": 720, "Associate": 520, "Graduate": 330}
RATE_JITTER = 0.125          # +/- 12.5% individual variation
YEARS_ADMITTED = {"Partner": (12, 25), "Senior Associate": (6, 11),
                  "Associate": (2, 5), "Graduate": (0, 1)}

OFFICES = ["Sydney", "Melbourne", "Brisbane", "Perth"]
OFFICE_MIX = [0.40, 0.30, 0.17, 0.13]

INACTIVE_RATE = 0.04

# --- practice areas -----------------------------------------------------
# Real Estate and Employment weighted up: both use fixed/capped fees more, which
# is both realistic and what keeps Page 3's non-hourly cells large enough.
PRACTICE_AREAS = ["Litigation", "Real Estate", "M&A", "Banking & Finance", "Employment"]
AREA_MIX = [0.22, 0.22, 0.20, 0.18, 0.18]

FEE_BY_AREA = {                      # Hourly, Capped, Fixed Fee
    "Litigation":         [0.85, 0.12, 0.03],
    "Banking & Finance":  [0.55, 0.30, 0.15],
    "M&A":                [0.50, 0.30, 0.20],
    "Employment":         [0.45, 0.20, 0.35],
    "Real Estate":        [0.35, 0.25, 0.40],
}
FEE_ARRANGEMENTS = ["Hourly", "Capped", "Fixed Fee"]

# JUDGEMENT. Assigned independently of practice area on purpose: clustering
# barrier work in one group would make Page 5 read as a departmental problem.
TIERS = ["Standard", "Restricted", "Barrier"]
TIER_MIX = [0.85, 0.12, 0.03]
N_BARRIER_GROUPS = 8

CLIENT_SECTORS = ["Financial Services", "Mining & Resources", "Technology",
                  "Healthcare", "Energy & Infrastructure", "Retail & Consumer",
                  "Government", "Property"]

# matter duration in months, by area
MATTER_MONTHS = {"Litigation": (12, 26), "M&A": (3, 9), "Banking & Finance": (2, 8),
                 "Employment": (2, 6), "Real Estate": (1, 4)}
TEAM_SIZE = (2, 6)

# --- tools --------------------------------------------------------------
# Harvey lands later than the rest, which is what makes the governance alert
# "first use of a newly deployed tool on a restricted matter" mean anything.
TOOLS = [
    # name,       deployment,          in $/1k, out $/1k, source_system
    ("Copilot",   dt.date(2025, 3, 1), 0.0042, 0.0165, "m365_audit"),
    ("Firm Chat", dt.date(2025, 3, 1), 0.0031, 0.0120, "firm_chat_log"),
    ("Harvey",    dt.date(2025, 8, 1), 0.0110, 0.0380, "harvey_api"),
]
TOOL_SHARE_AFTER_HARVEY = {"Copilot": 0.42, "Firm Chat": 0.24, "Harvey": 0.34}
TOOL_SHARE_BEFORE_HARVEY = {"Copilot": 0.62, "Firm Chat": 0.38, "Harvey": 0.0}

# --- task types ---------------------------------------------------------
TASK_TYPES = [
    # name,                 risk,     base policy,  review_type
    ("Research",            "High",   "Mandatory",  "Citation verification"),
    ("Drafting",            "High",   "Mandatory",  "Supervisory review"),
    ("Due Diligence Review","Medium", "Mandatory",  "Sample audit"),
    ("Summarisation",       "Medium", "Optional",   "Supervisory review"),
    ("Translation",         "Medium", "Optional",   "Supervisory review"),
    ("Correspondence",      "Low",    "Optional",   "Supervisory review"),
]

# task mix conditional on practice area
TASK_MIX_BY_AREA = {
    "Litigation":        {"Research": .38, "Drafting": .20, "Due Diligence Review": .05,
                          "Summarisation": .22, "Translation": .03, "Correspondence": .12},
    "M&A":               {"Research": .12, "Drafting": .30, "Due Diligence Review": .30,
                          "Summarisation": .13, "Translation": .05, "Correspondence": .10},
    "Banking & Finance": {"Research": .14, "Drafting": .34, "Due Diligence Review": .20,
                          "Summarisation": .14, "Translation": .04, "Correspondence": .14},
    "Real Estate":       {"Research": .12, "Drafting": .36, "Due Diligence Review": .18,
                          "Summarisation": .14, "Translation": .02, "Correspondence": .18},
    "Employment":        {"Research": .24, "Drafting": .28, "Due Diligence Review": .06,
                          "Summarisation": .18, "Translation": .02, "Correspondence": .22},
}

# JUDGEMENT: probability an output leaves the firm, by task type.
CLIENT_FACING_P = {"Correspondence": .80, "Drafting": .55, "Translation": .45,
                   "Summarisation": .28, "Due Diligence Review": .20, "Research": .12}

# --- pattern 1 & 2: adoption -------------------------------------------
# monthly-active probability at plateau, by level
ADOPTION_BY_LEVEL = {"Graduate": 0.78, "Associate": 0.65,
                     "Senior Associate": 0.48, "Partner": 0.22}
# multiplier on adoption by the lawyer's practice group; M&A / Litigation = 2.5
ADOPTION_BY_AREA = {"M&A": 1.15, "Banking & Finance": 1.10, "Real Estate": 0.92,
                    "Employment": 0.87, "Litigation": 0.46}
ADOPTION_CEILING = 0.98
# The rates above are the monthly-active rates we want to OBSERVE. A lawyer who
# has adopted still has quiet months and may drop out, so the latent propensity
# threshold has to sit above the target by roughly those two factors.
ADOPTION_CALIBRATION = 1.52

# sessions per active lawyer-month
INTENSITY_BY_LEVEL = {"Graduate": 55, "Associate": 40, "Senior Associate": 25, "Partner": 12}
INTENSITY_BY_AREA = {"M&A": 1.30, "Banking & Finance": 1.25, "Real Estate": 1.00,
                     "Employment": 0.95, "Litigation": 0.55}
RAMP_MONTHS = 9              # months to reach plateau
RAMP_FLOOR = 0.30            # share of plateau in month 1

# --- pattern 3: edit distance ------------------------------------------
# JUDGEMENT: median share of AI output a lawyer rewrites, by practice area
EDIT_MEDIAN_BY_AREA = {"Litigation": 0.34, "Employment": 0.26, "Banking & Finance": 0.16,
                       "Real Estate": 0.14, "M&A": 0.12}
EDIT_TASK_ADJ = {"Research": 1.15, "Drafting": 1.05, "Due Diligence Review": 0.85,
                 "Summarisation": 0.80, "Translation": 0.70, "Correspondence": 0.90}
ACCEPT_BASE = 0.72
ACCEPT_EDIT_PENALTY = 0.55   # acceptance falls as edit distance rises

# --- pattern 4: fee-arrangement asymmetry ------------------------------
# Deliberately close together. The finding is that identical savings mean
# opposite things, not that one is bigger.
HOURS_EFFECT_BY_FEE = {"Fixed Fee": -0.11, "Capped": -0.10, "Hourly": -0.09}
AI_INTENSITY_THRESHOLD = 3   # sessions on a matter before the effect applies
REINVEST_SHARE = 0.60        # share of lawyers whose freed hours reappear elsewhere
REINVEST_WINDOW_DAYS = 30

# --- pattern 5: governance, one cause ----------------------------------
NONCOMPLIANT_SHARE = 0.04
TIER_USE_MULT = {                       # multiplier on picking a sensitive matter
    "Compliant":     {"Standard": 1.0, "Restricted": 0.10, "Barrier": 0.10},
    "Non-compliant": {"Standard": 1.0, "Restricted": 0.85, "Barrier": 0.85},
}
MANDATORY_REVIEW_RATE = {"Compliant": 0.94, "Non-compliant": 0.55}
OPTIONAL_REVIEW_RATE = {"Compliant": 0.34, "Non-compliant": 0.18}
# Per-lawyer variation in review diligence. Wide enough that the merely sloppy
# overlap with the cohort: if the two groups separate perfectly, Page 5 reduces
# to one filter and stops being analysis. The screen should produce a short list
# that a human still has to check.
REVIEW_DILIGENCE_SD = 0.14
DILIGENCE_CLIP = (0.55, 1.10)
# A slice of otherwise compliant lawyers who are simply careless. They exist so
# that the Page 5 screen returns a list containing both kinds of person, and no
# metric in the model can tell them apart. Distinguishing "ignored the policy"
# from "meant to and did not get to it" is a conversation, not a query.
CARELESS_SHARE = 0.06
CARELESS_FACTOR = (0.62, 0.78)

# --- pattern 6: incidents ----------------------------------------------
N_INCIDENTS_TARGET = 450
INCIDENT_RESOLVED_RATE = 0.80

# --- pattern 7: latency and the service failure ------------------------
LATENCY_P95_MS = 4200
LATENCY_LOG_SD = 0.62
HARVEY_LONGDOC_MULT = 1.45   # Harvey is slower on long-document tasks
LONG_DOC_TASKS = {"Drafting", "Due Diligence Review", "Summarisation"}
ERROR_RATE_BASE = 0.018
OUTAGE_START = dt.date(2025, 11, 10)
OUTAGE_END = dt.date(2025, 11, 14)      # 5 days
ERROR_RATE_OUTAGE = 0.14
ERROR_TYPES = ["Timeout", "RateLimit", "UpstreamError", "ContextLengthExceeded"]
ERROR_MIX_BASE = [0.30, 0.25, 0.28, 0.17]
ERROR_MIX_OUTAGE = [0.82, 0.06, 0.09, 0.03]

# --- pattern 8: the planted pipeline failure ---------------------------
# Deliberately in a different month from the service outage above.
PIPELINE_GAP_SOURCE = "m365_audit"       # the Copilot feed
PIPELINE_GAP_START = dt.date(2026, 2, 16)
PIPELINE_GAP_END = dt.date(2026, 2, 18)  # 3 days
REFERENTIAL_FAIL_SOURCE = "firm_chat_log"
REFERENTIAL_FAIL_RATE = 0.02             # chronic, never trips a daily threshold

# --- time entries -------------------------------------------------------
DAILY_HOURS = {"Partner": (6.5, 1.6), "Senior Associate": (8.2, 1.5),
               "Associate": (8.6, 1.5), "Graduate": (8.4, 1.6)}
BILLABLE_SHARE = {"Partner": 0.68, "Senior Associate": 0.82,
                  "Associate": 0.86, "Graduate": 0.83}
MATTERS_PER_DAY = [0.30, 0.45, 0.25]     # 1, 2 or 3 matter lines in a day
LEAVE_RATE = 0.085
TASK_CODES = ["A101 Plan and prepare", "A102 Research", "A103 Draft/revise",
              "A104 Review/analyse", "A105 Communicate", "A106 Court/hearing"]

OUT_DIR = "data/raw"
GROUND_TRUTH_DIR = "data/out"

# mean tokens (in, out) by task type
TOKENS_BY_TASK = {
    "Research":             (3000, 1200),
    "Drafting":             (4500, 2200),
    "Due Diligence Review": (9000, 1500),
    "Summarisation":        (6000,  900),
    "Translation":          (2500, 2400),
    "Correspondence":       ( 900,  500),
}
QUIET_MONTH_P = 0.12       # an adopter with no sessions in a given month
# Adoption and intensity ramp separately: the population of adopters grows
# slowly, but a lawyer who has adopted uses the tool fairly consistently from
# the start. Ramping both on one curve squares the effect and thins the data.
INTENSITY_RAMP_FLOOR = 0.65

# --- licence cost -------------------------------------------------------
# Token spend is about 1% of what a firm pays for these tools. The cost that
# decides whether a rollout is defensible is per-seat licensing, and the
# question it opens -- what are we paying for seats nobody uses -- is one only
# someone who has watched a firm buy software thinks to ask.
# JUDGEMENT: indicative AUD per seat per month.
LICENCE_COST_PER_SEAT_MONTH = {"Harvey": 140.0, "Copilot": 45.0, "Firm Chat": 8.0}
# Copilot and the internal chat tool go to everyone; Harvey is allocated to a
# subset at rollout, weighted toward transactional practice but NOT toward the
# individual lawyers who turn out to use it. Allocation is not usage, and the
# gap between them is the point.
UNIVERSAL_SEAT_TOOLS = {"Copilot", "Firm Chat"}
HARVEY_SEAT_SHARE = 0.60
HARVEY_SEAT_AREA_WEIGHT = {"M&A": 1.5, "Banking & Finance": 1.4, "Real Estate": 0.9,
                           "Employment": 0.8, "Litigation": 0.5}
