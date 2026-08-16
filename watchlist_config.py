"""Shared Watchlist ticker list — used by Flask UI and update_jobs (no Flask import)."""

from __future__ import annotations

import re
from typing import Iterable

# Long-term saved names (until per-user accounts exist).
MY_WATCHLIST = [
    "TSLA",
    "AAPL",
    "IBM",
    "DVA",
    "GOOG",
    "INTU",
    "DELL",
    "XBI",
    "JEPI",
    "DGRO",
    "NVDA",
]

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")


def validate_ticker_token(ticker: str) -> bool:
    t = (ticker or "").strip().upper()
    return bool(t and _TICKER_RE.match(t))


def collect_watchlist_tickers(temp_tickers: Iterable[str] | None = None) -> list[str]:
    """
    Setup (超卖/强势回调, dist < -10%) ∪ mine ∪ optional temp.
    Safe to call from CLI jobs (does not import Flask).
    """
    seen: set[str] = set()
    out: list[str] = []
    pools: list[str] = []
    try:
        from db import list_setup

        pools.extend(r["ticker"] for r in list_setup(-10.0) if r.get("ticker"))
    except Exception:
        pass
    pools.extend(MY_WATCHLIST)
    if temp_tickers:
        pools.extend(temp_tickers)
    for t in pools:
        u = (t or "").strip().upper()
        if u and u not in seen and validate_ticker_token(u):
            seen.add(u)
            out.append(u)
    return out
