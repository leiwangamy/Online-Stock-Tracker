"""
Safe Margin — Target Ratio < 80% watchlist, Target ASC queue + risk filters.

SOURCE = Watchlist 「Target Ratio < 80%」 (list_low_target_ratio).
RANK   = Target Ratio = price / 1Y Target, ascending (cheapest vs target first).
FILTER = risk gates then top TOP_N (default 15, Owner band 10–20):
           Market Cap > $2B · Price > $5 · Avg volatility < 3%
           · Financial ≥ 60% · Knife level ≠ HIGH
TRADE  = READY queue on SAFE_MARGIN book.
EXITS  = 10% trailing stop, no Take Profit; on EXIT auto-buy then-top unused
         name on the refreshed Target ASC queue.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ai_buy import status_emoji
from db import get_conn, get_setting, list_low_target_ratio, set_setting
from strategies import STRATEGY_SAFE_MARGIN, assign_primary_ranks, cap_category

TOP_N = 15
STOP_LOSS_PCT = 10.0  # trailing; no take-profit
TRAILING_STOP = True
MIN_MARKET_CAP = 2_000_000_000.0
MIN_PRICE = 5.0
MAX_AVG_MOVE_PCT = 3.0
MIN_FINANCIAL_PASS = 0.60  # ok / total_known

META_AS_OF = "safe_margin_as_of"
META_BUILT = "safe_margin_built_at"


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _target_ratio(row: dict[str, Any]) -> float | None:
    try:
        px = float(row.get("price"))
        tgt = float(row.get("target_1y"))
    except (TypeError, ValueError):
        return None
    if px <= 0 or tgt <= 0:
        return None
    return px / tgt


def _financial_pass_rate(fund: dict[str, Any] | None) -> float | None:
    if not fund:
        return None
    try:
        ok = fund.get("ok")
        known = fund.get("total_known")
        if ok is None or known is None or float(known) <= 0:
            return None
        return float(ok) / float(known)
    except (TypeError, ValueError):
        return None


def _risk_fail_reasons(row: dict[str, Any]) -> list[str]:
    """Return risk filter failure codes (empty = pass)."""
    fails: list[str] = []
    try:
        mcap = row.get("market_cap")
        if mcap is None or float(mcap) <= MIN_MARKET_CAP:
            fails.append("MCAP")
    except (TypeError, ValueError):
        fails.append("MCAP")

    try:
        px = row.get("price")
        if px is None or float(px) <= MIN_PRICE:
            fails.append("PRICE")
    except (TypeError, ValueError):
        fails.append("PRICE")

    try:
        adm = row.get("avg_move_pct")
        if adm is None or float(adm) >= MAX_AVG_MOVE_PCT:
            fails.append("VOL")
    except (TypeError, ValueError):
        fails.append("VOL")

    fin = row.get("financial_pass_rate")
    if fin is None or float(fin) < MIN_FINANCIAL_PASS:
        fails.append("FIN")

    knife = row.get("knife") if isinstance(row.get("knife"), dict) else {}
    level = (knife.get("level") or row.get("knife_level") or "").upper()
    score = knife.get("score") if knife else row.get("knife_score")
    # KNIFE ≠ HIGH (also block worse KNIFE band)
    if level in ("HIGH", "KNIFE") or (
        score is not None and float(score) >= 45
    ):
        fails.append("KNIFE_HIGH")
    return fails


def target_watchlist_rows() -> list[dict[str, Any]]:
    """Full Target Ratio < 80% screen with ratio attached, sorted ASC."""
    rows = [dict(r) for r in list_low_target_ratio(0.8)]
    for r in rows:
        r["ticker"] = (r.get("ticker") or "").strip().upper()
        r["target_ratio"] = _target_ratio(r)
    rows.sort(
        key=lambda r: (
            r.get("target_ratio")
            if r.get("target_ratio") is not None
            else 9999.0,
            r.get("ticker") or "",
        )
    )
    return rows


def build_safe_margin_snapshot(
    *, persist: bool = True, top_n: int = TOP_N
) -> dict[str, Any]:
    from knife_risk import attach_knife_risk, ensure_benchmark_returns
    from market_data import get_fund_cached_only

    pool_rows = target_watchlist_rows()
    pool_count = len(pool_rows)
    n = max(10, min(20, int(top_n or TOP_N)))

    tickers = [r["ticker"] for r in pool_rows if r.get("ticker")]
    fund_map = get_fund_cached_only(tickers) if tickers else {}

    for r in pool_rows:
        t = r["ticker"]
        fund = fund_map.get(t)
        r["fund"] = fund
        r["financial_pass_rate"] = _financial_pass_rate(fund)
        if fund:
            r["financial_ok"] = fund.get("ok")
            r["financial_known"] = fund.get("total_known")

    if pool_rows:
        try:
            ensure_benchmark_returns(force=False)
            attach_knife_risk(pool_rows, ensure_bench=False)
        except Exception:
            for r in pool_rows:
                r.setdefault("knife", None)

    # Risk filter first, then keep Target ASC order, then top-N queue.
    passed: list[dict[str, Any]] = []
    for r in pool_rows:
        fails = _risk_fail_reasons(r)
        r["risk_fail_reasons"] = fails
        r["risk_pass"] = not fails
        if not fails:
            passed.append(r)

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
                (STRATEGY_SAFE_MARGIN,),
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
        t = r["ticker"]
        r["setup_rank"] = i
        r["queue_rank"] = i
        r["sources"] = [f"TARGET#{i}"]
        r["source_codes"] = f"TARGET#{i}"
        r["cap_bucket"] = cap_category(r.get("market_cap"))
        k = r.get("knife") or {}
        r["knife_score"] = k.get("score") if isinstance(k, dict) else r.get("knife_score")
        r["knife_level"] = (
            k.get("level") if isinstance(k, dict) else r.get("knife_level")
        )
        r["news_status"] = r.get("news_status") or "PASS"
        r["buy_allowed"] = True
        r["block_reasons"] = []
        r["high_block"] = False
        r["knife_block"] = False
        r["news_block"] = False
        timing = "READY"
        r["timing_status"] = timing
        r["price_zone"] = "QUEUE"
        r["buy_score"] = i
        status = "HOLD" if t in held else timing
        r["buy_status"] = status
        r["status_emoji"] = status_emoji(status)
        parts = [
            f"target#{i}",
            f"TR={float(r['target_ratio']):.2f}"
            if r.get("target_ratio") is not None
            else "TR=—",
            "trail_stop=10%",
            "no_take",
        ]
        if r.get("financial_pass_rate") is not None:
            parts.append(f"Fin={float(r['financial_pass_rate'])*100:.0f}%")
        if status == "HOLD":
            parts.append(f"was={timing}")
        r["reason"] = " · ".join(parts)
        counts[status] = counts.get(status, 0) + 1
        out.append(r)

    out = assign_primary_ranks(
        out, metric_key="target_ratio", metric_name="target_ratio", ascending=True
    )
    out.sort(
        key=lambda x: (
            x.get("setup_rank") if x.get("setup_rank") is not None else 9999,
            x.get("target_ratio") if x.get("target_ratio") is not None else 9999.0,
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
        "definition": "target_ratio_asc_risk_filtered",
        "strategy_id": STRATEGY_SAFE_MARGIN,
        "stop_loss_pct": STOP_LOSS_PCT,
        "trailing_stop": TRAILING_STOP,
        "take_profit_pct": None,
        "notes": (
            f"Target Ratio < 80% → risk filters → Dist/Target ASC top {n} "
            f"(pool {pool_count}, passed {len(passed)}). "
            f"Trailing stop −{STOP_LOSS_PCT:.0f}% · no Take · EXIT→next unused."
        ),
    }


def load_safe_margin_view(*, recompute: bool = True, top_n: int = TOP_N) -> dict[str, Any]:
    return build_safe_margin_snapshot(persist=True, top_n=top_n)
