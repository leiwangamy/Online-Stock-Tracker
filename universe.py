"""Build deduplicated S&P 500 + Nasdaq-100 universe."""

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


def fetch_sp500() -> list[dict[str, Any]]:
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    df = _read_wikipedia_table(url, match="symbol")
    # Common columns: Symbol, Security, GICS Sector, GICS Sub-Industry
    symbol_col = next(c for c in df.columns if str(c).lower() in {"symbol", "ticker"})
    name_col = next((c for c in df.columns if "security" in str(c).lower() or "company" in str(c).lower()), None)
    sector_col = next((c for c in df.columns if "sector" in str(c).lower()), None)
    industry_col = next((c for c in df.columns if "sub-industry" in str(c).lower() or "industry" in str(c).lower()), None)

    rows = []
    for _, row in df.iterrows():
        ticker = str(row[symbol_col]).strip().upper().replace(".", "-")
        if not ticker or ticker == "NAN":
            continue
        rows.append(
            {
                "ticker": ticker,
                "name": str(row[name_col]).strip() if name_col is not None else "",
                "sector": str(row[sector_col]).strip() if sector_col is not None else "",
                "industry": str(row[industry_col]).strip() if industry_col is not None else "",
                "in_sp500": 1,
                "in_ndx100": 0,
            }
        )
    return rows


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
                "in_sp500": 0,
                "in_ndx100": 1,
            }
        )
    return rows


def merge_universe(sp500: list[dict[str, Any]], ndx100: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in sp500:
        by_ticker[row["ticker"]] = dict(row)
    for row in ndx100:
        ticker = row["ticker"]
        if ticker in by_ticker:
            existing = by_ticker[ticker]
            existing["in_ndx100"] = 1
            if not existing.get("name") and row.get("name"):
                existing["name"] = row["name"]
            if not existing.get("industry") and row.get("industry"):
                existing["industry"] = row["industry"]
        else:
            by_ticker[ticker] = dict(row)
    return [by_ticker[k] for k in sorted(by_ticker)]


def refresh_universe() -> dict[str, Any]:
    sp500 = fetch_sp500()
    ndx100 = fetch_ndx100()
    merged = merge_universe(sp500, ndx100)
    count = upsert_universe(merged)
    return {
        "sp500": len(sp500),
        "ndx100": len(ndx100),
        "unique": count,
    }


def ensure_universe() -> list[dict[str, Any]]:
    if universe_count() == 0:
        refresh_universe()
    return list_universe()
