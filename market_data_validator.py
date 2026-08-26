# -*- coding: utf-8 -*-
"""
LeiBot Market Data Validation — DATA QUALITY (not trading quality).

Pipeline:
  RAW daily closes → validate history → derive SMA25_D / Dist / 63D →
  validate derived → AI BUY (DATA_BLOCK hard gate) → READY final check.

SMA25 used by AI BUY means: 25 trading-day SMA from DAILY closes (SMA25_D).
DATA ERROR is a hard BUY block, never a scoring penalty.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from db import get_setting

# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------
PASS = "PASS"
WARNING = "WARNING"
ERROR = "ERROR"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
STALE_DATA = "STALE_DATA"

STATUS_RANK = {
    PASS: 0,
    WARNING: 1,
    STALE_DATA: 2,
    INSUFFICIENT_DATA: 3,
    ERROR: 4,
}

# Defaults (overridable via settings)
DEFAULT_SMA_RECALC_WARN_PCT = 0.5
DEFAULT_SMA_RECALC_ERROR_PCT = 1.0
DEFAULT_DIST_IMPLIED_TOL_PCT = 0.5  # relative error on implied SMA
DEFAULT_STALE_HOURS = 36.0
DEFAULT_POS63_TOL = 0.05  # allow tiny float drift outside 0–100


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _f(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or math.isinf(v):  # NaN / Inf
        return None
    return v


def _setting_float(key: str, default: float) -> float:
    raw = get_setting(key, default)
    try:
        return float(raw if raw is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _setting_int(key: str, default: int) -> int:
    raw = get_setting(key, default)
    try:
        return int(raw if raw is not None else default)
    except (TypeError, ValueError):
        return int(default)


def merge_status(*statuses: str | None) -> str:
    best = PASS
    for s in statuses:
        if not s:
            continue
        if STATUS_RANK.get(s, 0) > STATUS_RANK.get(best, 0):
            best = s
    return best


# ---------------------------------------------------------------------------
# Canonical formulas (single source of truth for AI BUY)
# ---------------------------------------------------------------------------
def sma_n_d(closes: pd.Series, n: int) -> float | None:
    """N-trading-day SMA from DAILY closes (last n valid bars)."""
    clean = sanitize_daily_closes(closes)
    if clean is None or len(clean) < n:
        return None
    return float(clean.iloc[-n:].mean())


def dist_sma_pct(price: float, sma: float) -> float | None:
    """
    Dist_SMA_pct = (Current_Price − SMA) / SMA × 100

    Same as LeiBot dashboard: (price / sma − 1) × 100.
    """
    p, s = _f(price), _f(sma)
    if p is None or s is None or s == 0:
        return None
    return round((p - s) / s * 100.0, 2)


def implied_sma_from_dist(price: float, dist_pct: float) -> float | None:
    """Implied_SMA = Current_Price / (1 + Dist_pct / 100)."""
    p, d = _f(price), _f(dist_pct)
    if p is None or d is None:
        return None
    denom = 1.0 + d / 100.0
    if abs(denom) < 1e-12:
        return None
    return p / denom


def avg_daily_move_63d_pct(closes: pd.Series, lookback: int = 63) -> float | None:
    """
    Average absolute daily % change over up to `lookback` trading days.
    Matches market_data._avg_daily_move.
    """
    clean = sanitize_daily_closes(closes)
    if clean is None or len(clean) < 2:
        return None
    pct = clean.pct_change().dropna().abs()
    if pct.empty:
        return None
    window = pct.iloc[-lookback:] if len(pct) >= lookback else pct
    return round(float(window.mean()) * 100.0, 2)


def range_63d_metrics(
    closes: pd.Series, lookback: int = 63
) -> dict[str, float | None]:
    """63 trading-day low / high / position% from DAILY closes."""
    clean = sanitize_daily_closes(closes)
    if clean is None or len(clean) < lookback:
        return {"low": None, "high": None, "pos": None, "ok": False}
    window = clean.iloc[-lookback:]
    low = float(window.min())
    high = float(window.max())
    last = float(window.iloc[-1])
    if low <= 0 or high <= 0 or low > high:
        return {"low": None, "high": None, "pos": None, "ok": False}
    if high == low:
        return {"low": round(low, 2), "high": round(high, 2), "pos": None, "ok": True}
    pos = (last - low) / (high - low) * 100.0
    return {
        "low": round(low, 2),
        "high": round(high, 2),
        "pos": round(pos, 2),
        "ok": True,
    }


# ---------------------------------------------------------------------------
# History sanitization / validation
# ---------------------------------------------------------------------------
def sanitize_daily_closes(closes: pd.Series | None) -> pd.Series | None:
    """Drop NaN/Inf/non-positive; keep chronological order; dedupe dates (keep last)."""
    if closes is None:
        return None
    try:
        s = closes.astype(float).copy()
    except Exception:
        return None
    s = s.replace([math.inf, -math.inf], float("nan")).dropna()
    s = s[s > 0]
    if s.empty:
        return None
    if not s.index.is_unique:
        s = s[~s.index.duplicated(keep="last")]
    try:
        s = s.sort_index()
    except Exception:
        pass
    return s


def validate_daily_history(
    closes: pd.Series | None,
    *,
    ticker: str = "",
    min_bars: int = 1,
) -> dict[str, Any]:
    reasons: list[str] = []
    if not (ticker or "").strip() and closes is None:
        return {
            "status": ERROR,
            "ok": False,
            "reasons": ["missing_ticker_and_history"],
            "n": 0,
            "closes": None,
        }
    clean = sanitize_daily_closes(closes)
    if clean is None or clean.empty:
        return {
            "status": INSUFFICIENT_DATA,
            "ok": False,
            "reasons": ["no_valid_daily_closes"],
            "n": 0,
            "closes": None,
        }
    n = len(clean)
    if n < min_bars:
        reasons.append(f"insufficient_bars:{n}<{min_bars}")
        return {
            "status": INSUFFICIENT_DATA,
            "ok": False,
            "reasons": reasons,
            "n": n,
            "closes": clean,
        }
    # Chronology
    try:
        if not clean.index.is_monotonic_increasing:
            reasons.append("dates_not_sorted")
    except Exception:
        pass
    status = WARNING if reasons else PASS
    return {
        "status": status,
        "ok": status in (PASS, WARNING),
        "reasons": reasons,
        "n": n,
        "closes": clean,
    }


def validate_current_price(
    price: Any,
    *,
    updated_at: str | None = None,
    stale_hours: float | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    p = _f(price)
    if p is None or p <= 0:
        return {
            "status": ERROR,
            "ok": False,
            "reasons": ["invalid_price"],
            "price": None,
            "stale": False,
        }
    stale = False
    hours = (
        float(stale_hours)
        if stale_hours is not None
        else _setting_float("mdv_stale_hours", DEFAULT_STALE_HOURS)
    )
    if updated_at:
        try:
            ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
            if age_h > hours:
                stale = True
                reasons.append(f"stale_price:{age_h:.1f}h>{hours:.0f}h")
        except Exception:
            reasons.append("bad_price_timestamp")
    else:
        reasons.append("missing_price_timestamp")
    if stale:
        status = STALE_DATA
    elif reasons:
        status = WARNING
    else:
        status = PASS
    return {
        "status": status,
        "ok": status in (PASS, WARNING, STALE_DATA),
        "reasons": reasons,
        "price": round(p, 4),
        "stale": stale,
    }


def validate_sma25(
    closes: pd.Series | None = None,
    *,
    sma_primary: Any = None,
    period: int = 25,
) -> dict[str, Any]:
    """
    SMA25_D = mean(last 25 valid daily closes).
    Sanity: 25D_LOW <= SMA25_D <= 25D_HIGH.
    Optional recalc vs primary with warn/error thresholds.
    """
    reasons: list[str] = []
    hist = validate_daily_history(closes, min_bars=period) if closes is not None else None
    sma_check = None
    lo = hi = None
    if hist and hist.get("closes") is not None and hist["ok"]:
        clean = hist["closes"]
        window = clean.iloc[-period:]
        sma_check = float(window.mean())
        lo = float(window.min())
        hi = float(window.max())
        if not (lo - 1e-9 <= sma_check <= hi + 1e-9):
            reasons.append("sma25_outside_25d_high_low")
            return {
                "status": ERROR,
                "ok": False,
                "reasons": reasons,
                "sma25_d": None,
                "sma25_check": round(sma_check, 6),
                "low_25d": round(lo, 4),
                "high_25d": round(hi, 4),
                "error_pct": None,
            }
    elif closes is not None:
        return {
            "status": INSUFFICIENT_DATA,
            "ok": False,
            "reasons": hist.get("reasons") if hist else ["insufficient_for_sma25"],
            "sma25_d": None,
            "sma25_check": None,
            "low_25d": None,
            "high_25d": None,
            "error_pct": None,
        }

    primary = _f(sma_primary)
    if primary is None and sma_check is None:
        return {
            "status": INSUFFICIENT_DATA,
            "ok": False,
            "reasons": ["no_sma25"],
            "sma25_d": None,
            "sma25_check": None,
            "low_25d": None,
            "high_25d": None,
            "error_pct": None,
        }

    # Prefer check from raw closes as truth for validation; primary is stored field.
    sma25_d = primary if primary is not None else sma_check
    error_pct = None
    if primary is not None and sma_check is not None and abs(sma_check) > 1e-12:
        error_pct = abs(primary - sma_check) / abs(sma_check) * 100.0
        warn_t = _setting_float("mdv_sma_recalc_warn_pct", DEFAULT_SMA_RECALC_WARN_PCT)
        err_t = _setting_float("mdv_sma_recalc_error_pct", DEFAULT_SMA_RECALC_ERROR_PCT)
        if error_pct > err_t:
            reasons.append(f"sma25_recalc_error:{error_pct:.3f}%>{err_t}%")
            return {
                "status": ERROR,
                "ok": False,
                "reasons": reasons,
                "sma25_d": round(primary, 4),
                "sma25_check": round(sma_check, 4),
                "low_25d": None if lo is None else round(lo, 4),
                "high_25d": None if hi is None else round(hi, 4),
                "error_pct": round(error_pct, 4),
            }
        if error_pct > warn_t:
            reasons.append(f"sma25_recalc_warn:{error_pct:.3f}%")

    # Range check on primary when we have window
    if primary is not None and lo is not None and hi is not None:
        if not (lo - 1e-9 <= primary <= hi + 1e-9):
            reasons.append("sma25_primary_outside_25d_high_low")
            return {
                "status": ERROR,
                "ok": False,
                "reasons": reasons,
                "sma25_d": round(primary, 4),
                "sma25_check": None if sma_check is None else round(sma_check, 4),
                "low_25d": round(lo, 4),
                "high_25d": round(hi, 4),
                "error_pct": error_pct,
            }

    status = WARNING if reasons else PASS
    return {
        "status": status,
        "ok": True,
        "reasons": reasons,
        "sma25_d": None if sma25_d is None else round(float(sma25_d), 4),
        "sma25_check": None if sma_check is None else round(sma_check, 4),
        "low_25d": None if lo is None else round(lo, 4),
        "high_25d": None if hi is None else round(hi, 4),
        "error_pct": None if error_pct is None else round(error_pct, 4),
    }


def validate_sma63(
    closes: pd.Series | None = None,
    *,
    sma_primary: Any = None,
    period: int = 63,
) -> dict[str, Any]:
    """Same pattern as SMA25 for SMA63_D (optional / research)."""
    return validate_sma25(closes, sma_primary=sma_primary, period=period)


def validate_dist_sma25(
    *,
    price: Any,
    sma25_d: Any,
    dist_primary: Any = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    p, s = _f(price), _f(sma25_d)
    if p is None or p <= 0 or s is None or s <= 0:
        return {
            "status": ERROR,
            "ok": False,
            "reasons": ["missing_price_or_sma25"],
            "dist_pct": None,
            "dist_check": None,
            "implied_sma": None,
        }
    dist_check = dist_sma_pct(p, s)
    dist_stored = _f(dist_primary)
    implied = implied_sma_from_dist(p, dist_stored if dist_stored is not None else dist_check)
    tol = _setting_float("mdv_dist_implied_tol_pct", DEFAULT_DIST_IMPLIED_TOL_PCT)
    if implied is not None and s > 0:
        rel = abs(implied - s) / s * 100.0
        if rel > tol:
            reasons.append(f"dist_implied_sma_mismatch:{rel:.3f}%>{tol}%")
            return {
                "status": ERROR,
                "ok": False,
                "reasons": reasons,
                "dist_pct": dist_stored,
                "dist_check": dist_check,
                "implied_sma": round(implied, 4),
            }
    if dist_stored is not None and dist_check is not None:
        if abs(dist_stored - dist_check) > max(0.05, abs(dist_check) * 0.02 + 0.05):
            reasons.append(
                f"dist_recalc_mismatch:stored={dist_stored} check={dist_check}"
            )
            return {
                "status": ERROR,
                "ok": False,
                "reasons": reasons,
                "dist_pct": dist_stored,
                "dist_check": dist_check,
                "implied_sma": None if implied is None else round(implied, 4),
            }
    status = WARNING if reasons else PASS
    return {
        "status": status,
        "ok": True,
        "reasons": reasons,
        "dist_pct": dist_stored if dist_stored is not None else dist_check,
        "dist_check": dist_check,
        "implied_sma": None if implied is None else round(implied, 4),
    }


def validate_63d_metrics(
    *,
    closes: pd.Series | None = None,
    range_low: Any = None,
    range_high: Any = None,
    range_pos: Any = None,
    price: Any = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    if closes is not None:
        hist = validate_daily_history(closes, min_bars=63)
        if not hist["ok"]:
            return {
                "status": hist["status"],
                "ok": False,
                "reasons": hist["reasons"] or ["insufficient_63d_history"],
                "low": None,
                "high": None,
                "pos": None,
            }
        m = range_63d_metrics(hist["closes"], 63)
        lo, hi, pos = m["low"], m["high"], m["pos"]
    else:
        lo, hi, pos = _f(range_low), _f(range_high), _f(range_pos)

    if lo is None or hi is None:
        return {
            "status": INSUFFICIENT_DATA,
            "ok": False,
            "reasons": ["missing_63d_high_low"],
            "low": lo,
            "high": hi,
            "pos": pos,
        }
    if lo > hi:
        return {
            "status": ERROR,
            "ok": False,
            "reasons": ["63d_low_gt_high"],
            "low": lo,
            "high": hi,
            "pos": pos,
        }
    # Mixed-scale signature (MNST-class): huge span + price near low + elevated SMA handled elsewhere
    if lo > 0 and hi / lo >= 1.75:
        p = _f(price)
        if p is not None and pos is not None and pos <= 25.0 and hi / p >= 1.75:
            reasons.append("63d_mixed_scale_signature")
            return {
                "status": ERROR,
                "ok": False,
                "reasons": reasons,
                "low": lo,
                "high": hi,
                "pos": pos,
            }

    tol = _setting_float("mdv_pos63_tol", DEFAULT_POS63_TOL)
    if pos is not None and not (-tol <= pos <= 100.0 + tol):
        reasons.append(f"63d_pos_out_of_bounds:{pos}")
        return {
            "status": ERROR,
            "ok": False,
            "reasons": reasons,
            "low": lo,
            "high": hi,
            "pos": pos,
        }

    # Current price vs window: breakout after window is OK — only warn if wildly off mixed scale
    status = WARNING if reasons else PASS
    return {
        "status": status,
        "ok": True,
        "reasons": reasons,
        "low": lo,
        "high": hi,
        "pos": pos,
    }


def validate_avg_daily_move(
    *,
    closes: pd.Series | None = None,
    avg_move_pct: Any = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    check = None
    if closes is not None:
        hist = validate_daily_history(closes, min_bars=2)
        if hist["ok"]:
            check = avg_daily_move_63d_pct(hist["closes"], 63)
    primary = _f(avg_move_pct)
    val = primary if primary is not None else check
    if val is None:
        return {
            "status": INSUFFICIENT_DATA,
            "ok": False,
            "reasons": ["no_avg_daily_move"],
            "avg_move_pct": None,
            "avg_move_check": check,
        }
    if val < 0:
        return {
            "status": ERROR,
            "ok": False,
            "reasons": ["avg_daily_move_negative"],
            "avg_move_pct": val,
            "avg_move_check": check,
        }
    # Extreme is review-only (WARNING), not automatic ERROR — high activity can be real.
    if val > 25.0:
        reasons.append(f"avg_daily_move_extreme:{val}%")
    status = WARNING if reasons else PASS
    return {
        "status": status,
        "ok": True,
        "reasons": reasons,
        "avg_move_pct": val,
        "avg_move_check": check,
    }


# ---------------------------------------------------------------------------
# Aggregate validation
# ---------------------------------------------------------------------------
def _empty_report(ticker: str) -> dict[str, Any]:
    return {
        "ticker": (ticker or "").upper(),
        "data_quality_status": ERROR,
        "data_quality_reason": ["no_input"],
        "data_quality_checked_at": _utcnow(),
        "data_block": True,
        "buy_data_ok": False,
        "checks": {},
        "detail": {},
    }


def validate_buy_data(
    ticker: str,
    row: dict[str, Any] | None = None,
    *,
    closes: pd.Series | None = None,
    require_live_history: bool = False,
) -> dict[str, Any]:
    """
    Final / pre-READY validation of stored (and optional live) BUY inputs.

    Does NOT re-download unless caller passes `closes`.
    ERROR / INSUFFICIENT_DATA → data_block=True (hard AI BUY gate).
    WARNING / STALE_DATA may remain research-usable; READY requires PASS
    (STALE_DATA blocks READY in V1).
    """
    t = (ticker or "").strip().upper()
    if not t:
        return _empty_report("")
    row = dict(row or {})

    if require_live_history and closes is None:
        try:
            from market_data import load_yahoo_daily_closes

            closes, _hist, _meta = load_yahoo_daily_closes(t, period="1y")
        except Exception:
            closes = None

    checks: dict[str, Any] = {}
    reasons: list[str] = []

    price_v = validate_current_price(row.get("price"), updated_at=row.get("updated_at"))
    checks["price"] = price_v
    reasons.extend(price_v["reasons"])

    hist_v = (
        validate_daily_history(closes, ticker=t, min_bars=25)
        if closes is not None
        else {
            "status": PASS if row.get("price") else INSUFFICIENT_DATA,
            "ok": bool(row.get("price")),
            "reasons": [] if row.get("price") else ["no_history_for_recheck"],
            "n": None,
            "closes": None,
        }
    )
    checks["daily_history"] = {
        k: hist_v[k] for k in ("status", "ok", "reasons", "n") if k in hist_v
    }
    reasons.extend(hist_v.get("reasons") or [])

    sma_v = validate_sma25(
        hist_v.get("closes") if isinstance(hist_v.get("closes"), pd.Series) else closes,
        sma_primary=row.get("sma"),
        period=_setting_int("sma_period", 25),
    )
    checks["sma25"] = sma_v
    reasons.extend(sma_v["reasons"])

    dist_v = validate_dist_sma25(
        price=row.get("price"),
        sma25_d=sma_v.get("sma25_d") or row.get("sma"),
        dist_primary=row.get("dist_pct"),
    )
    checks["dist_sma25"] = dist_v
    reasons.extend(dist_v["reasons"])

    m63 = validate_63d_metrics(
        closes=closes,
        range_low=row.get("range_63d_low"),
        range_high=row.get("range_63d_high"),
        range_pos=row.get("range_63d_pos"),
        price=row.get("price"),
    )
    checks["metrics_63d"] = m63
    reasons.extend(m63["reasons"])

    adm = validate_avg_daily_move(
        closes=closes, avg_move_pct=row.get("avg_move_pct")
    )
    checks["avg_daily_move"] = adm
    reasons.extend(adm["reasons"])

    # Legacy ai_note / corrupt-scale helpers
    try:
        from market_data import is_data_quality_error

        if is_data_quality_error(row):
            reasons.append("legacy_data_error_flag")
            checks["legacy"] = {"status": ERROR, "ok": False}
    except Exception:
        pass

    status = merge_status(
        price_v["status"],
        hist_v.get("status"),
        sma_v["status"],
        dist_v["status"],
        m63["status"],
        adm["status"],
        checks.get("legacy", {}).get("status"),
    )
    # Deduplicate reasons preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for r in reasons:
        if r and r not in seen:
            seen.add(r)
            uniq.append(r)

    # Hard block: ERROR or INSUFFICIENT critical inputs for BUY
    critical_error = status in (ERROR, INSUFFICIENT_DATA) or (
        not sma_v.get("ok") or not dist_v.get("ok") or not price_v.get("ok")
    )
    # READY gate: only PASS (WARNING ok for research table filter; READY needs PASS)
    ready_ok = status == PASS and not critical_error

    detail = {
        "current_price": price_v.get("price"),
        "price_timestamp": row.get("updated_at"),
        "sma25_d": sma_v.get("sma25_d"),
        "sma25_check": sma_v.get("sma25_check"),
        "sma_error_pct": sma_v.get("error_pct"),
        "sma25_range_check": "PASS"
        if sma_v.get("ok") and not any("outside" in x for x in sma_v.get("reasons") or [])
        else sma_v.get("status"),
        "dist_sma25_stored": row.get("dist_pct"),
        "dist_sma25_recalc": dist_v.get("dist_check"),
        "dist_check": dist_v.get("status"),
        "range_63d_low": m63.get("low"),
        "range_63d_high": m63.get("high"),
        "range_63d_pos": m63.get("pos"),
        "avg_daily_move_63d": adm.get("avg_move_pct"),
        "timeframe_note": "SMA25_D = 25 trading-day SMA from DAILY closes (not hourly/intraday)",
    }

    return {
        "ticker": t,
        "data_quality_status": status,
        "data_quality_reason": uniq,
        "data_quality_checked_at": _utcnow(),
        "data_block": bool(critical_error),
        "buy_data_ok": bool(ready_ok),
        "research_ok": status in (PASS, WARNING),  # STALE/ERROR not for NEXT focus
        "checks": checks,
        "detail": detail,
    }


def attach_data_quality_to_row(
    row: dict[str, Any],
    *,
    closes: pd.Series | None = None,
) -> dict[str, Any]:
    """Mutate row with data_quality_* fields + data_block for AI BUY."""
    report = validate_buy_data(row.get("ticker") or "", row, closes=closes)
    row["data_quality_status"] = report["data_quality_status"]
    row["data_quality_reason"] = report["data_quality_reason"]
    row["data_quality_checked_at"] = report["data_quality_checked_at"]
    row["data_block"] = report["data_block"]
    row["data_quality_detail"] = report["detail"]
    row["buy_data_ok"] = report["buy_data_ok"]
    # Alias documentation field (DB still uses sma)
    if row.get("sma") is not None:
        row["sma25_d"] = row.get("sma")
    return report


def validate_rows_batch(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Owner batch summary from dashboard/AI BUY rows (no re-download)."""
    counts = {
        PASS: 0,
        WARNING: 0,
        ERROR: 0,
        INSUFFICIENT_DATA: 0,
        STALE_DATA: 0,
    }
    errors: list[dict[str, Any]] = []
    for r in rows:
        rep = validate_buy_data(r.get("ticker") or "", r)
        st = rep["data_quality_status"]
        counts[st] = counts.get(st, 0) + 1
        if st in (ERROR, INSUFFICIENT_DATA):
            errors.append(
                {
                    "ticker": rep["ticker"],
                    "status": st,
                    "reasons": rep["data_quality_reason"][:6],
                }
            )
    return {
        "checked": len(rows),
        "counts": counts,
        "errors": errors,
        "checked_at": _utcnow(),
    }


