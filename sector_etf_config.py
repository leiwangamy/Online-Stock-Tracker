"""
Central config for SECTOR ETF ROTATION research (FULL / local).

V1 = 11 S&P Select Sector ETFs + SPY benchmark.
Future industry ETF groups can be added as extra SectorEtfGroup entries
without changing the rotation engine.
"""

from __future__ import annotations

from typing import Any, TypedDict


class SectorEtfDef(TypedDict):
    key: str
    etf: str
    sector: str
    storage_ticker: str


class SectorEtfGroup(TypedDict):
    key: str
    label: str
    members: tuple[SectorEtfDef, ...]


# SPY is the market benchmark for relative strength — not a sector row.
BENCHMARK_ETF = "SPY"
BENCHMARK_STORAGE = "SPY"
BENCHMARK_NAME = "S&P 500 ETF"


# Order matches a common sector rotation scan (tech → cyclical → defensive).
SELECT_SECTOR_ETFS: tuple[SectorEtfDef, ...] = (
    {
        "key": "xlk",
        "etf": "XLK",
        "sector": "Technology",
        "storage_ticker": "XLK",
    },
    {
        "key": "xlf",
        "etf": "XLF",
        "sector": "Financials",
        "storage_ticker": "XLF",
    },
    {
        "key": "xlv",
        "etf": "XLV",
        "sector": "Health Care",
        "storage_ticker": "XLV",
    },
    {
        "key": "xly",
        "etf": "XLY",
        "sector": "Consumer Discretionary",
        "storage_ticker": "XLY",
    },
    {
        "key": "xlc",
        "etf": "XLC",
        "sector": "Communication Services",
        "storage_ticker": "XLC",
    },
    {
        "key": "xli",
        "etf": "XLI",
        "sector": "Industrials",
        "storage_ticker": "XLI",
    },
    {
        "key": "xlp",
        "etf": "XLP",
        "sector": "Consumer Staples",
        "storage_ticker": "XLP",
    },
    {
        "key": "xle",
        "etf": "XLE",
        "sector": "Energy",
        "storage_ticker": "XLE",
    },
    {
        "key": "xlu",
        "etf": "XLU",
        "sector": "Utilities",
        "storage_ticker": "XLU",
    },
    {
        "key": "xlre",
        "etf": "XLRE",
        "sector": "Real Estate",
        "storage_ticker": "XLRE",
    },
    {
        "key": "xlb",
        "etf": "XLB",
        "sector": "Materials",
        "storage_ticker": "XLB",
    },
)


SECTOR_ETF_GROUPS: tuple[SectorEtfGroup, ...] = (
    {
        "key": "select_sector",
        "label": "S&P Select Sector ETFs",
        "members": SELECT_SECTOR_ETFS,
    },
    # Future (not V1): semiconductors, software, biotech, banks, …
)


DEFAULT_GROUP_KEY = "select_sector"


def list_groups() -> list[dict[str, Any]]:
    out = []
    for g in SECTOR_ETF_GROUPS:
        out.append(
            {
                "key": g["key"],
                "label": g["label"],
                "count": len(g["members"]),
            }
        )
    return out


def get_group(key: str | None = None) -> SectorEtfGroup:
    k = (key or DEFAULT_GROUP_KEY).strip().lower() or DEFAULT_GROUP_KEY
    for g in SECTOR_ETF_GROUPS:
        if g["key"] == k:
            return g
    return SECTOR_ETF_GROUPS[0]


def list_sector_etfs(group_key: str | None = None) -> list[SectorEtfDef]:
    g = get_group(group_key)
    return [dict(x) for x in g["members"]]  # type: ignore[misc]


def sector_storage_tickers(group_key: str | None = None) -> list[str]:
    return [x["storage_ticker"] for x in list_sector_etfs(group_key)]


def all_storage_tickers(group_key: str | None = None) -> list[str]:
    """Sector ETFs + SPY benchmark (for IBKR refresh / DB load)."""
    tickers = sector_storage_tickers(group_key)
    if BENCHMARK_STORAGE not in tickers:
        tickers.append(BENCHMARK_STORAGE)
    return tickers
