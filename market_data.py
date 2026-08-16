"""Yahoo Finance market data → SMA / distance / rebound metrics."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from db import get_setting, list_universe, save_dashboard_rows
from universe import ensure_universe


# Long-term trend rule (fixed, independent of the configurable dist SMA):
#   UP    : SMA63 > SMA252 and SMA252 sloping up
#   DOWN  : SMA63 < SMA252 and SMA252 sloping down
#   MIXED : anything else (relationship and slope disagree)
TREND_FAST = 63
TREND_SLOW = 252
TREND_SLOPE_LOOKBACK = 21


def _sma(series: pd.Series, period: int) -> float | None:
    clean = series.dropna()
    if len(clean) < period:
        return None
    return float(clean.iloc[-period:].mean())


# Window for "average daily move" (typical daily volatility), in trading days.
AVG_MOVE_LOOKBACK = 63
RANGE_63D_LOOKBACK = 63  # trading days for 63D High/Low/Position% (same window as avg move)


def _change_pct(series: pd.Series) -> float | None:
    """Latest daily change % from the last two closes."""
    clean = series.dropna()
    if len(clean) < 2:
        return None
    prev = float(clean.iloc[-2])
    last = float(clean.iloc[-1])
    if prev == 0:
        return None
    return round((last / prev - 1) * 100, 2)


def _avg_daily_move(series: pd.Series, lookback: int = AVG_MOVE_LOOKBACK) -> float | None:
    """Average absolute daily % change over the lookback window (volatility proxy)."""
    clean = series.dropna()
    if len(clean) < 2:
        return None
    pct = clean.pct_change().dropna().abs()
    if pct.empty:
        return None
    window = pct.iloc[-lookback:] if len(pct) >= lookback else pct
    return round(float(window.mean()) * 100, 2)


def _trend(series: pd.Series) -> str | None:
    """Long-term trend from SMA63 vs SMA252 plus the SMA252 slope."""
    clean = series.dropna()
    if len(clean) < TREND_SLOW:
        return None
    fast = float(clean.iloc[-TREND_FAST:].mean())
    slow = float(clean.iloc[-TREND_SLOW:].mean())
    if len(clean) >= TREND_SLOW + TREND_SLOPE_LOOKBACK:
        slow_prev = float(
            clean.iloc[-(TREND_SLOW + TREND_SLOPE_LOOKBACK):-TREND_SLOPE_LOOKBACK].mean()
        )
    else:
        slow_prev = slow
    slope_up = slow > slow_prev
    slope_down = slow < slow_prev
    if fast > slow and slope_up:
        return "UP"
    if fast < slow and slope_down:
        return "DOWN"
    return "MIXED"


def _volume_stats(volume: pd.Series) -> tuple[float | None, float | None]:
    """Return (avg_vol_20d, rvol) where RVOL = latest volume / prior-20-day average.

    RVOL is a *relative* volume gauge (today vs its own normal), comparable across
    large and small caps, unlike raw volume. Interpret it together with Daily %.
    """
    clean = volume.dropna()
    clean = clean[clean > 0]
    if clean.empty:
        return None, None
    latest = float(clean.iloc[-1])
    # Average of the 20 sessions *before* the latest, so RVOL isn't self-referential.
    prior = clean.iloc[-21:-1] if len(clean) >= 21 else clean.iloc[:-1]
    avg20 = float(clean.iloc[-20:].mean()) if len(clean) >= 1 else None
    base = float(prior.mean()) if len(prior) else None
    rvol = round(latest / base, 2) if base and base > 0 else None
    return (round(avg20) if avg20 else None), rvol


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


def _range_63d(
    series: pd.Series, lookback: int = RANGE_63D_LOOKBACK
) -> tuple[float | None, float | None, float | None]:
    """
    63-trading-day low / high / position%.
    Position% = (price - low) / (high - low) × 100.
    Returns (None, None, None) if fewer than `lookback` bars; position None if high==low.
    """
    clean = series.dropna()
    if len(clean) < lookback:
        return None, None, None
    window = clean.iloc[-lookback:]
    try:
        low = float(window.min())
        high = float(window.max())
        last = float(window.iloc[-1])
    except (TypeError, ValueError):
        return None, None, None
    if any(v != v for v in (low, high, last)):  # NaN check
        return None, None, None
    if low <= 0 or high <= 0:
        return None, None, None
    low_r, high_r = round(low, 2), round(high, 2)
    if high == low:
        return low_r, high_r, None
    pos = (last - low) / (high - low) * 100
    if pos != pos:  # NaN
        return low_r, high_r, None
    return low_r, high_r, round(pos, 2)


def mos_pct(est_value: float | None, price: float | None) -> float | None:
    """
    Margin of Safety % = (Est.Value − Price) / Est.Value × 100.
    Positive when Est.Value > price. Does not alter AI / Opportunity score.

    Price MUST be the same figure shown on Watchlist (dashboard_cache / live
    row price), not a separate Yahoo snapshot from the valuation fetch,
    and never a price stored inside the intrinsic_value cache.
    """
    if est_value is None or price is None:
        return None
    try:
        ev = float(est_value)
        px = float(price)
    except (TypeError, ValueError):
        return None
    if ev == 0 or ev != ev or px != px:
        return None
    return round((ev - px) / ev * 100, 2)


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def resolve_watchlist_mos_price(
    row: dict[str, Any],
    *,
    stale_hours: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    MOS price = Watchlist / Market Data row Current Price only.

    - Never reads valuation / intrinsic_value cache prices.
    - If price timestamp is missing or older than MOS_PRICE_STALE_HOURS → stale.
    - Stale prices must not produce a current MOS% (caller shows — / warning).
    """
    import valuation_config as cfg

    price = None
    try:
        if row.get("price") is not None:
            price = float(row["price"])
    except (TypeError, ValueError):
        price = None
    source = row.get("price_source") or (
        "dashboard_cache" if row.get("updated_at") else "watchlist_row"
    )
    as_of = row.get("updated_at") or row.get("price_as_of") or None
    limit_h = cfg.MOS_PRICE_STALE_HOURS if stale_hours is None else stale_hours
    ts = _parse_ts(as_of)
    now_utc = now or datetime.now(timezone.utc)
    age_hours: float | None = None
    stale = False
    stale_reason: str | None = None
    if price is None:
        stale = True
        stale_reason = "missing_price"
    elif ts is None:
        stale = True
        stale_reason = "missing_price_timestamp"
    else:
        age_hours = (now_utc - ts).total_seconds() / 3600.0
        if age_hours > float(limit_h):
            stale = True
            stale_reason = f"price_stale_{age_hours:.0f}h>{limit_h}h"

    return {
        "price": price,
        "source": source,
        "as_of": as_of,
        "age_hours": None if age_hours is None else round(age_hours, 1),
        "stale": stale,
        "stale_reason": stale_reason,
        "stale_hours_limit": float(limit_h),
    }


