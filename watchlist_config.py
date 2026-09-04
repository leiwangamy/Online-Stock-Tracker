"""Shared Watchlist ticker list — used by Flask UI and update_jobs (no Flask import)."""

from __future__ import annotations

import re
from typing import Any, Iterable

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


# ── Trade Candidates (human flag for AI Trading Watchlist eligibility) ─────
# Independent of My Watchlist membership and of paper Priority / AI Score.


def get_trade_candidates() -> list[str]:
    """Manual Trade Candidate tickers (settings JSON list). Empty until set."""
    from db import get_setting

    raw = get_setting("trade_candidates", None)
    if not isinstance(raw, list) or not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        u = (str(item) if item is not None else "").strip().upper()
        if u and u not in seen and validate_ticker_token(u):
            seen.add(u)
            out.append(u)
    return out


def set_trade_candidates(tickers: Iterable[str]) -> list[str]:
    from db import set_setting

    out: list[str] = []
    seen: set[str] = set()
    for item in tickers:
        u = (str(item) if item is not None else "").strip().upper()
        if u and u not in seen and validate_ticker_token(u):
            seen.add(u)
            out.append(u)
    set_setting("trade_candidates", out)
    return out


def add_trade_candidate(ticker: str) -> list[str]:
    t = (ticker or "").strip().upper()
    if not validate_ticker_token(t):
        raise ValueError("invalid ticker")
    cur = get_trade_candidates()
    if t not in cur:
        cur.append(t)
    return set_trade_candidates(cur)


def remove_trade_candidate(ticker: str) -> list[str]:
    t = (ticker or "").strip().upper()
    cur = [x for x in get_trade_candidates() if x != t]
    return set_trade_candidates(cur)


def is_trade_candidate(ticker: str) -> bool:
    t = (ticker or "").strip().upper()
    return bool(t) and t in set(get_trade_candidates())


