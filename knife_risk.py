"""
Knife Risk — current downside velocity + relative weakness (0–100).

Independent of AI Score. Not an oversold / location score.
Hard gate for AI Auto Trading when score >= KNIFE_AUTO_BLOCK_THRESHOLD.
"""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any

import yfinance as yf

from rising_now import _load_recent_closes_by_ticker

# Configurable Auto-Trade hard block (Paper / AI candidates).
KNIFE_AUTO_BLOCK_THRESHOLD = 45

_BENCH_PATH = Path(__file__).resolve().parent / "data" / "logs" / "knife_bench_cache.json"
_BENCH_TTL_SEC = 6 * 3600
_bench_lock = threading.Lock()
_bench_mem: dict[str, Any] | None = None
_bench_mtime: float | None = None

# Broad sector ETFs (Yahoo sector labels → ETF).
SECTOR_ETF_MAP: dict[str, str] = {
    "technology": "XLK",
    "information technology": "XLK",
    "financials": "XLF",
    "financial services": "XLF",
    "healthcare": "XLV",
    "health care": "XLV",
    "consumer discretionary": "XLY",
    "consumer cyclical": "XLY",
    "consumer staples": "XLP",
    "consumer defensive": "XLP",
    "industrials": "XLI",
    "energy": "XLE",
    "materials": "XLB",
    "basic materials": "XLB",
    "utilities": "XLU",
    "real estate": "XLRE",
    "communication services": "XLC",
    "communication": "XLC",
}

MARKET_ETF = "SPY"
_ALL_BENCH_TICKERS = sorted({MARKET_ETF, *SECTOR_ETF_MAP.values()})


def sector_etf(sector: str | None) -> str | None:
    if not sector:
        return None
    return SECTOR_ETF_MAP.get(str(sector).strip().lower())


def knife_level(score: int | None) -> str | None:
    if score is None:
        return None
    if score >= 70:
        return "KNIFE"
    if score >= 45:
        return "HIGH"
    if score >= 25:
        return "WATCH"
    return "LOW"


def knife_auto_blocked(score: int | None, *, threshold: int | None = None) -> bool:
    if score is None:
        return False
    thr = KNIFE_AUTO_BLOCK_THRESHOLD if threshold is None else int(threshold)
    try:
        from db import get_setting

        configured = get_setting("knife_auto_block_threshold", None)
        if configured is not None:
            thr = int(configured)
    except Exception:
        pass
    return int(score) >= thr


# ---------------------------------------------------------------------------
# Momentum from closes
# ---------------------------------------------------------------------------

def _daily_returns(closes: list[float]) -> list[float]:
    """Ascending closes → daily % returns (len = len(closes)-1)."""
    out: list[float] = []
    for i in range(1, len(closes)):
        a, b = float(closes[i - 1]), float(closes[i])
        if a <= 0 or b <= 0:
            return []
        out.append((b / a - 1.0) * 100.0)
    return out


def momentum_from_closes(closes: list[float]) -> dict[str, Any] | None:
    """
    Need >= 6 closes for a full 5D window.
    With >= 11 / >= 21 closes also fills 10D / 20D log-regression trends.
    """
    if len(closes) < 6:
        return None
    window = [float(x) for x in closes[-6:]]
    if any(c <= 0 for c in window):
        return None
    rets = _daily_returns(window)  # 5 daily returns: d1..d5 (oldest→newest)
    if len(rets) < 5:
        return None

    ret_1d = rets[-1]
    ret_3d = (window[-1] / window[-4] - 1.0) * 100.0
    ret_5d = (window[-1] / window[0] - 1.0) * 100.0

    down_days = 0
    for r in reversed(rets):
        if r < 0:
            down_days += 1
        else:
            break

    # Recent = last 2 days; previous = days -5..-3 (first 3 of the 5)
    recent_vel = (rets[-2] + rets[-1]) / 2.0
    prev_vel = (rets[0] + rets[1] + rets[2]) / 3.0
    accel = recent_vel - prev_vel  # more negative → accelerating down

    trend_10d = log_regression_daily_pct(closes, 10)
    trend_20d = log_regression_daily_pct(closes, 20)

    return {
        "ret_1d": round(ret_1d, 2),
        "ret_3d": round(ret_3d, 2),
        "ret_5d": round(ret_5d, 2),
        "down_days": down_days,
        "recent_vel": round(recent_vel, 2),
        "prev_vel": round(prev_vel, 2),
        "accel": round(accel, 2),
        "trend_10d": None if trend_10d is None else round(trend_10d, 3),
        "trend_20d": None if trend_20d is None else round(trend_20d, 3),
    }


