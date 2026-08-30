"""
Stable Growth — Watchlist GROWTH pool, Dist SMA25 ascending queue.

SOURCE = Watchlist 「GROWTH」 (long-horizon sleeve).
RANK   = Dist% vs SMA25, ascending (deepest / most negative first).
FILTER = top TOP_N (default 15, Owner band 10–20).
TRADE  = READY queue (same Knife/HIGH/News gates as Alert Buy).
EXITS  = Stop −3% only (no Take Profit); on any EXIT, auto-buy the then-top
         unused name on the refreshed Dist queue (STABLE_GROWTH book only).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ai_buy import eval_blocks, status_emoji
from db import get_conn, get_dashboard_by_tickers, get_setting, set_setting
from strategies import STRATEGY_STABLE_GROWTH, assign_primary_ranks, cap_category
from watchlist_config import get_growth_watchlist

# Middle of Owner's 10–20 purchase-queue band.
TOP_N = 15
STOP_LOSS_PCT = 3.0  # no take-profit for this strategy

META_AS_OF = "stable_growth_as_of"
META_BUILT = "stable_growth_built_at"


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def growth_pool_rows() -> list[dict[str, Any]]:
    """Full GROWTH watchlist with dashboard metrics (Dist / price / name)."""
    tickers = [t.strip().upper() for t in get_growth_watchlist() if t and str(t).strip()]
    if not tickers:
        return []
    dash = get_dashboard_by_tickers(tickers)
    rows: list[dict[str, Any]] = []
    for t in tickers:
        d = dict(dash.get(t) or {"ticker": t})
        d["ticker"] = t
        rows.append(d)
    # Dist ASC — smallest (most negative) first; missing Dist last.
    rows.sort(
        key=lambda r: (
            r.get("dist_pct") if r.get("dist_pct") is not None else 9999.0,
            r.get("ticker") or "",
        )
    )
    return rows


def stable_growth_universe(*, top_n: int = TOP_N) -> list[dict[str, Any]]:
    """Top-N GROWTH names by Dist SMA25 ascending — the buy queue."""
    n = max(10, min(20, int(top_n or TOP_N)))
    rows = growth_pool_rows()[:n]
    for i, r in enumerate(rows, start=1):
        r["setup_rank"] = i
        r["queue_rank"] = i
        r["sources"] = [f"GROWTH#{i}"]
        r["source_codes"] = f"GROWTH#{i}"
        r["cap_bucket"] = cap_category(r.get("market_cap"))
    return rows


def build_stable_growth_snapshot(
    *, persist: bool = True, top_n: int = TOP_N
) -> dict[str, Any]:
    """
    Dist-ordered GROWTH queue with Alert Buy–style BLOCK gates.
    HOLD scoped to STABLE_GROWTH open paper trades.
    """
    from knife_risk import attach_knife_risk, ensure_benchmark_returns

    pool_rows = growth_pool_rows()
    pool_count = len(pool_rows)
    n = max(10, min(20, int(top_n or TOP_N)))
    rows = stable_growth_universe(top_n=n)

    held: set[str] = set()
    try:
        with get_conn() as conn:
            opens = conn.execute(
                """
                SELECT DISTINCT ticker FROM paper_trades
                WHERE status='open'
                  AND UPPER(COALESCE(strategy_id, '')) = ?
                """,
                (STRATEGY_STABLE_GROWTH,),
            ).fetchall()
            held = {(r["ticker"] or "").upper() for r in opens}
    except Exception:
        pass

    for r in rows:
        t = (r.get("ticker") or "").strip().upper()
        r["ticker"] = t
        r["news_status"] = r.get("news_status") or "PASS"
        r["review_flag"] = False
        if r.get("sma") is not None:
            r["sma25_d"] = r.get("sma")

    if rows:
        try:
            ensure_benchmark_returns(force=False)
            attach_knife_risk(rows, ensure_bench=False)
        except Exception:
            for r in rows:
                r.setdefault("knife", None)

    out: list[dict[str, Any]] = []
    counts = {
        "READY": 0,
        "STABILIZING": 0,
        "BLOCKED": 0,
        "HOLD": 0,
        "WAIT": 0,
    }

    for r in rows:
        k = r.get("knife") or {}
        r["knife_score"] = k.get("score") if isinstance(k, dict) else r.get("knife_score")

        try:
            from market_data_validator import attach_data_quality_to_row

            attach_data_quality_to_row(r)
        except Exception:
            r.setdefault("data_block", False)
            r.setdefault("data_quality_status", "WARNING")

        blocks = eval_blocks(r)
        r.update(blocks)

        # Queue strategy: Dist order is the rank; timing = READY if gates pass.
        timing = "READY" if blocks.get("buy_allowed") else "BLOCKED"
        r["timing_status"] = timing
        r["price_zone"] = "QUEUE"
        r["buy_score"] = r.get("setup_rank")
        r["recovery_score"] = None
        status = "HOLD" if r["ticker"] in held else timing
        r["buy_status"] = status
        r["status_emoji"] = status_emoji(status)
        parts = [f"growth#{r.get('setup_rank')}", "stop=3%", "no_take"]
        if not r.get("buy_allowed", blocks["buy_allowed"]):
            parts.append(
                "BLOCK:" + "/".join(r.get("block_reasons") or blocks["block_reasons"])
            )
        if status == "HOLD" and timing:
            parts.append(f"was={timing}")
        dist = r.get("dist_pct")
        if dist is not None:
            parts.append(f"Dist={float(dist):+.1f}%")
        r["reason"] = " · ".join(parts)
        counts[status] = counts.get(status, 0) + 1
        out.append(r)

    out = assign_primary_ranks(
        out, metric_key="dist_pct", metric_name="dist_sma25", ascending=True
    )
    out.sort(
        key=lambda x: (
            x.get("setup_rank") if x.get("setup_rank") is not None else 9999,
            x.get("dist_pct") if x.get("dist_pct") is not None else 9999.0,
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
        "top_n": n,
        "counts": counts,
        "rows": out,
        "definition": "growth_dist_asc_top_n",
        "strategy_id": STRATEGY_STABLE_GROWTH,
        "stop_loss_pct": STOP_LOSS_PCT,
        "take_profit_pct": None,
        "notes": (
            f"GROWTH Dist ASC top {n} (pool {pool_count}). "
            f"Stop −{STOP_LOSS_PCT:.0f}% · no Take · EXIT→next unused on queue."
        ),
    }


def load_stable_growth_view(*, recompute: bool = True, top_n: int = TOP_N) -> dict[str, Any]:
    return build_stable_growth_snapshot(persist=True, top_n=top_n)
