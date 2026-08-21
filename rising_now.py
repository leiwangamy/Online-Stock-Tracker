"""
Rising Now / 正在上涨 — independent dynamic Watchlist group.

V1 rule (no retention, no 63D Position filter):
  Up Days >= 3 out of latest 5 trading days
  AND 5D Total Return >= +3%

Uses cached daily_bars closes (no extra Yahoo fetch). Display fields join dashboard_cache.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from db import get_conn, init_db

# Latest 5 trading-day window (needs 6 closes: T-5 … T).
RISING_LOOKBACK_DAYS = 5
RISING_UP_DAYS_MIN = 3
RISING_RETURN_MIN_PCT = 3.0

# Calendar pad so we always have >= 6 sessions after weekends/holidays.
_BARS_CALENDAR_PAD_DAYS = 21

_POOLS_SELECT = (
    "SELECT d.ticker, d.name, d.industry, d.sector, d.price, d.change_pct, "
    "d.avg_move_pct, d.range_63d_low, d.range_63d_high, d.range_63d_pos, "
    "d.target_1y, d.sma, d.dist_pct, d.rebound_pct, d.trend, d.market_cap, "
    "d.avg_vol_20d, d.rvol, d.sma_period, d.earnings_date, d.ai_note, d.updated_at, "
    "COALESCE(u.in_sp500, 0) AS in_sp500, "
    "COALESCE(u.in_ndx100, 0) AS in_ndx100, "
    "COALESCE(u.in_sp400, 0) AS in_sp400, "
    "COALESCE(u.in_sp600, 0) AS in_sp600, "
    "COALESCE(u.in_tsx, 0) AS in_tsx "
    "FROM dashboard_cache d LEFT JOIN universe u ON u.ticker = d.ticker"
)


def rising_rule_summary(*, lang: str = "en") -> str:
    if (lang or "en").lower() == "zh":
        return "近5日上涨 ≥ 3天 · 5日累计涨幅 ≥ +3%"
    return "Up Days ≥ 3/5 · 5D Return ≥ +3%"


def rising_count_label(n: int, *, lang: str = "en") -> str:
    if (lang or "en").lower() == "zh":
        return f"正在上涨：{n} 只股票"
    return f"Rising Now: {n} stocks"


def _up_days_and_return_5d(closes: list[float]) -> tuple[int, float] | None:
    """
    From ascending closes, use the latest 6 points [T-5 … T]:

      Up day i: close[T-5+i] > close[T-5+i-1]  for i = 1..5
      5D Return %: (close[T] / close[T-5] - 1) * 100
    """
    need = RISING_LOOKBACK_DAYS + 1
    if len(closes) < need:
        return None
    window = closes[-need:]
    base = float(window[0])
    last = float(window[-1])
    if base <= 0 or last <= 0:
        return None
    up_days = 0
    for i in range(1, need):
        if float(window[i]) > float(window[i - 1]):
            up_days += 1
    ret_pct = (last / base - 1.0) * 100.0
    return up_days, ret_pct


def _load_recent_closes_by_ticker(
    tickers: set[str] | None = None,
) -> dict[str, list[float]]:
    """Last ~calendar-pad closes per ticker from daily_bars, ascending by date."""
    init_db()
    if tickers:
        ph = ",".join("?" * len(tickers))
        sql = f"""
            SELECT ticker, date, close
            FROM daily_bars
            WHERE ticker IN ({ph}) AND date >= date('now', ?)
            ORDER BY ticker ASC, date ASC
            """
        params: list[Any] = [*tickers, f"-{_BARS_CALENDAR_PAD_DAYS} days"]
    else:
        sql = """
            SELECT ticker, date, close
            FROM daily_bars
            WHERE date >= date('now', ?)
            ORDER BY ticker ASC, date ASC
            """
        params = [f"-{_BARS_CALENDAR_PAD_DAYS} days"]
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    by_ticker: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        t = (r["ticker"] or "").upper()
        if not t:
            continue
        try:
            by_ticker[t].append(float(r["close"]))
        except (TypeError, ValueError):
            continue
    return by_ticker


def _load_dashboard_rows() -> dict[str, dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(_POOLS_SELECT).fetchall()
    return {(r["ticker"] or "").upper(): dict(r) for r in rows if r["ticker"]}


def rising_metrics_map() -> dict[str, dict[str, Any]]:
    """
    Up Days / 5D Return for every ticker with enough daily_bars history.
    Does not apply Rising Now qualification thresholds.
    """
    return rising_metrics_for_tickers(None)


def rising_metrics_for_tickers(
    tickers: Iterable[str] | None,
) -> dict[str, dict[str, Any]]:
    """
    Up Days / 5D Return for selected tickers (or all if tickers is None).
    Does not apply Rising Now qualification thresholds.
    """
    wanted: set[str] | None = None
    if tickers is not None:
        wanted = {(t or "").strip().upper() for t in tickers if t}
        if not wanted:
            return {}

    closes_map = _load_recent_closes_by_ticker(wanted)
    out: dict[str, dict[str, Any]] = {}
    for ticker, closes in closes_map.items():
        metrics = _up_days_and_return_5d(closes)
        if metrics is None:
            continue
        up_days, ret_pct = metrics
        out[ticker] = {
            "up_days_5": int(up_days),
            "return_5d_pct": round(float(ret_pct), 2),
        }
    return out


def list_rising_now(
    *,
    min_up_days: int = RISING_UP_DAYS_MIN,
    min_return_pct: float = RISING_RETURN_MIN_PCT,
) -> list[dict[str, Any]]:
    """
    Qualifying Rising Now rows (dynamic; no retention).

    Default sort: 5D Return desc, then Up Days desc, then ticker.
    """
    closes_map = _load_recent_closes_by_ticker()
    dash = _load_dashboard_rows()
    out: list[dict[str, Any]] = []

    for ticker, closes in closes_map.items():
        metrics = _up_days_and_return_5d(closes)
        if metrics is None:
            continue
        up_days, ret_pct = metrics
        if up_days < min_up_days or ret_pct < min_return_pct:
            continue
        row = dict(dash.get(ticker) or {"ticker": ticker})
        row["ticker"] = ticker
        # Prefer dashboard price/day%; fall back to last bar close.
        if row.get("price") is None and closes:
            row["price"] = float(closes[-1])
        row["up_days_5"] = int(up_days)
        row["return_5d_pct"] = round(float(ret_pct), 2)
        row["price_source"] = "dashboard_cache" if ticker in dash else "daily_bars"
        out.append(row)

    out.sort(
        key=lambda r: (
            -(r.get("return_5d_pct") if r.get("return_5d_pct") is not None else float("-inf")),
            -(r.get("up_days_5") if r.get("up_days_5") is not None else -1),
            r.get("ticker") or "",
        )
    )
    return out
