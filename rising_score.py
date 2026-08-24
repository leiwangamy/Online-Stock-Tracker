"""
Rising Score — upward strength & persistence (0–100).

Independent of Knife Risk and AI Score.
Mirrors Knife Risk's structure on the *upside* (not 100 − Knife).

Entry to Rising Now stays weak (Up Days ≥ 3/5 · 5D Return ≥ +3%).
Rising Score then ranks how strong / persistent that rise is, emphasizing
10D / 20D / 63D (5D already used for discovery — kept light here).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from knife_risk import (
    MARKET_ETF,
    _clamp,
    _daily_returns,
    ensure_benchmark_returns,
    get_benchmark_returns_cached,
    log_regression_daily_pct,
    sector_etf,
)

# Strong Up Day: daily return at or above this % (20D count component).
STRONG_UP_DAY_PCT = 1.5


def rising_level(score: int | None) -> str | None:
    """Display band (comparable thresholds to Knife levels, upside meaning)."""
    if score is None:
        return None
    if score >= 70:
        return "HOT"
    if score >= 45:
        return "STRONG"
    if score >= 25:
        return "FIRM"
    return "MILD"


def _load_rising_closes(tickers: set[str]) -> dict[str, list[float]]:
    """
    daily_bars for ~100 calendar days (≥63 trading sessions when available).
    Same local history Knife uses — no separate Yahoo download by default.
    """
    from db import get_conn, init_db

    if not tickers:
        return {}
    init_db()
    pad_days = 120
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
        from knife_risk import _load_knife_closes

        return _load_knife_closes(tickers)


def upward_momentum_from_closes(closes: list[float]) -> dict[str, Any] | None:
    """
    Upside twin of knife_risk.momentum_from_closes.

    Adds 20D Strong Up Count. Needs ≥6 closes; 10D/20D/63D fill when available.
    """
    if len(closes) < 6:
        return None
    window = [float(x) for x in closes[-6:]]
    if any(c <= 0 for c in window):
        return None
    rets = _daily_returns(window)
    if len(rets) < 5:
        return None

    ret_1d = rets[-1]
    ret_3d = (window[-1] / window[-4] - 1.0) * 100.0
    ret_5d = (window[-1] / window[0] - 1.0) * 100.0

    # Consecutive up days from the newest bar backward.
    up_days = 0
    for r in reversed(rets):
        if r > 0:
            up_days += 1
        else:
            break

    recent_vel = (rets[-2] + rets[-1]) / 2.0
    prev_vel = (rets[0] + rets[1] + rets[2]) / 3.0
    # Positive accel = recent upside velocity stronger than earlier window.
    accel = recent_vel - prev_vel

    trend_10d = log_regression_daily_pct(closes, 10)
    trend_20d = log_regression_daily_pct(closes, 20)

    # 20D Strong Up Count (days with daily return >= STRONG_UP_DAY_PCT).
    strong_up_20 = None
    if len(closes) >= 21:
        rets20 = _daily_returns([float(x) for x in closes[-21:]])
        if len(rets20) >= 20:
            strong_up_20 = sum(1 for r in rets20[-20:] if r >= STRONG_UP_DAY_PCT)

    # 63D position from closes when enough history.
    pos_63d = None
    if len(closes) >= 63:
        w63 = [float(x) for x in closes[-63:]]
        lo, hi = min(w63), max(w63)
        if hi > lo:
            pos_63d = (w63[-1] - lo) / (hi - lo) * 100.0

    return {
        "ret_1d": round(ret_1d, 2),
        "ret_3d": round(ret_3d, 2),
        "ret_5d": round(ret_5d, 2),
        "up_days": up_days,
        "recent_vel": round(recent_vel, 2),
        "prev_vel": round(prev_vel, 2),
        "accel": round(accel, 2),
        "trend_10d": None if trend_10d is None else round(trend_10d, 3),
        "trend_20d": None if trend_20d is None else round(trend_20d, 3),
        "strong_up_20": strong_up_20,
        "pos_63d": None if pos_63d is None else round(pos_63d, 2),
    }


# ---------------------------------------------------------------------------
# Component scorers (upside mirrors of Knife; 5D kept light)
# ---------------------------------------------------------------------------

def _slope_strength_unit(daily_pct: float | None) -> float:
    """Map positive daily % trend to 0..1 strength unit (mirror of knife risk unit)."""
    if daily_pct is None or daily_pct <= 0:
        return 0.0
    mag = float(daily_pct)
    if mag <= 0.25:
        return mag / 0.25 * 0.25
    if mag <= 0.5:
        return 0.25 + (mag - 0.25) / 0.25 * 0.25
    if mag <= 1.0:
        return 0.5 + (mag - 0.5) / 0.5 * 0.3
    if mag <= 1.5:
        return 0.8 + (mag - 1.0) / 0.5 * 0.2
    return 1.0


def _score_5d_rise(ret_5d: float) -> float:
    """0–6 pts — light (5D already gates Rising Now)."""
    if ret_5d <= 2.0:
        return _clamp(ret_5d / 2.0 * 1.5, 0.0, 1.5)
    if ret_5d <= 10.0:
        return 1.5 + (ret_5d - 2.0) / 8.0 * 4.5
    return 6.0


def _score_3d_rise(ret_3d: float) -> float:
    """0–3 pts."""
    if ret_3d <= 1.0:
        return 0.0
    if ret_3d <= 6.0:
        return (ret_3d - 1.0) / 5.0 * 3.0
    return 3.0


def _score_up_days(up_days: int, ret_5d: float) -> float:
    """0–4 pts — consecutive up days, muted if rise is tiny."""
    if up_days <= 1:
        base = 0.0
    elif up_days == 2:
        base = 1.0
    elif up_days == 3:
        base = 2.0
    elif up_days == 4:
        base = 3.0
    else:
        base = 4.0
    mag = _clamp(ret_5d / 6.0, 0.25, 1.0) if ret_5d > 0 else 0.0
    return base * mag


def _score_up_acceleration(accel: float, recent_vel: float) -> float:
    """0–4 pts when recent upside velocity beats the prior window."""
    if recent_vel <= 0.2:
        return 0.0
    if accel <= 0.3:
        return 0.0
    return _clamp((accel - 0.3) / 1.7 * 4.0, 0.0, 4.0)


def _score_rel_strength(rel: float, *, max_pts: float) -> float:
    """Stock − bench. More positive → higher Rising Score."""
    if rel <= 1.0:
        return 0.0
    if rel <= 10.0:
        return (rel - 1.0) / 9.0 * max_pts
    return max_pts


def _score_uptrend_persistence(
    trend_10d: float | None,
    trend_20d: float | None,
) -> float:
    """
    0–35 pts from 10D/20D log-regression (primary Rising Score engine).

    Mirror of knife _score_trend_persistence with signs flipped.
    """
    p10 = _slope_strength_unit(trend_10d) * 14.0
    p20 = _slope_strength_unit(trend_20d) * 14.0
    interact = 0.0

    if trend_10d is not None and trend_20d is not None:
        if trend_10d > 0 and trend_20d > 0:
            interact += _clamp(min(trend_10d, trend_20d) / 1.0 * 4.0, 0.0, 4.0)
            if trend_10d > trend_20d:
                # Recent window steeper → accelerating up
                interact += _clamp((trend_10d - trend_20d) / 0.5 * 2.0, 0.0, 2.0)
            if trend_20d >= 0.75:
                interact += _clamp((trend_20d - 0.5) / 1.0 * 8.0, 0.0, 8.0)
        elif trend_10d > 0 and trend_20d <= 0:
            # Fresh breakout vs older weakness — keep 10D, damp 20D hangover
            p20 *= 0.25
            interact += _clamp(trend_10d / 0.8 * 2.0, 0.0, 2.0)
        elif trend_10d <= 0 and trend_20d > 0:
            # Longer uptrend cooling off — keep partial 20D
            p10 *= 0.35
    elif trend_10d is None and trend_20d is not None:
        p20 = _slope_strength_unit(trend_20d) * 22.0
    elif trend_20d is None and trend_10d is not None:
        p10 = _slope_strength_unit(trend_10d) * 22.0

    return _clamp(p10 + p20 + interact, 0.0, 35.0)


def _score_strong_up_count(n: int | None) -> float:
    """0–12 pts from 20D Strong Up Day count."""
    if n is None or n <= 0:
        return 0.0
    if n <= 2:
        return n / 2.0 * 3.0
    if n <= 5:
        return 3.0 + (n - 2) / 3.0 * 5.0
    if n <= 8:
        return 8.0 + (n - 5) / 3.0 * 3.0
    return 12.0


def _score_63d_position(pos: float | None) -> float:
    """0–18 pts — high 63D position (mirror of knife caring about low position)."""
    if pos is None:
        return 0.0
    if pos < 40.0:
        return 0.0
    if pos < 70.0:
        return (pos - 40.0) / 30.0 * 8.0
    if pos < 90.0:
        return 8.0 + (pos - 70.0) / 20.0 * 7.0
    return 15.0 + _clamp((pos - 90.0) / 10.0 * 3.0, 0.0, 3.0)


def compute_rising_score(
    *,
    mom: dict[str, Any] | None,
    sector: str | None,
    bench: dict[str, float | None] | None,
    range_63d_pos: float | None = None,
) -> dict[str, Any]:
    """
    Rising Score payload:
      Upside Speed 17 + Relative 18 + Trend 35 + StrongUp20 12 + 63D 18
    Independent of Knife Risk (never 100 − Knife).
    """
    if not mom:
        return {
            "score": None,
            "level": None,
            "partial": True,
            "coverage": "none",
            "detail": "Rising Score unavailable - need >=6 daily closes",
            "components": {},
        }

    ret_5d = float(mom["ret_5d"])
    ret_3d = float(mom["ret_3d"])
    up_days = int(mom["up_days"])
    accel = float(mom["accel"])
    recent_vel = float(mom["recent_vel"])
    trend_10d = mom.get("trend_10d")
    trend_20d = mom.get("trend_20d")
    strong_up_20 = mom.get("strong_up_20")
    try:
        trend_10d = float(trend_10d) if trend_10d is not None else None
    except (TypeError, ValueError):
        trend_10d = None
    try:
        trend_20d = float(trend_20d) if trend_20d is not None else None
    except (TypeError, ValueError):
        trend_20d = None
    try:
        strong_up_20 = int(strong_up_20) if strong_up_20 is not None else None
    except (TypeError, ValueError):
        strong_up_20 = None

    pos_63d = range_63d_pos
    if pos_63d is None:
        pos_63d = mom.get("pos_63d")
    try:
        pos_63d = float(pos_63d) if pos_63d is not None else None
    except (TypeError, ValueError):
        pos_63d = None

    speed = _clamp(
        _score_5d_rise(ret_5d)
        + _score_3d_rise(ret_3d)
        + _score_up_days(up_days, ret_5d)
        + _score_up_acceleration(accel, recent_vel),
        0.0,
        17.0,
    )
    trend_pts = _score_uptrend_persistence(trend_10d, trend_20d)
    strong_pts = _score_strong_up_count(strong_up_20)
    pos_pts = _score_63d_position(pos_63d)

    bench = bench or {}
    spy_5d = bench.get(MARKET_ETF)
    etf = sector_etf(sector)
    sector_5d = bench.get(etf) if etf else None
    rel_spy = (ret_5d - float(spy_5d)) if spy_5d is not None else None
    rel_sector = (ret_5d - float(sector_5d)) if sector_5d is not None else None

    coverage = "full"
    if rel_spy is not None and rel_sector is not None:
        rel_pts = _score_rel_strength(rel_spy, max_pts=10.0) + _score_rel_strength(
            rel_sector, max_pts=8.0
        )
    elif rel_spy is not None:
        rel_pts = _score_rel_strength(rel_spy, max_pts=10.0) * (18.0 / 10.0)
        coverage = "market_only"
    elif rel_sector is not None:
        rel_pts = _score_rel_strength(rel_sector, max_pts=8.0) * (18.0 / 8.0)
        coverage = "sector_only"
    else:
        rel_pts = 0.0
        coverage = "core_only"

    rel_pts = _clamp(rel_pts, 0.0, 18.0)
    raw = speed + rel_pts + trend_pts + strong_pts + pos_pts  # max ≈ 100
    # If missing 63D / strong-up / benches, scale remaining core toward 100.
    if coverage == "core_only" and pos_pts <= 0 and strong_pts <= 0:
        core = speed + trend_pts  # max 52
        score = int(round(_clamp(core * (100.0 / 52.0), 0.0, 100.0)))
        partial = True
    elif pos_pts <= 0 or strong_pts <= 0 or coverage != "full":
        score = int(round(_clamp(raw, 0.0, 100.0)))
        partial = True
        if coverage == "full":
            coverage = "partial_history"
    else:
        score = int(round(_clamp(raw, 0.0, 100.0)))
        partial = False

    level = rising_level(score)
    detail = _format_rising_detail(
        score=score,
        level=level,
        mom=mom,
        pos_63d=pos_63d,
        strong_up_20=strong_up_20,
        coverage=coverage,
        speed=speed,
        rel_pts=rel_pts,
        trend_pts=trend_pts,
        strong_pts=strong_pts,
        pos_pts=pos_pts,
        rel_spy=rel_spy,
        rel_sector=rel_sector,
    )
    return {
        "score": score,
        "level": level,
        "partial": partial,
        "coverage": coverage,
        "detail": detail,
        "components": {
            "speed": round(speed, 2),
            "relative": round(rel_pts, 2),
            "trend": round(trend_pts, 2),
            "strong_up_20": round(strong_pts, 2),
            "pos_63d": round(pos_pts, 2),
            "ret_5d": ret_5d,
            "up_days": up_days,
            "trend_10d": trend_10d,
            "trend_20d": trend_20d,
            "strong_up_count": strong_up_20,
            "range_63d_pos": pos_63d,
        },
    }


def _format_rising_detail(
    *,
    score: int,
    level: str | None,
    mom: dict[str, Any],
    pos_63d: float | None,
    strong_up_20: int | None,
    coverage: str,
    speed: float,
    rel_pts: float,
    trend_pts: float,
    strong_pts: float,
    pos_pts: float,
    rel_spy: float | None,
    rel_sector: float | None,
) -> str:
    lines = [
        f"Rising Score {score}" + (f" {level}" if level else ""),
        (
            f"5D {mom.get('ret_5d')}% · UpDays {mom.get('up_days')} · "
            f"10D {mom.get('trend_10d')} · 20D {mom.get('trend_20d')}"
        ),
        (
            f"StrongUp20 {strong_up_20 if strong_up_20 is not None else '—'} · "
            f"63D Pos {pos_63d if pos_63d is not None else '—'}%"
        ),
        (
            f"Parts: speed {speed:.1f} + rel {rel_pts:.1f} + trend {trend_pts:.1f} "
            f"+ strongUp {strong_pts:.1f} + 63D {pos_pts:.1f}"
        ),
    ]
    if rel_spy is not None:
        lines.append(f"Rel SPY {rel_spy:+.1f}%")
    if rel_sector is not None:
        lines.append(f"Rel sector {rel_sector:+.1f}%")
    if coverage != "full":
        lines.append(f"Coverage: {coverage}")
    return "\n".join(lines)


def attach_rising_score(
    rows: list[dict[str, Any]],
    *,
    ensure_bench: bool = True,
) -> None:
    """Mutate rows in-place: set row['rising'] payload (score / level / detail)."""
    tickers = [
        (r.get("ticker") or "").strip().upper()
        for r in rows
        if r.get("ticker") and not r.get("not_found")
    ]
    tickers = [t for t in tickers if t]
    try:
        closes_map = _load_rising_closes(set(tickers)) if tickers else {}
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

    for r in rows:
        t = (r.get("ticker") or "").strip().upper()
        if not t or r.get("not_found"):
            r["rising"] = None
            continue
        try:
            mom = upward_momentum_from_closes(closes_map.get(t) or [])
            pos = r.get("range_63d_pos")
            try:
                pos = float(pos) if pos is not None else None
            except (TypeError, ValueError):
                pos = None
            r["rising"] = compute_rising_score(
                mom=mom,
                sector=r.get("sector"),
                bench=bench,
                range_63d_pos=pos,
            )
        except Exception:
            r["rising"] = {
                "score": None,
                "level": None,
                "partial": True,
                "coverage": "error",
                "detail": "Rising Score calculation error",
                "components": {},
            }