def log_regression_daily_pct(closes: list[float], n: int) -> float | None:
    """
    Fit ln(price) = a + b * t on the last n closes (t = 0..n-1).
    Return approximate daily % trend: (exp(b) - 1) * 100.
    Uses every point in the window (not just endpoints).
    """
    if n < 3 or len(closes) < n:
        return None
    ys: list[float] = []
    for c in closes[-n:]:
        try:
            v = float(c)
        except (TypeError, ValueError):
            return None
        if v <= 0:
            return None
        ys.append(math.log(v))
    m = len(ys)
    # Ordinary least squares for slope b
    # t = 0..m-1; mean_t = (m-1)/2; sum (t - mean_t)^2 = m(m^2-1)/12
    mean_t = (m - 1) / 2.0
    mean_y = sum(ys) / m
    num = 0.0
    den = 0.0
    for i, y in enumerate(ys):
        dt = i - mean_t
        num += dt * (y - mean_y)
        den += dt * dt
    if den <= 0:
        return None
    b = num / den
    return (math.exp(b) - 1.0) * 100.0


def _load_knife_closes(tickers: set[str]) -> dict[str, list[float]]:
    """
    Load enough daily_bars for 20D regression (~45 calendar days).
    Falls back to rising_now's shorter loader if the query fails.
    """
    from collections import defaultdict

    from db import get_conn, init_db

    if not tickers:
        return {}
    init_db()
    pad_days = 45
    ph = ",".join("?" * len(tickers))
    try:
        with get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT ticker, date, close
                FROM daily_bars
                WHERE ticker IN ({ph}) AND date >= date('now', ?)
                ORDER BY ticker ASC, date ASC
                """,
                [*tickers, f"-{pad_days} days"],
            ).fetchall()
        by_ticker: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            t = (r["ticker"] or "").upper()
            if not t:
                continue
            try:
                by_ticker[t].append(float(r["close"]))
            except (TypeError, ValueError):
                continue
        return dict(by_ticker)
    except Exception:
        return _load_recent_closes_by_ticker(tickers)


# ---------------------------------------------------------------------------
# Benchmark cache (SPY + sector ETFs once)
# ---------------------------------------------------------------------------

def _load_bench_disk() -> dict[str, Any]:
    global _bench_mem, _bench_mtime
    with _bench_lock:
        try:
            if _BENCH_PATH.exists():
                mtime = _BENCH_PATH.stat().st_mtime
                if _bench_mem is not None and _bench_mtime == mtime:
                    return _bench_mem
                raw = json.loads(_BENCH_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    _bench_mem = raw
                    _bench_mtime = mtime
                    return raw
        except Exception:
            pass
        _bench_mem = {}
        _bench_mtime = None
        return _bench_mem


def _save_bench_disk(data: dict[str, Any]) -> None:
    global _bench_mem, _bench_mtime
    with _bench_lock:
        try:
            _BENCH_PATH.parent.mkdir(parents=True, exist_ok=True)
            _BENCH_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=0),
                encoding="utf-8",
            )
            _bench_mem = data
            _bench_mtime = _BENCH_PATH.stat().st_mtime
        except Exception:
            _bench_mem = data


def _fetch_etf_return_5d(ticker: str) -> float | None:
    """Yahoo history → 5D return %. Prefer daily_bars if present."""
    from db import get_conn, init_db

    t = ticker.upper()
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT close FROM daily_bars
            WHERE ticker = ? AND date >= date('now', '-21 days')
            ORDER BY date ASC
            """,
            (t,),
        ).fetchall()
    closes = []
    for r in rows:
        try:
            closes.append(float(r["close"]))
        except (TypeError, ValueError):
            continue
    if len(closes) >= 6:
        m = momentum_from_closes(closes)
        if m:
            return float(m["ret_5d"])

    try:
        hist = yf.Ticker(t).history(period="1mo", auto_adjust=True)
        if hist is None or hist.empty or "Close" not in hist.columns:
            return None
        series = [float(x) for x in hist["Close"].dropna().tolist()]
        if len(series) < 6:
            return None
        m = momentum_from_closes(series)
        return float(m["ret_5d"]) if m else None
    except Exception:
        return None


