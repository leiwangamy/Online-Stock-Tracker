"""
MOMENTUM — continuation experiment on observed compounded session totals.

No artificial UP/DOWN/SIDEWAYS / Trend / Acceleration / Recency scores.

Primary rank: ABS(5D TOTAL) DESC
Direction:    sign(5D TOTAL) → LONG / SHORT
Capital:      $750 LONG sleeve + $750 SHORT sleeve (auto-split until used)
Stop:         fixed 1% (LONG Entry×0.99, SHORT Entry×1.01)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from db import get_conn, get_dashboard_by_tickers, get_setting, init_db, set_setting
from momentum_sessions import (
    format_session_history,
    latest_session_price,
    refresh_momentum_watchlist_sessions,
)
from strategies import STRATEGY_MOMENTUM
from watchlist_config import get_momentum_watchlist

META_AS_OF = "momentum_as_of"
META_BUILT = "momentum_built_at"
STOP_LOSS_PCT = 1.0  # configured stop distance (gaps/slippage may differ)
LONG_SLEEVE_USD = 750.0
SHORT_SLEEVE_USD = 750.0
MOMENTUM_RULES_VERSION = "v2_sleeve_750_abs5d"

GUIDANCE_EN = (
    "MOMENTUM follows the current dominant price movement rather than "
    "predicting a reversal. Review the five recent daily movements from "
    "oldest to newest. Give particular attention to the most recent 1–2 "
    "days and to acceleration/deceleration. A sequence whose movements "
    "continue in the same direction, especially with increasing magnitude, "
    "provides stronger evidence of continuation. If recent movements are "
    "shrinking, changing sign, or repeatedly alternating between positive "
    "and negative, continuation is less clear and the trade may be skipped. "
    "This is a probabilistic observation, not a prediction of certainty."
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_momentum_trade_log_table() -> None:
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS momentum_trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_trade_id INTEGER,
                symbol TEXT NOT NULL,
                signal_ts TEXT NOT NULL,
                d_m4 REAL,
                d_m3 REAL,
                d_m2 REAL,
                d_m1 REAL,
                d_0 REAL,
                total_5d REAL,
                direction TEXT NOT NULL,
                entry_price REAL,
                stop_price REAL,
                exit_price REAL,
                exit_reason TEXT,
                pnl REAL,
                return_pct REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_momentum_trade_log_trade
            ON momentum_trade_log(paper_trade_id)
            """
        )


def stop_price_for(entry: float, direction: str, *, stop_pct: float = STOP_LOSS_PCT) -> float | None:
    try:
        px = float(entry)
    except (TypeError, ValueError):
        return None
    if px <= 0:
        return None
    d = (direction or "").strip().upper()
    if d == "LONG":
        return round(px * (1.0 - float(stop_pct) / 100.0), 4)
    if d == "SHORT":
        return round(px * (1.0 + float(stop_pct) / 100.0), 4)
    return None


def direction_from_5d(total_5d: float | None) -> str:
    if total_5d is None:
        return "NEUTRAL"
    try:
        v = float(total_5d)
    except (TypeError, ValueError):
        return "NEUTRAL"
    if v > 0:
        return "LONG"
    if v < 0:
        return "SHORT"
    return "NEUTRAL"


