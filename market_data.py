"""Yahoo Finance market data → SMA / distance / rebound metrics."""

from __future__ import annotations

import json
import logging
import random
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

log = logging.getLogger("leibot.market_data")


# Long-term trend rule (fixed, independent of the configurable dist SMA):
#   UP    : SMA63 > SMA252 and SMA252 sloping up
#   DOWN  : SMA63 < SMA252 and SMA252 sloping down
#   MIXED : anything else (relationship and slope disagree)
TREND_FAST = 63
TREND_SLOW = 252
TREND_SLOPE_LOOKBACK = 21

# Yahoo sometimes leaves Close on a mixed pre/post-split scale (e.g. MNST 2026-08-11).
# Flag when SMA window still looks corrupted after repair.
DATA_ERROR_NOTE_PREFIX = "DATA ERROR"
# Max High/Low ratio inside the SMA window before we treat the series as bad scale.
_SMA_WINDOW_MAX_SPAN = 1.65


def _sma(series: pd.Series, period: int) -> float | None:
    clean = series.dropna()
    if len(clean) < period:
        return None
    return float(clean.iloc[-period:].mean())


def apply_yahoo_split_factors(
    closes: pd.Series, splits: pd.Series | None
) -> pd.Series:
    """
    Re-base raw Close onto the latest share scale using Yahoo Stock Splits.
    Walk newest → oldest; on a split day, subsequent (older) bars are divided
    by the cumulative split factor.
    """
    out = closes.astype(float).copy()
    if splits is None or len(splits) == 0:
        return out
    sp = splits.reindex(out.index).fillna(0.0).astype(float)
    factor = 1.0
    vals = out.to_numpy(dtype=float, copy=True)
    for i in range(len(vals) - 1, -1, -1):
        vals[i] = vals[i] / factor
        s = float(sp.iloc[i] or 0.0)
        if s > 0.0 and abs(s - 1.0) > 1e-9:
            factor *= s
    return pd.Series(vals, index=out.index, name=out.name)


def repair_close_scale_jumps(closes: pd.Series) -> tuple[pd.Series, int]:
    """
    Fix residual ~2×/3×/4× day-to-day jumps left when Yahoo partially adjusts
    some bars but not others. Walk newest → oldest so the current price scale wins.
    """
    if closes is None or len(closes) == 0:
        return closes, 0
    vals = closes.astype(float).to_numpy(copy=True)
    fixes = 0
    for i in range(len(vals) - 2, -1, -1):
        newer = vals[i + 1]
        cur = vals[i]
        if newer <= 0 or cur <= 0 or not (newer == newer and cur == cur):
            continue
        r = cur / newer
        if 1.75 <= r <= 2.4:
            vals[i] = cur / 2.0
            fixes += 1
        elif 1.75 <= (1.0 / r) <= 2.4:
            vals[i] = cur * 2.0
            fixes += 1
        elif 2.7 <= r <= 3.4:
            vals[i] = cur / 3.0
            fixes += 1
        elif 2.7 <= (1.0 / r) <= 3.4:
            vals[i] = cur * 3.0
            fixes += 1
        elif 3.6 <= r <= 4.5:
            vals[i] = cur / 4.0
            fixes += 1
        elif 3.6 <= (1.0 / r) <= 4.5:
            vals[i] = cur * 4.0
            fixes += 1
    return pd.Series(vals, index=closes.index, name=closes.name), fixes


def assess_sma_window_quality(
    closes: pd.Series, period: int
) -> dict[str, Any]:
    """
    SMA of the last `period` closes must lie inside that window's High/Low.
    Flag DATA ERROR only when the window looks like mixed pre/post-split scales
    (discontinuous jump), not merely a wide but continuous volatile range.
    """
    clean = closes.dropna()
    if len(clean) < period:
        return {
            "ok": False,
            "reason": "insufficient_bars",
            "sma": None,
            "low": None,
            "high": None,
            "span_ratio": None,
        }
    window = clean.iloc[-period:]
    lo = float(window.min())
    hi = float(window.max())
    sma = float(window.mean())
    span = (hi / lo) if lo > 0 else float("inf")
    inside = (lo - 1e-6) <= sma <= (hi + 1e-6)
    # Detect overnight scale jumps (~2x / ~4x), not gradual volatility.
    has_scale_jump = False
    vals = [float(x) for x in window.tolist() if float(x) > 0]
    for i in range(1, len(vals)):
        r = vals[i] / vals[i - 1]
        if r >= 1.8 or r <= (1.0 / 1.8):
            has_scale_jump = True
            break
    if not inside:
        reason = "sma_outside_high_low"
        ok = False
    elif span > _SMA_WINDOW_MAX_SPAN and has_scale_jump:
        reason = "window_span_too_wide"
        ok = False
    else:
        reason = "ok"
        ok = True
    return {
        "ok": ok,
        "reason": reason,
        "sma": sma,
        "low": lo,
        "high": hi,
        "span_ratio": round(span, 4) if span != float("inf") else None,
    }