def ensure_benchmark_returns(*, force: bool = False) -> dict[str, float | None]:
    """
    Return {ETF: 5D return %} for SPY + sector ETFs.
    Uses disk cache (6h); fetches only missing/stale symbols once per cycle.
    """
    now = time.time()
    disk = _load_bench_disk()
    out: dict[str, float | None] = {}
    todo: list[str] = []

    for t in _ALL_BENCH_TICKERS:
        entry = disk.get(t) if isinstance(disk.get(t), dict) else None
        if (
            not force
            and entry
            and (now - float(entry.get("ts") or 0)) <= _BENCH_TTL_SEC
            and entry.get("ret_5d") is not None
        ):
            try:
                out[t] = float(entry["ret_5d"])
                continue
            except (TypeError, ValueError):
                pass
        todo.append(t)

    if todo:
        for t in todo:
            ret = _fetch_etf_return_5d(t)
            out[t] = ret
            disk[t] = {"ts": now, "ret_5d": ret}
        _save_bench_disk(disk)

    # Include any still-cached that weren't in todo path
    for t in _ALL_BENCH_TICKERS:
        if t not in out:
            entry = disk.get(t) if isinstance(disk.get(t), dict) else None
            if entry and entry.get("ret_5d") is not None:
                try:
                    out[t] = float(entry["ret_5d"])
                except (TypeError, ValueError):
                    out[t] = None
            else:
                out[t] = None
    return out


def get_benchmark_returns_cached() -> dict[str, float | None]:
    """Read cache only (no Yahoo). Ensures once if empty/stale lightly."""
    now = time.time()
    disk = _load_bench_disk()
    out: dict[str, float | None] = {}
    need_ensure = False
    for t in (MARKET_ETF,):
        entry = disk.get(t) if isinstance(disk.get(t), dict) else None
        if not entry or (now - float(entry.get("ts") or 0)) > _BENCH_TTL_SEC:
            need_ensure = True
            break
    if need_ensure or not disk:
        return ensure_benchmark_returns(force=False)
    for t in _ALL_BENCH_TICKERS:
        entry = disk.get(t) if isinstance(disk.get(t), dict) else None
        if entry and entry.get("ret_5d") is not None:
            try:
                out[t] = float(entry["ret_5d"])
            except (TypeError, ValueError):
                out[t] = None
        else:
            out[t] = None
    return out


# ---------------------------------------------------------------------------
# Scoring (0–100)
#   Falling Speed / Acceleration : 35
#   Relative Weakness            : 35
#   Trend Persistence (10D/20D)  : 30
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _score_5d_decline(ret_5d: float) -> float:
    """0–18 pts from 5D return (magnitude). Continuous."""
    if ret_5d >= -2.0:
        return _clamp((-ret_5d) * 1.5, 0.0, 3.0)
    if ret_5d >= -10.0:
        return 3.0 + (-ret_5d - 2.0) / 8.0 * 15.0
    return 18.0


def _score_3d_decline(ret_3d: float) -> float:
    """0–6 pts."""
    if ret_3d >= -1.5:
        return 0.0
    if ret_3d >= -8.0:
        return (-ret_3d - 1.5) / 6.5 * 6.0
    return 6.0


def _score_down_days(down_days: int, ret_5d: float) -> float:
    """0–5 pts — persistence, muted if decline is tiny."""
    if down_days <= 1:
        base = 0.0
    elif down_days == 2:
        base = 1.2
    elif down_days == 3:
        base = 2.5
    elif down_days == 4:
        base = 3.8
    else:
        base = 5.0
    mag = _clamp((-ret_5d) / 6.0, 0.25, 1.0) if ret_5d < 0 else 0.0
    return base * mag


