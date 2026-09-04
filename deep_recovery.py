"""
Deep Recovery — Alert Buy–style timing on Watchlist Oversold pullback.

SOURCE = Watchlist 「Oversold pullback」 (Dist% < −10%), same sort as that tab:
  Trend UP > MIXED > DOWN, then deepest Dist% first.
FILTER = take top TOP_N (default 15 — middle of Owner's 10–20 band).
READY = Financial PASS (≥60%) + News PASS only (same as Alert Buy v3).
HIGH / Downside Risk / Recovery·Buy scores are display-only — do not gate Status.
Primary rank remains Dist SMA25 ASC. 5D + LIVE are observational (Yahoo LIVE).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ai_buy import (
    attach_news_fin_eligibility,
    compute_buy_score,
    compute_recovery_score,
    eval_blocks,
    price_score_from_dist,
    status_emoji,
    timing_status_without_hold,
)
from db import get_conn, get_setting, init_db, list_setup, set_setting
from strategies import STRATEGY_DEEP_RECOVERY, assign_primary_ranks, cap_category

# Middle of Owner's 10–20 guidance. Wide enough to study rebound; narrow enough
# that the queue stays actionable (paper ladder still only fills ~6 slots).
TOP_N = 15
DIST_THRESHOLD = -10.0
DEEP_RECOVERY_RULES_VERSION = "v3_news_fin_only"

META_AS_OF = "deep_recovery_as_of"
META_BUILT = "deep_recovery_built_at"


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def oversold_pullback_rows(*, limit: int | None = None) -> list[dict[str, Any]]:
    """Full Oversold pullback screen (Watchlist setup order)."""
    rows = [dict(r) for r in list_setup(DIST_THRESHOLD)]
    if limit is not None:
        return rows[: max(0, int(limit))]
    return rows


def deep_recovery_universe(*, top_n: int = TOP_N) -> list[dict[str, Any]]:
    """Top-N Oversold pullback names — the Deep Recovery observation set."""
    n = max(1, int(top_n or TOP_N))
    rows = oversold_pullback_rows(limit=n)
    for i, r in enumerate(rows, start=1):
        r["setup_rank"] = i
        r["sources"] = [f"OVERSOLD#{i}"]
        r["source_codes"] = f"OVERSOLD#{i}"
        r["cap_bucket"] = cap_category(r.get("market_cap"))
    return rows


def build_deep_recovery_snapshot(
    *, persist: bool = True, top_n: int = TOP_N
) -> dict[str, Any]:
    """
    Same BUY timing pipeline as Alert Buy, on Oversold top-N only.
    HOLD is scoped to DEEP_RECOVERY open paper trades.
    """
    from knife_risk import attach_knife_risk, ensure_benchmark_returns
    from rising_now import list_rising_now
    from rising_score import attach_rising_score

    pool_rows = oversold_pullback_rows()
    pool_count = len(pool_rows)
    rows = deep_recovery_universe(top_n=top_n)
    rising_set = {(r.get("ticker") or "").upper() for r in list_rising_now()}

    held: set[str] = set()
    try:
        with get_conn() as conn:
            opens = conn.execute(
                """
                SELECT DISTINCT ticker FROM paper_trades
                WHERE status='open'
                  AND UPPER(COALESCE(strategy_id, '')) = ?
                """,
                (STRATEGY_DEEP_RECOVERY,),
            ).fetchall()
            held = {(r["ticker"] or "").upper() for r in opens}
    except Exception:
        pass

    for r in rows:
        t = (r.get("ticker") or "").strip().upper()
        r["ticker"] = t
        r["in_rising"] = t in rising_set
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
        try:
            attach_rising_score(rows, ensure_bench=False)
        except Exception:
            for r in rows:
                r.setdefault("rising", None)
        try:
            from session_moves import attach_session_moves

            attach_session_moves(rows, include_live=True)
        except Exception:
            for r in rows:
                r.setdefault("day_pcts_5", None)
                r.setdefault("live_pct", None)
        try:
            attach_news_fin_eligibility(rows)
        except Exception:
            for r in rows:
                r.setdefault("financial_status", "FAIL")
                r.setdefault("news_status", r.get("news_status") or "PASS")

    out: list[dict[str, Any]] = []
    counts = {
        "READY": 0,
        "STABILIZING": 0,
        "APPROACHING": 0,
        "WAIT": 0,
        "BLOCKED": 0,
        "HOLD": 0,
        "REVIEW": 0,
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

        # Eligibility = News PASS + Fin PASS only.
        blocks = eval_blocks(r, downside_risk_blocks=False)
        r.update(blocks)

        ps, zone = price_score_from_dist(r.get("dist_pct"))
        r["price_score"] = ps
        r["price_zone"] = zone
        rec = compute_recovery_score(r, use_downside_risk=False)
        r["recovery_score"] = rec
        if blocks["buy_allowed"]:
            bs = compute_buy_score(price_score=ps, recovery_score=rec)
        else:
            bs = None
        r["buy_score"] = bs
        timing = timing_status_without_hold(
            buy_allowed=blocks["buy_allowed"],
            price_zone=zone,
            buy_score=bs,
            recovery_score=rec,
            review_flag=False,
        )
        # Admin DATA column only — never force Status BLOCK from data_quality.
        if timing == "READY":
            try:
                from market_data_validator import validate_buy_data

                final = validate_buy_data(r.get("ticker") or "", r)
                r["data_quality_status"] = final["data_quality_status"]
                r["data_quality_reason"] = final["data_quality_reason"]
                r["buy_data_ok"] = final.get("buy_data_ok")
                r["data_block"] = bool(final.get("data_block"))
            except Exception:
                pass
        r["timing_status"] = timing
        status = "HOLD" if r["ticker"] in held else timing
        r["buy_status"] = status
        r["status_emoji"] = status_emoji(status)
        parts = [f"setup#{r.get('setup_rank')}"]
        if not r.get("buy_allowed", blocks["buy_allowed"]):
            parts.append(
                "BLOCK:" + "/".join(r.get("block_reasons") or blocks["block_reasons"])
            )
        if status == "HOLD" and timing:
            parts.append(f"was={timing}")
        if zone:
            parts.append(f"zone={zone}")
        if ps is not None:
            parts.append(f"P={ps}")
        if rec is not None:
            parts.append(f"R={rec}")
        cap = r.get("cap_bucket")
        if cap and cap != "UNKNOWN":
            parts.append(f"cap={cap}")
        r["reason"] = " · ".join(parts)
        counts[status] = counts.get(status, 0) + 1
        out.append(r)

    # Keep Oversold screen order as primary (setup_rank), Dist already embedded.
    # Re-assert Dist ASC as display rank (BLOCK never reorders).
    out = assign_primary_ranks(
        out, metric_key="dist_pct", metric_name="dist_sma25", ascending=True
    )
    # Prefer setup_rank when present so UI matches Watchlist Oversold top-N.
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
        "top_n": int(top_n or TOP_N),
        "counts": counts,
        "rows": out,
        "definition": "oversold_pullback_top_n",
        "strategy_id": STRATEGY_DEEP_RECOVERY,
        "rules_version": DEEP_RECOVERY_RULES_VERSION,
        "notes": (
            f"Top {int(top_n or TOP_N)} of Oversold pullback "
            f"(pool {pool_count}). Dist ASC · READY = Fin≥60% + News PASS · "
            f"HIGH/Knife/scores display-only · 5D/LIVE observational."
        ),
    }


def load_deep_recovery_view(*, recompute: bool = True, top_n: int = TOP_N) -> dict[str, Any]:
    return build_deep_recovery_snapshot(persist=True, top_n=top_n)
