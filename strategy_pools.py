"""
LeiBot Strategy Pools — Research-owned source universes for AI Trading.

Principle: Calculate Once — Classify Many — Trade Many.
Pools are cheap filters / set unions over shared Market Data.
They do NOT download prices or recompute SMA/fundamentals.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from strategies import (
    STRATEGY_ALERT_BUY,
    STRATEGY_DEEP_RECOVERY,
    STRATEGY_SAFE_MARGIN,
    STRATEGY_SHORT_SELL,
    STRATEGY_STABLE_GROWTH,
    STRATEGY_IDS,
    normalize_strategy_id,
)

# Pool IDs mirror strategy IDs (1:1 for V1).
POOL_ALERT_BUY = STRATEGY_ALERT_BUY
POOL_DEEP_RECOVERY = STRATEGY_DEEP_RECOVERY
POOL_STABLE_GROWTH = STRATEGY_STABLE_GROWTH
POOL_SAFE_MARGIN = STRATEGY_SAFE_MARGIN
POOL_SHORT_SELL = STRATEGY_SHORT_SELL

POOL_IDS = STRATEGY_IDS

# Dist SMA25 threshold for Deep Recovery dynamic membership (percent points).
DEEP_RECOVERY_DIST_MAX = -10.0


POOL_META: dict[str, dict[str, Any]] = {
    POOL_ALERT_BUY: {
        "name": "Alert Buy Pool",
        "short": "ALERT BUY",
        "pool_type": "MANAGED + SYSTEM",
        "source_label": "MY ∪ NDX100 ∪ AI APPROVED",
        "source_detail": "High-quality observation universe (deduped).",
        "filter_label": "Alert-zone candidates (Dist bands)",
        "rank_label": "Dist SMA25 — deepest first",
        "used_by": STRATEGY_ALERT_BUY,
        "dynamic": False,
        "empty_ok": False,
    },
    POOL_DEEP_RECOVERY: {
        "name": "Deep Recovery Pool",
        "short": "DEEP RECOVERY",
        "pool_type": "DYNAMIC",
        "source_label": "OVERSOLD PULLBACK (top 15)",
        "source_detail": (
            "Watchlist Oversold pullback (Dist% < −10%), same sort as that tab, "
            "then take top 15 for Alert Buy–style timing."
        ),
        "filter_label": "Top 15 Oversold pullback",
        "rank_label": "Watchlist setup order (UP>MIXED>DOWN, Dist ASC)",
        "used_by": STRATEGY_DEEP_RECOVERY,
        "dynamic": True,
        "empty_ok": True,
    },
    POOL_STABLE_GROWTH: {
        "name": "Stable Growth Pool",
        "short": "STABLE GROWTH",
        "pool_type": "DYNAMIC / RESEARCH",
        "source_label": "Watchlist GROWTH",
        "source_detail": "Long-horizon GROWTH sleeve; Dist SMA25 ascending queue (top 10–20).",
        "filter_label": "Top 10–20 by Dist SMA25 ASC",
        "rank_label": "Dist SMA25 ascending (deepest first)",
        "used_by": STRATEGY_STABLE_GROWTH,
        "dynamic": True,
        "empty_ok": True,
    },
    POOL_SAFE_MARGIN: {
        "name": "Safe Margin Pool",
        "short": "SAFE MARGIN",
        "pool_type": "LONG-TERM / HYBRID",
        "source_label": "TARGET RATIO < 80%",
        "source_detail": (
            "Watchlist Target Ratio screen; risk filters then Target ASC "
            "queue (top 10–20)."
        ),
        "filter_label": (
            "MCap>$2B · Price>$5 · AvgVol<3% · Fin≥60% · Knife≠HIGH → top 10–20"
        ),
        "rank_label": "Target Ratio ascending (cheapest vs target first)",
        "used_by": STRATEGY_SAFE_MARGIN,
        "dynamic": True,
        "empty_ok": True,
    },
    POOL_SHORT_SELL: {
        "name": "Short Sell Pool",
        "short": "SHORT SELL",
        "pool_type": "DYNAMIC / RESEARCH",
        "source_label": "Watchlist SHORT",
        "source_detail": (
            "ETF-heavy SHORT sleeve + stables; Dist DESC after 63D>80% and Day%<0."
        ),
        "filter_label": "63D Position > 80% · Day % < 0 → top 10–20",
        "rank_label": "Dist SMA25 descending (most extended first)",
        "used_by": STRATEGY_SHORT_SELL,
        "dynamic": True,
        "empty_ok": True,
    },
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_pool_tables() -> None:
    from db import get_conn, init_db

    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_pool_membership (
                pool_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                asset_type TEXT NOT NULL DEFAULT 'STOCK',
                source TEXT,
                status TEXT,
                manual_override INTEGER NOT NULL DEFAULT 0,
                added_at TEXT,
                last_evaluated_at TEXT,
                PRIMARY KEY (pool_id, ticker)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_spm_ticker
            ON strategy_pool_membership(ticker)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_pool_meta (
                pool_id TEXT PRIMARY KEY,
                member_count INTEGER,
                last_refreshed_at TEXT,
                notes TEXT
            )
            """
        )