def _score_acceleration(accel: float, recent_vel: float) -> float:
    """0–6 pts when recent velocity is more negative than previous."""
    if recent_vel >= -0.2:
        return 0.0
    if accel >= -0.3:
        return 0.0
    return _clamp((-accel - 0.3) / 1.7 * 6.0, 0.0, 6.0)


def _score_rel(rel: float, *, max_pts: float) -> float:
    """Relative return (stock - bench). More negative → higher risk."""
    if rel >= -1.0:
        return 0.0
    if rel >= -10.0:
        return (-rel - 1.0) / 9.0 * max_pts
    return max_pts


def _slope_risk_unit(daily_pct: float | None) -> float:
    """Map daily % trend to 0..1 risk unit (continuous)."""
    if daily_pct is None or daily_pct >= 0:
        return 0.0
    mag = -float(daily_pct)
    # 0 → 0; 0.25 → 0.25; 0.5 → 0.5; 1.0 → 0.8; 1.5+ → 1.0
    if mag <= 0.25:
        return mag / 0.25 * 0.25
    if mag <= 0.5:
        return 0.25 + (mag - 0.25) / 0.25 * 0.25
    if mag <= 1.0:
        return 0.5 + (mag - 0.5) / 0.5 * 0.3
    if mag <= 1.5:
        return 0.8 + (mag - 1.0) / 0.5 * 0.2
    return 1.0


def _score_trend_persistence(
    trend_10d: float | None,
    trend_20d: float | None,
) -> float:
    """
    0–30 pts from 10D/20D log-regression daily trends.

    Both strongly negative → high persistence risk.
    10D positive while 20D still negative → early recovery (cut 20D weight).
    10D more negative than 20D → worsening (interaction bonus).
    """
    p10 = _slope_risk_unit(trend_10d) * 12.0
    p20 = _slope_risk_unit(trend_20d) * 12.0
    interact = 0.0

    if trend_10d is not None and trend_20d is not None:
        if trend_10d < 0 and trend_20d < 0:
            # Persistent dual downtrend
            interact += _clamp(min(-trend_10d, -trend_20d) / 1.0 * 4.0, 0.0, 4.0)
            if trend_10d < trend_20d:
                # Recent window steeper → worsening
                interact += _clamp((trend_20d - trend_10d) / 0.5 * 2.0, 0.0, 2.0)
            # Strong 20D hangover while 10D still down (e.g. CHRW-style collapse memory)
            if trend_20d <= -0.75:
                interact += _clamp((-trend_20d - 0.5) / 1.0 * 8.0, 0.0, 8.0)
        elif trend_10d >= 0 and trend_20d < 0:
            # Short-term recovery against older decline
            p20 *= 0.35
        elif trend_10d < 0 and trend_20d >= 0:
            # Fresh breakdown vs older uptrend
            interact += _clamp((-trend_10d) / 0.8 * 2.0, 0.0, 2.0)
    elif trend_10d is None and trend_20d is not None:
        p20 = _slope_risk_unit(trend_20d) * 18.0  # scale sole available leg
    elif trend_20d is None and trend_10d is not None:
        p10 = _slope_risk_unit(trend_10d) * 18.0

    return _clamp(p10 + p20 + interact, 0.0, 30.0)


def _accel_label(accel: float, recent_vel: float) -> str:
    pts = _score_acceleration(accel, recent_vel)
    if pts >= 4.5:
        return "HIGH"
    if pts >= 2.0:
        return "MODERATE"
    if pts > 0:
        return "MILD"
    return "NONE"


