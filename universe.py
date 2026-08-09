"""Build deduplicated S&P 500 / 400 / 600 + Nasdaq-100 universe."""

from __future__ import annotations

from io import StringIO
from typing import Any

import pandas as pd
import requests

from db import list_universe, universe_count, upsert_universe

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _read_wikipedia_table(url: str, match: str | None = None) -> pd.DataFrame:
    resp = requests.get(url, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text), flavor="lxml")
    if match:
        for table in tables:
            cols = " ".join(str(c).lower() for c in table.columns)
            if match.lower() in cols:
                return table
    return tables[0]


# All index membership flags a universe row can carry.
_FLAGS = ("in_sp500", "in_ndx100", "in_sp400", "in_sp600", "in_tsx")


def _blank_flags(active: str) -> dict[str, int]:
    return {flag: (1 if flag == active else 0) for flag in _FLAGS}


def _fetch_index(
    url: str,
    flag: str,
    *,
    match: str = "symbol",
    suffix: str = "",
) -> list[dict[str, Any]]:
    """Fetch an index constituent list from Wikipedia.

    S&P 500 / 400 / 600 pages share a column shape (Symbol, Security,
    GICS Sector, GICS Sub-Industry). The S&P/TSX page uses (Ticker, Company,
    Sector, Industry) and needs a `.TO` Yahoo suffix.
    """
    df = _read_wikipedia_table(url, match=match)
    symbol_col = next(c for c in df.columns if str(c).lower() in {"symbol", "ticker"})
    name_col = next((c for c in df.columns if "security" in str(c).lower() or "company" in str(c).lower()), None)
    sector_col = next((c for c in df.columns if "sector" in str(c).lower()), None)
    industry_col = next((c for c in df.columns if "sub-industry" in str(c).lower() or "industry" in str(c).lower()), None)

    rows = []
    for _, row in df.iterrows():
        ticker = str(row[symbol_col]).strip().upper().replace(".", "-")
        if not ticker or ticker == "NAN":
            continue
        if suffix and not ticker.endswith(suffix):
            ticker = f"{ticker}{suffix}"
        rows.append(
            {
                "ticker": ticker,
                "name": str(row[name_col]).strip() if name_col is not None else "",
                "sector": str(row[sector_col]).strip() if sector_col is not None else "",
                "industry": str(row[industry_col]).strip() if industry_col is not None else "",
                **_blank_flags(flag),
            }
        )
    return rows


def fetch_sp500() -> list[dict[str, Any]]:
    return _fetch_index(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "in_sp500"
    )


def fetch_sp400() -> list[dict[str, Any]]:
    return _fetch_index(
        "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", "in_sp400"
    )


def fetch_sp600() -> list[dict[str, Any]]:
    return _fetch_index(
        "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", "in_sp600"
    )


def fetch_tsx() -> list[dict[str, Any]]:
    # Yahoo Finance uses the ".TO" suffix for Toronto-listed tickers.
    return _fetch_index(
        "https://en.wikipedia.org/wiki/S%26P/TSX_Composite_Index",
        "in_tsx",
        match="ticker",
        suffix=".TO",
    )


def fetch_ndx100() -> list[dict[str, Any]]:
    url = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"
    df = _read_wikipedia_table(url, match="ticker")
    symbol_col = next(
        c for c in df.columns if "ticker" in str(c).lower() or str(c).lower() == "symbol"
    )
    name_col = next(
        (c for c in df.columns if "company" in str(c).lower() or "security" in str(c).lower()),
        None,
    )
    industry_col = next(
        (c for c in df.columns if "industry" in str(c).lower() or "subsector" in str(c).lower()),
        None,
    )

    rows = []
    for _, row in df.iterrows():
        ticker = str(row[symbol_col]).strip().upper().replace(".", "-")
        if not ticker or ticker == "NAN":
            continue
        rows.append(
            {
                "ticker": ticker,
                "name": str(row[name_col]).strip() if name_col is not None else "",
                "sector": "",
                "industry": str(row[industry_col]).strip() if industry_col is not None else "",
                **_blank_flags("in_ndx100"),
            }
        )
    return rows


def merge_universe(*lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge any number of index lists, OR-ing membership flags per ticker."""
    by_ticker: dict[str, dict[str, Any]] = {}
    for rows in lists:
        for row in rows:
            ticker = row["ticker"]
            if ticker in by_ticker:
                existing = by_ticker[ticker]
                for flag in _FLAGS:
                    if row.get(flag):
                        existing[flag] = 1
                if not existing.get("name") and row.get("name"):
                    existing["name"] = row["name"]
                if not existing.get("industry") and row.get("industry"):
                    existing["industry"] = row["industry"]
                if not existing.get("sector") and row.get("sector"):
                    existing["sector"] = row["sector"]
            else:
                by_ticker[ticker] = dict(row)
    return [by_ticker[k] for k in sorted(by_ticker)]


def refresh_universe() -> dict[str, Any]:
    sp500 = fetch_sp500()
    ndx100 = fetch_ndx100()
    sp400 = fetch_sp400()
    sp600 = fetch_sp600()
    tsx = fetch_tsx()
    merged = merge_universe(sp500, ndx100, sp400, sp600, tsx)
    count = upsert_universe(merged)
    return {
        "sp500": len(sp500),
        "ndx100": len(ndx100),
        "sp400": len(sp400),
        "sp600": len(sp600),
        "tsx": len(tsx),
        "unique": count,
    }


def ensure_universe() -> list[dict[str, Any]]:
    if universe_count() == 0:
        refresh_universe()
    return list_universe()