def format_data_check_text(report: dict[str, Any]) -> str:
    """Human-readable Owner DATA CHECK report."""
    d = report.get("detail") or {}
    lines = [
        f"Ticker: {report.get('ticker')}",
        "",
        f"Current Price: {d.get('current_price')}",
        f"Price Timestamp: {d.get('price_timestamp')}",
        "",
        f"SMA25_D: {d.get('sma25_d')}",
        f"SMA25 Recalculated: {d.get('sma25_check')}",
        f"SMA Error: {d.get('sma_error_pct')}%",
        f"SMA25 Range Check: {d.get('sma25_range_check')}",
        "",
        f"Dist SMA25 Stored: {d.get('dist_sma25_stored')}",
        f"Dist SMA25 Recalculated: {d.get('dist_sma25_recalc')}",
        f"Dist Check: {d.get('dist_check')}",
        "",
        f"63D Low/High: {d.get('range_63d_low')} / {d.get('range_63d_high')}",
        f"63D Position: {d.get('range_63d_pos')}",
        f"Avg Daily Move 63D: {d.get('avg_daily_move_63d')}%",
        "",
        f"Timeframe: {d.get('timeframe_note')}",
        "",
        f"FINAL DATA STATUS: {report.get('data_quality_status')}",
    ]
    reasons = report.get("data_quality_reason") or []
    if reasons:
        lines.append("Reasons:")
        for r in reasons:
            lines.append(f"  - {r}")
    if report.get("data_block"):
        lines.append("BUY BLOCKED (DATA ERROR)")
    return "\n".join(lines)
