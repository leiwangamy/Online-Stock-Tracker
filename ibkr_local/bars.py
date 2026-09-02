"""
IBKR daily bars → pandas Close series (Paper TWS, read-only).

Used by Phase 4 to feed the shared LeiBot indicator engine.
Never places orders. Never writes production.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

import pandas as pd

from ibkr_local import (
    DEFAULT_CLIENT_ID,
    DEFAULT_HOST,
    DEFAULT_PAPER_PORT,
    _env_client_id,
    _env_host,
    _env_port,
)


def _bar_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def bars_to_close_series(bars: list[Any]) -> pd.Series:
    """Convert ib_insync BarDataList to a DatetimeIndex Close series."""
    dates: list[pd.Timestamp] = []
    closes: list[float] = []
    for bar in bars or []:
        d = _bar_date(getattr(bar, "date", None))
        if d is None:
            continue
        try:
            c = float(bar.close)
        except (TypeError, ValueError):
            continue
        if c != c or c <= 0:
            continue
        dates.append(pd.Timestamp(d))
        closes.append(c)
    if not dates:
        return pd.Series(dtype=float, name="Close")
    s = pd.Series(closes, index=pd.DatetimeIndex(dates), name="Close")
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


async def _one_symbol(
    ib: Any,
    symbol: str,
    *,
    duration: str,
    what_to_show: str,
) -> dict[str, Any]:
    from ib_insync import Stock

    if not ib.isConnected():
        return {
            "ok": False,
            "error": "disconnected",
            "closes": pd.Series(dtype=float),
            "what_to_show": what_to_show,
            "bars": 0,
        }

    contract = Stock(symbol, "SMART", "USD")
    try:
        qualified = await ib.qualifyContractsAsync(contract)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"qualify failed: {exc}",
            "closes": pd.Series(dtype=float),
            "what_to_show": what_to_show,
            "bars": 0,
        }
    if not qualified:
        return {
            "ok": False,
            "error": "no SMART/USD contract",
            "closes": pd.Series(dtype=float),
            "what_to_show": what_to_show,
            "bars": 0,
        }
    contract = qualified[0]

    try:
        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting="1 day",
            whatToShow=what_to_show,
            useRTH=True,
            formatDate=1,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": f"historical failed: {exc}",
            "closes": pd.Series(dtype=float),
            "what_to_show": what_to_show,
            "bars": 0,
        }

    closes = bars_to_close_series(list(bars or []))
    return {
        "ok": not closes.empty,
        "error": None if not closes.empty else "empty bars",
        "closes": closes,
        "what_to_show": what_to_show,
        "bars": int(len(closes)),
        "latest_bar_date": None
        if closes.empty
        else closes.index[-1].date().isoformat(),
    }


async def _fetch_ibkr_closes_async(
    symbols: list[str],
    *,
    host: str,
    port: int,
    client_id: int,
    duration: str = "2 Y",
    what_to_show: str = "TRADES",
) -> dict[str, dict[str, Any]]:
    from ib_insync import IB

    out: dict[str, dict[str, Any]] = {}
    ib = IB()
    try:
        await ib.connectAsync(
            host, port, clientId=client_id, readonly=True, timeout=10
        )
    except Exception as exc:
        err = f"IBKR connect failed {host}:{port} clientId={client_id}: {exc}"
        for sym in symbols:
            out[sym] = {
                "ok": False,
                "error": err,
                "closes": pd.Series(dtype=float),
                "what_to_show": what_to_show,
                "bars": 0,
            }
        return out

    try:
        for sym in symbols:
            out[sym] = await _one_symbol(
                ib, sym, duration=duration, what_to_show=what_to_show
            )
            await asyncio.sleep(0.4)
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass
    return out


def fetch_ibkr_daily_closes(
    tickers: list[str] | tuple[str, ...],
    *,
    host: str | None = None,
    port: int | None = None,
    client_id: int | None = None,
    duration: str = "2 Y",
    what_to_show: str = "TRADES",
) -> dict[str, dict[str, Any]]:
    """
    Fetch daily closes from Paper TWS.

    Default whatToShow=TRADES (split-aware trade closes; not dividend-adjusted).
    Closer to LeiBot Yahoo path than ADJUSTED_LAST.
    """
    symbols = [(t or "").strip().upper() for t in tickers if (t or "").strip()]
    host = host or _env_host() or DEFAULT_HOST
    port = int(port if port is not None else (_env_port() or DEFAULT_PAPER_PORT))
    client_id = int(
        client_id if client_id is not None else (_env_client_id() or DEFAULT_CLIENT_ID)
    )
    return asyncio.run(
        _fetch_ibkr_closes_async(
            symbols,
            host=host,
            port=port,
            client_id=client_id,
            duration=duration,
            what_to_show=what_to_show,
        )
    )