def compute_row_mos(
    est_value: float | None,
    row: dict[str, Any],
    *,
    stale_hours: float | None = None,
) -> dict[str, Any]:
    """
    Dynamic MOS from Est.Value (may be cached) × latest row Current Price.
    Does not trigger DCF. Returns mos_pct=None when price is stale/missing.
    """
    mos_px = resolve_watchlist_mos_price(row, stale_hours=stale_hours)
    mos = None
    if not mos_px["stale"] and est_value is not None:
        mos = mos_pct(est_value, mos_px["price"])
    return {
        **mos_px,
        "mos_pct": mos,
    }


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


def _market_cap(ticker_obj: yf.Ticker) -> float | None:
    """Best-effort market cap via fast_info (cheap), else price * shares."""
    try:
        fi = ticker_obj.fast_info
    except Exception:
        return None
    for key in ("market_cap", "marketCap"):
        try:
            val = fi[key] if hasattr(fi, "__getitem__") else None
        except Exception:
            val = None
        if val is None:
            val = getattr(fi, "market_cap", None)
        if val:
            try:
                return float(val)
            except Exception:
                pass
    # Fallback: last price * shares outstanding
    try:
        price = float(getattr(fi, "last_price", None) or fi["last_price"])
        shares = float(getattr(fi, "shares", None) or fi["shares"])
        if price and shares:
            return price * shares
    except Exception:
        pass
    return None


def _target_1y(ticker_obj: yf.Ticker) -> float | None:
    """Yahoo 1-year analyst mean target price (targetMeanPrice)."""
    try:
        info = ticker_obj.info or {}
    except Exception:
        info = {}
    for key in ("targetMeanPrice", "targetMean", "targetMeanPriceRaw"):
        val = info.get(key)
        if val is None:
            continue
        try:
            f = float(val)
            if f > 0:
                return round(f, 2)
        except (TypeError, ValueError):
            continue
    return None


def target_ratio(price: float | None, target_1y: float | None) -> float | None:
    """Target Ratio = Current Price / 1Y Target. Smaller → more interesting."""
    try:
        if price is None or target_1y is None:
            return None
        p, t = float(price), float(target_1y)
        if t <= 0 or p <= 0:
            return None
        return round(p / t, 2)
    except (TypeError, ValueError):
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
        # 2y gives enough bars for SMA252 and its slope (needs >252 sessions).
        hist = t.history(period="2y", auto_adjust=True)
        if hist is None or hist.empty or "Close" not in hist:
            return None
        closes = hist["Close"]
        price = float(closes.iloc[-1])
        sma = _sma(closes, sma_period)
        dist_pct = None if sma is None or sma == 0 else round((price / sma - 1) * 100, 2)
        rebound = _rebound_pct(closes, rebound_lookback)
        change_pct = _change_pct(closes)
        avg_move_pct = _avg_daily_move(closes)
        range_low, range_high, range_pos = _range_63d(closes)
        trend = _trend(closes)
        avg_vol_20d, rvol = _volume_stats(hist["Volume"]) if "Volume" in hist else (None, None)
        market_cap = _market_cap(t)
        earnings_date = _next_earnings_date(t)
        target_1y = _target_1y(t)
        return {
            "ticker": ticker,
            "name": meta.get("name") or "",
            "industry": meta.get("industry") or "",
            "sector": meta.get("sector") or "",
            "price": round(price, 2),
            "change_pct": change_pct,
            "avg_move_pct": avg_move_pct,
            "range_63d_low": range_low,
            "range_63d_high": range_high,
            "range_63d_pos": range_pos,
            "sma": None if sma is None else round(sma, 2),
            "dist_pct": dist_pct,
            "rebound_pct": rebound,
            "trend": trend,
            "market_cap": market_cap,
            "avg_vol_20d": avg_vol_20d,
            "rvol": rvol,
            "sma_period": sma_period,
            "earnings_date": earnings_date,
            "target_1y": target_1y,
            "ai_note": None,  # reserved
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception:
        return None


def refresh_dashboard_cache(
    *,
    max_workers: int = 8,
    limit: int | None = None,
    group: str | None = None,
) -> dict[str, Any]:
    ensure_universe()
    universe = list_universe(group)
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
        "group": group,
    }


