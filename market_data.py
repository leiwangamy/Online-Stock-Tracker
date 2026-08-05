"""Yahoo Finance market data → SMA / distance / rebound metrics."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from db import get_setting, list_universe, save_dashboard_rows
from universe import ensure_universe


def _sma(series: pd.Series, period: int) -> float | None:
    clean = series.dropna()
    if len(clean) < period:
        return None
    return float(clean.iloc[-period:].mean())


def _rebound_pct(series: pd.Series, lookback: int) -> float | None:
    """Percent rebound from the lowest close in the lookback window to latest close."""
    clean = series.dropna()
    if len(clean) < 2:
        return None
    window = clean.iloc[-lookback:] if len(clean) >= lookback else clean
    low = float(window.min())
    last = float(window.iloc[-1])
    if low <= 0:
        return None
    return round((last / low - 1) * 100, 2)


def _format_earnings_date(value: Any) -> str | None:
    """Return a short calendar date only (for evening news / decision checks)."""
    if value is None or value == "":
        return None
    try:
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return None
        # Drop timezone — we only care about the calendar day for evening decisions
        if getattr(ts, "tzinfo", None) is not None or getattr(ts, "tz", None) is not None:
            try:
                ts = ts.tz_convert(None)
            except Exception:
                try:
                    ts = ts.tz_localize(None)
                except Exception:
                    pass
        d = ts.date()
        return f"{d.month}/{d.day}/{d.year}"
    except Exception:
        text = str(value).strip()
        # Last resort: pull YYYY-MM-DD or M/D/YYYY out of a messy string
        for part in text.replace(",", " ").split():
            if part.count("-") == 2 or part.count("/") >= 2:
                try:
                    return _format_earnings_date(part)
                except Exception:
                    continue
        return None


def _next_earnings_date(ticker_obj: yf.Ticker) -> str | None:
    """Best-effort next earnings date from Yahoo; date only, no time."""
    today = datetime.now().date()

    # 1) earnings_dates table (often includes upcoming rows)
    try:
        ed = ticker_obj.get_earnings_dates(limit=12)
        if ed is not None and not ed.empty:
            upcoming: list[Any] = []
            for idx in ed.index:
                ts = pd.Timestamp(idx)
                if pd.isna(ts):
                    continue
                d = ts.to_pydatetime().date() if hasattr(ts.to_pydatetime(), "date") else ts.date()
                if d >= today:
                    upcoming.append(ts)
            if upcoming:
                upcoming.sort()
                return _format_earnings_date(upcoming[0])
            # fallback: most recent known date if nothing upcoming
            return _format_earnings_date(ed.index[0])
    except Exception:
        pass

    # 2) calendar["Earnings Date"]
    try:
        cal = ticker_obj.calendar
        if cal is not None:
            if isinstance(cal, dict):
                raw = cal.get("Earnings Date") or cal.get("earningsDate")
            else:
                raw = None
                try:
                    if "Earnings Date" in cal.index:
                        raw = cal.loc["Earnings Date"]
                except Exception:
                    raw = None
            if raw is not None:
                if isinstance(raw, (list, tuple)) and raw:
                    raw = raw[0]
                if hasattr(raw, "__iter__") and not isinstance(raw, (str, bytes, datetime)):
                    try:
                        raw = list(raw)[0]
                    except Exception:
                        pass
                return _format_earnings_date(raw)
    except Exception:
        pass

    return None


def fetch_metrics_for_ticker(
    ticker: str,
    *,
    sma_period: int,
    rebound_lookback: int,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    meta = meta or {}
    try:
        t = yf.Ticker(ticker)
        # Need enough bars for longest settings
        hist = t.history(period="1y", auto_adjust=True)
        if hist is None or hist.empty or "Close" not in hist:
            return None
        closes = hist["Close"]
        price = float(closes.iloc[-1])
        sma = _sma(closes, sma_period)
        dist_pct = None if sma is None or sma == 0 else round((price / sma - 1) * 100, 2)
        rebound = _rebound_pct(closes, rebound_lookback)
        earnings_date = _next_earnings_date(t)
        return {
            "ticker": ticker,
            "name": meta.get("name") or "",
            "industry": meta.get("industry") or "",
            "sector": meta.get("sector") or "",
            "price": round(price, 2),
            "sma": None if sma is None else round(sma, 2),
            "dist_pct": dist_pct,
            "rebound_pct": rebound,
            "sma_period": sma_period,
            "earnings_date": earnings_date,
            "ai_note": None,  # reserved
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None


def refresh_dashboard_cache(
    *,
    max_workers: int = 8,
    limit: int | None = None,
) -> dict[str, Any]:
    ensure_universe()
    universe = list_universe()
    if limit:
        universe = universe[:limit]

    sma_period = int(get_setting("sma_period", 25))
    rebound_lookback = int(get_setting("rebound_lookback", sma_period))
    # Keep rebound lookback at least as long as SMA period by default when equal settings
    if rebound_lookback < 5:
        rebound_lookback = sma_period

    meta_by_ticker = {row["ticker"]: row for row in universe}
    rows: list[dict[str, Any]] = []
    errors = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                fetch_metrics_for_ticker,
                row["ticker"],
                sma_period=sma_period,
                rebound_lookback=rebound_lookback,
                meta=row,
            ): row["ticker"]
            for row in universe
        }
        for fut in as_completed(futures):
            result = fut.result()
            if result is None:
                errors += 1
            else:
                rows.append(result)

    save_dashboard_rows(rows)
    return {
        "ok": len(rows),
        "errors": errors,
        "sma_period": sma_period,
        "rebound_lookback": rebound_lookback,
        "universe": len(universe),
    }