def is_data_quality_error(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    note = str(row.get("ai_note") or "")
    if note.upper().startswith(DATA_ERROR_NOTE_PREFIX):
        return True
    return row_has_corrupt_price_scale(row)


def row_has_corrupt_price_scale(row: dict[str, Any] | None) -> bool:
    """
    Detect stale/mixed-scale dashboard rows (classic MNST-style):
    63D High is ~2× current price, Position claims near-lows, SMA still elevated.
    Live Yahoo fetch+repair is usually fine; cached rows can lag.
    """
    if not row:
        return False
    try:
        price = float(row.get("price") or 0)
    except (TypeError, ValueError):
        return False
    if price <= 0:
        return False
    hi = row.get("range_63d_high")
    lo = row.get("range_63d_low")
    pos = row.get("range_63d_pos")
    sma = row.get("sma")
    try:
        if hi is None or lo is None:
            return False
        hi_f = float(hi)
        lo_f = float(lo)
        if lo_f <= 0 or hi_f / lo_f < 1.75:
            return False
        pos_f = float(pos) if pos is not None else None
        # High on wrong share scale while price sits near the low of that span.
        if pos_f is not None and pos_f <= 25.0 and hi_f / price >= 1.75:
            return True
        if (
            pos_f is not None
            and pos_f <= 20.0
            and sma is not None
            and float(sma) / price >= 1.25
        ):
            return True
    except (TypeError, ValueError):
        return False
    return False


def _yahoo_transient_error(exc: BaseException | None, hist_empty: bool = False) -> bool:
    """True when we should retry (rate limit / crumb / empty under pressure)."""
    if hist_empty:
        return True
    if exc is None:
        return False
    msg = f"{type(exc).__name__} {exc}".lower()
    needles = (
        "ratelimit",
        "rate limit",
        "too many requests",
        "unauthorized",
        "invalid crumb",
        "crumb",
        "401",
        "429",
        "timeout",
        "timed out",
        "temporarily",
        "connection",
    )
    return any(n in msg for n in needles)


def load_yahoo_daily_closes(
    ticker: str,
    *,
    period: str = "2y",
    retries: int = 4,
) -> tuple[pd.Series | None, pd.DataFrame | None, dict[str, Any]]:
    """
    Yahoo daily Close on the *current* share scale.

    Uses auto_adjust=False + manual Stock Splits, then jump repair.
    (auto_adjust=True alone is not reliable around some recent splits, e.g. MNST.)
    Retries on Yahoo rate-limit / crumb failures.
    """
    meta: dict[str, Any] = {"jump_fixes": 0, "quality": None, "attempts": 0}
    last_exc: BaseException | None = None
    for attempt in range(max(1, int(retries))):
        meta["attempts"] = attempt + 1
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period=period, auto_adjust=False, actions=True)
        except Exception as exc:
            last_exc = exc
            if attempt + 1 < retries and _yahoo_transient_error(exc):
                time.sleep(1.2 * (2**attempt) + random.uniform(0.0, 0.6))
                continue
            return None, None, meta
        if hist is None or hist.empty or "Close" not in hist.columns:
            last_exc = None
            if attempt + 1 < retries and _yahoo_transient_error(None, hist_empty=True):
                time.sleep(1.0 * (2**attempt) + random.uniform(0.0, 0.5))
                continue
            return None, None, meta
        closes = hist["Close"].dropna().astype(float)
        splits = hist["Stock Splits"] if "Stock Splits" in hist.columns else None
        closes = apply_yahoo_split_factors(closes, splits)
        closes, fixes = repair_close_scale_jumps(closes)
        closes = closes.dropna()
        meta["jump_fixes"] = fixes
        return closes, hist, meta
    if last_exc is not None:
        log.debug("Yahoo history failed for %s after retries: %s", ticker, last_exc)
    return None, None, meta


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