def _set_pool_meta(pool_id: str, member_count: int) -> None:
    from db import get_conn

    ensure_pool_tables()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO strategy_pool_meta (pool_id, member_count, last_refreshed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(pool_id) DO UPDATE SET
              member_count = excluded.member_count,
              last_refreshed_at = excluded.last_refreshed_at
            """,
            (pool_id, int(member_count), _utcnow()),
        )


def get_pool_meta_row(pool_id: str) -> dict[str, Any]:
    from db import get_conn

    ensure_pool_tables()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM strategy_pool_meta WHERE pool_id = ?", (pool_id,)
        ).fetchone()
    return dict(row) if row else {}


def list_alert_buy_pool_tickers() -> list[str]:
    """MY ∪ NDX100 ∪ AI APPROVED — reuse AI BUY observation helper."""
    try:
        from ai_buy import buy_observation_tickers

        return list(buy_observation_tickers())
    except Exception:
        return []


def list_deep_recovery_pool_rows(*, limit: int = 15) -> list[dict[str, Any]]:
    """
    Watchlist Oversold pullback top-N (default 15) — same membership as Deep Recovery.
    """
    try:
        from deep_recovery import TOP_N, deep_recovery_universe

        n = int(limit) if limit else TOP_N
        return deep_recovery_universe(top_n=min(n, TOP_N) if n > 0 else TOP_N)
    except Exception:
        from db import list_setup

        rows = [dict(r) for r in list_setup(DEEP_RECOVERY_DIST_MAX)]
        return rows[: max(0, int(limit or 15))]


def list_stable_growth_pool_tickers() -> list[dict[str, Any]]:
    """Watchlist GROWTH sleeve — Dist will be applied by stable_growth.py."""
    from watchlist_config import get_growth_watchlist

    out: list[dict[str, Any]] = []
    for t in get_growth_watchlist() or []:
        u = str(t or "").strip().upper()
        if not u:
            continue
        out.append({"ticker": u, "asset_type": "STOCK", "source": "GROWTH"})
    return out


def list_safe_margin_pool_tickers() -> list[dict[str, Any]]:
    """Target Ratio < 80% screen — risk filter applied by safe_margin.py."""
    from db import list_low_target_ratio

    out: list[dict[str, Any]] = []
    for r in list_low_target_ratio(0.8) or []:
        u = str(r.get("ticker") or "").strip().upper()
        if not u:
            continue
        out.append(
            {
                "ticker": u,
                "asset_type": "STOCK",
                "source": "TARGET",
                "price": r.get("price"),
                "target_1y": r.get("target_1y"),
            }
        )
    return out


def list_short_sell_pool_tickers() -> list[dict[str, Any]]:
    """Watchlist SHORT sleeve — Dist / filters applied by short_sell.py."""
    from watchlist_config import get_short_watchlist

    out: list[dict[str, Any]] = []
    for t in get_short_watchlist() or []:
        u = str(t or "").strip().upper()
        if not u:
            continue
        out.append({"ticker": u, "asset_type": "STOCK", "source": "SHORT"})
    return out


def list_manual_pool_members(pool_id: str) -> list[dict[str, Any]]:
    from db import get_conn

    ensure_pool_tables()
    pid = normalize_strategy_id(pool_id)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT pool_id, ticker, asset_type, source, status,
                   manual_override, added_at, last_evaluated_at
            FROM strategy_pool_membership
            WHERE pool_id = ?
            ORDER BY ticker
            """,
            (pid,),
        ).fetchall()
    return [dict(r) for r in rows]