# ---------------------------------------------------------------------------
# Fundamentals (财报) + News (新闻) signals — Yahoo Finance, cached in-process.
# Fund portion is also mirrored to disk so lightweight tables can read without refetch.
# ---------------------------------------------------------------------------

NEWS_LOOKBACK_DAYS = 30
_SIGNAL_TTL = 900  # seconds (15 min) — .info / .news are slow, so cache hard
_signal_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_FUND_DISK_PATH = Path(__file__).resolve().parent / "data" / "logs" / "fund_cache.json"
_NEWS_DISK_PATH = Path(__file__).resolve().parent / "data" / "logs" / "news_cache.json"
_NEWS_DISK_TTL = 6 * 3600  # 6h — shared news cache; missing/expired → refetch
_fund_disk_cache: dict[str, Any] | None = None
_news_disk_cache: dict[str, Any] | None = None
_fund_disk_lock = threading.Lock()
_news_disk_lock = threading.Lock()


def fund_cache_path() -> Path:
    return _FUND_DISK_PATH


def news_cache_path() -> Path:
    return _NEWS_DISK_PATH


def _fund_payload_valid(fund: Any) -> bool:
    """True when Financial Score payload is usable for display (existing rules)."""
    return isinstance(fund, dict) and fund.get("health") not in (None, "unknown")


def _fund_period_meta(info: dict[str, Any] | None) -> dict[str, Any]:
    """Snapshot of filing identity fields — used to detect new quarter/year later."""
    info = info or {}
    return {
        "mostRecentQuarter": info.get("mostRecentQuarter"),
        "lastFiscalYearEnd": info.get("lastFiscalYearEnd"),
        "earningsTimestamp": info.get("earningsTimestamp") or info.get("earningsTimestampStart"),
    }


def _fund_entry_valid(entry: Any) -> bool:
    return isinstance(entry, dict) and _fund_payload_valid(entry.get("fund"))


def _fund_entry_stale_vs_info(entry: dict[str, Any], info: dict[str, Any] | None) -> bool:
    """
    Stale when Yahoo reports a newer quarter / fiscal year / earnings stamp
    than what we stored. Missing meta on either side → not forced stale
    (caller may still treat empty fund as invalid).
    """
    if not info:
        return False
    live = _fund_period_meta(info)
    for key in ("mostRecentQuarter", "lastFiscalYearEnd", "earningsTimestamp"):
        old = entry.get(key)
        new = live.get(key)
        if old is None or new is None:
            continue
        # Normalize timestamps / dates to string for stable compare
        if str(old) != str(new):
            return True
    return False


