"""
Valuation Engine V1 — global assumptions (mechanical, tunable, backtest-friendly).

All DCF knobs live here; do not hardcode rates inside valuation_engine.py.

=== BASELINE FROZEN (do not retune to match single-stock market prices) ===
  Growth Engine          v1.1  — revenue-anchored normalized growth
  Share-count integrity  v1.2  — MarketCap ≈ Price×Shares or Est.Value = —
  Tiered fallback Kd     v1.3  — mutually exclusive D/E & Debt/FCFF spreads
  MOS price protection       — Watchlist row price only; stale > MOS_PRICE_STALE_HOURS → —

Policy: anomalies → record as validation cases (see valuation_validation_cases.py).
Only change this config when the same failure mode appears across a class of
comparable companies — never to pull one ticker's Est.Value toward its market price.
=========================================================================
"""

from __future__ import annotations

# Set True while baseline is frozen; flip only when intentionally shipping a rule change.
VALUATION_BASELINE_FROZEN = True
VALUATION_BASELINE_LABEL = "v1.3-frozen"

# --- Market / WACC ---
RISK_FREE_RATE = 0.042  # long-term Treasury proxy (configurable)
EQUITY_RISK_PREMIUM = 0.05
DEFAULT_BETA = 1.0
BETA_MIN = 0.4
BETA_MAX = 2.5

DEFAULT_PRE_TAX_COST_OF_DEBT = 0.055  # legacy flat fallback (replaced by tiered when no company Kd)
DEFAULT_TAX_RATE = 0.21
TAX_RATE_MIN = 0.05
TAX_RATE_MAX = 0.40

WACC_MIN = 0.06
WACC_MAX = 0.16

# --- Fallback Cost of Debt (Scheme A: mutually exclusive leverage tiers) ---
# Used only when no reliable company debt yield / credit spread is available.
# Credit Spread = Base + D/E tier premium + Debt/FCFF tier premium (tiers exclusive within class).
# Kd_pre = min(KD_MAX, Rf + Credit Spread)
KD_BASE_SPREAD = 0.015
KD_MAX = 0.12
KD_COMPANY_YIELD_MIN = 0.02  # accept explicit company Kd only inside [min, max]
KD_COMPANY_YIELD_MAX = 0.14

# D/E tiers: v <= b0 → p0; b0 < v <= b1 → p1; ...; v > blast → p_last
# <=0.5 → +0%; (0.5,1] → +1%; (1,2] → +2.5%; >2 → +5%
KD_DE_TIER_BOUNDS = (0.5, 1.0, 2.0)
KD_DE_TIER_PREMIUMS = (0.00, 0.01, 0.025, 0.05)

# Debt/FCFF: <=5 → +0%; (5,10] → +1%; >10 → +2%
KD_DEBT_FCFF_TIER_BOUNDS = (5.0, 10.0)
KD_DEBT_FCFF_TIER_PREMIUMS = (0.00, 0.01, 0.02)

# high_leverage_warning when kd_default and either metric exceeds:
HIGH_LEVERAGE_DE_MIN = 1.0
HIGH_LEVERAGE_DEBT_FCFF_MIN = 8.0
# If warning fires, Confidence capped at MEDIUM (Base Value still shown).
# Future (not enabled): Debt/FCFF > 12 and D/E > 1 → maybe Est.Value = —
HIGH_LEVERAGE_SUPPRESS_DEBT_FCFF = 12.0  # reserved; suppress not active
HIGH_LEVERAGE_SUPPRESS_BASE = False

# --- Growth path (5-year explicit + terminal) ---
EXPLICIT_YEARS = 5
GROWTH_LOOKBACK_YEARS = 4  # use up to this many YoY spans (~3–5y history)
GROWTH_CAP_Y1 = 0.25  # do not extrapolate extreme history
GROWTH_FLOOR_Y1 = -0.05  # mild contraction allowed; deeper → ineligible FCF path
# Fade weights toward terminal: year i uses mix of recent growth and mature g
GROWTH_FADE = (1.00, 0.75, 0.50, 0.30, 0.15)  # applied to (g_recent - g_terminal)

# Normalized growth (revenue-anchored; applies to all tickers)
# Revenue CAGR is the primary sustainable-growth anchor.
# EBIT/FCFF CAGRs may only add a capped premium when margins are stable
# and YoY growth is not erratic (margin expansion / low-base / one-offs).
GROWTH_REV_WEIGHT = 0.60
GROWTH_EBIT_WEIGHT_STABLE = 0.25
GROWTH_EBIT_WEIGHT_UNSTABLE = 0.10
GROWTH_FCFF_WEIGHT_STABLE = 0.20
GROWTH_FCFF_WEIGHT_UNSTABLE = 0.05
# Max (signal − rev_cagr) kept when blending; excess treated as non-sustainable
GROWTH_PREMIUM_CAP_STABLE = 0.03
GROWTH_PREMIUM_CAP_UNSTABLE = 0.00
# FCFF/Rev (or EBIT/Rev) margin CV below this → "stable"
GROWTH_MARGIN_CV_STABLE = 0.20
# Std-dev of YoY FCFF (or EBIT) growth above this → "unstable path"
GROWTH_YOY_STD_UNSTABLE = 0.25
# If earliest positive FCFF margin is very low vs later median → low-base flag
GROWTH_LOW_BASE_MARGIN_RATIO = 0.55
# Relative margin expansion newest/oldest − 1 above this → treat as expansion (cap premium)
GROWTH_MARGIN_EXPAND_RATIO = 0.20

