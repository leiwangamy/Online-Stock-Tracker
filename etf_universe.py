"""
LeiBot ETF Universe V1 — curated metadata only.

ETFs share the stock price / SMA / validation pipeline (market_data.py).
They are NOT mixed into Core Universe, AI BUY observation pools, or company
fundamentals. One ticker → one canonical row; use tags for multi-group membership.
"""

from __future__ import annotations

from typing import Any

# Primary category values used by UI filters.
ETF_CATEGORIES = (
    "BROAD_MARKET",
    "SECTOR",
    "INDUSTRY",
    "FACTOR_STYLE",
    "BOND",
    "COMMODITY",
    "REAL_ESTATE",
    "INTERNATIONAL",
    "COUNTRY",
    "CANADA",
    "OTHER",
)

# Filter chips on the ETF dashboard (maps UI key → category / tag match).
ETF_FILTERS = (
    ("ALL", None),
    ("BROAD_MARKET", "BROAD_MARKET"),
    ("SECTOR", "SECTOR"),
    ("INDUSTRY", "INDUSTRY"),
    ("FACTOR_STYLE", "FACTOR_STYLE"),
    ("BOND", "BOND"),
    ("COMMODITY", "COMMODITY"),
    ("REAL_ESTATE", "REAL_ESTATE"),
    ("INTERNATIONAL", "INTERNATIONAL"),
    ("CANADA", "CANADA"),
)