def _load_fund_disk() -> dict[str, Any]:
    global _fund_disk_cache
    if _fund_disk_cache is not None:
        return _fund_disk_cache
    cache: dict[str, Any] = {}
    if _FUND_DISK_PATH.exists():
        try:
            raw = json.loads(_FUND_DISK_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cache = raw
        except Exception:
            cache = {}
    _fund_disk_cache = cache
    return cache


def _persist_fund_disk(
    ticker: str,
    fund: dict[str, Any] | None,
    *,
    info: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Write/update one ticker's fund snapshot immediately (shared persistent cache)."""
    if not fund:
        return
    t = (ticker or "").strip().upper()
    if not t:
        return
    period = meta if meta is not None else _fund_period_meta(info)
    entry = {
        "ts": time.time(),
        "fund": fund,
        "mostRecentQuarter": period.get("mostRecentQuarter"),
        "lastFiscalYearEnd": period.get("lastFiscalYearEnd"),
        "earningsTimestamp": period.get("earningsTimestamp"),
    }
    with _fund_disk_lock:
        cache = _load_fund_disk()
        cache[t] = entry
        try:
            _FUND_DISK_PATH.parent.mkdir(parents=True, exist_ok=True)
            _FUND_DISK_PATH.write_text(
                json.dumps(cache, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass


def get_fund_cached_only(tickers: list[str]) -> dict[str, dict[str, Any] | None]:
    """
    Batch-read Financial Score / 财报 from existing caches only.
    Order: in-process signal cache → disk fund_cache.json.
    Never hits Yahoo, never refreshes TTL, never loads news.
    Missing / invalid tickers map to None.
    """
    disk = _load_fund_disk()
    out: dict[str, dict[str, Any] | None] = {}
    seen: set[str] = set()
    for raw in tickers:
        t = (raw or "").strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        fund = None
        mem = _signal_cache.get(t)
        if mem and isinstance(mem[1], dict) and _fund_payload_valid(mem[1].get("fund")):
            fund = mem[1].get("fund")
        if fund is None:
            entry = disk.get(t)
            if _fund_entry_valid(entry):
                fund = entry.get("fund")
        out[t] = fund if _fund_payload_valid(fund) else None
    return out


def _fetch_fund_one(ticker: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Fund-only Yahoo read + existing Financial Score logic (no news / DCF / CLV).
    Returns (fund, info).
    """
    tk = yf.Ticker(ticker)
    try:
        info = tk.info or {}
    except Exception:
        info = {}
    if not isinstance(info, dict):
        info = {}
    cf = _cashflow_trend(tk)
    fund = _fundamentals_from_info(info, cf)
    return fund, info


def ensure_fund_cache(
    tickers: list[str],
    *,
    max_workers: int = 3,
    force: bool = False,
) -> dict[str, Any]:
    """
    Fill shared persistent fund_cache for tickers.
    Reuses valid disk/memory entries; only downloads missing/invalid (unless force).
    Writes each success immediately. Failures are collected; job continues.
    Does not run News / AI / DCF / CLV.
    """
    uniq: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        t = (raw or "").strip().upper()
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)

    disk = _load_fund_disk()
    already = [t for t in uniq if (not force) and _fund_entry_valid(disk.get(t))]
    # Also treat valid in-memory as already present
    for t in uniq:
        if t in already:
            continue
        mem = _signal_cache.get(t)
        if (
            not force
            and mem
            and isinstance(mem[1], dict)
            and _fund_payload_valid(mem[1].get("fund"))
        ):
            # Mirror memory → disk so all pages share it
            _persist_fund_disk(t, mem[1].get("fund"))
            already.append(t)

    already_set = set(already)
    todo = [t for t in uniq if t not in already_set]

    ok_new: list[str] = []
    failures: list[dict[str, str]] = []

    def _one(t: str) -> tuple[str, str | None]:
        try:
            fund, info = _fetch_fund_one(t)
            if not _fund_payload_valid(fund):
                return t, "fundamentals unavailable / unknown health"
            _persist_fund_disk(t, fund, info=info)
            # Refresh in-process signal cache fund slice without inventing news
            prev = _signal_cache.get(t)
            news = (prev[1].get("news") if prev and isinstance(prev[1], dict) else None)
            _signal_cache[t] = (time.time(), {"fund": fund, "news": news})
            return t, None
        except Exception as exc:
            return t, f"{type(exc).__name__}: {exc}"

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 4))) as pool:
            futures = {pool.submit(_one, t): t for t in todo}
            for fut in as_completed(futures):
                t, err = fut.result()
                if err:
                    failures.append({"ticker": t, "reason": err})
                else:
                    ok_new.append(t)

    # Reload disk for final coverage
    disk = _load_fund_disk()
    final_hits = sum(1 for t in uniq if _fund_entry_valid(disk.get(t)))
    return {
        "total": len(uniq),
        "already_cached": len(already_set),
        "fetched": len(todo),
        "ok_new": len(ok_new),
        "ok_new_tickers": ok_new,
        "failures": failures,
        "failed": len(failures),
        "final_cached": final_hits,
        "coverage": round(final_hits / len(uniq), 4) if uniq else 0.0,
        "cache_path": str(_FUND_DISK_PATH),
    }


def _load_news_disk() -> dict[str, Any]:
    global _news_disk_cache
    if _news_disk_cache is not None:
        return _news_disk_cache
    cache: dict[str, Any] = {}
    if _NEWS_DISK_PATH.exists():
        try:
            raw = json.loads(_NEWS_DISK_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cache = raw
        except Exception:
            cache = {}
    _news_disk_cache = cache
    return cache


def _news_payload_valid(news: Any) -> bool:
    return isinstance(news, dict) and ("tone" in news or "label" in news)


def _news_entry_valid(entry: Any, *, now: float | None = None, ttl: float = _NEWS_DISK_TTL) -> bool:
    if not isinstance(entry, dict) or not _news_payload_valid(entry.get("news")):
        return False
    ts = entry.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    age_limit = now if now is not None else time.time()
    return (age_limit - float(ts)) <= ttl


def _persist_news_disk(ticker: str, news: dict[str, Any] | None) -> None:
    """Write/update one ticker's news snapshot immediately (shared persistent cache)."""
    if not _news_payload_valid(news):
        return
    t = (ticker or "").strip().upper()
    if not t:
        return
    entry = {"ts": time.time(), "news": news}
    with _news_disk_lock:
        cache = _load_news_disk()
        cache[t] = entry
        try:
            _NEWS_DISK_PATH.parent.mkdir(parents=True, exist_ok=True)
            _NEWS_DISK_PATH.write_text(
                json.dumps(cache, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass


def get_news_cached_only(tickers: list[str]) -> dict[str, dict[str, Any] | None]:
    """
    Batch-read news from existing caches only (memory → disk).
    Never hits Yahoo. Missing / expired → None.
    """
    now = time.time()
    disk = _load_news_disk()
    out: dict[str, dict[str, Any] | None] = {}
    seen: set[str] = set()
    for raw in tickers:
        t = (raw or "").strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        news = None
        mem = _signal_cache.get(t)
        if mem and (now - mem[0]) <= _SIGNAL_TTL and isinstance(mem[1], dict):
            cand = mem[1].get("news")
            if _news_payload_valid(cand):
                news = cand
        if news is None:
            entry = disk.get(t)
            if _news_entry_valid(entry, now=now):
                news = entry.get("news")
        out[t] = news if _news_payload_valid(news) else None
    return out


def _cashflow_trend(tk: "yf.Ticker") -> dict[str, Any] | None:
    """YoY direction of Operating CF, CapEx and Free CF from the annual cash-flow
    statement. Used to read FCF changes in context (FCF ~= OCF - CapEx)."""
    try:
        cf = tk.cashflow  # columns are period dates, most recent first
    except Exception:
        cf = None
    if cf is None or getattr(cf, "empty", True) or cf.shape[1] < 2:
        return None

    def row(*names):
        for n in names:
            if n in cf.index:
                vals = cf.loc[n].tolist()
                if len(vals) >= 2:
                    return vals[0], vals[1]
        return None, None

    ocf_l, ocf_p = row("Operating Cash Flow", "Total Cash From Operating Activities",
                        "Cash Flow From Continuing Operating Activities")
    capex_l, capex_p = row("Capital Expenditure", "Capital Expenditures")
    fcf_l, fcf_p = row("Free Cash Flow")
    if fcf_l is None and ocf_l is not None and capex_l is not None:
        # CapEx is reported as a negative outflow, so FCF = OCF + CapEx.
        fcf_l = ocf_l + capex_l
        fcf_p = (ocf_p + capex_p) if (ocf_p is not None and capex_p is not None) else None

    def direction(latest, prior, *, spend=False):
        if latest is None or prior is None:
            return None
        a, b = (abs(latest), abs(prior)) if spend else (latest, prior)
        if b == 0:
            return None
        change = (a - b) / abs(b)
        if change > 0.03:
            return "up"
        if change < -0.03:
            return "down"
        return "flat"

    return {
        "ocf": {"latest": ocf_l, "dir": direction(ocf_l, ocf_p)},
        "capex": {"latest": capex_l, "dir": direction(capex_l, capex_p, spend=True)},
        "fcf": {"latest": fcf_l, "dir": direction(fcf_l, fcf_p)},
    }


_ARROW = {"up": "↑", "down": "↓", "flat": "→"}


def _fundamentals_from_info(info: dict[str, Any], cf: dict[str, Any] | None = None) -> dict[str, Any]:
    """Score fundamentals. REV/EPS/CUR/DEBT/CASH are red-flag checks. The cash-flow
    trio OCF/CAPEX/FCF is read together (FCF ~= OCF - CapEx): CapEx is context only
    (never a red flag), and a falling FCF only flags when OCF is also falling."""
    rev = info.get("revenueGrowth")
    eps = info.get("earningsGrowth")
    if eps is None:
        eps = info.get("earningsQuarterlyGrowth")
    ocf = info.get("operatingCashflow")
    cr = info.get("currentRatio")
    de = info.get("debtToEquity")  # yfinance reports as percent (e.g. 150 = 1.5x)
    cash = info.get("totalCash")
    debt = info.get("totalDebt")

    def pct(x):
        return "N/A" if x is None else f"{x * 100:.1f}%"

    def money(x):
        if x is None:
            return "N/A"
        a = abs(x)
        for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
            if a >= div:
                return f"${x / div:.2f}{unit}"
        return f"${x:.0f}"

    # Cash-flow trio directions (from the annual statement when available).
    ocf_dir = capex_dir = fcf_dir = None
    ocf_val = ocf
    if cf:
        ocf_dir = cf.get("ocf", {}).get("dir")
        capex_dir = cf.get("capex", {}).get("dir")
        fcf_dir = cf.get("fcf", {}).get("dir")
        if cf.get("ocf", {}).get("latest") is not None:
            ocf_val = cf["ocf"]["latest"]

    # OCF is a red flag if operating cash is negative, or it is deteriorating while
    # free cash flow also falls (scenario 2: risk, not capex-driven expansion).
    ocf_ok = None
    if ocf_val is not None:
        ocf_ok = ocf_val > 0 and not (ocf_dir == "down" and fcf_dir == "down")

    # (code, label, ok?, display) — flaggable checks only.
    checks = [
        ("REV", "Revenue Growth YoY", None if rev is None else rev >= 0, pct(rev)),
        ("EPS", "EPS Growth YoY", None if eps is None else eps >= 0, pct(eps)),
        ("OCF", "Operating Cash Flow", ocf_ok, money(ocf_val)),
        ("CUR", "Current Ratio", None if cr is None else cr >= 1, "N/A" if cr is None else f"{cr:.2f}"),
        ("DEBT", "Debt / Equity", None if de is None else de <= 150, "N/A" if de is None else f"{de / 100:.2f}x"),
        ("CASH", "Cash vs Debt", None if (cash is None or debt is None) else cash >= debt,
         f"{money(cash)} vs {money(debt)}"),
    ]

    known = [c for c in checks if c[2] is not None]
    ok = sum(1 for c in known if c[2])
    warnings = sum(1 for c in known if not c[2])
    total_known = len(known)

    if total_known == 0:
        health = "unknown"
    elif warnings == 0:
        health = "good"
    elif warnings <= 2:
        health = "ok"
    else:
        health = "bad"

    # Red-flag codes (failing checks), news-style e.g. "REV− DEBT−".
    flag_codes = [f"{code}−" for code, _label, okflag, _disp in checks if okflag is False]

    # Cash-flow context reads as directions, e.g. "OCF↑ CAPEX↑ FCF↓".
    flow_bits = []
    for name, d in (("OCF", ocf_dir), ("CAPEX", capex_dir), ("FCF", fcf_dir)):
        if d:
            flow_bits.append(f"{name}{_ARROW[d]}")
    flow = " ".join(flow_bits)

    code = " ".join(flag_codes) if flag_codes else ("OK" if total_known else "N/A")

    detail_lines = []
    for _code, label, okflag, disp in checks:
        mark = "•" if okflag is None else ("✓" if okflag else "⚠")
        detail_lines.append(f"{mark} {label}: {disp}")
    if flow:
        detail_lines.append("")
        detail_lines.append(f"现金流方向: {flow}")
        if fcf_dir == "down" and ocf_dir == "up" and capex_dir == "up":
            detail_lines.append("→ FCF下降主要因资本支出增加（扩张投资），非经营恶化。")
        elif fcf_dir == "down" and ocf_dir == "down":
            detail_lines.append("→ 经营现金流与自由现金流同时下降，风险偏高。")

    return {
        "health": health,
        "ok": ok,
        "warnings": warnings,
        "total_known": total_known,
        "code": code,
        "flow": flow,
        "detail": "\n".join(detail_lines),
    }


# News classification: (code, stars, keywords). Order = priority when matching.
_NEWS_CATEGORIES = [
    ("A", 5, ["earning", "eps", "revenue", "guidance", "profit warning", "forecast",
              "outlook", "results", "quarter", "beats", "misses", "preliminary"]),
    ("E", 5, ["lawsuit", "litigation", "investigation", "sec ", "probe", "subpoena",
              "fraud", "default", "bankruptcy", "dilution", "offering", "liquidity",
              "going concern", "regulatory", "fine", "settlement", "delist", "debt"]),
    ("B", 4, ["product", "launch", "contract", "customer", "order", "partnership",
              "deal", "expansion", "collaboration", "agreement", "supply", "unveil",
              "approval", "approved"]),
    ("D", 4, ["ceo", "cfo", "executive", "management", "merger", "acqui", "takeover",
              "restructur", "layoff", "resign", "appoint", "buyout", "spin-off", "stake"]),
    ("C", 3, ["analyst", "rating", "upgrade", "downgrade", "price target", "initiate",
              "coverage", "reiterate", "overweight", "underweight"]),
]

_NEWS_NEG = ["miss", "cut", "lower", "decline", "fall", "fell", "drop", "weak", "warning",
             "warn", "lawsuit", "investigation", "probe", "downgrade", "layoff",
             "bankruptcy", "loss", "plunge", "slump", "recall", "halt", "resign",
             "fraud", "default", "dilution", "short seller", "slash", "disappoint",
             "below", "sell-off", "delist"]
_NEWS_POS = ["beat", "raise", "upgrade", "surge", "jump", "rally", "record", "win",
             "award", "approval", "approved", "strong", "expand", "gain", "profit",
             "outperform", "soar", "tops", "above", "boost", "milestone", "unveil"]


def _classify_headline(title: str) -> tuple[str, int]:
    low = title.lower()
    for code, stars, keys in _NEWS_CATEGORIES:
        if any(k in low for k in keys):
            return code, stars
    return "O", 0


def _headline_sentiment(title: str) -> str:
    low = title.lower()
    neg = sum(1 for k in _NEWS_NEG if k in low)
    pos = sum(1 for k in _NEWS_POS if k in low)
    if neg > pos:
        return "NEG"
    if pos > neg:
        return "POS"
    return "NEUTRAL"


def _news_title(item: dict[str, Any]) -> str | None:
    if item.get("title"):
        return item["title"]
    content = item.get("content")
    if isinstance(content, dict):
        return content.get("title")
    return None


def _news_ts(item: dict[str, Any]) -> float | None:
    t = item.get("providerPublishTime")
    if isinstance(t, (int, float)):
        return float(t)
    content = item.get("content")
    if isinstance(content, dict):
        for key in ("pubDate", "displayTime"):
            v = content.get(key)
            if v:
                try:
                    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
                except Exception:
                    continue
    return None


def _news_from_ticker(tk: yf.Ticker, days: int = NEWS_LOOKBACK_DAYS) -> dict[str, Any]:
    try:
        items = tk.news or []
    except Exception:
        items = []

    cutoff = time.time() - days * 86400
    parsed = []
    for it in items:
        title = _news_title(it)
        if not title:
            continue
        ts = _news_ts(it)
        if ts is not None and ts < cutoff:
            continue
        code, stars = _classify_headline(title)
        sent = _headline_sentiment(title)
        parsed.append({"title": title, "ts": ts or 0, "code": code, "stars": stars, "sent": sent})

    if not parsed:
        return {"has_news": False, "label": "NO NEWS", "tone": "none", "sort": 0,
                "neg_codes": [], "detail": "近30天无重要新闻"}

    # Rank by impact (stars) then recency.
    parsed.sort(key=lambda x: (x["stars"], x["ts"]), reverse=True)

    sign = {"POS": "+", "NEG": "−", "NEUTRAL": ""}
    tags, seen = [], set()
    for p in parsed:
        if p["code"] == "O":
            continue
        tag = f"{p['code']}{sign[p['sent']]}"
        if tag not in seen:
            seen.add(tag)
            tags.append(tag)
        if len(tags) >= 3:
            break

    major = [p for p in parsed if p["stars"] >= 4]
    if any(p["sent"] == "NEG" for p in major):
        tone = "neg"
        sort = 3
    elif any(p["sent"] == "POS" for p in major):
        tone = "pos"
        sort = 2
    else:
        tone = "neutral"
        sort = 1

    if not tags:
        # Only "Other" news found — treat as neutral background noise.
        label = "NEUTRAL"
        tone = "neutral"
        sort = 1
    else:
        label = " / ".join(tags)

    detail_lines = []
    for p in parsed[:6]:
        d = datetime.fromtimestamp(p["ts"]).strftime("%m/%d") if p["ts"] else "—"
        detail_lines.append(f"[{p['code']}{sign[p['sent']]}] {d} {p['title']}")

    neg_codes = sorted({p["code"] for p in parsed if p["sent"] == "NEG" and p["code"] != "O"})

    return {
        "has_news": True,
        "label": label,
        "tone": tone,
        "sort": sort,
        "count": len(parsed),
        "neg_codes": neg_codes,
        "detail": "\n".join(detail_lines),
    }


def _fetch_signals_one(ticker: str) -> dict[str, Any]:
    tk = yf.Ticker(ticker)
    disk_entry = _load_fund_disk().get(ticker)
    # Shared fund cache: reuse valid entry (skip Yahoo fund/info).
    if _fund_entry_valid(disk_entry):
        fund = disk_entry.get("fund")
    else:
        try:
            info = tk.info or {}
        except Exception:
            info = {}
        if not isinstance(info, dict):
            info = {}
        cf = _cashflow_trend(tk)
        fund = _fundamentals_from_info(info, cf)
        if _fund_payload_valid(fund):
            _persist_fund_disk(ticker, fund, info=info)

    news_entry = _load_news_disk().get(ticker)
    if _news_entry_valid(news_entry):
        news = news_entry.get("news")
    else:
        news = _news_from_ticker(tk)
        _persist_news_disk(ticker, news)

    return {"fund": fund, "news": news}


def _fetch_news_one(ticker: str) -> dict[str, Any]:
    """News-only Yahoo read using existing 30-day classifier (no fund/DCF/CLV)."""
    tk = yf.Ticker(ticker)
    return _news_from_ticker(tk)


def ensure_news_cache(
    tickers: list[str],
    *,
    max_workers: int = 3,
    force: bool = False,
) -> dict[str, Any]:
    """
    Fill shared persistent news_cache for tickers (existing news logic only).
    Reuses valid non-expired disk/memory entries; fetches missing/expired.
    """
    uniq: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        t = (raw or "").strip().upper()
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)

    now = time.time()
    disk = _load_news_disk()
    already: list[str] = []
    for t in uniq:
        if force:
            continue
        if _news_entry_valid(disk.get(t), now=now):
            already.append(t)
            continue
        mem = _signal_cache.get(t)
        if (
            mem
            and (now - mem[0]) <= _SIGNAL_TTL
            and isinstance(mem[1], dict)
            and _news_payload_valid(mem[1].get("news"))
        ):
            _persist_news_disk(t, mem[1].get("news"))
            already.append(t)

    already_set = set(already)
    todo = [t for t in uniq if t not in already_set]
    ok_new: list[str] = []
    failures: list[dict[str, str]] = []
    results: dict[str, dict[str, Any] | None] = {}

    # Seed results with already-cached news for classification report
    cached_map = get_news_cached_only(already)
    for t in already:
        results[t] = cached_map.get(t)

    def _one(t: str) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            news = _fetch_news_one(t)
            if not _news_payload_valid(news):
                return t, None, "news payload invalid"
            _persist_news_disk(t, news)
            prev = _signal_cache.get(t)
            fund = (prev[1].get("fund") if prev and isinstance(prev[1], dict) else None)
            _signal_cache[t] = (time.time(), {"fund": fund, "news": news})
            return t, news, None
        except Exception as exc:
            return t, None, f"{type(exc).__name__}: {exc}"

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 4))) as pool:
            futures = {pool.submit(_one, t): t for t in todo}
            for fut in as_completed(futures):
                t, news, err = fut.result()
                if err:
                    failures.append({"ticker": t, "reason": err})
                    results[t] = None
                else:
                    ok_new.append(t)
                    results[t] = news

    def _bucket(news: dict[str, Any] | None) -> str:
        if not news:
            return "failed"
        tone = news.get("tone")
        if tone == "pos":
            return "pos"
        if tone == "neg":
            return "neg"
        return "neutral"  # neutral / none / NO NEWS

    counts = {"pos": 0, "neutral": 0, "neg": 0, "failed": 0}
    for t in uniq:
        if t in {f["ticker"] for f in failures}:
            counts["failed"] += 1
        else:
            counts[_bucket(results.get(t))] += 1

    disk = _load_news_disk()
    final_hits = sum(1 for t in uniq if _news_entry_valid(disk.get(t)))
    return {
        "total": len(uniq),
        "already_cached": len(already_set),
        "fetched": len(todo),
        "ok_new": len(ok_new),
        "failures": failures,
        "failed": len(failures),
        "final_cached": final_hits,
        "counts": counts,
        "results": results,
        "cache_path": str(_NEWS_DISK_PATH),
    }


def get_signals(tickers: list[str], *, max_workers: int = 8) -> dict[str, dict[str, Any]]:
    """Return {ticker: {fund, news}} using a 15-min in-process cache.

    Fund/news prefer shared persistent caches when still valid.
    """
    now = time.time()
    uniq = []
    seen = set()
    for t in tickers:
        t = (t or "").strip().upper()
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)

    todo = [t for t in uniq if t not in _signal_cache or now - _signal_cache[t][0] > _SIGNAL_TTL]
    if todo:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_signals_one, t): t for t in todo}
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    _signal_cache[t] = (now, fut.result())
                except Exception:
                    _signal_cache[t] = (now, {"fund": None, "news": None})

    return {t: _signal_cache[t][1] for t in uniq if t in _signal_cache}


