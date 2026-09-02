"""
LeiBot IBKR Local Sync — Phase 2 (Paper TWS smoke test only).

- Connects ONLY to a locally running, already-authenticated TWS / IB Gateway.
- Never stores IBKR username, password, or 2FA.
- Never places orders.
- Never talks to the production server.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


# Paper TWS defaults (live TWS is typically 7496 — do NOT use in Phase 2).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PAPER_PORT = 7497
DEFAULT_CLIENT_ID = 71
DEFAULT_TEST_TICKERS = ("AAPL", "MSFT", "SPY")


@dataclass
class TickerSmokeResult:
    ticker: str
    ibkr_connection: str
    contract_resolved: str
    latest_price: float | None
    previous_close: float | None
    price_timestamp: str | None
    historical_daily_available: bool
    daily_bars_count: int
    error: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _env_host() -> str:
    return (os.environ.get("IBKR_HOST") or DEFAULT_HOST).strip() or DEFAULT_HOST


def _env_port() -> int:
    raw = (os.environ.get("IBKR_PAPER_PORT") or os.environ.get("IBKR_PORT") or "").strip()
    if raw:
        return int(raw)
    return DEFAULT_PAPER_PORT


def _env_client_id() -> int:
    raw = (os.environ.get("IBKR_CLIENT_ID") or "").strip()
    if raw:
        return int(raw)
    return DEFAULT_CLIENT_ID


def _fmt_ts(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _disconnected_rows(symbols: list[str], error: str) -> list[dict[str, Any]]:
    return [
        TickerSmokeResult(
            ticker=sym,
            ibkr_connection="DISCONNECTED",
            contract_resolved="SKIPPED",
            latest_price=None,
            previous_close=None,
            price_timestamp=None,
            historical_daily_available=False,
            daily_bars_count=0,
            error=error,
        ).as_dict()
        for sym in symbols
    ]


def run_paper_smoke_test(
    tickers: list[str] | tuple[str, ...] | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    client_id: int | None = None,
    readonly: bool = True,
) -> dict[str, Any]:
    """
    Connect to Paper TWS/Gateway and probe a small ticker set.
    Returns a report dict suitable for printing/logging.
    """
    host = host or _env_host()
    port = int(port if port is not None else _env_port())
    client_id = int(client_id if client_id is not None else _env_client_id())
    symbols = [
        (t or "").strip().upper()
        for t in (tickers or DEFAULT_TEST_TICKERS)
        if (t or "").strip()
    ]
    if not symbols:
        symbols = list(DEFAULT_TEST_TICKERS)

    report: dict[str, Any] = {
        "ok": False,
        "phase": 2,
        "mode": "paper_tws_smoke",
        "host": host,
        "port": port,
        "client_id": client_id,
        "readonly": bool(readonly),
        "connected": False,
        "account_hint": None,
        "results": [],
        "error": None,
    }

    try:
        # Import after ensuring we can create a loop; Python 3.14 + ib_insync
        # need asyncio.run / Task context for wait_for timeouts.
        report = asyncio.run(
            _run_smoke_async(
                symbols,
                host=host,
                port=port,
                client_id=client_id,
                readonly=bool(readonly),
                report=report,
            )
        )
    except ImportError as exc:
        report["error"] = (
            "ib_insync is not installed. On Windows run: "
            "pip install -r requirements-ibkr.txt"
        )
        report["detail"] = str(exc)
        report["results"] = _disconnected_rows(symbols, report["error"])
    except Exception as exc:
        report["error"] = (
            f"Could not connect to IBKR at {host}:{port} (clientId={client_id}). "
            f"Open Paper TWS, log in, enable API, then retry. Detail: {exc}"
        )
        report["results"] = _disconnected_rows(symbols, report["error"])

    priced = [r for r in report["results"] if r.get("latest_price") is not None]
    report["ok"] = bool(report.get("connected") and priced)
    return report


async def _run_smoke_async(
    symbols: list[str],
    *,
    host: str,
    port: int,
    client_id: int,
    readonly: bool,
    report: dict[str, Any],
) -> dict[str, Any]:
    from ib_insync import IB

    ib = IB()
    try:
        await ib.connectAsync(
            host,
            port,
            clientId=client_id,
            readonly=readonly,
            timeout=8,
        )
    except Exception as exc:
        report["error"] = (
            f"Could not connect to IBKR at {host}:{port} (clientId={client_id}). "
            f"Open Paper TWS, log in, enable API, then retry. Detail: {exc}"
        )
        report["results"] = _disconnected_rows(symbols, report["error"])
        return report

    report["connected"] = True
    try:
        accounts = list(ib.managedAccounts() or [])
        report["account_hint"] = accounts[:3] if accounts else None
    except Exception:
        report["account_hint"] = None

    for sym in symbols:
        row = await _probe_symbol_async(ib, sym)
        report["results"].append(row.as_dict())

    try:
        ib.disconnect()
    except Exception:
        pass
    return report


async def _probe_symbol_async(ib: Any, symbol: str) -> TickerSmokeResult:
    from ib_insync import Stock

    conn_status = "CONNECTED" if ib.isConnected() else "DISCONNECTED"
    if not ib.isConnected():
        return TickerSmokeResult(
            ticker=symbol,
            ibkr_connection=conn_status,
            contract_resolved="SKIPPED",
            latest_price=None,
            previous_close=None,
            price_timestamp=None,
            historical_daily_available=False,
            daily_bars_count=0,
            error="IBKR session disconnected",
        )

    contract = Stock(symbol, "SMART", "USD")
    try:
        qualified = await ib.qualifyContractsAsync(contract)
    except Exception as exc:
        return TickerSmokeResult(
            ticker=symbol,
            ibkr_connection=conn_status,
            contract_resolved="FAILED",
            latest_price=None,
            previous_close=None,
            price_timestamp=None,
            historical_daily_available=False,
            daily_bars_count=0,
            error=f"qualifyContracts failed: {exc}",
        )

    if not qualified:
        return TickerSmokeResult(
            ticker=symbol,
            ibkr_connection=conn_status,
            contract_resolved="FAILED",
            latest_price=None,
            previous_close=None,
            price_timestamp=None,
            historical_daily_available=False,
            daily_bars_count=0,
            error="No contract returned for SMART/USD",
        )

    contract = qualified[0]
    latest: float | None = None
    prev_close: float | None = None
    price_ts: str | None = None
    bars_count = 0
    hist_ok = False
    err: str | None = None

    # Live/delayed snapshot (market data subscription may be delayed on Paper).
    try:
        ticker = ib.reqMktData(contract, "", False, False)
        await asyncio.sleep(2.5)
        for attr in ("last", "close", "marketPrice", "delayedLast", "delayedClose"):
            val = getattr(ticker, attr, None)
            try:
                if val is not None and float(val) == float(val) and float(val) > 0:
                    if attr in ("last", "marketPrice", "delayedLast") and latest is None:
                        latest = float(val)
                    if attr in ("close", "delayedClose") and prev_close is None:
                        prev_close = float(val)
            except (TypeError, ValueError):
                pass
        price_ts = _fmt_ts(getattr(ticker, "time", None))
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass
    except Exception as exc:
        err = f"reqMktData failed: {exc}"

    # Historical daily bars (also used as price fallback).
    try:
        bars = await ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr="1 Y",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        bars_count = len(bars or [])
        hist_ok = bars_count > 0
        if hist_ok:
            last_bar = bars[-1]
            if latest is None:
                try:
                    latest = float(last_bar.close)
                except (TypeError, ValueError):
                    pass
            if prev_close is None and bars_count >= 2:
                try:
                    prev_close = float(bars[-2].close)
                except (TypeError, ValueError):
                    pass
            if price_ts is None:
                price_ts = _fmt_ts(getattr(last_bar, "date", None))
    except Exception as exc:
        hist_msg = f"reqHistoricalData failed: {exc}"
        err = f"{err}; {hist_msg}" if err else hist_msg

    if latest is None and not err:
        err = "No usable last/close price (check market data subscriptions / delayed data)"

    return TickerSmokeResult(
        ticker=symbol,
        ibkr_connection=conn_status,
        contract_resolved="OK",
        latest_price=None if latest is None else round(latest, 4),
        previous_close=None if prev_close is None else round(prev_close, 4),
        price_timestamp=price_ts,
        historical_daily_available=hist_ok,
        daily_bars_count=bars_count,
        error=err,
    )


def format_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=== LeiBot IBKR Phase 2 - Paper TWS smoke test ===")
    lines.append(
        f"Host: {report.get('host')}  Port: {report.get('port')}  "
        f"ClientId: {report.get('client_id')}  ReadOnly: {report.get('readonly')}"
    )
    lines.append(f"Connected: {report.get('connected')}  OK: {report.get('ok')}")
    if report.get("account_hint") is not None:
        lines.append(f"Managed accounts (hint only): {report.get('account_hint')}")
    if report.get("error"):
        lines.append(f"ERROR: {report['error']}")
    lines.append("")
    for r in report.get("results") or []:
        lines.append(f"--- {r.get('ticker')} ---")
        lines.append(f"  IBKR connection:        {r.get('ibkr_connection')}")
        lines.append(f"  Contract resolved:      {r.get('contract_resolved')}")
        lines.append(f"  Latest price:           {r.get('latest_price')}")
        lines.append(f"  Previous close:         {r.get('previous_close')}")
        lines.append(f"  Price timestamp:        {r.get('price_timestamp')}")
        lines.append(f"  Historical daily avail: {r.get('historical_daily_available')}")
        lines.append(f"  Daily bars returned:    {r.get('daily_bars_count')}")
        lines.append(f"  Error/message:          {r.get('error')}")
        lines.append("")
    lines.append("Phase 2 complete - no production sync, no Yahoo changes, no orders.")
    return "\n".join(lines)