def compute_target_proxy_mos(
    price: float | None,
    target_1y: float | None,
) -> dict[str, Any]:
    """
    Temporary Target-Based Valuation (separate from DCF Est.Value / MOS).

    Bear T = 1Y Target × 0.60
    Base T = 1Y Target × 0.80  (main reference)
    Bull T = 1Y Target × 1.00
    MOS T = (Base T − Price) / Base T × 100

    valuation_method = "analyst_target_proxy"
    Missing/invalid price or target → MOS T is None (UI shows —).
    """
    out: dict[str, Any] = {
        "valuation_method_target": "analyst_target_proxy",
        "bear_t": None,
        "base_t": None,
        "bull_t": None,
        "mos_t": None,
    }
    if price is None or target_1y is None:
        return out
    try:
        px = float(price)
        tgt = float(target_1y)
    except (TypeError, ValueError):
        return out
    if px != px or tgt != tgt or px <= 0 or tgt <= 0:
        return out
    bear_t = round(tgt * 0.60, 2)
    base_t = round(tgt * 0.80, 2)
    bull_t = round(tgt * 1.00, 2)
    if base_t <= 0:
        return out
    mos_t = round((base_t - px) / base_t * 100, 2)
    out.update(
        {
            "bear_t": bear_t,
            "base_t": base_t,
            "bull_t": bull_t,
            "mos_t": mos_t,
        }
    )
    return out


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


def _period_return_pct(series: pd.Series, lookback: int) -> float | None:
    """Simple close-to-close return over `lookback` trading days (%)."""
    if series is None or len(series) < lookback + 1:
        return None
    try:
        a = float(series.iloc[-(lookback + 1)])
        b = float(series.iloc[-1])
        if a == 0 or a != a or b != b:
            return None
        return round((b / a - 1.0) * 100.0, 2)
    except (TypeError, ValueError, IndexError):
        return None


def fetch_metrics_for_ticker(
    ticker: str,
    *,
    sma_period: int,
    rebound_lookback: int,
    meta: dict[str, Any] | None = None,
    asset_type: str | None = None,
) -> dict[str, Any] | None:
    meta = meta or {}
    atype = (asset_type or meta.get("asset_type") or "STOCK").strip().upper()
    if atype not in ("STOCK", "ETF"):
        atype = "STOCK"
    try:
        closes, hist, load_meta = load_yahoo_daily_closes(ticker, period="2y")
        if closes is None or hist is None or closes.empty:
            return None
        price = float(closes.iloc[-1])
        quality = assess_sma_window_quality(closes, sma_period)
        load_meta["quality"] = quality
        ai_note = None
        if not quality["ok"]:
            # Guard: do not feed corrupted SMA / Dist into Oversold / AI Score / trading.
            ai_note = (
                f"{DATA_ERROR_NOTE_PREFIX}: SMA{sma_period} vs recent High/Low "
                f"({quality.get('reason')}; "
                f"low={quality.get('low')}, high={quality.get('high')}, "
                f"sma={None if quality.get('sma') is None else round(float(quality['sma']), 2)}, "
                f"span={quality.get('span_ratio')})"
            )
            sma = None
            dist_pct = None
        else:
            sma = _sma(closes, sma_period)
            # Canonical Dist: (price − SMA) / SMA × 100  (= price/sma − 1)
            from market_data_validator import dist_sma_pct

            dist_pct = dist_sma_pct(price, sma) if sma else None
        rebound = _rebound_pct(closes, rebound_lookback)
        change_pct = _change_pct(closes)
        avg_move_pct = _avg_daily_move(closes)
        range_low, range_high, range_pos = _range_63d(closes)
        trend = _trend(closes)
        avg_vol_20d, rvol = (
            _volume_stats(hist["Volume"]) if "Volume" in hist.columns else (None, None)
        )
        sma63 = _sma(closes, 63)
        from market_data_validator import dist_sma_pct as _dist63

        dist_sma63_pct = _dist63(price, sma63) if sma63 else None
        ret_20d = _period_return_pct(closes, 20)
        ret_63d = _period_return_pct(closes, 63)
        ret_126d = _period_return_pct(closes, 126)
        ret_252d = _period_return_pct(closes, 252)
        avg_dollar_vol = None
        if avg_vol_20d is not None and price > 0:
            try:
                avg_dollar_vol = round(float(avg_vol_20d) * float(price), 2)
            except (TypeError, ValueError):
                avg_dollar_vol = None

        # Company-only fields — skip for ETFs (no fake zeros).
        market_cap = None
        earnings_date = None
        target_1y = None
        if atype != "ETF":
            t = yf.Ticker(ticker)
            market_cap = _market_cap(t)
            earnings_date = _next_earnings_date(t)
            target_1y = _target_1y(t)

        row = {
            "ticker": ticker,
            "name": meta.get("name") or "",
            "industry": meta.get("industry") or meta.get("etf_subcategory") or "",
            "sector": meta.get("sector") or meta.get("etf_category") or "",
            "price": round(price, 2),
            "change_pct": change_pct,
            "avg_move_pct": avg_move_pct,
            "range_63d_low": range_low,
            "range_63d_high": range_high,
            "range_63d_pos": range_pos,
            "sma": None if sma is None else round(sma, 2),
            # SMA25_D alias — same daily trading-day SMA stored in `sma`
            "sma25_d": None if sma is None else round(sma, 2),
            "dist_pct": dist_pct,
            "rebound_pct": rebound,
            "trend": trend,
            "market_cap": market_cap,
            "avg_vol_20d": avg_vol_20d,
            "rvol": rvol,
            "sma_period": sma_period,
            "earnings_date": earnings_date,
            "target_1y": target_1y,
            "ai_note": ai_note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "asset_type": atype,
            "sma63": None if sma63 is None else round(sma63, 2),
            "dist_sma63_pct": dist_sma63_pct,
            "ret_20d": ret_20d,
            "ret_63d": ret_63d,
            "ret_126d": ret_126d,
            "ret_252d": ret_252d,
            "avg_dollar_vol": avg_dollar_vol,
        }
        try:
            from market_data_validator import attach_data_quality_to_row

            attach_data_quality_to_row(row, closes=closes)
            if row.get("data_block") and not row.get("ai_note"):
                reasons = row.get("data_quality_reason") or ["validation_failed"]
                row["ai_note"] = (
                    f"{DATA_ERROR_NOTE_PREFIX}: " + "; ".join(str(x) for x in reasons[:4])
                )
                row["sma"] = None
                row["sma25_d"] = None
                row["dist_pct"] = None
        except Exception:
            pass
        return row
    except Exception:
        return None