# ---------------------------------------------------------------------------
# AI Score V1
#
# Opportunity (base, 0–100) = pullback 30 + trend 20 + rebound 15 + volume 10
#                             + financial 15 + news 10.
# Risk penalty (0–55) subtracted afterwards, so every point is explainable:
#   severe financials 0–15, major negative news 0–15, volume crash 0–5,
#   volatility/liquidity 0–5, upcoming earnings 0–15.
# Final AI = clamp(base − risk, 0, 100).  Goal: transparent, not "perfect".
# ---------------------------------------------------------------------------

def _score_pullback(dist: float | None) -> int:
    if dist is None or dist > -3:
        return 0
    if dist > -5:
        return 5
    if dist > -10:
        return 10
    if dist > -15:
        return 15
    if dist > -20:
        return 20
    if dist > -30:
        return 25
    return 30


def _score_trend(trend: str | None) -> int:
    return {"UP": 20, "MIXED": 10, "DOWN": 0}.get(trend, 0)


def _score_rebound(reb: float | None, change: float | None) -> int:
    if reb is None or reb <= 0:
        base = 0
    elif reb <= 3:
        base = 4
    elif reb <= 8:
        base = 8
    elif reb <= 15:
        base = 12
    else:
        base = 15
    if change is not None:
        if change <= -2:
            base = max(0, base - 3)  # still selling off → weaker confirmation
        elif change > 0:
            base = min(15, base + 1)  # green today → confirmation strengthens
    return base


