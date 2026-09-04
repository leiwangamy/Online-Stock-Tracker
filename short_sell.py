"""
Short Sell — Dist25 relative Top-N% SHORT WATCH + MOMENTUM timing.

POSITION (Dist25 Top %) determines WHERE to wait.
MOMENTUM (same P/D/A compounded D-4…D0 / 5D TOTAL) determines WHEN.

Pool:
  Broad stock universe (universe ∩ dashboard_cache with Dist25)
       → Dist25 DESC
       → Top X% (configurable; default 1%)
       → SHORT WATCH

Timing (display + paper experiment):
  Reuse momentum_sessions format_session_history (same as MOMENTUM).
  5D TOTAL < 0  → DOWN  (eligible for paper SHORT experiment)
  5D TOTAL >= 0 → WATCH (wait; do not auto-short)

Primary table rank remains Dist25 DESC (never Momentum).
Paper experiment ranks DOWN names by 5D TOTAL ASC; cover stop +3%; no Take Profit.

Does NOT invent UP/DOWN/SIDEWAYS AI scores.
Does NOT reset paper history or change other strategies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ai_buy import status_emoji
from db import get_conn, get_setting, set_setting
from strategies import STRATEGY_SHORT_SELL, assign_primary_ranks, cap_category

TOP_N = 15  # legacy; snapshot sizing uses top_pct
STOP_LOSS_PCT = 3.0
TAKE_PROFIT_PCT = None  # stop-only (no Take Profit)
TRAILING_STOP = False

SETTING_TOP_PCT = "short_watch_top_pct"
DEFAULT_TOP_PCT = 1.0  # Top 1% (also try 0.5 / 2)

META_AS_OF = "short_sell_as_of"
META_BUILT = "short_sell_built_at"
META_CUTOFF = "short_watch_last_cutoff_dist25"
META_ELIGIBLE = "short_watch_last_eligible_n"
SHORT_RULES_VERSION = "v2_dist25_top_pct_down_momentum"

GUIDANCE_EN = (
    "High position alone is not a short signal. The SHORT pool contains "
    "stocks with the highest Dist25 relative to the market. Wait for actual "
    "downward movement before shorting. Give particular attention to the "
    "most recent 1–2 days and to acceleration/deceleration. A stock that "
    "remains strongly upward should not be shorted merely because it is "
    "high. A weakening rise followed by increasingly negative recent "
    "movement provides stronger evidence of a downward transition. "
    "All directional judgments are probabilistic."
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_short_watch_top_pct() -> float:
    """Configurable Top-X% threshold (e.g. 0.5 / 1 / 2). Clamped to a safe band."""
    raw = get_setting(SETTING_TOP_PCT, DEFAULT_TOP_PCT)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = DEFAULT_TOP_PCT
    if v <= 0:
        v = DEFAULT_TOP_PCT
    return max(0.1, min(10.0, v))


def set_short_watch_top_pct(pct: float) -> float:
    v = max(0.1, min(10.0, float(pct)))
    set_setting(SETTING_TOP_PCT, v)
    return v


def _eligible_dist_rows() -> list[dict[str, Any]]:
    """
    Broad eligible stock universe with Dist25 available.
    Source: stock `universe` joined to `dashboard_cache` (not a fixed SHORT sleeve).
    """
    sql = """
        SELECT d.*, u.in_sp500, u.in_ndx100, u.in_sp400, u.in_sp600, u.in_tsx,
               u.name AS universe_name, u.sector AS universe_sector,
               u.industry AS universe_industry
        FROM dashboard_cache d
        JOIN universe u ON u.ticker = d.ticker
        WHERE d.dist_pct IS NOT NULL
          AND d.sma IS NOT NULL
          AND d.price IS NOT NULL
          AND d.price > 0
          AND (d.ai_note IS NULL OR UPPER(d.ai_note) NOT LIKE 'DATA ERROR%')
        ORDER BY d.dist_pct DESC, d.ticker ASC
    """
    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        t = (d.get("ticker") or "").strip().upper()
        if not t:
            continue
        d["ticker"] = t
        if not d.get("name"):
            d["name"] = d.get("universe_name")
        if not d.get("sector"):
            d["sector"] = d.get("universe_sector")
        if not d.get("industry"):
            d["industry"] = d.get("universe_industry")
        d["dist25"] = d.get("dist_pct")
        d["sma25"] = d.get("sma")
        out.append(d)
    return out


def _attach_percentiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Dist25 percentile among eligible names.
    100 = most extended (rank 1). Primary order remains Dist25 DESC.
    """
    n = len(rows)
    for i, r in enumerate(rows):
        rank = i + 1
        pctile = 100.0 * (n - rank + 1) / n if n else None
        r["dist_rank"] = rank
        r["dist25_percentile"] = round(pctile, 2) if pctile is not None else None
        r["eligible_n"] = n
    return rows


