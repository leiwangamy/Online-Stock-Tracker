"""
Short Sell — Watchlist SHORT pool, Dist SMA25 descending short queue.

SOURCE = Watchlist 「SHORT」 (ETF-heavy + stables).
FILTER = 63D Position > 80% · Day % < 0, then Dist SMA25 descending.
RANK   = Dist% vs SMA25, descending (most extended above SMA first).
QUEUE  = top TOP_N (default 15, Owner band 10–20) → SHORT CANDIDATE / READY.
ENTRY  = SELL SHORT on SHORT_SELL book.
EXITS  = Cover stop +3% above entry · Take Profit −6% below entry (fixed).
         EXIT → next unused on queue.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ai_buy import status_emoji
from db import get_conn, get_dashboard_by_tickers, get_setting, set_setting
from strategies import STRATEGY_SHORT_SELL, assign_primary_ranks, cap_category
from watchlist_config import get_short_watchlist

TOP_N = 15
STOP_LOSS_PCT = 3.0  # cover stop above short entry
TAKE_PROFIT_PCT = 6.0  # buy-to-cover profit below entry
TRAILING_STOP = False
MIN_63D_POS = 80.0
# Day % must be strictly negative (red day).

META_AS_OF = "short_sell_as_of"
META_BUILT = "short_sell_built_at"


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _candidate_fail_reasons(row: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    try:
        pos = row.get("range_63d_pos")
        if pos is None or float(pos) <= MIN_63D_POS:
            fails.append("63D")
    except (TypeError, ValueError):
        fails.append("63D")

    try:
        chg = row.get("change_pct")
        if chg is None or float(chg) >= 0:
            fails.append("DAY")
    except (TypeError, ValueError):
        fails.append("DAY")

    if row.get("dist_pct") is None:
        fails.append("DIST")
    try:
        px = row.get("price")
        if px is None or float(px) <= 0:
            fails.append("PRICE")
    except (TypeError, ValueError):
        fails.append("PRICE")
    return fails


def short_pool_rows() -> list[dict[str, Any]]:
    """Full SHORT watchlist with dashboard metrics."""
    tickers = [t.strip().upper() for t in get_short_watchlist() if t and str(t).strip()]
    if not tickers:
        return []
    dash = get_dashboard_by_tickers(tickers)
    rows: list[dict[str, Any]] = []
    for t in tickers:
        d = dict(dash.get(t) or {"ticker": t})
        d["ticker"] = t
        rows.append(d)
    # Dist DESC — most extended first; missing Dist last.
    rows.sort(
        key=lambda r: (
            -(float(r["dist_pct"]) if r.get("dist_pct") is not None else -9999.0),
            r.get("ticker") or "",
        )
    )
    return rows


def build_short_sell_snapshot(
    *, persist: bool = True, top_n: int = TOP_N
) -> dict[str, Any]:
    pool_rows = short_pool_rows()
    pool_count = len(pool_rows)
    n = max(10, min(20, int(top_n or TOP_N)))

    passed: list[dict[str, Any]] = []
    for r in pool_rows:
        fails = _candidate_fail_reasons(r)
        r["short_fail_reasons"] = fails
        r["short_pass"] = not fails
        if not fails:
            passed.append(r)

    # Already Dist DESC from short_pool_rows; keep order among passed.
    rows = passed[:n]

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
        "READY": 0,
        "STABILIZING": 0,
        "BLOCKED": 0,
        "HOLD": 0,
        "WAIT": 0,
    }

    for i, r in enumerate(rows, start=1):
        t = (r.get("ticker") or "").strip().upper()
        r["ticker"] = t
        r["setup_rank"] = i
        r["queue_rank"] = i
        r["sources"] = [f"SHORT#{i}"]
        r["source_codes"] = f"SHORT#{i}"
        r["cap_bucket"] = cap_category(r.get("market_cap"))
        r["side"] = "short"
        r["news_status"] = r.get("news_status") or "PASS"
        r["buy_allowed"] = True
        r["block_reasons"] = []
        r["high_block"] = False
        r["knife_block"] = False
        r["news_block"] = False
        timing = "READY"
        r["timing_status"] = timing
        r["price_zone"] = "SHORT_QUEUE"
        r["buy_score"] = i
        status = "HOLD" if t in held else timing
        r["buy_status"] = status
        r["status_emoji"] = status_emoji(status)
        parts = [
            f"short#{i}",
            f"Dist={float(r['dist_pct']):+.1f}%"
            if r.get("dist_pct") is not None
            else "Dist=—",
            f"63D={float(r['range_63d_pos']):.0f}%"
            if r.get("range_63d_pos") is not None
            else "63D=—",
            f"Day={float(r['change_pct']):+.2f}%"
            if r.get("change_pct") is not None
            else "Day=—",
            f"cover=+{STOP_LOSS_PCT:.0f}%/−{TAKE_PROFIT_PCT:.0f}%",
            "SELL SHORT",
        ]
        if status == "HOLD":
            parts.append(f"was={timing}")
        r["reason"] = " · ".join(parts)
        counts[status] = counts.get(status, 0) + 1
        out.append(r)

    out = assign_primary_ranks(
        out, metric_key="dist_pct", metric_name="dist_sma25", ascending=False
    )
    out.sort(
        key=lambda x: (
            x.get("setup_rank") if x.get("setup_rank") is not None else 9999,
            -(float(x["dist_pct"]) if x.get("dist_pct") is not None else -9999.0),
            x.get("ticker") or "",
        )
    )

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if persist:
        set_setting(META_AS_OF, as_of)
        set_setting(META_BUILT, _utcnow())

    return {
        "as_of": as_of,
        "built_at": get_setting(META_BUILT, "") or _utcnow(),
        "universe_count": len(out),
        "pool_count": pool_count,
        "passed_count": len(passed),
        "top_n": n,
        "counts": counts,
        "rows": out,
        "definition": "short_dist_desc_63d_day_filter",
        "strategy_id": STRATEGY_SHORT_SELL,
        "stop_loss_pct": STOP_LOSS_PCT,
        "trailing_stop": TRAILING_STOP,
        "take_profit_pct": TAKE_PROFIT_PCT,
        "side": "short",
        "notes": (
            f"SHORT watchlist → 63D>{MIN_63D_POS:.0f}% · Day%<0 → Dist DESC top {n} "
            f"(pool {pool_count}, passed {len(passed)}). "
            f"SELL SHORT · cover stop +{STOP_LOSS_PCT:.0f}% · "
            f"Take −{TAKE_PROFIT_PCT:.0f}% · EXIT→next unused."
        ),
    }


def load_short_sell_view(*, recompute: bool = True, top_n: int = TOP_N) -> dict[str, Any]:
    return build_short_sell_snapshot(persist=True, top_n=top_n)