def refresh_etf_dashboard_cache(*, max_workers: int = 4) -> dict[str, Any]:
    """
    Refresh prices/derived metrics for LeiBot ETF Universe via the shared pipeline.
    Does not touch equity universe membership or AI BUY pools.
    """
    from etf_universe import ensure_etf_universe
    from db import get_setting, list_etf_universe, save_dashboard_rows

    ensure_etf_universe()
    universe = list_etf_universe()
    sma_period = int(get_setting("sma_period", 25))
    rebound_lookback = int(get_setting("rebound_lookback", sma_period))
    if rebound_lookback < 5:
        rebound_lookback = sma_period

    rows: list[dict[str, Any]] = []
    errors = 0
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                fetch_metrics_for_ticker,
                row["ticker"],
                sma_period=sma_period,
                rebound_lookback=rebound_lookback,
                meta=row,
                asset_type="ETF",
            ): row["ticker"]
            for row in universe
        }
        for fut in as_completed(futures):
            tkr = futures[fut]
            result = fut.result()
            if result is None:
                errors += 1
                failed.append(tkr)
            else:
                rows.append(result)

    save_dashboard_rows(rows)
    us = sum(1 for r in universe if (r.get("market") or "US").upper() == "US")
    ca = sum(1 for r in universe if (r.get("market") or "").upper() == "CANADA")
    return {
        "ok": len(rows),
        "errors": errors,
        "failed": failed,
        "sma_period": sma_period,
        "universe": len(universe),
        "us_count": us,
        "canada_count": ca,
        "asset_type": "ETF",
    }

def refresh_dashboard_cache(
    *,
    max_workers: int = 2,
    limit: int | None = None,
    group: str | None = None,
    batch_size: int = 50,
    batch_pause_sec: float = 2.5,
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

    rows: list[dict[str, Any]] = []
    errors = 0
    workers = max(1, min(int(max_workers or 2), 4))
    bsz = max(10, int(batch_size or 50))
    pause = max(0.0, float(batch_pause_sec or 0.0))

    for i in range(0, len(universe), bsz):
        chunk = universe[i : i + bsz]
        chunk_rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    fetch_metrics_for_ticker,
                    row["ticker"],
                    sma_period=sma_period,
                    rebound_lookback=rebound_lookback,
                    meta=row,
                ): row["ticker"]
                for row in chunk
            }
            for fut in as_completed(futures):
                result = fut.result()
                if result is None:
                    errors += 1
                else:
                    chunk_rows.append(result)
        if chunk_rows:
            save_dashboard_rows(chunk_rows)
            rows.extend(chunk_rows)
        if i + bsz < len(universe) and pause > 0:
            time.sleep(pause)

    return {
        "ok": len(rows),
        "errors": errors,
        "sma_period": sma_period,
        "rebound_lookback": rebound_lookback,
        "universe": len(universe),
        "group": group,
        "max_workers": workers,
        "batch_size": bsz,
    }