def select_short_watch(
    *, top_pct: float | None = None
) -> dict[str, Any]:
    """
    Rank eligible universe by Dist25 DESC and take approximately Top X%.
    Returns watch rows + sizing metadata (position only — not a short signal).
    """
    pct = float(top_pct) if top_pct is not None else get_short_watch_top_pct()
    pct = max(0.1, min(10.0, pct))
    eligible = _attach_percentiles(_eligible_dist_rows())
    n = len(eligible)
    k = max(1, int(round(n * (pct / 100.0)))) if n else 0
    watch = eligible[:k]
    cutoff = float(watch[-1]["dist_pct"]) if watch else None
    return {
        "top_pct": pct,
        "eligible_n": n,
        "watch_n": len(watch),
        "cutoff_dist25": cutoff,
        "rows": watch,
    }


def short_pool_rows() -> list[dict[str, Any]]:
    """SHORT WATCH rows (Dist25 Top %), Dist DESC — for pool browsers / refresh."""
    return list(select_short_watch().get("rows") or [])


def _attach_momentum_days(
    rows: list[dict[str, Any]], *, refresh_sessions: bool = True
) -> dict[str, Any]:
    """
    Attach MOMENTUM-compatible D-4…D0 / 5D TOTAL via momentum_sessions.
    Optionally refresh Yahoo session obs for these tickers first.
    """
    from momentum_sessions import (
        format_session_history,
        refresh_momentum_watchlist_sessions,
    )

    tickers = [
        (r.get("ticker") or "").strip().upper()
        for r in rows
        if (r.get("ticker") or "").strip()
    ]
    session_refresh: dict[str, Any] = {}
    if refresh_sessions and tickers:
        try:
            session_refresh = refresh_momentum_watchlist_sessions(tickers)
        except Exception as exc:
            session_refresh = {"ok": False, "error": str(exc)}

    for r in rows:
        t = (r.get("ticker") or "").strip().upper()
        try:
            hist = format_session_history(t, n_days=5) if t else {}
        except Exception:
            hist = {}
        totals = list(hist.get("daily_totals_pct") or [])
        while len(totals) < 5:
            totals.append(None)
        r["day_totals"] = totals[:5]
        r["total_5d_pct"] = hist.get("total_5d_pct")
        r["session_history_compact"] = hist.get("public_compact") or "—"
        r["momentum_source"] = "momentum_sessions"
    return session_refresh


def _short_timing_from_5d(total_5d: Any) -> str:
    """WATCH unless overall 5D movement is negative → DOWN."""
    if total_5d is None:
        return "WATCH"
    try:
        v = float(total_5d)
    except (TypeError, ValueError):
        return "WATCH"
    if v < 0:
        return "DOWN"
    return "WATCH"