def compute_knife_risk(
    *,
    mom: dict[str, Any] | None,
    sector: str | None,
    bench: dict[str, float | None] | None,
    rvol: float | None = None,
    change_pct: float | None = None,
) -> dict[str, Any]:
    """
    Build Knife Risk payload:
      Speed 35 + Relative 35 + Trend Persistence 30 (+ tiny optional volume).
    Does not use 63D / rebound / SMA distance / financial / news.
    """
    if not mom:
        return {
            "score": None,
            "level": None,
            "partial": True,
            "coverage": "none",
            "auto_blocked": False,
            "detail": "Knife Risk unavailable - need >=6 daily closes",
            "components": {},
        }

    ret_5d = float(mom["ret_5d"])
    ret_3d = float(mom["ret_3d"])
    ret_1d = float(mom["ret_1d"])
    down_days = int(mom["down_days"])
    accel = float(mom["accel"])
    recent_vel = float(mom["recent_vel"])
    prev_vel = float(mom["prev_vel"])
    trend_10d = mom.get("trend_10d")
    trend_20d = mom.get("trend_20d")
    try:
        trend_10d = float(trend_10d) if trend_10d is not None else None
    except (TypeError, ValueError):
        trend_10d = None
    try:
        trend_20d = float(trend_20d) if trend_20d is not None else None
    except (TypeError, ValueError):
        trend_20d = None

    speed = (
        _score_5d_decline(ret_5d)
        + _score_3d_decline(ret_3d)
        + _score_down_days(down_days, ret_5d)
        + _score_acceleration(accel, recent_vel)
    )
    speed = _clamp(speed, 0.0, 35.0)

    trend_pts = _score_trend_persistence(trend_10d, trend_20d)

    bench = bench or {}
    spy_5d = bench.get(MARKET_ETF)
    etf = sector_etf(sector)
    sector_5d = bench.get(etf) if etf else None

    rel_spy = (ret_5d - float(spy_5d)) if spy_5d is not None else None
    rel_sector = (ret_5d - float(sector_5d)) if sector_5d is not None else None

    rel_pts = 0.0
    coverage = "full"
    if rel_spy is not None and rel_sector is not None:
        rel_pts = _score_rel(rel_spy, max_pts=20.0) + _score_rel(rel_sector, max_pts=15.0)
    elif rel_spy is not None:
        rel_pts = _score_rel(rel_spy, max_pts=20.0) * (35.0 / 20.0)
        coverage = "market_only"
    elif rel_sector is not None:
        rel_pts = _score_rel(rel_sector, max_pts=15.0) * (35.0 / 15.0)
        coverage = "sector_only"
    else:
        coverage = "speed_trend_only"
        # No benchmarks: scale speed+trend toward 100 (do not pretend low risk)
        raw_core = speed + trend_pts  # max 65
        score = int(round(_clamp(raw_core * (100.0 / 65.0), 0.0, 100.0)))
        level = knife_level(score)
        blocked = knife_auto_blocked(score)
        detail = _format_detail(
            score=score,
            level=level,
            mom=mom,
            spy_5d=None,
            rel_spy=None,
            etf=etf,
            sector_5d=None,
            rel_sector=None,
            coverage=coverage,
            blocked=blocked,
            speed=speed,
            rel_pts=0.0,
            trend_pts=trend_pts,
            vol_adj=0.0,
        )
        return {
            "score": score,
            "level": level,
            "partial": True,
            "coverage": coverage,
            "auto_blocked": blocked,
            "detail": detail,
            "components": {
                "speed": round(speed, 1),
                "relative": 0.0,
                "trend": round(trend_pts, 1),
                "volume": 0.0,
                "ret_1d": ret_1d,
                "ret_3d": ret_3d,
                "ret_5d": ret_5d,
                "down_days": down_days,
                "accel": accel,
                "accel_label": _accel_label(accel, recent_vel),
                "trend_10d": trend_10d,
                "trend_20d": trend_20d,
                "spy_5d": None,
                "rel_spy": None,
                "sector_etf": etf,
                "sector_5d": None,
                "rel_sector": None,
            },
        }

    rel_pts = _clamp(rel_pts, 0.0, 35.0)

    vol_adj = 0.0
    if (
        rvol is not None
        and change_pct is not None
        and float(change_pct) < -1.0
        and float(rvol) >= 1.5
    ):
        vol_adj = _clamp((float(rvol) - 1.5) / 1.5 * 4.0, 0.0, 4.0)

    raw = speed + rel_pts + trend_pts + vol_adj
    score = int(round(_clamp(raw, 0.0, 100.0)))
    level = knife_level(score)
    blocked = knife_auto_blocked(score)
    partial = coverage != "full" or trend_10d is None or trend_20d is None
    if trend_10d is None or trend_20d is None:
        if coverage == "full":
            coverage = "partial_trend"

    detail = _format_detail(
        score=score,
        level=level,
        mom=mom,
        spy_5d=spy_5d,
        rel_spy=rel_spy,
        etf=etf,
        sector_5d=sector_5d,
        rel_sector=rel_sector,
        coverage=coverage,
        blocked=blocked,
        speed=speed,
        rel_pts=rel_pts,
        trend_pts=trend_pts,
        vol_adj=vol_adj,
    )
    return {
        "score": score,
        "level": level,
        "partial": partial,
        "coverage": coverage,
        "auto_blocked": blocked,
        "detail": detail,
        "components": {
            "speed": round(speed, 1),
            "relative": round(rel_pts, 1),
            "trend": round(trend_pts, 1),
            "volume": round(vol_adj, 1),
            "ret_1d": ret_1d,
            "ret_3d": ret_3d,
            "ret_5d": ret_5d,
            "down_days": down_days,
            "accel": accel,
            "accel_label": _accel_label(accel, recent_vel),
            "recent_vel": recent_vel,
            "prev_vel": prev_vel,
            "trend_10d": trend_10d,
            "trend_20d": trend_20d,
            "spy_5d": None if spy_5d is None else round(float(spy_5d), 2),
            "rel_spy": None if rel_spy is None else round(float(rel_spy), 2),
            "sector_etf": etf,
            "sector_5d": None if sector_5d is None else round(float(sector_5d), 2),
            "rel_sector": None if rel_sector is None else round(float(rel_sector), 2),
        },
    }