def _e(
    ticker: str,
    name: str,
    category: str,
    subcategory: str = "",
    *,
    market: str = "US",
    currency: str = "USD",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    t = ticker.strip().upper()
    tag_list = list(tags or [])
    if category and category not in tag_list:
        tag_list.insert(0, category)
    return {
        "ticker": t,
        "name": name.strip(),
        "etf_category": category,
        "etf_subcategory": subcategory or "",
        "market": market,
        "currency": currency,
        "tags": tag_list,
        "asset_type": "ETF",
    }


# Curated LEIBOT ETF UNIVERSE (~100). Prefer liquid, representative funds.
# XLRE lives once under SECTOR; REAL_ESTATE is also in tags.
LEIBOT_ETF_UNIVERSE: list[dict[str, Any]] = [
    # —— Broad market ——
    _e("SPY", "SPDR S&P 500 ETF Trust", "BROAD_MARKET", "SP500"),
    _e("IVV", "iShares Core S&P 500 ETF", "BROAD_MARKET", "SP500"),
    _e("VOO", "Vanguard S&P 500 ETF", "BROAD_MARKET", "SP500"),
    _e("QQQ", "Invesco QQQ Trust", "BROAD_MARKET", "NASDAQ100"),
    _e("QQQM", "Invesco NASDAQ 100 ETF", "BROAD_MARKET", "NASDAQ100"),
    _e("DIA", "SPDR Dow Jones Industrial Average ETF", "BROAD_MARKET", "DOW"),
    _e("IWM", "iShares Russell 2000 ETF", "BROAD_MARKET", "SMALL_CAP"),
    _e("VTI", "Vanguard Total Stock Market ETF", "BROAD_MARKET", "TOTAL_MARKET"),
    _e("RSP", "Invesco S&P 500 Equal Weight ETF", "BROAD_MARKET", "EQUAL_WEIGHT"),
    _e("MDY", "SPDR S&P MidCap 400 ETF", "BROAD_MARKET", "MID_CAP"),
    _e("IJR", "iShares Core S&P Small-Cap ETF", "BROAD_MARKET", "SMALL_CAP"),
    _e("IWB", "iShares Russell 1000 ETF", "BROAD_MARKET", "LARGE_CAP"),
    _e("VT", "Vanguard Total World Stock ETF", "BROAD_MARKET", "WORLD"),
    # —— US sectors (GICS) ——
    _e("XLC", "Communication Services Select Sector SPDR", "SECTOR", "COMMUNICATION"),
    _e("XLY", "Consumer Discretionary Select Sector SPDR", "SECTOR", "CONSUMER_DISCRETIONARY"),
    _e("XLP", "Consumer Staples Select Sector SPDR", "SECTOR", "CONSUMER_STAPLES"),
    _e("XLE", "Energy Select Sector SPDR", "SECTOR", "ENERGY"),
    _e("XLF", "Financial Select Sector SPDR", "SECTOR", "FINANCIALS"),
    _e("XLV", "Health Care Select Sector SPDR", "SECTOR", "HEALTH_CARE"),
    _e("XLI", "Industrial Select Sector SPDR", "SECTOR", "INDUSTRIALS"),
    _e("XLK", "Technology Select Sector SPDR", "SECTOR", "TECHNOLOGY"),
    _e("XLB", "Materials Select Sector SPDR", "SECTOR", "MATERIALS"),
    _e("XLRE", "Real Estate Select Sector SPDR", "SECTOR", "REAL_ESTATE", tags=["SECTOR", "REAL_ESTATE"]),
    _e("XLU", "Utilities Select Sector SPDR", "SECTOR", "UTILITIES"),
    # —— Industry / thematic (representative) ——
    _e("SMH", "VanEck Semiconductor ETF", "INDUSTRY", "SEMICONDUCTORS"),
    _e("SOXX", "iShares Semiconductor ETF", "INDUSTRY", "SEMICONDUCTORS"),
    _e("XBI", "SPDR S&P Biotech ETF", "INDUSTRY", "BIOTECH"),
    _e("IBB", "iShares Biotechnology ETF", "INDUSTRY", "BIOTECH"),
    _e("KRE", "SPDR S&P Regional Banking ETF", "INDUSTRY", "REGIONAL_BANKS"),
    _e("XHB", "SPDR S&P Homebuilders ETF", "INDUSTRY", "HOMEBUILDERS"),
    _e("ITB", "iShares U.S. Home Construction ETF", "INDUSTRY", "HOMEBUILDERS"),
    _e("XRT", "SPDR S&P Retail ETF", "INDUSTRY", "RETAIL"),
    _e("XOP", "SPDR S&P Oil & Gas Exploration ETF", "INDUSTRY", "OIL_GAS_E_P"),
    _e("OIH", "VanEck Oil Services ETF", "INDUSTRY", "OIL_SERVICES"),
    _e("ITA", "iShares U.S. Aerospace & Defense ETF", "INDUSTRY", "AERO_DEFENSE"),
    _e("IYT", "iShares U.S. Transportation ETF", "INDUSTRY", "TRANSPORT"),
    _e("IGV", "iShares Expanded Tech-Software ETF", "INDUSTRY", "SOFTWARE"),
    _e("SKYY", "First Trust Cloud Computing ETF", "INDUSTRY", "CLOUD"),
    _e("CIBR", "First Trust NASDAQ Cybersecurity ETF", "INDUSTRY", "CYBERSECURITY"),
    _e("BOTZ", "Global X Robotics & AI ETF", "INDUSTRY", "ROBOTICS_AI"),
    _e("HACK", "Amplify Cybersecurity ETF", "INDUSTRY", "CYBERSECURITY"),
    _e("TAN", "Invesco Solar ETF", "INDUSTRY", "SOLAR"),
    _e("LIT", "Global X Lithium & Battery Tech ETF", "INDUSTRY", "LITHIUM"),
    _e("XME", "SPDR S&P Metals & Mining ETF", "INDUSTRY", "METALS_MINING"),
    _e("GDX", "VanEck Gold Miners ETF", "INDUSTRY", "GOLD_MINERS"),
    # —— Factor / style ——
    _e("VUG", "Vanguard Growth ETF", "FACTOR_STYLE", "GROWTH"),
    _e("IWF", "iShares Russell 1000 Growth ETF", "FACTOR_STYLE", "GROWTH"),
    _e("SCHG", "Schwab U.S. Large-Cap Growth ETF", "FACTOR_STYLE", "GROWTH"),
    _e("VTV", "Vanguard Value ETF", "FACTOR_STYLE", "VALUE"),
    _e("IWD", "iShares Russell 1000 Value ETF", "FACTOR_STYLE", "VALUE"),
    _e("SCHD", "Schwab U.S. Dividend Equity ETF", "FACTOR_STYLE", "DIVIDEND"),
    _e("VIG", "Vanguard Dividend Appreciation ETF", "FACTOR_STYLE", "DIVIDEND"),
    _e("DGRO", "iShares Core Dividend Growth ETF", "FACTOR_STYLE", "DIVIDEND"),
    _e("QUAL", "iShares MSCI USA Quality Factor ETF", "FACTOR_STYLE", "QUALITY"),
    _e("MTUM", "iShares MSCI USA Momentum Factor ETF", "FACTOR_STYLE", "MOMENTUM"),
    _e("USMV", "iShares MSCI USA Min Vol Factor ETF", "FACTOR_STYLE", "LOW_VOLATILITY"),
    _e("VLUE", "iShares MSCI USA Value Factor ETF", "FACTOR_STYLE", "VALUE"),
    # —— Bonds ——
    _e("TLT", "iShares 20+ Year Treasury Bond ETF", "BOND", "LONG_TREASURY"),
    _e("IEF", "iShares 7-10 Year Treasury Bond ETF", "BOND", "INTERMEDIATE_TREASURY"),
    _e("SHY", "iShares 1-3 Year Treasury Bond ETF", "BOND", "SHORT_TREASURY"),
    _e("GOVT", "iShares U.S. Treasury Bond ETF", "BOND", "TREASURY"),
    _e("BND", "Vanguard Total Bond Market ETF", "BOND", "AGGREGATE_BOND"),
    _e("AGG", "iShares Core U.S. Aggregate Bond ETF", "BOND", "AGGREGATE_BOND"),
    _e("LQD", "iShares iBoxx $ Investment Grade Corporate Bond ETF", "BOND", "INVESTMENT_GRADE"),
    _e("HYG", "iShares iBoxx $ High Yield Corporate Bond ETF", "BOND", "HIGH_YIELD"),
    _e("JNK", "SPDR Bloomberg High Yield Bond ETF", "BOND", "HIGH_YIELD"),
    _e("TIP", "iShares TIPS Bond ETF", "BOND", "TIPS"),
    _e("SGOV", "iShares 0-3 Month Treasury Bond ETF", "BOND", "CASH_EQUIVALENT"),
    _e("BIL", "SPDR Bloomberg 1-3 Month T-Bill ETF", "BOND", "CASH_EQUIVALENT"),
    # —— Commodities ——
    _e("GLD", "SPDR Gold Shares", "COMMODITY", "GOLD"),
    _e("IAU", "iShares Gold Trust", "COMMODITY", "GOLD"),
    _e("SLV", "iShares Silver Trust", "COMMODITY", "SILVER"),
    _e("USO", "United States Oil Fund", "COMMODITY", "OIL"),
    _e("UNG", "United States Natural Gas Fund", "COMMODITY", "NATURAL_GAS"),
    _e("DBC", "Invesco DB Commodity Index Tracking Fund", "COMMODITY", "BROAD_COMMODITY"),
    _e("PDBC", "Invesco Optimum Yield Diversified Commodity Strategy", "COMMODITY", "BROAD_COMMODITY"),
    # —— Real estate (dedicated; XLRE also tagged REAL_ESTATE) ——
    _e("VNQ", "Vanguard Real Estate ETF", "REAL_ESTATE", "REIT"),
    _e("IYR", "iShares U.S. Real Estate ETF", "REAL_ESTATE", "REIT"),
    # —— International / country ——
    _e("EFA", "iShares MSCI EAFE ETF", "INTERNATIONAL", "DEVELOPED_MARKETS"),
    _e("VEA", "Vanguard FTSE Developed Markets ETF", "INTERNATIONAL", "DEVELOPED_MARKETS"),
    _e("EEM", "iShares MSCI Emerging Markets ETF", "INTERNATIONAL", "EMERGING_MARKETS"),
    _e("VWO", "Vanguard FTSE Emerging Markets ETF", "INTERNATIONAL", "EMERGING_MARKETS"),
    _e("EWJ", "iShares MSCI Japan ETF", "COUNTRY", "JAPAN", tags=["COUNTRY", "INTERNATIONAL"]),
    _e("EWG", "iShares MSCI Germany ETF", "COUNTRY", "GERMANY", tags=["COUNTRY", "INTERNATIONAL"]),
    _e("EWU", "iShares MSCI United Kingdom ETF", "COUNTRY", "UK", tags=["COUNTRY", "INTERNATIONAL"]),
    _e("FXI", "iShares China Large-Cap ETF", "COUNTRY", "CHINA", tags=["COUNTRY", "INTERNATIONAL"]),
    _e("MCHI", "iShares MSCI China ETF", "COUNTRY", "CHINA", tags=["COUNTRY", "INTERNATIONAL"]),
    _e("INDA", "iShares MSCI India ETF", "COUNTRY", "INDIA", tags=["COUNTRY", "INTERNATIONAL"]),
    _e("EWZ", "iShares MSCI Brazil ETF", "COUNTRY", "BRAZIL", tags=["COUNTRY", "INTERNATIONAL"]),
    _e("EWC", "iShares MSCI Canada ETF", "COUNTRY", "CANADA", tags=["COUNTRY", "INTERNATIONAL", "CANADA"]),
    _e("EWY", "iShares MSCI South Korea ETF", "COUNTRY", "KOREA", tags=["COUNTRY", "INTERNATIONAL"]),
    _e("EWA", "iShares MSCI Australia ETF", "COUNTRY", "AUSTRALIA", tags=["COUNTRY", "INTERNATIONAL"]),
    # —— Canada-listed ——
    _e("VFV.TO", "Vanguard S&P 500 Index ETF", "CANADA", "SP500", market="CANADA", currency="CAD"),
    _e("ZSP.TO", "BMO S&P 500 Index ETF", "CANADA", "SP500", market="CANADA", currency="CAD"),
    _e("XIC.TO", "iShares Core S&P/TSX Capped Composite Index ETF", "CANADA", "TSX", market="CANADA", currency="CAD"),
    _e("XIU.TO", "iShares S&P/TSX 60 Index ETF", "CANADA", "TSX60", market="CANADA", currency="CAD"),
    _e("XEQT.TO", "iShares Core Equity ETF Portfolio", "CANADA", "ASSET_ALLOCATION", market="CANADA", currency="CAD"),
    _e("VEQT.TO", "Vanguard All-Equity ETF Portfolio", "CANADA", "ASSET_ALLOCATION", market="CANADA", currency="CAD"),
    _e("TEC.TO", "TD Global Technology Leaders Index ETF", "CANADA", "TECHNOLOGY", market="CANADA", currency="CAD"),
    _e("VDY.TO", "Vanguard FTSE Canadian High Dividend Yield Index ETF", "CANADA", "DIVIDEND", market="CANADA", currency="CAD"),
    _e("ZCN.TO", "BMO S&P/TSX Capped Composite Index ETF", "CANADA", "TSX", market="CANADA", currency="CAD"),
    _e("XEF.TO", "iShares Core MSCI EAFE IMI Index ETF", "CANADA", "DEVELOPED_MARKETS", market="CANADA", currency="CAD"),
]


def curated_etf_rows() -> list[dict[str, Any]]:
    """Deduped curated list (last write wins if a ticker appears twice)."""
    by_t: dict[str, dict[str, Any]] = {}
    for row in LEIBOT_ETF_UNIVERSE:
        t = str(row["ticker"]).upper()
        by_t[t] = row
    return list(by_t.values())


def ensure_etf_universe(*, force_seed: bool = False) -> dict[str, Any]:
    """
    Ensure etf_universe table is seeded from the curated list.
    Does not download prices. Safe to call on app start / before ETF refresh.
    """
    from db import etf_universe_count, init_db, upsert_etf_universe

    init_db()
    n = etf_universe_count()
    if n > 0 and not force_seed:
        return {"seeded": False, "count": n}
    rows = curated_etf_rows()
    upsert_etf_universe(rows, replace_all=True)
    return {"seeded": True, "count": len(rows)}


def etf_category_counts() -> dict[str, int]:
    from db import list_etf_universe

    counts: dict[str, int] = {}
    for r in list_etf_universe():
        cat = (r.get("etf_category") or "OTHER").upper()
        counts[cat] = counts.get(cat, 0) + 1
    return counts
