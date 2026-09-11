"""
IBKR connection profiles + hard safety for live order placement.

Upper LeiBot modules must NOT branch on Paper vs Live ports.
They call the shared adapter; this module alone maps mode → socket profile.

LIVE_TRADING_ENABLED defaults to false and is env-only.
Selecting LIVE connection mode never enables live order placement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

MODE_PAPER = "PAPER"
MODE_LIVE = "LIVE"
MODES = (MODE_PAPER, MODE_LIVE)

DEFAULT_HOST = "127.0.0.1"

# TWS defaults (IB Gateway: Paper 4002 / Live 4001 — override via env).
DEFAULT_PAPER_PORT = 7497
DEFAULT_LIVE_PORT = 7496
DEFAULT_PAPER_CLIENT_ID = 71
DEFAULT_LIVE_CLIENT_ID = 72

# Settings key — connection profile only (not trading permission).
SETTINGS_CONNECTION_MODE = "ibkr_connection_mode"


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def live_trading_enabled() -> bool:
    """
    Hard safety gate for LIVE brokerage order placement.

    Default: False.
    Must be set explicitly in the process environment.
    Must NEVER be flipped by choosing LIVE in the UI.
    """
    return _truthy(os.environ.get("LIVE_TRADING_ENABLED"))


def normalize_mode(raw: str | None) -> str:
    m = (raw or MODE_PAPER).strip().upper()
    return m if m in MODES else MODE_PAPER


def get_connection_mode() -> str:
    """Active PAPER/LIVE profile (settings, then env IBKR_MODE, default PAPER)."""
    env = (os.environ.get("IBKR_MODE") or "").strip()
    if env:
        return normalize_mode(env)
    try:
        from db import get_setting

        return normalize_mode(get_setting(SETTINGS_CONNECTION_MODE, MODE_PAPER))
    except Exception:
        return MODE_PAPER


def set_connection_mode(mode: str) -> str:
    """
    Persist PAPER/LIVE connection profile only.
    Does not enable LIVE trading.
    """
    cleaned = normalize_mode(mode)
    from db import set_setting

    set_setting(SETTINGS_CONNECTION_MODE, cleaned)
    return cleaned


@dataclass(frozen=True)
class ConnectionProfile:
    """Socket profile for one IBKR session. Shared adapter uses this only."""

    mode: str
    host: str
    port: int
    client_id: int
    # Market-data / account-read sessions are always readonly=True.
    readonly: bool = True

    @property
    def label(self) -> str:
        return f"{self.mode}@{self.host}:{self.port}#cid{self.client_id}"


def _env_host() -> str:
    return (os.environ.get("IBKR_HOST") or DEFAULT_HOST).strip() or DEFAULT_HOST


def _paper_port() -> int:
    raw = (
        os.environ.get("IBKR_PAPER_PORT") or os.environ.get("IBKR_PORT") or ""
    ).strip()
    return int(raw) if raw else DEFAULT_PAPER_PORT


def _live_port() -> int:
    raw = (os.environ.get("IBKR_LIVE_PORT") or "").strip()
    return int(raw) if raw else DEFAULT_LIVE_PORT


def _paper_client_id() -> int:
    raw = (os.environ.get("IBKR_CLIENT_ID") or os.environ.get("IBKR_PAPER_CLIENT_ID") or "").strip()
    return int(raw) if raw else DEFAULT_PAPER_CLIENT_ID


def _live_client_id() -> int:
    raw = (os.environ.get("IBKR_LIVE_CLIENT_ID") or "").strip()
    return int(raw) if raw else DEFAULT_LIVE_CLIENT_ID


def get_connection_profile(mode: str | None = None) -> ConnectionProfile:
    """
    Resolve host/port/clientId for PAPER or LIVE.
    Callers outside ibkr_local should not hard-code ports.
    """
    m = normalize_mode(mode or get_connection_mode())
    host = _env_host()
    if m == MODE_LIVE:
        return ConnectionProfile(
            mode=MODE_LIVE,
            host=host,
            port=_live_port(),
            client_id=_live_client_id(),
            readonly=True,
        )
    return ConnectionProfile(
        mode=MODE_PAPER,
        host=host,
        port=_paper_port(),
        client_id=_paper_client_id(),
        readonly=True,
    )


class LiveTradingDisabled(PermissionError):
    """Raised when LIVE order placement is attempted without LIVE_TRADING_ENABLED."""


def assert_order_placement_allowed(mode: str | None = None) -> None:
    """
    Gate immediately before any placeOrder / modifyOrder to IBKR.

    PAPER: allowed for future paper-order testing (still subject to other safety).
    LIVE: requires LIVE_TRADING_ENABLED=true in the environment.
    """
    m = normalize_mode(mode or get_connection_mode())
    if m == MODE_LIVE and not live_trading_enabled():
        raise LiveTradingDisabled(
            "LIVE order placement blocked: LIVE_TRADING_ENABLED is false "
            "(selecting LIVE connection mode does not enable live trading)"
        )


def safety_status() -> dict[str, Any]:
    """Compact status for Settings / diagnostics."""
    mode = get_connection_mode()
    profile = get_connection_profile(mode)
    return {
        "connection_mode": mode,
        "profile": {
            "host": profile.host,
            "port": profile.port,
            "client_id": profile.client_id,
            "readonly": profile.readonly,
        },
        "live_trading_enabled": live_trading_enabled(),
        "orders_allowed_on_active_mode": (
            True
            if mode == MODE_PAPER
            else live_trading_enabled()
        ),
        "note": (
            "LIVE = market data / account reads only unless "
            "LIVE_TRADING_ENABLED=true is set in the environment."
        ),
    }