def build_momentum_snapshot(*, persist: bool = True, refresh_sessions: bool = True) -> dict[str, Any]:
    """
    Build MOMENTUM rows from real D-4..D0 / 5D TOTAL only.
    Rank by ABS(5D TOTAL) DESC. Direction from sign(5D TOTAL).
    """
    tickers = get_momentum_watchlist()
    session_refresh: dict[str, Any] = {}
    price_refresh: dict[str, Any] = {}
    if refresh_sessions and tickers:
        try:
            from market_data import refresh_dashboard_for_tickers

            price_refresh = refresh_dashboard_for_tickers(tickers, max_workers=4)
        except Exception as exc:
            price_refresh = {"ok": 0, "errors": len(tickers), "error": str(exc)}
        try:
            session_refresh = refresh_momentum_watchlist_sessions(tickers)
        except Exception as exc:
            session_refresh = {"ok": False, "error": str(exc)}

    dash = get_dashboard_by_tickers(tickers) if tickers else {}
    rows: list[dict[str, Any]] = []
    for t in tickers:
        d = dict(dash.get(t) or {"ticker": t})
        d["ticker"] = t
        hist = format_session_history(t, n_days=5)
        d["session_history"] = hist
        d["session_history_compact"] = hist.get("compact") or "—"
        totals = hist.get("daily_totals_pct") or [None] * 5
        while len(totals) < 5:
            totals.append(None)
        d["day_totals"] = totals[:5]
        d["day_total_details"] = hist.get("daily_total_details") or []
        total_5d = hist.get("total_5d_pct")
        d["total_5d_pct"] = total_5d
        d["public_day_totals_compact"] = hist.get("public_compact") or "N/A | N/A | N/A | N/A | N/A | N/A"
        try:
            abs_5d = abs(float(total_5d)) if total_5d is not None else None
        except (TypeError, ValueError):
            abs_5d = None
        d["abs_5d_total"] = abs_5d
        direction = direction_from_5d(total_5d)
        d["momentum_direction"] = direction
        d["side"] = "short" if direction == "SHORT" else ("long" if direction == "LONG" else None)
        sess_px = latest_session_price(t)
        if sess_px is not None:
            d["price"] = sess_px
            d["price_source"] = "momentum_session"
        elif d.get("price") is not None:
            d["price_source"] = "dashboard_cache"
        stop_px = stop_price_for(d.get("price"), direction)
        d["stop_price"] = stop_px
        d["stop_loss_pct"] = STOP_LOSS_PCT if direction in ("LONG", "SHORT") else None
        d["tradeable"] = bool(
            direction in ("LONG", "SHORT")
            and total_5d is not None
            and d.get("price") is not None
            and float(d.get("price") or 0) > 0
        )
        d["source_codes"] = "MOMENTUM"
        rows.append(d)

    # Primary ranking: ABS(5D TOTAL) DESC — missing totals last.
    rows.sort(
        key=lambda r: (
            0 if r.get("abs_5d_total") is not None else 1,
            -(float(r["abs_5d_total"]) if r.get("abs_5d_total") is not None else 0.0),
            r.get("ticker") or "",
        )
    )
    for i, r in enumerate(rows, start=1):
        r["primary_rank"] = i
        r["setup_rank"] = i
        r["queue_rank"] = i
        r["primary_metric_name"] = "abs_5d_total"
        r["primary_metric_value"] = r.get("abs_5d_total")

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if persist:
        set_setting(META_AS_OF, as_of)
        set_setting(META_BUILT, _utcnow())

    return {
        "as_of": as_of,
        "built_at": get_setting(META_BUILT, "") or _utcnow(),
        "universe_count": len(rows),
        "pool_count": len(tickers),
        "rows": rows,
        "definition": "momentum_abs5d_continuation",
        "strategy_id": STRATEGY_MOMENTUM,
        "rules_version": MOMENTUM_RULES_VERSION,
        "stop_loss_pct": STOP_LOSS_PCT,
        "session_refresh": session_refresh,
        "price_refresh": price_refresh,
        "guidance": GUIDANCE_EN,
        "notes": (
            f"Continuation: ABS(5D TOTAL) DESC · +5D LONG / −5D SHORT · "
            f"${LONG_SLEEVE_USD:g} long + ${SHORT_SLEEVE_USD:g} short sleeves · "
            f"fixed {STOP_LOSS_PCT:g}% stop · auto-allocate until used."
        ),
    }


def load_momentum_view(*, recompute: bool = True) -> dict[str, Any]:
    return build_momentum_snapshot(persist=True, refresh_sessions=recompute)


def record_momentum_trade_open(
    *,
    paper_trade_id: int | None,
    row: dict[str, Any],
    entry_price: float,
    stop_price: float | None,
    signal_ts: str | None = None,
) -> None:
    """Persist experiment snapshot at entry for later statistical review."""
    ensure_momentum_trade_log_table()
    totals = row.get("day_totals") or [None] * 5
    while len(totals) < 5:
        totals.append(None)
    now = _utcnow()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO momentum_trade_log (
              paper_trade_id, symbol, signal_ts,
              d_m4, d_m3, d_m2, d_m1, d_0, total_5d, direction,
              entry_price, stop_price,
              exit_price, exit_reason, pnl, return_pct,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
            """,
            (
                paper_trade_id,
                (row.get("ticker") or "").strip().upper(),
                signal_ts or now,
                totals[0],
                totals[1],
                totals[2],
                totals[3],
                totals[4],
                row.get("total_5d_pct"),
                (row.get("momentum_direction") or "NEUTRAL").upper(),
                entry_price,
                stop_price,
                now,
                now,
            ),
        )


def finalize_momentum_trade_log(
    paper_trade_id: int,
    *,
    exit_price: float | None,
    exit_reason: str | None,
    pnl: float | None,
    return_pct: float | None,
) -> None:
    ensure_momentum_trade_log_table()
    now = _utcnow()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE momentum_trade_log
            SET exit_price = ?, exit_reason = ?, pnl = ?, return_pct = ?, updated_at = ?
            WHERE paper_trade_id = ?
            """,
            (exit_price, exit_reason, pnl, return_pct, now, int(paper_trade_id)),
        )