def _score_volume(rvol: float | None, change: float | None) -> int:
    if rvol is None:
        return 5  # unknown → neutral
    up = change is not None and change > 0
    down = change is not None and change < 0
    if up:
        if rvol >= 2:
            return 10
        if rvol >= 1.2:
            return 8
        if rvol >= 0.7:
            return 5
        return 3
    if down:
        # High volume on a down day should NOT earn a high score.
        if rvol >= 2:
            return 2
        if rvol >= 1.2:
            return 4
        if rvol >= 0.7:
            return 5
        return 4
    if rvol >= 1.2:
        return 6
    if rvol >= 0.7:
        return 5
    return 4


def _score_financial(fund: dict[str, Any] | None) -> int:
    if not fund or fund.get("health") == "unknown":
        return 7  # neutral when unknown, so missing data doesn't dominate
    health = fund.get("health")
    w = fund.get("warnings", 0)
    if health == "good":
        return 15
    if health == "ok":
        return 12 if w <= 1 else 8
    # bad
    if w <= 3:
        return 5
    if w == 4:
        return 3
    return 0


def _score_news(news: dict[str, Any] | None) -> int:
    if not news:
        return 5
    tone = news.get("tone")
    if tone == "pos":
        return 10
    if tone == "neg":
        return 0
    return 5  # neutral / none