def build_short_sell_snapshot(
    *,
    persist: bool = True,
    top_n: int | None = None,
    top_pct: float | None = None,
    refresh_sessions: bool = True,
) -> dict[str, Any]:
    """
    Build SHORT WATCH table ranked Dist25 DESC, with MOMENTUM day columns.
    Status DOWN only when 5D TOTAL < 0 (paper experiment eligibility).
    """
    del top_n  # kept for call-site compatibility; sizing is Top-X%
    sel = select_short_watch(top_pct=top_pct)
    rows_in = list(sel.get("rows") or [])
    pool_count = int(sel.get("eligible_n") or 0)
    watch_n = int(sel.get("watch_n") or 0)
    pct = float(sel.get("top_pct") or DEFAULT_TOP_PCT)
    cutoff = sel.get("cutoff_dist25")

    session_refresh = _attach_momentum_days(rows_in, refresh_sessions=refresh_sessions)

    held: set[str] = set()
    try:
        with get_conn() as conn:
            opens = conn.execute(
                """
                SELECT DISTINCT ticker FROM paper_trades
                WHERE status='open'
                  AND UPPER(COALESCE(strategy_id, '')) = ?
                """,
                (STRATEGY_SHORT_SELL,),
            ).fetchall()
            held = {(r["ticker"] or "").upper() for r in opens}
    except Exception:
        pass

    out: list[dict[str, Any]] = []
    counts = {
        "WATCH": 0,
        "DOWN": 0,
        "READY": 0,  # unused — kept for UI status bars that expect the key
        "HOLD": 0,
        "BLOCKED": 0,
        "WEAKENING": 0,
        "STABILIZING": 0,
        "WAIT": 0,
    }

    for i, r in enumerate(rows_in, start=1):
        t = (r.get("ticker") or "").strip().upper()
        r["ticker"] = t
        r["setup_rank"] = i
        r["queue_rank"] = i
        r["sources"] = [f"SHORT_WATCH#{i}"]
        r["source_codes"] = f"SHORT_WATCH#{i}"
        r["cap_bucket"] = cap_category(r.get("market_cap"))
        r["side"] = "short"
        r["news_status"] = r.get("news_status") or "PASS"
        r["high_block"] = False
        r["knife_block"] = False
        r["news_block"] = False

        timing = _short_timing_from_5d(r.get("total_5d_pct"))
        r["short_status"] = timing
        r["timing_status"] = timing
        r["down_momentum"] = timing == "DOWN"
        # Paper experiment may short DOWN only — not a complex READY score.
        r["buy_allowed"] = timing == "DOWN"
        r["price_zone"] = "SHORT_WATCH"
        r["buy_score"] = i
        r["experiment_stop_loss_pct"] = STOP_LOSS_PCT
        status = "HOLD" if t in held else timing
        r["buy_status"] = status
        r["status_emoji"] = status_emoji(status) if status != "DOWN" else "🔻"

        parts = [
            f"watch#{i}",
            f"Dist25={float(r['dist_pct']):+.1f}%"
            if r.get("dist_pct") is not None
            else "Dist25=—",
            f"Pctl={float(r['dist25_percentile']):.1f}"
            if r.get("dist25_percentile") is not None
            else "Pctl=—",
            f"Top {pct:g}%",
            f"5D={float(r['total_5d_pct']):+.1f}%"
            if r.get("total_5d_pct") is not None
            else "5D=N/A",
            timing,
        ]
        if timing == "DOWN":
            parts.append(f"cover=+{STOP_LOSS_PCT:g}% · no Take")
        else:
            parts.append("high position only — wait for negative 5D")
        if status == "HOLD":
            parts.append(f"was={timing}")
        r["reason"] = " · ".join(parts)
        counts[status] = counts.get(status, 0) + 1
        out.append(r)

    # Primary rank = Dist25 DESC only (Momentum never reorders discovery).
    out = assign_primary_ranks(
        out, metric_key="dist_pct", metric_name="dist_sma25", ascending=False
    )
    out.sort(
        key=lambda x: (
            -(float(x["dist_pct"]) if x.get("dist_pct") is not None else -9999.0),
            x.get("setup_rank") if x.get("setup_rank") is not None else 9999,
            x.get("ticker") or "",
        )
    )
    for i, r in enumerate(out, start=1):
        r["setup_rank"] = i
        r["queue_rank"] = i

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if persist:
        set_setting(META_AS_OF, as_of)
        set_setting(META_BUILT, _utcnow())
        if cutoff is not None:
            set_setting(META_CUTOFF, cutoff)
        set_setting(META_ELIGIBLE, pool_count)

    return {
        "as_of": as_of,
        "built_at": get_setting(META_BUILT, "") or _utcnow(),
        "universe_count": len(out),
        "pool_count": pool_count,
        "passed_count": watch_n,
        "watch_count": watch_n,
        "down_count": int(counts.get("DOWN") or 0),
        "top_n": watch_n,
        "top_pct": pct,
        "cutoff_dist25": cutoff,
        "counts": counts,
        "rows": out,
        "definition": "short_watch_dist25_top_pct_down_5d",
        "strategy_id": STRATEGY_SHORT_SELL,
        "rules_version": SHORT_RULES_VERSION,
        "stop_loss_pct": STOP_LOSS_PCT,
        "trailing_stop": TRAILING_STOP,
        "take_profit_pct": TAKE_PROFIT_PCT,
        "side": "short",
        "guidance": GUIDANCE_EN,
        "session_refresh": session_refresh,
        "notes": (
            f"Broad stock universe ({pool_count} with Dist25) -> Dist25 DESC -> "
            f"Top {pct:g}% ({watch_n} names"
            + (f", Dist25>={cutoff:+.2f}%" if cutoff is not None else "")
            + "). Status DOWN when 5D TOTAL<0 (MOMENTUM session method). "
            f"Paper: DOWN only · 5D ASC · cover +{STOP_LOSS_PCT:g}% · no Take."
        ),
    }


def load_short_sell_view(
    *,
    recompute: bool = True,
    top_n: int | None = None,
    top_pct: float | None = None,
) -> dict[str, Any]:
    return build_short_sell_snapshot(
        persist=True, top_n=top_n, top_pct=top_pct, refresh_sessions=recompute
    )
