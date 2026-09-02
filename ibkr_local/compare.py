"""
LeiBot IBKR Local Sync — Phase 3 (IBKR vs Yahoo/LeiBot compare only).

- Paper TWS read-only
- Live Yahoo history via LeiBot's load_yahoo_daily_closes (no schedule changes)
- Local dashboard_cache read (no writes)
- Never syncs to production
- Never places orders
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ibkr_local import (
    DEFAULT_CLIENT_ID,
    DEFAULT_HOST,
    DEFAULT_PAPER_PORT,
    DEFAULT_TEST_TICKERS,
    _env_client_id,
    _env_host,
    _env_port,
    _fmt_ts,
)


@dataclass
class SideSnapshot:
    source: str
    latest_price: float | None
    previous_close: float | None
    price_timestamp: str | None
    latest_daily_bar_date: str | None
    latest_daily_close: float | None
    daily_bars_count: int
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    # ISO / IBKR-ish
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in ("%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except ValueError:
            continue
    return None


def _trading_day_lag(older: date | None, newer: date | None) -> int | None:
    """How many NYSE-ish business days newer is ahead of older (can be negative)."""
    if older is None or newer is None:
        return None
    return int(np.busday_count(older, newer))


def _pct_diff(ibkr: float | None, yahoo: float | None) -> float | None:
    if ibkr is None or yahoo is None or yahoo == 0:
        return None
    return round((ibkr / yahoo - 1.0) * 100.0, 3)


def _local_db_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "leibot.db"


def fetch_leibot_cache(tickers: list[str]) -> dict[str, SideSnapshot]:
    path = _local_db_path()
    out: dict[str, SideSnapshot] = {}
    if not path.exists():
        for t in tickers:
            out[t] = SideSnapshot(
                source="leibot_cache",
                latest_price=None,
                previous_close=None,
                price_timestamp=None,
                latest_daily_bar_date=None,
                latest_daily_close=None,
                daily_bars_count=0,
                error=f"Local DB missing: {path}",
            )
        return out

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        for t in tickers:
            row = conn.execute(
                "SELECT ticker, price, updated_at FROM dashboard_cache WHERE ticker=?",
                (t,),
            ).fetchone()
            if not row:
                out[t] = SideSnapshot(
                    source="leibot_cache",
                    latest_price=None,
                    previous_close=None,
                    price_timestamp=None,
                    latest_daily_bar_date=None,
                    latest_daily_close=None,
                    daily_bars_count=0,
                    error="Not in local dashboard_cache",
                )
                continue
            updated = row["updated_at"]
            d = _parse_date(updated)
            price = None if row["price"] is None else float(row["price"])
            out[t] = SideSnapshot(
                source="leibot_cache",
                latest_price=None if price is None else round(price, 4),
                previous_close=None,
                price_timestamp=str(updated) if updated else None,
                # Cache has no explicit bar date; use updated_at calendar date as proxy.
                latest_daily_bar_date=d.isoformat() if d else None,
                latest_daily_close=None if price is None else round(price, 4),
                daily_bars_count=0,
                error=None,
            )
    finally:
        conn.close()
    return out


def fetch_yahoo_live(tickers: list[str]) -> dict[str, SideSnapshot]:
    """Read-only: same Yahoo daily path LeiBot uses. Does not write cache."""
    from market_data import load_yahoo_daily_closes

    out: dict[str, SideSnapshot] = {}
    for t in tickers:
        try:
            closes, hist, meta = load_yahoo_daily_closes(t, period="1y")
            if closes is None or closes.empty:
                out[t] = SideSnapshot(
                    source="yahoo_live",
                    latest_price=None,
                    previous_close=None,
                    price_timestamp=None,
                    latest_daily_bar_date=None,
                    latest_daily_close=None,
                    daily_bars_count=0,
                    error=f"Yahoo history empty (attempts={meta.get('attempts')})",
                )
                continue
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2]) if len(closes) >= 2 else None
            idx = closes.index[-1]
            bar_date = _parse_date(idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx)
            # Prefer Close timestamp from hist index
            out[t] = SideSnapshot(
                source="yahoo_live",
                latest_price=round(last, 4),
                previous_close=None if prev is None else round(prev, 4),
                price_timestamp=_fmt_ts(
                    idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
                ),
                latest_daily_bar_date=bar_date.isoformat() if bar_date else None,
                latest_daily_close=round(last, 4),
                daily_bars_count=int(len(closes)),
                error=None,
            )
        except Exception as exc:
            out[t] = SideSnapshot(
                source="yahoo_live",
                latest_price=None,
                previous_close=None,
                price_timestamp=None,
                latest_daily_bar_date=None,
                latest_daily_close=None,
                daily_bars_count=0,
                error=str(exc),
            )
    return out


async def _fetch_ibkr_async(
    symbols: list[str],
    *,
    host: str,
    port: int,
    client_id: int,
) -> tuple[bool, str | None, dict[str, SideSnapshot]]:
    from ib_insync import IB, Stock

    out: dict[str, SideSnapshot] = {}
    ib = IB()
    try:
        await ib.connectAsync(
            host, port, clientId=client_id, readonly=True, timeout=8
        )
    except Exception as exc:
        err = (
            f"Could not connect to IBKR at {host}:{port} (clientId={client_id}). "
            f"Detail: {exc}"
        )
        for sym in symbols:
            out[sym] = SideSnapshot(
                source="ibkr",
                latest_price=None,
                previous_close=None,
                price_timestamp=None,
                latest_daily_bar_date=None,
                latest_daily_close=None,
                daily_bars_count=0,
                error=err,
            )
        return False, err, out

    try:
        for sym in symbols:
            out[sym] = await _probe_ibkr_symbol(ib, sym)
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass
    return True, None, out


async def _probe_ibkr_symbol(ib: Any, symbol: str) -> SideSnapshot:
    from ib_insync import Stock

    if not ib.isConnected():
        return SideSnapshot(
            source="ibkr",
            latest_price=None,
            previous_close=None,
            price_timestamp=None,
            latest_daily_bar_date=None,
            latest_daily_close=None,
            daily_bars_count=0,
            error="IBKR session disconnected",
        )

    contract = Stock(symbol, "SMART", "USD")
    try:
        qualified = await ib.qualifyContractsAsync(contract)
    except Exception as exc:
        return SideSnapshot(
            source="ibkr",
            latest_price=None,
            previous_close=None,
            price_timestamp=None,
            latest_daily_bar_date=None,
            latest_daily_close=None,
            daily_bars_count=0,
            error=f"qualifyContracts failed: {exc}",
        )
    if not qualified:
        return SideSnapshot(
            source="ibkr",
            latest_price=None,
            previous_close=None,
            price_timestamp=None,
            latest_daily_bar_date=None,
            latest_daily_close=None,
            daily_bars_count=0,
            error="No contract returned for SMART/USD",
        )
    contract = qualified[0]

    latest: float | None = None
    prev_close: float | None = None
    price_ts: str | None = None
    bar_date: str | None = None
    bar_close: float | None = None
    bars_count = 0
    err: str | None = None

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
        if bars_count > 0:
            last_bar = bars[-1]
            try:
                bar_close = float(last_bar.close)
            except (TypeError, ValueError):
                bar_close = None
            d = _parse_date(getattr(last_bar, "date", None))
            bar_date = d.isoformat() if d else None
            if latest is None and bar_close is not None:
                latest = bar_close
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
        err = "No usable IBKR price"

    return SideSnapshot(
        source="ibkr",
        latest_price=None if latest is None else round(latest, 4),
        previous_close=None if prev_close is None else round(prev_close, 4),
        price_timestamp=price_ts,
        latest_daily_bar_date=bar_date,
        latest_daily_close=None if bar_close is None else round(bar_close, 4),
        daily_bars_count=bars_count,
        error=err,
    )


def run_phase3_compare(
    tickers: list[str] | tuple[str, ...] | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    client_id: int | None = None,
) -> dict[str, Any]:
    symbols = [
        (t or "").strip().upper()
        for t in (tickers or DEFAULT_TEST_TICKERS)
        if (t or "").strip()
    ] or list(DEFAULT_TEST_TICKERS)

    host = host or _env_host()
    port = int(port if port is not None else _env_port())
    client_id = int(client_id if client_id is not None else _env_client_id())

    connected, ib_err, ibkr = asyncio.run(
        _fetch_ibkr_async(symbols, host=host, port=port, client_id=client_id)
    )
    yahoo = fetch_yahoo_live(symbols)
    cache = fetch_leibot_cache(symbols)

    rows: list[dict[str, Any]] = []
    for sym in symbols:
        ib = ibkr.get(sym) or SideSnapshot("ibkr", None, None, None, None, None, 0, "missing")
        y = yahoo.get(sym) or SideSnapshot("yahoo_live", None, None, None, None, None, 0, "missing")
        c = cache.get(sym) or SideSnapshot("leibot_cache", None, None, None, None, None, 0, "missing")

        ib_d = _parse_date(ib.latest_daily_bar_date)
        y_d = _parse_date(y.latest_daily_bar_date)
        c_d = _parse_date(c.latest_daily_bar_date)

        lag_yahoo = _trading_day_lag(y_d, ib_d)
        lag_cache = _trading_day_lag(c_d, ib_d)
        diff_yahoo = _pct_diff(ib.latest_price, y.latest_price)
        diff_cache = _pct_diff(ib.latest_price, c.latest_price)

        yahoo_stale = lag_yahoo is not None and lag_yahoo > 0
        cache_stale = lag_cache is not None and lag_cache > 0

        rows.append(
            {
                "ticker": sym,
                "ibkr": ib.as_dict(),
                "yahoo_live": y.as_dict(),
                "leibot_cache": c.as_dict(),
                "ibkr_price": ib.latest_price,
                "ibkr_date": ib.latest_daily_bar_date,
                "yahoo_price": y.latest_price,
                "yahoo_date": y.latest_daily_bar_date,
                "lag_days_yahoo_vs_ibkr": lag_yahoo,
                "diff_pct_ibkr_vs_yahoo": diff_yahoo,
                "cache_price": c.latest_price,
                "cache_updated_date": c.latest_daily_bar_date,
                "lag_days_cache_vs_ibkr": lag_cache,
                "diff_pct_ibkr_vs_cache": diff_cache,
                "yahoo_stale": yahoo_stale,
                "cache_stale": cache_stale,
            }
        )

    any_cache_stale = any(r["cache_stale"] for r in rows)
    any_yahoo_stale = any(r["yahoo_stale"] for r in rows)
    max_cache_lag = max(
        (r["lag_days_cache_vs_ibkr"] for r in rows if r["lag_days_cache_vs_ibkr"] is not None),
        default=None,
    )
    max_yahoo_lag = max(
        (r["lag_days_yahoo_vs_ibkr"] for r in rows if r["lag_days_yahoo_vs_ibkr"] is not None),
        default=None,
    )

    # Verdict focused on the motivation: LeiBot cache going stale when Yahoo fails.
    if connected and any_cache_stale:
        solves = (
            "YES — IBKR returned fresher daily bars than local LeiBot/Yahoo cache "
            f"(max cache lag ≈ {max_cache_lag} trading day(s)). "
            "IBKR can fix the stale-cache problem that motivated this integration."
        )
    elif connected and not any_yahoo_stale and not any_cache_stale:
        solves = (
            "PARTIAL / NOT NEEDED TODAY — IBKR, live Yahoo, and local cache dates "
            "align for this tiny set; IBKR still provides a non-Yahoo backup path "
            "when Yahoo rate-limits or cache refresh fails."
        )
    elif connected and any_yahoo_stale:
        solves = (
            "YES — live Yahoo daily bars lag IBKR "
            f"(max lag ≈ {max_yahoo_lag} trading day(s)); IBKR is fresher."
        )
    elif not connected:
        solves = "INCONCLUSIVE — could not connect to Paper TWS."
    else:
        solves = (
            "INCONCLUSIVE — connected but could not compute lag "
            "(missing bar dates on one side)."
        )

    return {
        "ok": bool(connected and any(r["ibkr_price"] is not None for r in rows)),
        "phase": 3,
        "mode": "ibkr_vs_yahoo_compare",
        "host": host,
        "port": port,
        "client_id": client_id,
        "readonly": True,
        "connected": connected,
        "error": ib_err,
        "rows": rows,
        "verdict": solves,
        "notes": [
            "Lag Days = NYSE business-day count (IBKR latest daily bar date − Yahoo/cache date).",
            "Positive Lag Days means Yahoo/LeiBot is behind IBKR (stale).",
            "Difference % = (IBKR price / Yahoo-or-cache price − 1) × 100.",
            "LeiBot cache bar date is proxied from dashboard_cache.updated_at (no bar-date column).",
            "No production sync, no Yahoo workflow changes, no orders.",
        ],
    }


def format_compare_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=== LeiBot IBKR Phase 3 - IBKR vs Yahoo/LeiBot compare ===")
    lines.append(
        f"Host: {report.get('host')}  Port: {report.get('port')}  "
        f"ClientId: {report.get('client_id')}  ReadOnly: {report.get('readonly')}"
    )
    lines.append(f"Connected: {report.get('connected')}  OK: {report.get('ok')}")
    if report.get("error"):
        lines.append(f"ERROR: {report['error']}")
    lines.append("")
    lines.append(
        "Ticker | IBKR Price | IBKR Date | Yahoo Price | Yahoo Date | Lag Days | Difference %"
    )
    lines.append("-" * 96)
    for r in report.get("rows") or []:
        lag = r.get("lag_days_yahoo_vs_ibkr")
        lag_s = "n/a" if lag is None else str(lag)
        diff = r.get("diff_pct_ibkr_vs_yahoo")
        diff_s = "n/a" if diff is None else f"{diff:+.3f}%"
        lines.append(
            f"{r.get('ticker'):<6} | "
            f"{_fmt_num(r.get('ibkr_price')):>10} | "
            f"{str(r.get('ibkr_date') or 'n/a'):<10} | "
            f"{_fmt_num(r.get('yahoo_price')):>11} | "
            f"{str(r.get('yahoo_date') or 'n/a'):<10} | "
            f"{lag_s:>8} | "
            f"{diff_s:>12}"
        )
    lines.append("")
    lines.append(
        "Ticker | IBKR Price | IBKR Date | Cache Price | Cache Date | Lag Days | Difference %"
    )
    lines.append("-" * 96)
    for r in report.get("rows") or []:
        lag = r.get("lag_days_cache_vs_ibkr")
        lag_s = "n/a" if lag is None else str(lag)
        diff = r.get("diff_pct_ibkr_vs_cache")
        diff_s = "n/a" if diff is None else f"{diff:+.3f}%"
        lines.append(
            f"{r.get('ticker'):<6} | "
            f"{_fmt_num(r.get('ibkr_price')):>10} | "
            f"{str(r.get('ibkr_date') or 'n/a'):<10} | "
            f"{_fmt_num(r.get('cache_price')):>11} | "
            f"{str(r.get('cache_updated_date') or 'n/a'):<10} | "
            f"{lag_s:>8} | "
            f"{diff_s:>12}"
        )
    lines.append("")
    lines.append("=== Detail (per ticker) ===")
    for r in report.get("rows") or []:
        ib = r.get("ibkr") or {}
        y = r.get("yahoo_live") or {}
        c = r.get("leibot_cache") or {}
        lines.append(f"--- {r.get('ticker')} ---")
        lines.append(
            f"  IBKR   price={ib.get('latest_price')}  prev_close={ib.get('previous_close')}  "
            f"ts={ib.get('price_timestamp')}  bar_date={ib.get('latest_daily_bar_date')}  "
            f"bar_close={ib.get('latest_daily_close')}  bars={ib.get('daily_bars_count')}  "
            f"err={ib.get('error')}"
        )
        lines.append(
            f"  Yahoo  price={y.get('latest_price')}  prev_close={y.get('previous_close')}  "
            f"ts={y.get('price_timestamp')}  bar_date={y.get('latest_daily_bar_date')}  "
            f"bar_close={y.get('latest_daily_close')}  bars={y.get('daily_bars_count')}  "
            f"err={y.get('error')}"
        )
        lines.append(
            f"  Cache  price={c.get('latest_price')}  updated={c.get('price_timestamp')}  "
            f"date_proxy={c.get('latest_daily_bar_date')}  err={c.get('error')}"
        )
        lines.append(
            f"  Stale? yahoo={r.get('yahoo_stale')} (lag={r.get('lag_days_yahoo_vs_ibkr')})  "
            f"cache={r.get('cache_stale')} (lag={r.get('lag_days_cache_vs_ibkr')})"
        )
        lines.append("")
    for n in report.get("notes") or []:
        lines.append(f"NOTE: {n}")
    lines.append("")
    lines.append(f"VERDICT: {report.get('verdict')}")
    lines.append("Phase 3 complete - no production sync, no Yahoo changes, no orders.")
    return "\n".join(lines)


def _fmt_num(v: Any) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)
