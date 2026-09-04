"""
Recent regular-session daily moves (5D) + optional LIVE / extended-hours change.

Design:
- 5D uses completed daily closes (local daily_bars first; Yahoo history fallback).
- Extended-hours LIVE uses Yahoo quotes only (preMarket / postMarket).
- Never requires a broker terminal. Missing / invalid Yahoo extended price → LIVE = N/A.
- Optional local broker refresh paths elsewhere are untouched; this module has no broker import.
- Observational only — never a BLOCK or rank input.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

log = logging.getLogger("leibot.session_moves")

# Labels oldest → newest among the five completed sessions.
DAY_LABELS = ("D-4", "D-3", "D-2", "D-1", "D0")


def _daily_returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        a, b = float(closes[i - 1]), float(closes[i])
        if a <= 0 or b <= 0:
            return []
        out.append((b / a - 1.0) * 100.0)
    return out


def five_day_session_view(closes: list[float]) -> dict[str, Any] | None:
    """
    From ascending closes, build D-4..D0 daily % changes for the last 5
    completed regular sessions (needs ≥ 6 closes).
    """
    if len(closes) < 6:
        return None
    window = [float(x) for x in closes[-6:]]
    if any(c <= 0 for c in window):
        return None
    rets = _daily_returns(window)
    if len(rets) < 5:
        return None
    day_pcts = [round(float(r), 2) for r in rets[-5:]]
    total = round((window[-1] / window[0] - 1.0) * 100.0, 2)
    up_days = sum(1 for r in day_pcts if r > 0)
    down_days = sum(1 for r in day_pcts if r < 0)
    if up_days >= 4 or (total > 0.5 and down_days <= 1):
        direction = "UP"
    elif down_days >= 4 or (total < -0.5 and up_days <= 1):
        direction = "DOWN"
    else:
        direction = "MIXED"
    return {
        "day_pcts": day_pcts,  # [D-4, D-3, D-2, D-1, D0]
        "labels": list(DAY_LABELS),
        "total_5d": total,
        "up_days": up_days,
        "down_days": down_days,
        "direction": direction,
        "last_close": round(window[-1], 4),
    }


def _yahoo_daily_closes(ticker: str, *, min_n: int = 6) -> list[float]:
    """Yahoo regular-session daily closes (auto-adjusted)."""
    try:
        import yfinance as yf

        hist = yf.Ticker(ticker).history(period="1mo", auto_adjust=True)
        if hist is None or hist.empty or "Close" not in hist.columns:
            return []
        series = [float(x) for x in hist["Close"].dropna().tolist()]
        return series if len(series) >= min_n else []
    except Exception:
        return []


def _yahoo_extended_live_pct(ticker: str, *, last_close: float | None) -> dict[str, Any]:
    """
    LIVE % = (Yahoo extended-hours price / latest regular close − 1) × 100.

    Uses Yahoo only. Never fabricates. Returns pct=None → display N/A.
    """
    out: dict[str, Any] = {
        "live_pct": None,
        "live_price": None,
        "live_base": None,
        "live_source": None,
        "market_state": None,
    }
    t = (ticker or "").strip().upper()
    if not t:
        return out
    try:
        import yfinance as yf

        info = yf.Ticker(t).info or {}
    except Exception as exc:
        log.debug("Yahoo LIVE info failed for %s: %s", t, exc)
        return out

    state = str(info.get("marketState") or "").upper()
    out["market_state"] = state or None

    pre = info.get("preMarketPrice")
    post = info.get("postMarketPrice")
    reg = info.get("regularMarketPrice")
    prev = info.get("regularMarketPreviousClose")

    ext_px: float | None = None
    source: str | None = None
    if state in ("PRE", "PREPRE") and pre is not None:
        try:
            ext_px = float(pre)
            source = "yahoo_pre"
        except (TypeError, ValueError):
            ext_px = None
    elif state in ("POST", "POSTPOST") and post is not None:
        try:
            ext_px = float(post)
            source = "yahoo_post"
        except (TypeError, ValueError):
            ext_px = None
    else:
        # Closed / unknown: prefer post, then pre if present and differs from reg.
        for raw, src in ((post, "yahoo_post"), (pre, "yahoo_pre")):
            if raw is None:
                continue
            try:
                px = float(raw)
            except (TypeError, ValueError):
                continue
            if px > 0:
                ext_px = px
                source = src
                break

    if ext_px is None or ext_px <= 0:
        return out

    base: float | None = None
    if last_close is not None and float(last_close) > 0:
        base = float(last_close)
    elif reg is not None:
        try:
            base = float(reg)
        except (TypeError, ValueError):
            base = None
    if base is None or base <= 0:
        if prev is not None:
            try:
                base = float(prev)
            except (TypeError, ValueError):
                base = None
    if base is None or base <= 0:
        return out

    # Guard: if "extended" price equals regular close within a tick, treat as N/A.
    if abs(ext_px - base) / base < 1e-6:
        return out

    out["live_pct"] = round((ext_px / base - 1.0) * 100.0, 2)
    out["live_price"] = round(ext_px, 4)
    out["live_base"] = round(base, 4)
    out["live_source"] = source
    return out


def attach_session_moves(
    rows: list[dict[str, Any]],
    *,
    include_live: bool = True,
    live_max_workers: int = 8,
) -> None:
    """
    Mutate rows: set session_5d / live_* fields.

    5D: daily_bars → Yahoo daily history fallback.
    LIVE: Yahoo extended-hours only (optional; N/A when missing).
    """
    from rising_now import _load_recent_closes_by_ticker

    tickers = [
        (r.get("ticker") or "").strip().upper()
        for r in rows
        if r.get("ticker") and not r.get("not_found")
    ]
    tickers = [t for t in tickers if t]
    try:
        closes_map = _load_recent_closes_by_ticker(set(tickers)) if tickers else {}
    except Exception:
        closes_map = {}

    # Yahoo daily fallback when local bars are thin (same spirit as knife_risk).
    thin = [t for t in tickers if len(closes_map.get(t) or []) < 6][:60]
    for t in thin:
        series = _yahoo_daily_closes(t)
        if len(series) > len(closes_map.get(t) or []):
            closes_map[t] = series

    for r in rows:
        t = (r.get("ticker") or "").strip().upper()
        r["session_5d"] = None
        r["day_pcts_5"] = None
        r["ret_5d_total"] = None
        r["session_up_days"] = None
        r["session_down_days"] = None
        r["session_5d_direction"] = None
        r["live_pct"] = None
        r["live_price"] = None
        r["live_base"] = None
        r["live_source"] = None
        if not t or r.get("not_found"):
            continue
        view = five_day_session_view(closes_map.get(t) or [])
        if view:
            r["session_5d"] = view
            r["day_pcts_5"] = view["day_pcts"]
            r["ret_5d_total"] = view["total_5d"]
            r["session_up_days"] = view["up_days"]
            r["session_down_days"] = view["down_days"]
            r["session_5d_direction"] = view["direction"]

    if not include_live or not tickers:
        return

    def _one(t: str) -> tuple[str, dict[str, Any]]:
        last_close = None
        view = five_day_session_view(closes_map.get(t) or [])
        if view:
            last_close = view.get("last_close")
        elif closes_map.get(t):
            try:
                last_close = float(closes_map[t][-1])
            except (TypeError, ValueError, IndexError):
                last_close = None
        return t, _yahoo_extended_live_pct(t, last_close=last_close)

    live_by_t: dict[str, dict[str, Any]] = {}
    workers = max(1, min(int(live_max_workers), len(tickers)))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_one, t) for t in tickers]
            for fut in as_completed(futs):
                try:
                    t, payload = fut.result()
                    live_by_t[t] = payload
                except Exception:
                    continue
    except Exception:
        log.exception("Yahoo LIVE batch attach failed")

    for r in rows:
        t = (r.get("ticker") or "").strip().upper()
        payload = live_by_t.get(t) or {}
        r["live_pct"] = payload.get("live_pct")
        r["live_price"] = payload.get("live_price")
        r["live_base"] = payload.get("live_base")
        r["live_source"] = payload.get("live_source")
        r["live_market_state"] = payload.get("market_state")