def refresh_stale_dashboard_tickers(
    *,
    older_than_hours: float = 20.0,
    max_workers: int = 2,
    batch_size: int = 40,
    batch_pause_sec: float = 3.0,
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    """Re-fetch tickers whose dashboard_cache row is missing or older than N hours."""
    from db import get_conn, list_universe as _lu

    universe = _lu() or []
    meta_by = {r["ticker"]: r for r in universe if r.get("ticker")}
    if tickers:
        want = []
        seen: set[str] = set()
        for t in tickers:
            u = (t or "").strip().upper()
            if u and u not in seen:
                seen.add(u)
                want.append(u)
    else:
        with get_conn() as conn:
            cached = {
                str(r["ticker"]).upper(): r["updated_at"]
                for r in conn.execute(
                    "SELECT ticker, updated_at FROM dashboard_cache"
                ).fetchall()
            }
        now = datetime.now(timezone.utc)
        want = []
        for r in universe:
            t = str(r.get("ticker") or "").upper()
            if not t:
                continue
            upd = cached.get(t)
            if not upd:
                want.append(t)
                continue
            try:
                dt = datetime.fromisoformat(str(upd).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_h = (now - dt).total_seconds() / 3600.0
                if age_h > float(older_than_hours):
                    want.append(t)
            except Exception:
                want.append(t)

    if not want:
        return {"ok": 0, "errors": 0, "requested": 0, "tickers": []}

    sma_period = int(get_setting("sma_period", 25))
    rebound_lookback = int(get_setting("rebound_lookback", sma_period))
    if rebound_lookback < 5:
        rebound_lookback = sma_period

    workers = max(1, min(int(max_workers or 2), 3))
    bsz = max(10, int(batch_size or 40))
    pause = max(0.0, float(batch_pause_sec or 0.0))
    rows_ok = 0
    errors = 0
    for i in range(0, len(want), bsz):
        chunk = want[i : i + bsz]
        chunk_rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    fetch_metrics_for_ticker,
                    t,
                    sma_period=sma_period,
                    rebound_lookback=rebound_lookback,
                    meta=meta_by.get(t) or {"ticker": t},
                ): t
                for t in chunk
            }
            for fut in as_completed(futures):
                result = fut.result()
                if result is None:
                    errors += 1
                else:
                    chunk_rows.append(result)
        if chunk_rows:
            save_dashboard_rows(chunk_rows)
            rows_ok += len(chunk_rows)
        if i + bsz < len(want) and pause > 0:
            time.sleep(pause)
    return {
        "ok": rows_ok,
        "errors": errors,
        "requested": len(want),
        "tickers": want[:20],
        "older_than_hours": older_than_hours,
    }


def refresh_dashboard_for_tickers(
    tickers: list[str],
    *,
    max_workers: int = 8,
) -> dict[str, Any]:
    """Force-refresh dashboard_cache for specific tickers (split/scale repair)."""
    from db import list_universe as _lu

    want = []
    seen: set[str] = set()
    for t in tickers:
        u = (t or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            want.append(u)
    if not want:
        return {"ok": 0, "errors": 0, "tickers": []}

    meta_by = {r["ticker"]: r for r in (_lu() or []) if r.get("ticker")}
    sma_period = int(get_setting("sma_period", 25))
    rebound_lookback = int(get_setting("rebound_lookback", sma_period))
    if rebound_lookback < 5:
        rebound_lookback = sma_period

    rows: list[dict[str, Any]] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                fetch_metrics_for_ticker,
                t,
                sma_period=sma_period,
                rebound_lookback=rebound_lookback,
                meta=meta_by.get(t) or {"ticker": t},
            ): t
            for t in want
        }
        for fut in as_completed(futures):
            result = fut.result()
            if result is None:
                errors += 1
            else:
                # Stamp DATA ERROR on residual inconsistent rows.
                if row_has_corrupt_price_scale(result) and not result.get("ai_note"):
                    result["ai_note"] = (
                        f"{DATA_ERROR_NOTE_PREFIX}: inconsistent 63D range vs price/SMA "
                        f"(possible mixed share scale)"
                    )
                    result["sma"] = None
                    result["dist_pct"] = None
                rows.append(result)
    if rows:
        save_dashboard_rows(rows)
    return {"ok": len(rows), "errors": errors, "tickers": want}


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
_fund_disk_mtime: float | None = None
_news_disk_mtime: float | None = None
_fund_disk_lock = threading.Lock()
_news_disk_lock = threading.Lock()


def fund_cache_path() -> Path:
    return _FUND_DISK_PATH


def news_cache_path() -> Path:
    return _NEWS_DISK_PATH