def _format_detail(
    *,
    score: int,
    level: str | None,
    mom: dict[str, Any],
    spy_5d: float | None,
    rel_spy: float | None,
    etf: str | None,
    sector_5d: float | None,
    rel_sector: float | None,
    coverage: str,
    blocked: bool,
    speed: float,
    rel_pts: float,
    trend_pts: float,
    vol_adj: float,
) -> str:
    t10 = mom.get("trend_10d")
    t20 = mom.get("trend_20d")
    lines = [
        f"Knife Risk {score}" + (f" {level}" if level else ""),
        f"1D Return: {mom['ret_1d']:+.2f}%",
        f"3D Return: {mom['ret_3d']:+.2f}%",
        f"5D Return: {mom['ret_5d']:+.2f}%",
        (
            f"10D Trend: {float(t10):+.3f}%/day"
            if t10 is not None
            else "10D Trend: unavailable"
        ),
        (
            f"20D Trend: {float(t20):+.3f}%/day"
            if t20 is not None
            else "20D Trend: unavailable"
        ),
        f"Consecutive Down Days: {mom['down_days']}",
        f"Downside Acceleration: {_accel_label(float(mom['accel']), float(mom['recent_vel']))}"
        f" (dVel {mom['accel']:+.2f}%/d)",
    ]
    if spy_5d is not None:
        lines.append(f"SPY 5D: {spy_5d:+.2f}%")
    else:
        lines.append("SPY 5D: unavailable")
    if rel_spy is not None:
        lines.append(f"Relative vs SPY: {rel_spy:+.2f}%")
    if etf:
        lines.append(f"Sector ETF: {etf}")
        if sector_5d is not None:
            lines.append(f"Sector 5D: {sector_5d:+.2f}%")
        else:
            lines.append("Sector 5D: unavailable")
        if rel_sector is not None:
            lines.append(f"Relative vs Sector: {rel_sector:+.2f}%")
    else:
        lines.append("Sector ETF: unmapped")
    lines.append(f"Speed: {speed:.1f}/35")
    lines.append(f"Relative Weakness: {rel_pts:.1f}/35")
    lines.append(f"Trend Persistence: {trend_pts:.1f}/30")
    if vol_adj:
        lines.append(f"Volume confirm: +{vol_adj:.1f}")
    lines.append(f"Final Knife Risk: {score}")
    if coverage == "market_only":
        lines.append("Coverage: Market only (sector missing)")
    elif coverage == "sector_only":
        lines.append("Coverage: Sector only (SPY missing)")
    elif coverage == "speed_trend_only":
        lines.append("Coverage: Speed+Trend only (benchmarks missing) - score scaled")
    elif coverage == "partial_trend":
        lines.append("Coverage: Partial trend (need more daily bars for 10D/20D)")
    lines.append(
        "AUTO TRADE: BLOCKED" if blocked else "AUTO TRADE: eligible (if other gates pass)"
    )
    lines.append(
        "Note: Knife Risk != oversold. Ignores 63D / SMA distance / Financial / News."
    )
    return "\n".join(lines)