def collect_watchlist_tickers(temp_tickers: Iterable[str] | None = None) -> list[str]:
    """
    Setup (超卖/强势回调, dist < -10%) ∪ mine ∪ growth ∪ short ∪ optional temp.
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
    try:
        pools.extend(get_growth_watchlist())
    except Exception:
        pass
    try:
        from short_sell import select_short_watch

        pools.extend(
            (r.get("ticker") or "")
            for r in (select_short_watch().get("rows") or [])
        )
    except Exception:
        try:
            pools.extend(get_short_watchlist())
        except Exception:
            pools.extend(DEFAULT_SHORT_WATCHLIST)
    if temp_tickers:
        pools.extend(temp_tickers)
    for t in pools:
        u = (t or "").strip().upper()
        if u and u not in seen and validate_ticker_token(u):
            seen.add(u)
            out.append(u)
    return out


# ── Named pools: GROWTH (long-horizon sleeve) / SHORT (ETF-heavy + stables) ─
# Same persistence pattern as My Watchlist. ALERT not enabled yet for these tabs.
#
# GROWTH selection method (review rarely — not a trading list):
# 1) Universe = S&P 500 ∪ Nasdaq-100 only
# 2) Sectors = Financials, Utilities, Health Care + industry Financial Exchanges & Data
# 3) Prefer large-cap durable franchises (liquidity + multi-year index stay)
# 4) Skip speculative / high-churn names in those buckets (e.g. COIN)
# 5) ETF sleeve = dividend growth + growth/style + broad + sector beta (user list)
# 6) Membership review quarterly/semi-annual at most

DEFAULT_GROWTH_WATCHLIST = [
    # ETFs — dividend growth / growth / broad / sector
    "DGRO",
    "VIG",
    "VIGI",
    "VUG",
    "SPYG",
    "SPY",
    "RSP",
    "XLF",
    "XLU",
    "VHT",
    "XLV",
    # Financials — mega/large compounders
    "BRK-B",
    "JPM",
    "V",
    "MA",
    "BAC",
    "MS",
    "GS",
    "WFC",
    "AXP",
    "SCHW",
    "BLK",
    "CB",
    "PGR",
    "BX",
    "PNC",
    # Financial Exchanges & Data (ex-COIN)
    "SPGI",
    "CME",
    "ICE",
    "MCO",
    "NDAQ",
    "MSCI",
    "CBOE",
    "FDS",
    # Utilities — regulated compounders
    "NEE",
    "SO",
    "DUK",
    "CEG",
    "AEP",
    "D",
    "SRE",
    "XEL",
    "EXC",
    "ED",
    "PEG",
    "WEC",
    "AWK",
    # Health Care — pharma / devices / managed care / tools
    "LLY",
    "JNJ",
    "ABBV",
    "MRK",
    "UNH",
    "AMGN",
    "TMO",
    "ABT",
    "DHR",
    "ISRG",
    "SYK",
    "MDT",
    "VRTX",
    "GILD",
    "ELV",
]

_GROWTH_DEFAULT_FUNDS = frozenset(
    {
        "DGRO",
        "VIG",
        "VIGI",
        "VUG",
        "SPYG",
        "SPY",
        "RSP",
        "XLF",
        "XLU",
        "VHT",
        "XLV",
    }
)

DEFAULT_SHORT_WATCHLIST = [
    # Broad Market
    "SPY", "QQQ", "DIA", "IWM", "MDY", "RSP",
    # Sector
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC",
    # Industry
    "SMH", "SOXX", "XBI", "IBB", "KRE", "XHB", "ITB", "XRT", "XOP", "OIH", "IYT", "IGV",
    # Asset / Country
    "GLD", "SLV", "USO", "TLT", "HYG", "EEM", "FXI", "INDA",
    # Leveraged
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UPRO", "SPXU",
    # Stable Stocks (equity — Financial/News OK)
    "KO", "PEP", "WMT", "MCD", "PG", "JNJ", "JPM",
]

# Equities in SHORT that still get Financial + News like My Watchlist.
SHORT_STABLE_STOCKS = frozenset({"KO", "PEP", "WMT", "MCD", "PG", "JNJ", "JPM"})

_SHORT_DEFAULT_FUNDS = frozenset(
    t for t in DEFAULT_SHORT_WATCHLIST if t not in SHORT_STABLE_STOCKS
)

SETTING_GROWTH = "wl_pool_growth"
SETTING_SHORT = "wl_pool_short"
SETTING_MOMENTUM = "wl_pool_momentum"

# Initial MOMENTUM pool — editable; not hard-wired into scoring.
DEFAULT_MOMENTUM_WATCHLIST = [
    "META",
    "DELL",
    "NVDA",
    "TSLA",
    "PLTR",
]


def _parse_ticker_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        u = (str(item) if item is not None else "").strip().upper()
        if u and u not in seen and validate_ticker_token(u):
            seen.add(u)
            out.append(u)
    return out


def get_growth_watchlist() -> list[str]:
    """GROWTH pool — seeds curated long-horizon list when unset or empty."""
    from db import get_setting, set_setting

    raw = get_setting(SETTING_GROWTH, None)
    parsed = _parse_ticker_list(raw) if isinstance(raw, list) else []
    if raw is None or not parsed:
        seed = list(DEFAULT_GROWTH_WATCHLIST)
        set_setting(SETTING_GROWTH, seed)
        return seed
    return parsed


def set_growth_watchlist(tickers: Iterable[str]) -> list[str]:
    from db import set_setting

    out = _parse_ticker_list(list(tickers))
    set_setting(SETTING_GROWTH, out)
    return out


def add_growth_watchlist_ticker(ticker: str) -> list[str]:
    t = (ticker or "").strip().upper()
    if not validate_ticker_token(t):
        raise ValueError("invalid ticker")
    cur = get_growth_watchlist()
    if t not in cur:
        cur.append(t)
    return set_growth_watchlist(cur)


def remove_growth_watchlist_ticker(ticker: str) -> list[str]:
    t = (ticker or "").strip().upper()
    return set_growth_watchlist([x for x in get_growth_watchlist() if x != t])


def get_short_watchlist() -> list[str]:
    """
    Legacy curated ETF/stable sleeve (settings key wl_pool_short).

    Watchlist → Short and AI Trading → Short Sell now use Dist25 Top %
    via short_sell.select_short_watch(). Kept for backward-compatible
    settings / rare callers only.
    """
    from db import get_setting, set_setting

    raw = get_setting(SETTING_SHORT, None)
    if raw is None:
        seed = list(DEFAULT_SHORT_WATCHLIST)
        set_setting(SETTING_SHORT, seed)
        return seed
    return _parse_ticker_list(raw)


def set_short_watchlist(tickers: Iterable[str]) -> list[str]:
    from db import set_setting

    out = _parse_ticker_list(list(tickers))
    set_setting(SETTING_SHORT, out)
    return out


def add_short_watchlist_ticker(ticker: str) -> list[str]:
    t = (ticker or "").strip().upper()
    if not validate_ticker_token(t):
        raise ValueError("invalid ticker")
    cur = get_short_watchlist()
    if t not in cur:
        cur.append(t)
    return set_short_watchlist(cur)


def remove_short_watchlist_ticker(ticker: str) -> list[str]:
    t = (ticker or "").strip().upper()
    return set_short_watchlist([x for x in get_short_watchlist() if x != t])


def get_momentum_watchlist() -> list[str]:
    """MOMENTUM pool — seeds META/DELL/NVDA/TSLA/PLTR when unset or empty."""
    from db import get_setting, set_setting

    raw = get_setting(SETTING_MOMENTUM, None)
    parsed = _parse_ticker_list(raw) if isinstance(raw, list) else []
    if raw is None or not parsed:
        seed = list(DEFAULT_MOMENTUM_WATCHLIST)
        set_setting(SETTING_MOMENTUM, seed)
        return seed
    return parsed


def set_momentum_watchlist(tickers: Iterable[str]) -> list[str]:
    from db import set_setting

    out = _parse_ticker_list(list(tickers))
    set_setting(SETTING_MOMENTUM, out)
    return out


def add_momentum_watchlist_ticker(ticker: str) -> list[str]:
    t = (ticker or "").strip().upper()
    if not validate_ticker_token(t):
        raise ValueError("invalid ticker")
    cur = get_momentum_watchlist()
    if t not in cur:
        cur.append(t)
    return set_momentum_watchlist(cur)


def remove_momentum_watchlist_ticker(ticker: str) -> list[str]:
    t = (ticker or "").strip().upper()
    return set_momentum_watchlist([x for x in get_momentum_watchlist() if x != t])


def is_fund_like(ticker: str, row: dict | None = None) -> bool:
    """
    Funds / ETFs: skip Financial + News on GROWTH/SHORT tabs.
    Stable stocks in SHORT are treated as equities.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return False
    if t in SHORT_STABLE_STOCKS:
        return False
    at = str((row or {}).get("asset_type") or "").strip().upper()
    if at == "ETF":
        return True
    if t in _GROWTH_DEFAULT_FUNDS or t in _SHORT_DEFAULT_FUNDS:
        return True
    try:
        from db import get_conn, init_db

        init_db()
        with get_conn() as conn:
            hit = conn.execute(
                "SELECT 1 FROM etf_universe WHERE UPPER(ticker)=? LIMIT 1", (t,)
            ).fetchone()
        return hit is not None
    except Exception:
        return False