def _fund_payload_valid(fund: Any) -> bool:
    """True when Financial Score payload is usable for display (existing rules)."""
    return isinstance(fund, dict) and fund.get("health") not in (None, "unknown")


def fund_pass_rate(fund: dict[str, Any] | None) -> float | None:
    """
    Financial Pass Rate = Passed / Available indicators.
    Denominator is total_known (missing indicators excluded), not a fixed 6.
    """
    if not isinstance(fund, dict):
        return None
    total = fund.get("total_known")
    ok = fund.get("ok")
    if not isinstance(total, int) or total <= 0:
        return None
    if not isinstance(ok, (int, float)):
        return None
    return float(ok) / float(total)


def fund_qualifies_for_news(
    fund: dict[str, Any] | None,
    *,
    min_pass_rate: float = 0.60,
) -> bool:
    """True when Financial Pass Rate >= min_pass_rate (default 60%).

    This is only a News-analysis gate — not a buy signal.
    """
    rate = fund_pass_rate(fund)
    return rate is not None and rate >= min_pass_rate


def make_news_skipped(
    *,
    reason: str = "Financial Score < 60%",
) -> dict[str, Any]:
    """
    Explicit SKIPPED News payload (never analyzed / no API call).

    Distinct from NEUTRAL: both score 0, but SKIPPED means News was not run.
    Do not persist this object into news_cache.json.
    """
    return {
        "has_news": False,
        "label": "SKIPPED",
        "tone": "skipped",
        "status": "SKIPPED",
        "sort": -1,
        "news_score": 0,
        "skipped": True,
        "neg_codes": [],
        "detail": f"News skipped — {reason} (gate only; not a buy filter)",
    }


def is_news_skipped(news: dict[str, Any] | None) -> bool:
    if not isinstance(news, dict):
        return False
    return bool(
        news.get("skipped")
        or news.get("status") == "SKIPPED"
        or news.get("tone") == "skipped"
    )


def annotate_news_status(news: dict[str, Any] | None) -> dict[str, Any] | None:
    """Attach status / news_score on analyzed news; pass SKIPPED through."""
    if not isinstance(news, dict):
        return None
    if is_news_skipped(news):
        news = dict(news)
        news["status"] = "SKIPPED"
        news["tone"] = "skipped"
        news["label"] = news.get("label") or "SKIPPED"
        news["news_score"] = 0
        news["skipped"] = True
        return news
    out = dict(news)
    tone = out.get("tone")
    if tone == "pos":
        out["status"] = "POSITIVE"
        out["news_score"] = 5
    elif tone == "neg":
        out["status"] = "NEGATIVE"
        out["news_score"] = -5
    else:
        # Analyzed: no material pos/neg (includes NEUTRAL label and NO NEWS).
        out["status"] = "NEUTRAL"
        out["news_score"] = 0
    out["skipped"] = False
    return out


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
    """Load fund_cache.json; reload when another process updates the file."""
    global _fund_disk_cache, _fund_disk_mtime
    mtime = None
    try:
        if _FUND_DISK_PATH.exists():
            mtime = _FUND_DISK_PATH.stat().st_mtime
    except Exception:
        mtime = None
    if _fund_disk_cache is not None and mtime is not None and mtime == _fund_disk_mtime:
        return _fund_disk_cache
    if _fund_disk_cache is not None and mtime is None and _fund_disk_mtime is None:
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
    _fund_disk_mtime = mtime
    return cache