TERMINAL_GROWTH_DEFAULT = 0.025
TERMINAL_GROWTH_MIN = 0.015
TERMINAL_GROWTH_MAX = 0.030
# Mature / mega-cap: slightly lower terminal g
TERMINAL_GROWTH_MATURE = 0.022
MATURE_MARKET_CAP_USD = 100e9

# --- Eligibility / sanity ---
MIN_HISTORY_YEARS = 3  # need ≥3 annual periods for EBIT/Revenue
MIN_POSITIVE_FCFF_YEARS = 2  # of last N computed FCFF years
FINANCIAL_SECTOR_KEYWORDS = (
    "financial",
    "bank",
    "insurance",
    "capital markets",
    "credit services",
    "asset management",
)
# Skip if latest FCFF ≤ 0 or majority of recent FCFF ≤ 0
REQUIRE_LATEST_FCFF_POSITIVE = True

# Est.Value sanity vs price (reject absurd outputs)
EST_TO_PRICE_MAX_RATIO = 25.0  # Est / price
EST_TO_PRICE_MIN_RATIO = 0.02

# Share-count integrity: |Price×Shares − MarketCap| / MarketCap
# Above this → Est.Value = — (share_count_mismatch). Never "fix" via Mcap/Price.
SHARE_COUNT_MISMATCH_MAX = 0.18
# Heuristic labels only (still fail if mismatch exceeds max; no share rewrite)
SHARE_COUNT_DUAL_CLASS_NAME_HINTS = (
    "class a",
    "class b",
    "class c",
    "class d",
    "dual class",
)

# Cache
VALUATION_CACHE_DAYS = 14
VALUATION_METHOD = "DCF-FCFF"
VALUATION_VERSION = "v1.3"  # baseline frozen: growth v1.1 + share gate v1.2 + tiered Kd v1.3

# MOS uses Watchlist/Market Data row price only (never valuation-cache price).
# If row price timestamp is older than this, MOS shows — / stale (Est.Value unchanged).
MOS_PRICE_STALE_HOURS = 72  # 3d covers weekend; 5d-old prices must not drive MOS

# Rate limit when fetching Yahoo statements
YAHOO_PAUSE_S = 0.35

# --- Bear / Base / Bull sensitivity (Base Case math unchanged) ---
# Applied to Base Case outputs only for scenario Est.Value Range.
BEAR_GROWTH_DELTA = -0.04          # subtract from each explicit-year growth rate
BEAR_WACC_DELTA = 0.015            # add to WACC
BEAR_TERMINAL_G_DELTA = -0.005     # subtract from terminal g

BULL_GROWTH_DELTA = 0.03
BULL_WACC_DELTA = -0.010
BULL_TERMINAL_G_DELTA = 0.003

# After deltas, keep growth within these bounds
SCENARIO_GROWTH_FLOOR = -0.08
SCENARIO_GROWTH_CAP = 0.30

# (Bull - Bear) / Base ; above this → lower confidence + sensitivity flag
VALUATION_SPREAD_WIDE = 0.80
VALUATION_SPREAD_VERY_WIDE = 1.50

# --- Conservative Liquidation Value (CLV) V1 — independent of DCF v1.3 ---
# Mechanical balance-sheet floor; uniform haircuts for all tickers.
# Do NOT retune recoveries to match market prices.
CLV_VERSION = "v1.1"  # prefer latest quarterly BS over annual
CLV_CASH_RECOVERY = 1.00
CLV_MARKETABLE_SECURITIES_RECOVERY = 1.00
CLV_RECEIVABLE_RECOVERY = 0.80
CLV_INVENTORY_RECOVERY = 0.50
CLV_OTHER_CURRENT_ASSETS_RECOVERY = 0.00  # V1 conservative
CLV_NONMARKETABLE_INVESTMENT_RECOVERY = 0.50
CLV_PPE_RECOVERY = 0.25
CLV_GOODWILL_RECOVERY = 0.00
CLV_INTANGIBLES_RECOVERY = 0.00
CLV_DEFERRED_TAX_ASSETS_RECOVERY = 0.00
CLV_ROU_OTHER_LT_ASSETS_RECOVERY = 0.00
# Soft missing non-core BS lines → lower confidence; core missing → CLV = —
CLV_BALANCE_SHEET_STALE_DAYS = 150  # age warning only; does not change haircuts
CLV_CACHE_DAYS = 14
CLV_YAHOO_PAUSE_S = 0.35