def _pen_financial(fund: dict[str, Any] | None) -> int:
    if not fund:
        return 0
    code = fund.get("code", "") or ""
    severe = sum(1 for c in ("OCF−", "CASH−", "DEBT−") if c in code)
    return min(15, severe * 5)


def _pen_news(news: dict[str, Any] | None) -> int:
    if not news or news.get("tone") != "neg":
        return 0
    neg = set(news.get("neg_codes", []))
    if neg & {"A", "E"}:
        return 12  # earnings/guidance or financial/legal — most serious
    if neg & {"B", "D"}:
        return 8
    if "C" in neg:
        return 4  # analyst downgrade — least severe
    return 8


def _pen_vol_crash(change: float | None, rvol: float | None) -> int:
    if change is None or rvol is None:
        return 0
    if change <= -5 and rvol >= 2:
        return 5
    if change <= -3 and rvol >= 3:
        return 5
    return 0


def _pen_liquidity(avg_move: float | None, avg_vol: float | None) -> int:
    p = 0
    if avg_move is not None and avg_move >= 5:
        p += 3  # very jumpy
    if avg_vol is not None and avg_vol < 300_000:
        p += 2  # thin / hard to trade
    return min(5, p)


def _days_to_earnings(earnings_date: str | None) -> int | None:
    if not earnings_date:
        return None
    try:
        d = pd.Timestamp(earnings_date).date()
    except Exception:
        return None
    return (d - datetime.now().date()).days


