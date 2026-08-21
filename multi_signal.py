"""
Multi-Signal / 多重信号 — aggregation over existing system Watchlist groups.

V1 Match Count uses four current-condition screens only:
  Oversold Pullback, Target Ratio <80%, 63D Position <25%, Rising Now.

Strong Watchlist is shown as a separate indicator (not in Match Count).
No retention — membership is dynamic from current group lists.
"""

from __future__ import annotations

from typing import Any, Iterable

from db import get_conn, init_db
from rising_now import rising_metrics_for_tickers

MULTI_SIGNAL_MIN_MATCH = 2
MULTI_SIGNAL_TOTAL = 4


def _ticker_set(rows: Iterable[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for r in rows:
        t = (r.get("ticker") or "").strip().upper()
        if t:
            out.add(t)
    return out


def _row_index(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for r in rows:
        t = (r.get("ticker") or "").strip().upper()
        if t and t not in idx:
            idx[t] = r
    return idx


def active_strong_symbols() -> set[str]:
    """Symbols currently on Strong Watchlist (membership table = active only)."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute("SELECT symbol FROM strong_membership").fetchall()
    return {(r["symbol"] or "").upper() for r in rows if r["symbol"]}


def multi_signal_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Observation counts for Multi-Signal header (does not filter)."""
    n2 = n3 = n4 = 0
    low_rising = oversold_rising = target_rising = strong_rising = 0
    for r in rows:
        m = int(r.get("match_count") or 0)
        if m >= 2:
            n2 += 1
        if m >= 3:
            n3 += 1
        if m >= 4:
            n4 += 1
        rising = bool(r.get("in_rising"))
        if rising and r.get("in_low_63d"):
            low_rising += 1
        if rising and r.get("in_oversold"):
            oversold_rising += 1
        if rising and r.get("in_target"):
            target_rising += 1
        if rising and r.get("in_strong"):
            strong_rising += 1
    return {
        "match_ge_2": n2,
        "match_ge_3": n3,
        "match_eq_4": n4,
        "low_rising": low_rising,
        "oversold_rising": oversold_rising,
        "target_rising": target_rising,
        "strong_rising": strong_rising,
    }


def build_multi_signal(
    setup: list[dict[str, Any]],
    low_target: list[dict[str, Any]],
    low_63d: list[dict[str, Any]],
    rising: list[dict[str, Any]],
    *,
    min_match: int = MULTI_SIGNAL_MIN_MATCH,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Aggregate current membership from the four system lists.

    Returns (rows with match >= min_match, summary counts over those rows).
    Does not modify source group definitions.
    """
    oversold = _ticker_set(setup)
    target = _ticker_set(low_target)
    low63 = _ticker_set(low_63d)
    rising_set = _ticker_set(rising)

    by_ticker: dict[str, dict[str, Any]] = {}
    for src in (setup, low_target, low_63d, rising):
        for t, row in _row_index(src).items():
            if t not in by_ticker:
                by_ticker[t] = dict(row)

    strong = active_strong_symbols()
    rising_idx = _row_index(rising)

    # First pass: match counts; second pass attaches 5D metrics for non-rising hits.
    candidates: list[tuple[str, dict[str, Any], int, bool, bool, bool, bool]] = []
    for ticker, base in by_ticker.items():
        in_oversold = ticker in oversold
        in_target = ticker in target
        in_low = ticker in low63
        in_rising = ticker in rising_set
        match = int(in_oversold) + int(in_target) + int(in_low) + int(in_rising)
        if match < min_match:
            continue
        candidates.append(
            (ticker, base, match, in_oversold, in_target, in_low, in_rising)
        )

    need_metrics = {t for t, _b, _m, _o, _tg, _l, rising_f in candidates if not rising_f}
    metrics = rising_metrics_for_tickers(need_metrics) if need_metrics else {}

    out: list[dict[str, Any]] = []
    for ticker, base, match, in_oversold, in_target, in_low, in_rising in candidates:
        row = dict(base)
        row["ticker"] = ticker
        row["in_oversold"] = in_oversold
        row["in_target"] = in_target
        row["in_low_63d"] = in_low
        row["in_rising"] = in_rising
        row["in_strong"] = ticker in strong
        row["match_count"] = match
        row["match_label"] = f"{match}/{MULTI_SIGNAL_TOTAL}"

        rr = rising_idx.get(ticker) or {}
        met = metrics.get(ticker) or {}
        row["up_days_5"] = rr.get("up_days_5", met.get("up_days_5"))
        row["return_5d_pct"] = rr.get("return_5d_pct", met.get("return_5d_pct"))
        out.append(row)

    out.sort(
        key=lambda r: (
            -(int(r.get("match_count") or 0)),
            0 if r.get("in_rising") else 1,
            -(
                r.get("return_5d_pct")
                if r.get("return_5d_pct") is not None
                else float("-inf")
            ),
            r.get("ticker") or "",
        )
    )
    summary = multi_signal_summary(out)
    return out, summary
