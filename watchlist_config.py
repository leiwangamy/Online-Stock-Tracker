"""Shared Watchlist ticker list — used by Flask UI and update_jobs (no Flask import)."""

from __future__ import annotations

import re
from typing import Iterable

# Seed list when DB setting `my_watchlist` is empty (first run).
DEFAULT_MY_WATCHLIST = [
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

# Backward-compatible name: prefer get_my_watchlist() for live data.
MY_WATCHLIST = DEFAULT_MY_WATCHLIST

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")


def validate_ticker_token(ticker: str) -> bool:
    t = (ticker or "").strip().upper()
    return bool(t and _TICKER_RE.match(t))


def get_my_watchlist() -> list[str]:
    """Persisted 我的自选 from settings; seeds DEFAULT on first use."""
    from db import get_setting, set_setting

    raw = get_setting("my_watchlist", None)
    if not isinstance(raw, list) or not raw:
        set_setting("my_watchlist", list(DEFAULT_MY_WATCHLIST))
        return list(DEFAULT_MY_WATCHLIST)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        u = (str(item) if item is not None else "").strip().upper()
        if u and u not in seen and validate_ticker_token(u):
            seen.add(u)
            out.append(u)
    if not out:
        set_setting("my_watchlist", list(DEFAULT_MY_WATCHLIST))
        return list(DEFAULT_MY_WATCHLIST)
    return out


def set_my_watchlist(tickers: Iterable[str]) -> list[str]:
    """Replace persisted 我的自选. Returns cleaned list."""
    from db import set_setting

    out: list[str] = []
    seen: set[str] = set()
    for item in tickers:
        u = (str(item) if item is not None else "").strip().upper()
        if u and u not in seen and validate_ticker_token(u):
            seen.add(u)
            out.append(u)
    set_setting("my_watchlist", out)
    return out


def add_my_watchlist_ticker(ticker: str) -> list[str]:
    t = (ticker or "").strip().upper()
    if not validate_ticker_token(t):
        raise ValueError("invalid ticker")
    cur = get_my_watchlist()
    if t not in cur:
        cur.append(t)
    return set_my_watchlist(cur)


def remove_my_watchlist_ticker(ticker: str) -> list[str]:
    t = (ticker or "").strip().upper()
    cur = [x for x in get_my_watchlist() if x != t]
    return set_my_watchlist(cur)


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
    try:
        pools.extend(get_my_watchlist())
    except Exception:
        pools.extend(DEFAULT_MY_WATCHLIST)
    if temp_tickers:
        pools.extend(temp_tickers)
    for t in pools:
        u = (t or "").strip().upper()
        if u and u not in seen and validate_ticker_token(u):
            seen.add(u)
            out.append(u)
    return out