def _yahoo_closes(ticker: str, *, min_n: int = 6) -> list[float]:
    """Light Yahoo fallback when daily_bars are thin (1mo history)."""
    try:
        hist = yf.Ticker(ticker).history(period="1mo", auto_adjust=True)
        if hist is None or hist.empty or "Close" not in hist.columns:
            return []
        series = [float(x) for x in hist["Close"].dropna().tolist()]
        return series if len(series) >= min_n else []
    except Exception:
        return []


def attach_knife_risk(
    rows: list[dict[str, Any]],
    *,
    ensure_bench: bool = True,
) -> None:
    """
    Mutate rows in-place: set row['knife'] payload.
    Batch-loads daily_bars (45d window for 20D slope) + shared benchmark cache.
    Per-row errors never wipe the whole column.
    """
    tickers = [
        (r.get("ticker") or "").strip().upper()
        for r in rows
        if r.get("ticker") and not r.get("not_found")
    ]
    tickers = [t for t in tickers if t]
    try:
        closes_map = _load_knife_closes(set(tickers)) if tickers else {}
    except Exception:
        closes_map = {}
    try:
        bench = (
            ensure_benchmark_returns(force=False)
            if ensure_bench
            else get_benchmark_returns_cached()
        )
    except Exception:
        bench = {MARKET_ETF: None}

    # Yahoo fallback when bars < 21 (need 20D trend); cap to keep page fast.
    missing = [t for t in tickers if len(closes_map.get(t) or []) < 21][:40]
    for t in missing:
        series = _yahoo_closes(t)
        if len(series) > len(closes_map.get(t) or []):
            closes_map[t] = series

    for r in rows:
        t = (r.get("ticker") or "").strip().upper()
        if not t or r.get("not_found"):
            r["knife"] = None
            continue
        try:
            mom = momentum_from_closes(closes_map.get(t) or [])
            r["knife"] = compute_knife_risk(
                mom=mom,
                sector=r.get("sector"),
                bench=bench,
                rvol=r.get("rvol"),
                change_pct=r.get("change_pct"),
            )
        except Exception:
            r["knife"] = {
                "score": None,
                "level": None,
                "partial": True,
                "coverage": "error",
                "auto_blocked": False,
                "detail": "Knife Risk calculation error",
                "components": {},
            }


def compute_knife_risk_from_returns(
    *,
    ret_5d: float,
    ret_3d: float = 0.0,
    ret_1d: float = 0.0,
    down_days: int = 0,
    accel: float = 0.0,
    recent_vel: float | None = None,
    spy_5d: float | None = None,
    sector_5d: float | None = None,
    sector_etf_sym: str | None = None,
    trend_10d: float | None = None,
    trend_20d: float | None = None,
) -> dict[str, Any]:
    """Test helper: score from explicit returns / trends (cases A–D)."""
    rv = recent_vel if recent_vel is not None else (ret_1d if ret_1d < 0 else -0.5)
    mom = {
        "ret_1d": ret_1d,
        "ret_3d": ret_3d,
        "ret_5d": ret_5d,
        "down_days": down_days,
        "recent_vel": rv,
        "prev_vel": rv - accel,
        "accel": accel,
        "trend_10d": trend_10d,
        "trend_20d": trend_20d,
    }
    bench: dict[str, float | None] = {MARKET_ETF: spy_5d}
    if sector_etf_sym and sector_5d is not None:
        bench[sector_etf_sym] = sector_5d
    return compute_knife_risk(
        mom=mom,
        sector="Industrials" if sector_etf_sym == "XLI" else None,
        bench=bench,
    )
