"""LeiBot deployment mode: full (default) vs lite (online slim site).

Set LEIBOT_MODE=lite in production (/etc/leibot/prod.env or compose).

Lite online is ONLY:
  - Stock Tracker (charts + news search)
  - Watchlist: My + Nasdaq-100 (full technical columns)
  - Sector Rotation
  - Settings / Login (SMA + schedule; no Paper exits)

Local-only (FULL) — never required online:
  - AI Trading / Paper Trading / strategy books
  - AI Discovery / AI News / AI Select
  - Market Dashboard / full Research
  - IBKR sync & trading-order APIs

Default (unset LEIBOT_MODE) = full.
"""

from __future__ import annotations

import os

_LITE_TRUE = {"1", "true", "yes", "lite", "on"}


def is_lite() -> bool:
    raw = (os.environ.get("LEIBOT_MODE") or "full").strip().lower()
    return raw in _LITE_TRUE


# Flask endpoint names allowed when lite.
LITE_ALLOWED_ENDPOINTS = frozenset(
    {
        "home",
        "stock_tracker",
        "watchlist",
        "watchlist_alert_price",
        "strong_stock_monitor",  # view further restricts to rotation*
        "settings",
        "owner_login",
        "owner_logout",
        "set_language",
        "refresh_all_prices",  # runs Lite price job only
        "api_market_search",
    }
)

# Explicitly local-only (blocked even if allow-list is edited by mistake).
LITE_FORBIDDEN_ENDPOINTS = frozenset(
    {
        "ai_trading",
        "ai_trading_levels",
        "ai_trading_export_xlsx",
        "admin_order_requests",
        "api_trading_orders_pending",
        "api_trading_order_get",
        "api_trading_order_status",
        "api_market_ibkr_sync",
        "market_dashboard",
        "etf_dashboard",
        "refresh_etf_dashboard",
        "refresh_universe",
        "refresh_dashboard",
        "candidate_analysis",
    }
)

# Watchlist tabs visible in lite.
LITE_WATCHLIST_TABS = frozenset({"mine", "ndx100"})

# Research tabs visible in lite.
LITE_RESEARCH_TABS = frozenset({"rotation", "rotation_detail"})


def lite_watchlist_tab_ok(tab: str | None) -> bool:
    return (tab or "mine").strip().lower() in LITE_WATCHLIST_TABS


def lite_research_tab_ok(tab: str | None) -> bool:
    return (tab or "rotation").strip().lower() in LITE_RESEARCH_TABS


def lite_endpoint_allowed(endpoint: str | None) -> bool:
    """Return True if this Flask endpoint may run under Lite."""
    if not endpoint or endpoint == "static":
        return True
    if endpoint in LITE_FORBIDDEN_ENDPOINTS:
        return False
    return endpoint in LITE_ALLOWED_ENDPOINTS