def _pen_earnings(days: int | None) -> int:
    if days is None or days < 0 or days > 14:
        return 0
    if days >= 8:
        return 2
    if days >= 4:
        return 5
    if days >= 2:
        return 10
    return 15  # reporting today or tomorrow


def compute_ai_score(row: dict[str, Any]) -> dict[str, Any]:
    """AI Score V1 for a watchlist row (needs dist_pct/trend/rebound_pct/change_pct/
    rvol/avg_vol_20d/avg_move_pct/earnings_date plus enriched fund + news)."""
    fund = row.get("fund")
    news = row.get("news")

    parts = {
        "pullback": (_score_pullback(row.get("dist_pct")), 30),
        "trend": (_score_trend(row.get("trend")), 20),
        "rebound": (_score_rebound(row.get("rebound_pct"), row.get("change_pct")), 15),
        "volume": (_score_volume(row.get("rvol"), row.get("change_pct")), 10),
        "financial": (_score_financial(fund), 15),
        "news": (_score_news(news), 10),
    }
    base = sum(v for v, _m in parts.values())

    pen_fin = _pen_financial(fund)
    pen_news = _pen_news(news)
    pen_crash = _pen_vol_crash(row.get("change_pct"), row.get("rvol"))
    pen_liq = _pen_liquidity(row.get("avg_move_pct"), row.get("avg_vol_20d"))
    pen_fin_news = pen_fin + pen_news + pen_crash + pen_liq

    edays = _days_to_earnings(row.get("earnings_date"))
    pen_earn = _pen_earnings(edays)

    risk = pen_fin_news + pen_earn
    final = max(0, min(100, base - risk))

    labels = {
        "pullback": "回调", "trend": "趋势", "rebound": "反弹",
        "volume": "成交量", "financial": "财务", "news": "新闻",
    }
    detail_lines = [f"{labels[k]} {v}/{m}" for k, (v, m) in parts.items()]
    detail_lines.append(f"基础分 {base}/100")
    if pen_fin_news:
        detail_lines.append(f"财务/新闻/波动风险 −{pen_fin_news}")
    if pen_earn:
        detail_lines.append(f"财报 {edays}D −{pen_earn}")
    detail_lines.append(f"最终 {final}")

    return {
        "final": final,
        "opp": base,
        "risk": risk,
        "parts": parts,
        "pen_fin_news": pen_fin_news,
        "pen_earnings": pen_earn,
        "earnings_days": edays,
        "detail": "\n".join(detail_lines),
    }