def _persist_fund_disk(
    ticker: str,
    fund: dict[str, Any] | None,
    *,
    info: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Write/update one ticker's fund snapshot immediately (shared persistent cache)."""
    global _fund_disk_mtime
    if not fund:
        return
    t = (ticker or "").strip().upper()
    if not t:
        return
    period = meta if meta is not None else _fund_period_meta(info)
    entry = {
        "ts": time.time(),
        "fund": fund,
        **{k: period.get(k) for k in ("mostRecentQuarter", "lastFiscalYearEnd", "earningsTimestamp") if period.get(k) is not None},
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
            _fund_disk_mtime = _FUND_DISK_PATH.stat().st_mtime
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
    """Load news_cache.json; reload when another process updates the file."""
    global _news_disk_cache, _news_disk_mtime
    mtime = None
    try:
        if _NEWS_DISK_PATH.exists():
            mtime = _NEWS_DISK_PATH.stat().st_mtime
    except Exception:
        mtime = None
    if _news_disk_cache is not None and mtime is not None and mtime == _news_disk_mtime:
        return _news_disk_cache
    if _news_disk_cache is not None and mtime is None and _news_disk_mtime is None:
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
    _news_disk_mtime = mtime
    return cache


def _news_payload_valid(news: Any) -> bool:
    """True for analyzed news payloads. SKIPPED is ephemeral and must not be cached."""
    if not isinstance(news, dict):
        return False
    if news.get("skipped") or news.get("status") == "SKIPPED" or news.get("tone") == "skipped":
        return False
    return "tone" in news or "label" in news


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
    global _news_disk_mtime
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
            _news_disk_mtime = _NEWS_DISK_PATH.stat().st_mtime
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
        out[t] = annotate_news_status(news) if _news_payload_valid(news) else None
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
        # Numeric fields for Core Universe Filter (fractions, e.g. 0.069 = 6.9%).
        # None = missing — never treat as 0 growth.
        "revenue_growth_yoy": float(rev) if rev is not None else None,
        "earnings_growth_yoy": float(eps) if eps is not None else None,
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


def _fetch_signals_one(ticker: str, *, force_news: bool = False) -> dict[str, Any]:
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

    # News gate: Financial Pass Rate >= 60% before any news API / analysis.
    # Exception: force_news=True (My Watchlist) always analyzes News when possible.
    # Below threshold without force → explicit SKIPPED (score 0); never call News API.
    if not force_news and not fund_qualifies_for_news(fund):
        return {"fund": fund, "news": make_news_skipped()}

    news_entry = _load_news_disk().get(ticker)
    if _news_entry_valid(news_entry):
        news = annotate_news_status(news_entry.get("news"))
    else:
        news = annotate_news_status(_news_from_ticker(tk))
        _persist_news_disk(ticker, news)

    return {"fund": fund, "news": news}


def _fetch_news_one(ticker: str) -> dict[str, Any]:
    """News-only Yahoo read using existing 30-day classifier (no fund/DCF/CLV)."""
    tk = yf.Ticker(ticker)
    raw = _news_from_ticker(tk)
    return annotate_news_status(raw) or raw


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


def get_signals(
    tickers: list[str],
    *,
    max_workers: int = 8,
    force_news: bool = False,
) -> dict[str, dict[str, Any]]:
    """Return {ticker: {fund, news}} using a 15-min in-process cache.

    Fund/news prefer shared persistent caches when still valid.
    force_news=True: always analyze News (My Watchlist); bypass Financial≥60% gate.
    """
    from functools import partial

    now = time.time()
    uniq = []
    seen = set()
    for t in tickers:
        t = (t or "").strip().upper()
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)

    todo = [t for t in uniq if t not in _signal_cache or now - _signal_cache[t][0] > _SIGNAL_TTL]
    if force_news:
        # Refresh when prior cache only has SKIPPED / missing news.
        for t in uniq:
            if t in todo:
                continue
            mem = _signal_cache.get(t)
            if not mem or not isinstance(mem[1], dict):
                todo.append(t)
                continue
            news = mem[1].get("news")
            if is_news_skipped(news) or not _news_payload_valid(news):
                todo.append(t)

    if todo:
        worker = partial(_fetch_signals_one, force_news=force_news)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(worker, t): t for t in todo}
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    _signal_cache[t] = (now, fut.result())
                except Exception:
                    _signal_cache[t] = (now, {"fund": None, "news": None})

    # Hydrate news from shared disk when memory still has None / SKIPPED.
    out: dict[str, dict[str, Any]] = {}
    for t in uniq:
        if t not in _signal_cache:
            continue
        payload = _gate_news_in_signals(_signal_cache[t][1], force_news=force_news)
        fund = payload.get("fund")
        need_news = force_news or fund_qualifies_for_news(fund)
        if need_news and not _news_payload_valid(payload.get("news")):
            entry = _load_news_disk().get(t)
            if _news_entry_valid(entry, now=now):
                news = annotate_news_status(entry.get("news"))
                payload = {"fund": fund, "news": news}
                prev_ts = _signal_cache[t][0]
                _signal_cache[t] = (prev_ts, payload)
        out[t] = payload
    return out


def _gate_news_in_signals(
    payload: dict[str, Any] | None,
    *,
    force_news: bool = False,
) -> dict[str, Any]:
    """Apply Financial≥60% News gate unless force_news (My Watchlist)."""
    if not isinstance(payload, dict):
        return {
            "fund": None,
            "news": None if force_news else make_news_skipped(reason="no financial data"),
        }
    fund = payload.get("fund")
    if force_news or fund_qualifies_for_news(fund):
        return {"fund": fund, "news": annotate_news_status(payload.get("news"))}
    return {"fund": fund, "news": make_news_skipped()}


