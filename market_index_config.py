"""
Central config for MARKET INDEX research (FULL / local).

IBKR cash-index contracts (not Yahoo tickers, not Stock SMART).
Storage key in daily_bars: storage_ticker (e.g. IX:SPX) — never collides with equities.
"""

from __future__ import annotations

from typing import Any, TypedDict


class IndexDef(TypedDict):
    key: str
    name: str
    display_symbol: str
    ibkr_symbol: str
    exchange: str
    currency: str
    category: str
    storage_ticker: str
    what_to_show: str


# Order = default table order (large-cap / tech / small / mid / blue-chip mix).
MARKET_INDEXES: tuple[IndexDef, ...] = (
    {
        "key": "dow",
        "name": "Dow Jones Industrial Average",
        "display_symbol": "DOW",
        "ibkr_symbol": "INDU",
        "exchange": "CME",
        "currency": "USD",
        "category": "blue_chip",
        "storage_ticker": "IX:INDU",
        "what_to_show": "TRADES",
    },
    {
        "key": "spx",
        "name": "S&P 500",
        "display_symbol": "S&P 500",
        "ibkr_symbol": "SPX",
        "exchange": "CBOE",
        "currency": "USD",
        "category": "large_cap",
        "storage_ticker": "IX:SPX",
        "what_to_show": "TRADES",
    },
    {
        "key": "comp",
        "name": "Nasdaq Composite",
        "display_symbol": "NASDAQ",
        "ibkr_symbol": "COMP",
        "exchange": "NASDAQ",
        "currency": "USD",
        "category": "technology",
        "storage_ticker": "IX:COMP",
        "what_to_show": "TRADES",
    },
    {
        "key": "ndx",
        "name": "Nasdaq-100",
        "display_symbol": "NASDAQ 100",
        "ibkr_symbol": "NDX",
        "exchange": "NASDAQ",
        "currency": "USD",
        "category": "technology",
        "storage_ticker": "IX:NDX",
        "what_to_show": "TRADES",
    },
    {
        "key": "rut",
        "name": "Russell 2000",
        "display_symbol": "RUSSELL 2000",
        "ibkr_symbol": "RUT",
        "exchange": "RUSSELL",
        "currency": "USD",
        "category": "small_cap",
        "storage_ticker": "IX:RUT",
        "what_to_show": "TRADES",
    },
    {
        "key": "mid",
        "name": "S&P MidCap 400",
        "display_symbol": "MIDCAP 400",
        "ibkr_symbol": "MID",
        "exchange": "CME",
        "currency": "USD",
        "category": "mid_cap",
        "storage_ticker": "IX:MID",
        "what_to_show": "TRADES",
    },
)


def list_index_defs() -> list[IndexDef]:
    return [dict(x) for x in MARKET_INDEXES]  # type: ignore[misc]


def index_by_key(key: str) -> IndexDef | None:
    k = (key or "").strip().lower()
    for row in MARKET_INDEXES:
        if row["key"] == k:
            return dict(row)  # type: ignore[return-value]
    return None


def storage_tickers() -> list[str]:
    return [x["storage_ticker"] for x in MARKET_INDEXES]


def as_public(row: IndexDef) -> dict[str, Any]:
    return {
        "key": row["key"],
        "name": row["name"],
        "display_symbol": row["display_symbol"],
        "category": row["category"],
        "storage_ticker": row["storage_ticker"],
        "ibkr_symbol": row["ibkr_symbol"],
        "exchange": row["exchange"],
    }


# Optional IBKR qualify fallbacks when primary exchange/symbol fails.
INDEX_CONTRACT_FALLBACKS: dict[str, tuple[tuple[str, str], ...]] = {
    "dow": (
        ("INDU", "NYSE"),
        ("DJI", "CME"),
        ("INDU", "CBOT"),
    ),
    "mid": (
        ("MID", "CBOE"),
        ("SPW", "CME"),
        ("MID", "NYSE"),
    ),
    "rut": (
        ("RUT", "CBOE"),
        ("RTY", "CME"),
    ),
    "comp": (("COMP", "NASDAQ"),),
}
