"""
LeiBot Five-Strategy AI Trading framework (V1).

Core rule: PRIMARY RANKING and ELIGIBILITY (BLOCK) are separate.
Rank is assigned before BLOCK; BLOCK never reorders the list.
"""

from __future__ import annotations

from typing import Any

# Canonical strategy IDs (persist in DB / paper trades).
STRATEGY_ALERT_BUY = "ALERT_BUY"
STRATEGY_DEEP_RECOVERY = "DEEP_RECOVERY"
STRATEGY_STABLE_GROWTH = "STABLE_GROWTH"
STRATEGY_SAFE_MARGIN = "SAFE_MARGIN"
STRATEGY_SHORT_SELL = "SHORT_SELL"
STRATEGY_MOMENTUM = "MOMENTUM"

STRATEGY_IDS = (
    STRATEGY_ALERT_BUY,
    STRATEGY_DEEP_RECOVERY,
    STRATEGY_STABLE_GROWTH,
    STRATEGY_SAFE_MARGIN,
    STRATEGY_SHORT_SELL,
    STRATEGY_MOMENTUM,
)

# Per-strategy paper capital defaults (independent experiments).
DEFAULT_STRATEGY_CAPITAL = 2000.0
DEFAULT_STRATEGY_TRADING_LIMIT = 1500.0
DEFAULT_STRATEGY_RESERVE = 500.0

# Structured block reason codes.
BLOCK_DATA = "DATA_BLOCK"
BLOCK_NEWS = "NEWS_BLOCK"
BLOCK_KNIFE = "KNIFE_BLOCK"
BLOCK_LIQUIDITY = "LIQUIDITY_BLOCK"
BLOCK_FUNDAMENTAL = "FUNDAMENTAL_BLOCK"
BLOCK_STRATEGY = "STRATEGY_BLOCK"
BLOCK_HIGH = "HIGH_BLOCK"

# Common high-level trade states (strategies may add internal labels).
STATE_CANDIDATE = "CANDIDATE"
STATE_READY = "READY"
STATE_BLOCKED = "BLOCKED"
STATE_HOLDING = "HOLDING"
STATE_CLOSED = "CLOSED"

# Cap buckets for experiment analysis (display only — never reorders rank).
CAP_LARGE = "LARGE"
CAP_MID = "MID"
CAP_SMALL = "SMALL"
CAP_UNKNOWN = "UNKNOWN"

# Market-cap thresholds (USD). Align with common GICS/index practice.
_LARGE_MIN = 10_000_000_000.0  # ≥ $10B
_MID_MIN = 2_000_000_000.0  # ≥ $2B


STRATEGY_META: dict[str, dict[str, Any]] = {
    STRATEGY_ALERT_BUY: {
        "name": "Alert Buy",
        "short": "ALERT BUY",
        "hypothesis": (
            "High-quality observation names (My ∪ NDX100 ∪ AI Approved) "
            "at deeper Dist SMA25 discounts recover well."
        ),
        "primary_metric": "dist_sma25",
        "primary_metric_label": "Dist SMA25",
        "rank_direction": "asc",  # most negative first
        "status": "active",
        "side": "long",
        "universe": "observation_alert",
        "source_pool_label": "MY ∪ NDX100 ∪ AI APPROVED",
    },
    STRATEGY_DEEP_RECOVERY: {
        "name": "Deep Recovery",
        "short": "DEEP RECOVERY",
        "hypothesis": (
            "Watchlist Oversold pullback top names (often mid/small-cap) "
            "with the deepest Dist SMA25 discounts can rebound hard — "
            "lower quality than Alert Buy, larger swing potential. "
            "Queue = top 15 of that screen; same READY/BLOCK timing as Alert Buy."
        ),
        "primary_metric": "dist_sma25",
        "primary_metric_label": "Dist SMA25",
        "rank_direction": "asc",
        "status": "active",
        "side": "long",
        "universe": "oversold_pullback_top15",
        "source_pool_label": "OVERSOLD PULLBACK (top 15)",
    },
    STRATEGY_STABLE_GROWTH: {
        "name": "Stable Growth",
        "short": "STABLE GROWTH",
        "hypothesis": (
            "GROWTH sleeve names bought on Dist SMA25 pullbacks (deepest first) "
            "compound with a tight −3% stop and no take-profit; rotate to the "
            "next unused queue name on exit."
        ),
        "primary_metric": "dist_sma25",
        "primary_metric_label": "Dist SMA25 (ASC)",
        "rank_direction": "asc",
        "status": "active",
        "side": "long",
        "universe": "watchlist_growth",
        "source_pool_label": "Watchlist GROWTH (Dist ASC top 10–20)",
    },
    STRATEGY_SAFE_MARGIN: {
        "name": "Safe Margin",
        "short": "SAFE MARGIN",
        "hypothesis": (
            "Target Ratio < 80% names with risk filters (large-cap, price, "
            "low vol, Financial ≥60%, Knife ≠ HIGH), bought Target ASC with "
            "a 10% trailing stop and no take-profit; rotate to the next "
            "unused queue name on exit."
        ),
        "primary_metric": "target_ratio",
        "primary_metric_label": "Target Ratio (ASC)",
        "rank_direction": "asc",
        "status": "active",
        "side": "long",
        "universe": "target_ratio_lt_80",
        "source_pool_label": "TARGET RATIO < 80% (risk-filtered top 10–20)",
    },
    STRATEGY_SHORT_SELL: {
        "name": "Short Sell",
        "short": "SHORT SELL",
        "hypothesis": (
            "Dist25 Top-X% of the broad stock universe = SHORT WATCH (high "
            "position only). Same MOMENTUM P/D/A 5D TOTAL: negative → DOWN. "
            "Paper shorts DOWN only (5D ASC), cover +3%, no Take Profit."
        ),
        "primary_metric": "dist_sma25",
        "primary_metric_label": "Dist SMA25 (DESC)",
        "rank_direction": "desc",
        "status": "active",
        "side": "short",
        "universe": "dist25_top_pct_watch",
        "source_pool_label": "SHORT WATCH · Dist25 Top 1% (configurable)",
    },
    STRATEGY_MOMENTUM: {
        "name": "Momentum",
        "short": "MOMENTUM",
        "hypothesis": (
            "Continuation experiment on compounded PRE×REGULAR×AFTER daily "
            "totals: rank ABS(5D TOTAL) DESC; sign(5D) sets LONG/SHORT; fixed "
            "1% stop. No artificial AI scores — collect outcomes for later stats."
        ),
        "primary_metric": "abs_5d_total",
        "primary_metric_label": "|5D TOTAL| (DESC)",
        "rank_direction": "desc",
        "status": "active",
        "side": "both",
        "universe": "watchlist_momentum",
        "source_pool_label": "Watchlist MOMENTUM · ABS(5D) continuation",
    },
}