# ---------------------------------------------------------------------------
# AI Score V1
#
# Opportunity (base) = pullback 25 + trend 20 + rebound 15 + volume 10
#                      + financial 15 + news (±5) + MOS T 5.
# News is an independent signed component (max ±5):
#   POSITIVE +5 · NEUTRAL 0 · NEGATIVE −5 · SKIPPED 0
# SKIPPED (Financial Score < 60%, News never analyzed) ≠ NEUTRAL (analyzed).
# Financial ≥ 60% is only the News-analysis gate — not a buy condition.
# MOS T uses public analyst-target proxy only (never Admin Est.Value / real MOS).
# 63D Position is excluded (overlaps pullback).
# Risk penalty subtracted afterwards (financial / volume / liquidity / earnings).
# News risk is not double-counted — News Score already carries ±5.
# Final AI = clamp(base − risk, 0, 100).
# ---------------------------------------------------------------------------

def _score_pullback(dist: float | None) -> int:
    """Pullback depth — max 25 (was 30 before MOS T was added)."""
    if dist is None or dist > -3:
        return 0
    if dist > -5:
        return 4
    if dist > -10:
        return 8
    if dist > -15:
        return 12
    if dist > -20:
        return 17
    if dist > -30:
        return 21
    return 25


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
    """
    Independent News Score for AI (max ±5).

    POSITIVE +5 · NEUTRAL 0 · NEGATIVE −5 · SKIPPED 0
    SKIPPED and NEUTRAL both score 0 but remain different statuses.
    """
    if not news or is_news_skipped(news):
        return 0
    tone = news.get("tone")
    status = news.get("status")
    if tone == "pos" or status == "POSITIVE":
        return 5
    if tone == "neg" or status == "NEGATIVE":
        return -5
    return 0  # NEUTRAL / NO NEWS after analysis


def _score_mos_t(mos_t: float | None) -> int:
    """
    MOS T opportunity points (max 5). Uses public target-based MOS T only.
    Missing/invalid MOS T → 0 (does not invent valuation data).
    """
    if mos_t is None:
        return 0
    try:
        m = float(mos_t)
    except (TypeError, ValueError):
        return 0
    if m != m:  # NaN
        return 0
    if m >= 25:
        return 5
    if m >= 15:
        return 4
    if m >= 5:
        return 3
    if m >= 0:
        return 2
    if m >= -10:
        return 1
    return 0


def _pen_financial(fund: dict[str, Any] | None) -> int:
    if not fund:
        return 0
    code = fund.get("code", "") or ""
    severe = sum(1 for c in ("OCF−", "CASH−", "DEBT−") if c in code)
    return min(15, severe * 5)


def _pen_news(news: dict[str, Any] | None) -> int:
    """News impact is solely via News Score (±5); do not double-count here."""
    return 0


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
    rvol/avg_vol_20d/avg_move_pct/earnings_date plus enriched fund + news + MOS T).

    Uses public MOS T (analyst_target_proxy) only — never Admin Est.Value / real MOS.
    """
    if is_data_quality_error(row):
        return {
            "final": None,
            "opp": 0,
            "risk": 0,
            "parts": {},
            "pen_fin_news": 0,
            "pen_earnings": 0,
            "earnings_days": None,
            "detail": str(row.get("ai_note") or DATA_ERROR_NOTE_PREFIX),
            "data_error": True,
        }

    fund = row.get("fund")
    news = row.get("news")

    # Prefer precomputed mos_t on the row; otherwise derive from price / 1Y target.
    mos_t = row.get("mos_t")
    if mos_t is None and (row.get("price") is not None or row.get("target_1y") is not None):
        mos_t = compute_target_proxy_mos(row.get("price"), row.get("target_1y")).get("mos_t")

    parts = {
        "pullback": (_score_pullback(row.get("dist_pct")), 25),
        "trend": (_score_trend(row.get("trend")), 20),
        "rebound": (_score_rebound(row.get("rebound_pct"), row.get("change_pct")), 15),
        "volume": (_score_volume(row.get("rvol"), row.get("change_pct")), 10),
        "financial": (_score_financial(fund), 15),
        "news": (_score_news(news), 5),  # signed ±5
        "mos_t": (_score_mos_t(mos_t), 5),
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
        "mos_t": "MOS T",
    }
    detail_lines = []
    for k, (v, m) in parts.items():
        if k == "news":
            st = ""
            if isinstance(news, dict):
                st = f" [{news.get('status') or news.get('tone') or '—'}]"
            detail_lines.append(f"{labels[k]} {v:+d}/±{m}{st}")
        else:
            detail_lines.append(f"{labels[k]} {v}/{m}")
    detail_lines.append(f"基础分 {base}")
    if pen_fin_news:
        detail_lines.append(f"财务/波动风险 −{pen_fin_news}")
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