def refresh_dynamic_pools() -> dict[str, Any]:
    """
    Refresh pool meta counts from shared data (no Yahoo calls).
    """
    ensure_pool_tables()
    out: dict[str, Any] = {}

    alert_n = len(list_alert_buy_pool_tickers())
    _set_pool_meta(POOL_ALERT_BUY, alert_n)
    out[POOL_ALERT_BUY] = alert_n

    deep_n = len(list_deep_recovery_pool_rows(limit=15))
    _set_pool_meta(POOL_DEEP_RECOVERY, deep_n)
    out[POOL_DEEP_RECOVERY] = deep_n

    stable_n = len(list_stable_growth_pool_tickers())
    _set_pool_meta(POOL_STABLE_GROWTH, stable_n)
    out[POOL_STABLE_GROWTH] = stable_n

    safe_n = len(list_safe_margin_pool_tickers())
    _set_pool_meta(POOL_SAFE_MARGIN, safe_n)
    out[POOL_SAFE_MARGIN] = safe_n

    short_n = len(list_short_sell_pool_tickers())
    _set_pool_meta(POOL_SHORT_SELL, short_n)
    out[POOL_SHORT_SELL] = short_n

    return out


def pool_summary(pool_id: str) -> dict[str, Any]:
    """UI card payload for one pool."""
    pid = normalize_strategy_id(pool_id)
    meta = dict(POOL_META.get(pid) or {})
    stored = get_pool_meta_row(pid)
    count = stored.get("member_count")
    # Live count for dynamic pools when meta missing
    if count is None:
        if pid == POOL_ALERT_BUY:
            count = len(list_alert_buy_pool_tickers())
        elif pid == POOL_DEEP_RECOVERY:
            count = len(list_deep_recovery_pool_rows(limit=15))
        elif pid == POOL_STABLE_GROWTH:
            count = len(list_stable_growth_pool_tickers())
        elif pid == POOL_SAFE_MARGIN:
            count = len(list_safe_margin_pool_tickers())
        elif pid == POOL_SHORT_SELL:
            count = len(list_short_sell_pool_tickers())
        else:
            count = len(list_manual_pool_members(pid))
    return {
        "pool_id": pid,
        "name": meta.get("name") or pid,
        "short": meta.get("short") or pid,
        "pool_type": meta.get("pool_type") or "—",
        "source_label": meta.get("source_label") or "—",
        "source_detail": meta.get("source_detail") or "",
        "filter_label": meta.get("filter_label") or "—",
        "rank_label": meta.get("rank_label") or "—",
        "used_by": meta.get("used_by") or pid,
        "dynamic": bool(meta.get("dynamic")),
        "member_count": int(count or 0),
        "last_refreshed_at": stored.get("last_refreshed_at"),
        "strategy_tab": {
            POOL_ALERT_BUY: "buy",
            POOL_DEEP_RECOVERY: "deep_recovery",
            POOL_STABLE_GROWTH: "stable_growth",
            POOL_SAFE_MARGIN: "safe_margin",
            POOL_SHORT_SELL: "short_sell",
        }.get(pid, "overview"),
    }


def list_all_pool_summaries(*, refresh: bool = False) -> list[dict[str, Any]]:
    if refresh:
        try:
            refresh_dynamic_pools()
        except Exception:
            pass
    return [pool_summary(pid) for pid in POOL_IDS]


def list_pool_members_preview(pool_id: str, *, limit: int = 40) -> list[dict[str, Any]]:
    """Small member preview for Research pool detail (empty OK)."""
    pid = normalize_strategy_id(pool_id)
    if pid == POOL_ALERT_BUY:
        tickers = list_alert_buy_pool_tickers()[:limit]
        return [{"ticker": t, "asset_type": "STOCK", "source": "OBS"} for t in tickers]
    if pid == POOL_DEEP_RECOVERY:
        return list_deep_recovery_pool_rows(limit=limit)
    if pid == POOL_STABLE_GROWTH:
        return list_stable_growth_pool_tickers()[:limit]
    if pid == POOL_SAFE_MARGIN:
        return list_safe_margin_pool_tickers()[:limit]
    if pid == POOL_SHORT_SELL:
        return list_short_sell_pool_tickers()[:limit]
    return list_manual_pool_members(pid)[:limit]


def strategy_source_pipeline(strategy_id: str) -> dict[str, Any]:
    """Header block for AI Trading strategy pages."""
    pid = normalize_strategy_id(strategy_id)
    s = pool_summary(pid)
    return {
        "pool_id": pid,
        "source": s["source_label"],
        "filter": s["filter_label"],
        "rank": s["rank_label"],
        "block": "Data / News / Knife / strategy gates",
        "member_count": s["member_count"],
        "pool_short": s["short"],
    }