def normalize_strategy_id(value: str | None) -> str:
    raw = (value or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "AI_BUY": STRATEGY_ALERT_BUY,
        "ALERT": STRATEGY_ALERT_BUY,
        "BUY": STRATEGY_ALERT_BUY,
        "DEEP": STRATEGY_DEEP_RECOVERY,
        "STABLE": STRATEGY_STABLE_GROWTH,
        "SAFE": STRATEGY_SAFE_MARGIN,
        "SHORT": STRATEGY_SHORT_SELL,
        "MOMENTUM": STRATEGY_MOMENTUM,
        "MOMO": STRATEGY_MOMENTUM,
    }
    raw = aliases.get(raw, raw)
    if raw in STRATEGY_IDS:
        return raw
    return STRATEGY_ALERT_BUY


def strategy_label(strategy_id: str | None) -> str:
    sid = normalize_strategy_id(strategy_id)
    return str(STRATEGY_META.get(sid, {}).get("short") or sid)


def is_active_strategy(strategy_id: str | None) -> bool:
    sid = normalize_strategy_id(strategy_id)
    return STRATEGY_META.get(sid, {}).get("status") == "active"


def is_shell_strategy(strategy_id: str | None) -> bool:
    return STRATEGY_META.get(normalize_strategy_id(strategy_id), {}).get("status") == "shell"


def cap_category(market_cap: float | None) -> str:
    """LARGE / MID / SMALL for analysis — never used to reorder primary rank."""
    if market_cap is None:
        return CAP_UNKNOWN
    try:
        m = float(market_cap)
    except (TypeError, ValueError):
        return CAP_UNKNOWN
    if m != m or m <= 0:
        return CAP_UNKNOWN
    if m >= _LARGE_MIN:
        return CAP_LARGE
    if m >= _MID_MIN:
        return CAP_MID
    return CAP_SMALL


def assign_primary_ranks(
    rows: list[dict[str, Any]],
    *,
    metric_key: str = "dist_pct",
    metric_name: str = "dist_sma25",
    ascending: bool = True,
) -> list[dict[str, Any]]:
    """
    Sort by primary metric, assign primary_rank 1..N, then return.
    Call this BEFORE applying BLOCK styling / eligibility display filters.
    """

    def sort_key(r: dict[str, Any]):
        v = r.get(metric_key)
        missing = v is None
        try:
            fv = float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            missing = True
            fv = 0.0
        # ascending: most negative Dist first → smaller float first
        ordered = fv if ascending else -fv
        return (missing, ordered, r.get("ticker") or "")

    ordered = sorted(rows, key=sort_key)
    for i, r in enumerate(ordered, start=1):
        r["primary_rank"] = i
        r["primary_metric_name"] = metric_name
        try:
            r["primary_metric_value"] = (
                None if r.get(metric_key) is None else float(r.get(metric_key))
            )
        except (TypeError, ValueError):
            r["primary_metric_value"] = None
    return ordered


def normalize_block_reasons(reasons: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in reasons or []:
        code = (raw or "").strip().upper()
        if not code:
            continue
        # Map legacy free-text fragments to structured codes when obvious.
        if ("KNIFE" in code or "DOWNSIDE" in code) and "BLOCK" not in code:
            code = BLOCK_KNIFE
        elif code in ("HIGH", "HIGH_PRICE"):
            code = BLOCK_HIGH
        elif "NEWS" in code and "BLOCK" not in code:
            code = BLOCK_NEWS
        elif "DATA" in code and "BLOCK" not in code:
            code = BLOCK_DATA
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def block_display(reasons: list[str] | None) -> str:
    codes = normalize_block_reasons(reasons)
    if not codes:
        return ""
    short = []
    for c in codes:
        short.append(c.replace("_BLOCK", "").replace("BLOCK_", ""))
    return "BLOCK: " + "/".join(short)
