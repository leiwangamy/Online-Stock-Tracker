"""
Shared IBKR broker adapter — Paper and Live use the same logic.

Data roles (local Full):
  IBKR: real-time / delayed price, pre/RTH/AH session moves, historical
        daily bars, earnings events when subscribed, available news feeds.
  Yahoo: temporary supplement for News + Fundamentals when IBKR lacks them.
  Trading (future): same adapter; LIVE orders gated by LIVE_TRADING_ENABLED.

Upper modules must not care whether the active profile is PAPER or LIVE.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from ibkr_local.config import (
    ConnectionProfile,
    assert_order_placement_allowed,
    get_connection_profile,
    live_trading_enabled,
    normalize_mode,
)

log = logging.getLogger("leibot.ibkr.adapter")
ET = ZoneInfo("America/New_York")


def _ensure_asyncio_loop() -> None:
    """Python 3.14+: ib_insync/eventkit import needs a current event loop."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


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


def _bar_datetime_et(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        d = _bar_date(value)
        if d is None:
            return None
        dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def _period_to_duration(period: str) -> str:
    p = (period or "2y").strip().lower()
    return {
        "1mo": "1 M",
        "3mo": "3 M",
        "6mo": "6 M",
        "1y": "1 Y",
        "2y": "2 Y",
        "5y": "5 Y",
        "10y": "10 Y",
        "max": "10 Y",
    }.get(p, "2 Y")


def bars_to_ohlcv(bars: list[Any]) -> pd.DataFrame:
    """ib_insync bars → DataFrame with Close (+ Volume when present)."""
    rows: list[dict[str, Any]] = []
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
        vol = None
        try:
            if getattr(bar, "volume", None) is not None:
                vol = float(bar.volume)
        except (TypeError, ValueError):
            vol = None
        open_px = None
        try:
            if getattr(bar, "open", None) is not None:
                open_px = float(bar.open)
        except (TypeError, ValueError):
            open_px = None
        rows.append(
            {
                "date": pd.Timestamp(d),
                "Open": open_px,
                "Close": c,
                "Volume": vol,
            }
        )
    if not rows:
        return pd.DataFrame(columns=["Open", "Close", "Volume"])
    return (
        pd.DataFrame(rows)
        .drop_duplicates("date", keep="last")
        .set_index("date")
        .sort_index()
    )


def _session_bucket(dt: datetime) -> str | None:
    t = dt.time()
    if time(4, 0) <= t < time(9, 30):
        return "PRE"
    if time(9, 30) <= t < time(16, 0):
        return "REGULAR"
    if time(16, 0) <= t < time(20, 0):
        return "AFTER"
    return None


class BrokerAdapter:
    """Mode-agnostic IBKR façade (market data + future orders)."""

    def __init__(self, profile: ConnectionProfile | None = None):
        self.profile = profile or get_connection_profile()

    @classmethod
    def for_mode(cls, mode: str | None) -> BrokerAdapter:
        return cls(get_connection_profile(normalize_mode(mode)))

    def _fail(self, symbol: str, error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "ticker": symbol,
            "error": error,
            "closes": pd.Series(dtype=float),
            "hist": None,
            "bars": 0,
            "provider": "ibkr",
            "mode": self.profile.mode,
        }

    async def _connect(self, ib: Any) -> str | None:
        p = self.profile
        try:
            await ib.connectAsync(
                p.host,
                p.port,
                clientId=p.client_id,
                readonly=bool(p.readonly),
                timeout=10,
            )
            return None
        except Exception as exc:
            return f"IBKR connect failed {p.label}: {exc}"

    async def _qualify(self, ib: Any, Stock: Any, symbol: str) -> Any | None:
        contract = Stock(symbol, "SMART", "USD")
        try:
            qualified = await ib.qualifyContractsAsync(contract)
        except Exception:
            return None
        return qualified[0] if qualified else None

    async def _qualify_index(
        self,
        ib: Any,
        Index: Any,
        *,
        symbol: str,
        exchange: str,
        currency: str = "USD",
    ) -> Any | None:
        contract = Index(symbol, exchange, currency)
        try:
            qualified = await ib.qualifyContractsAsync(contract)
        except Exception:
            return None
        return qualified[0] if qualified else None

    def fetch_index_daily_bars(
        self,
        specs: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        *,
        period: str = "1y",
    ) -> dict[str, dict[str, Any]]:
        """
        Historical daily closes for IBKR Index contracts.

        Each spec needs: key (result map key), ibkr_symbol, exchange,
        optional currency / what_to_show.
        Never falls back to Stock or Yahoo.
        """
        clean = []
        for raw in specs or []:
            if not isinstance(raw, dict):
                continue
            key = (raw.get("key") or raw.get("storage_ticker") or "").strip()
            sym = (raw.get("ibkr_symbol") or "").strip().upper()
            exch = (raw.get("exchange") or "").strip().upper()
            if not key or not sym or not exch:
                continue
            clean.append(
                {
                    "key": key,
                    "ibkr_symbol": sym,
                    "exchange": exch,
                    "currency": (raw.get("currency") or "USD").strip().upper() or "USD",
                    "what_to_show": (raw.get("what_to_show") or "TRADES").strip().upper()
                    or "TRADES",
                    "fallbacks": list(raw.get("fallbacks") or ()),
                }
            )
        if not clean:
            return {}
        duration = _period_to_duration(period)
        try:
            _ensure_asyncio_loop()
            return asyncio.run(
                self._fetch_index_daily_async(clean, duration=duration)
            )
        except ImportError as exc:
            err = (
                "ib_insync is not installed. Run: pip install -r requirements-ibkr.txt"
            )
            return {s["key"]: self._fail(s["key"], f"{err} ({exc})") for s in clean}
        except Exception as exc:
            err = (
                f"IBKR connect failed {self.profile.label}: {exc}. "
                f"Open {self.profile.mode} TWS/Gateway, enable API, retry."
            )
            log.warning(err)
            return {s["key"]: self._fail(s["key"], err) for s in clean}

    async def _fetch_index_daily_async(
        self, specs: list[dict[str, Any]], *, duration: str
    ) -> dict[str, dict[str, Any]]:
        _ensure_asyncio_loop()
        from ib_insync import IB, Index

        out: dict[str, dict[str, Any]] = {}
        ib = IB()
        err = await self._connect(ib)
        if err:
            return {s["key"]: self._fail(s["key"], err) for s in specs}
        try:
            for spec in specs:
                out[spec["key"]] = await self._one_index_daily(
                    ib, Index, spec, duration=duration
                )
                await asyncio.sleep(0.4)
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass
        return out

    async def _one_index_daily(
        self, ib: Any, Index: Any, spec: dict[str, Any], *, duration: str
    ) -> dict[str, Any]:
        key = spec["key"]
        if not ib.isConnected():
            return self._fail(key, "disconnected")

        # Primary + optional fallbacks: [(symbol, exchange), ...]
        attempts: list[tuple[str, str]] = [
            (spec["ibkr_symbol"], spec["exchange"]),
        ]
        for pair in spec.get("fallbacks") or []:
            if not pair or len(pair) < 2:
                continue
            attempts.append((str(pair[0]).upper(), str(pair[1]).upper()))

        last_err = "qualify failed"
        for sym, exch in attempts:
            contract = await self._qualify_index(
                ib,
                Index,
                symbol=sym,
                exchange=exch,
                currency=spec.get("currency") or "USD",
            )
            if contract is None:
                last_err = f"qualify failed Index({sym},{exch})"
                continue
            try:
                bars = await ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime="",
                    durationStr=duration,
                    barSizeSetting="1 day",
                    whatToShow=spec.get("what_to_show") or "TRADES",
                    useRTH=True,
                    formatDate=1,
                )
            except Exception as exc:
                last_err = f"index historical failed Index({sym},{exch}): {exc}"
                continue

            df = bars_to_ohlcv(list(bars or []))
            if df.empty or "Close" not in df.columns:
                last_err = f"empty index bars Index({sym},{exch})"
                continue
            closes = df["Close"].dropna().astype(float)
            if closes.empty:
                last_err = f"empty closes Index({sym},{exch})"
                continue
            return {
                "ok": True,
                "ticker": key,
                "ibkr_symbol": sym,
                "exchange": exch,
                "error": None,
                "closes": closes,
                "hist": df.copy(),
                "bars": int(len(closes)),
                "provider": "ibkr",
                "mode": self.profile.mode,
                "latest_bar_date": closes.index[-1].date().isoformat(),
            }
        return self._fail(key, last_err)

    def fetch_daily_bars(
        self,
        tickers: list[str] | tuple[str, ...],
        *,
        period: str = "2y",
        what_to_show: str = "TRADES",
    ) -> dict[str, dict[str, Any]]:
        symbols = [(t or "").strip().upper() for t in tickers if (t or "").strip()]
        if not symbols:
            return {}
        duration = _period_to_duration(period)
        try:
            _ensure_asyncio_loop()
            return asyncio.run(
                self._fetch_daily_async(
                    symbols, duration=duration, what_to_show=what_to_show
                )
            )
        except ImportError as exc:
            err = (
                "ib_insync is not installed. Run: pip install -r requirements-ibkr.txt"
            )
            return {s: self._fail(s, f"{err} ({exc})") for s in symbols}
        except Exception as exc:
            err = (
                f"IBKR connect failed {self.profile.label}: {exc}. "
                f"Open {self.profile.mode} TWS/Gateway, enable API, retry."
            )
            log.warning(err)
            return {s: self._fail(s, err) for s in symbols}

    async def _fetch_daily_async(
        self,
        symbols: list[str],
        *,
        duration: str,
        what_to_show: str,
    ) -> dict[str, dict[str, Any]]:
        _ensure_asyncio_loop()
        from ib_insync import IB, Stock

        out: dict[str, dict[str, Any]] = {}
        ib = IB()
        err = await self._connect(ib)
        if err:
            return {s: self._fail(s, err) for s in symbols}
        try:
            for sym in symbols:
                out[sym] = await self._one_symbol_daily(
                    ib, Stock, sym, duration=duration, what_to_show=what_to_show
                )
                await asyncio.sleep(0.35)
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass
        return out

    async def _one_symbol_daily(
        self,
        ib: Any,
        Stock: Any,
        symbol: str,
        *,
        duration: str,
        what_to_show: str,
    ) -> dict[str, Any]:
        if not ib.isConnected():
            return self._fail(symbol, "disconnected")
        contract = await self._qualify(ib, Stock, symbol)
        if contract is None:
            return self._fail(symbol, "qualify / no SMART/USD contract")
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
            return self._fail(symbol, f"historical failed: {exc}")

        df = bars_to_ohlcv(list(bars or []))
        if df.empty or "Close" not in df.columns:
            return self._fail(symbol, "empty bars")
        closes = df["Close"].dropna().astype(float)
        return {
            "ok": True,
            "ticker": symbol,
            "error": None,
            "closes": closes,
            "hist": df.copy(),
            "bars": int(len(closes)),
            "provider": "ibkr",
            "mode": self.profile.mode,
            "latest_bar_date": closes.index[-1].date().isoformat(),
        }

    def fetch_snapshots(
        self, tickers: list[str] | tuple[str, ...]
    ) -> dict[str, dict[str, Any]]:
        symbols = [(t or "").strip().upper() for t in tickers if (t or "").strip()]
        if not symbols:
            return {}
        try:
            return asyncio.run(self._snapshots_async(symbols))
        except Exception as exc:
            return {
                s: {
                    "ok": False,
                    "ticker": s,
                    "error": str(exc),
                    "last": None,
                    "close": None,
                    "provider": "ibkr",
                    "mode": self.profile.mode,
                }
                for s in symbols
            }

    async def _snapshots_async(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        from ib_insync import IB, Stock

        out: dict[str, dict[str, Any]] = {}
        ib = IB()
        err = await self._connect(ib)
        if err:
            return {
                s: {
                    "ok": False,
                    "ticker": s,
                    "error": err,
                    "last": None,
                    "close": None,
                    "provider": "ibkr",
                    "mode": self.profile.mode,
                }
                for s in symbols
            }
        try:
            for sym in symbols:
                out[sym] = await self._one_snapshot(ib, Stock, sym)
                await asyncio.sleep(0.25)
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass
        return out

    async def _one_snapshot(self, ib: Any, Stock: Any, symbol: str) -> dict[str, Any]:
        base: dict[str, Any] = {
            "ok": False,
            "ticker": symbol,
            "last": None,
            "close": None,
            "bid": None,
            "ask": None,
            "provider": "ibkr",
            "mode": self.profile.mode,
            "error": None,
        }
        contract = await self._qualify(ib, Stock, symbol)
        if contract is None:
            base["error"] = "qualify failed"
            return base
        try:
            ticker = ib.reqMktData(contract, "", False, False)
            await asyncio.sleep(2.0)
            last = close = None
            for attr, bucket in (
                ("last", "last"),
                ("marketPrice", "last"),
                ("delayedLast", "last"),
                ("close", "close"),
                ("delayedClose", "close"),
            ):
                val = getattr(ticker, attr, None)
                try:
                    f = float(val) if val is not None else None
                except (TypeError, ValueError):
                    f = None
                if f is not None and f == f and f > 0:
                    if bucket == "last" and last is None:
                        last = f
                    if bucket == "close" and close is None:
                        close = f
            bid = ask = None
            try:
                if ticker.bid and float(ticker.bid) > 0:
                    bid = float(ticker.bid)
            except (TypeError, ValueError):
                pass
            try:
                if ticker.ask and float(ticker.ask) > 0:
                    ask = float(ticker.ask)
            except (TypeError, ValueError):
                pass
            try:
                ib.cancelMktData(contract)
            except Exception:
                pass
            base.update(
                {
                    "ok": last is not None or close is not None,
                    "last": None if last is None else round(last, 4),
                    "close": None if close is None else round(close, 4),
                    "bid": None if bid is None else round(bid, 4),
                    "ask": None if ask is None else round(ask, 4),
                }
            )
            if not base["ok"]:
                base["error"] = "no usable snapshot (check market data subscription)"
            return base
        except Exception as exc:
            base["error"] = str(exc)
            return base

    def fetch_session_moves(
        self, tickers: list[str] | tuple[str, ...], *, lookback_days: int = 3
    ) -> dict[str, dict[str, Any]]:
        symbols = [(t or "").strip().upper() for t in tickers if (t or "").strip()]
        if not symbols:
            return {}
        try:
            return asyncio.run(
                self._session_moves_async(symbols, lookback_days=lookback_days)
            )
        except Exception as exc:
            return {
                s: {
                    "ok": False,
                    "ticker": s,
                    "error": str(exc),
                    "pre_pct": None,
                    "regular_pct": None,
                    "after_pct": None,
                    "provider": "ibkr",
                    "mode": self.profile.mode,
                }
                for s in symbols
            }

    async def _session_moves_async(
        self, symbols: list[str], *, lookback_days: int
    ) -> dict[str, dict[str, Any]]:
        from ib_insync import IB, Stock

        out: dict[str, dict[str, Any]] = {}
        ib = IB()
        err = await self._connect(ib)
        if err:
            return {
                s: {
                    "ok": False,
                    "ticker": s,
                    "error": err,
                    "pre_pct": None,
                    "regular_pct": None,
                    "after_pct": None,
                    "provider": "ibkr",
                    "mode": self.profile.mode,
                }
                for s in symbols
            }
        duration = f"{max(2, int(lookback_days) + 1)} D"
        try:
            for sym in symbols:
                out[sym] = await self._one_session_move(
                    ib, Stock, sym, duration=duration
                )
                await asyncio.sleep(0.4)
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass
        return out

    async def _one_session_move(
        self, ib: Any, Stock: Any, symbol: str, *, duration: str
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "ok": False,
            "ticker": symbol,
            "trading_date": None,
            "pre_pct": None,
            "regular_pct": None,
            "after_pct": None,
            "provider": "ibkr",
            "mode": self.profile.mode,
            "error": None,
        }
        contract = await self._qualify(ib, Stock, symbol)
        if contract is None:
            base["error"] = "qualify failed"
            return base
        try:
            bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting="30 mins",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=1,
            )
        except Exception as exc:
            base["error"] = f"session hist failed: {exc}"
            return base

        by_day: dict[str, dict[str, list[tuple[datetime, float]]]] = {}
        for bar in bars or []:
            dt = _bar_datetime_et(getattr(bar, "date", None))
            if dt is None:
                continue
            bucket = _session_bucket(dt)
            if not bucket:
                continue
            try:
                px = float(bar.close)
            except (TypeError, ValueError):
                continue
            if px <= 0:
                continue
            day = dt.date().isoformat()
            by_day.setdefault(day, {}).setdefault(bucket, []).append((dt, px))

        if not by_day:
            base["error"] = "no extended-hours bars"
            return base
        day = sorted(by_day.keys())[-1]
        sessions = by_day[day]
        prior_close = None
        prev_days = [d for d in sorted(by_day.keys()) if d < day]
        if prev_days:
            prev = by_day[prev_days[-1]]
            reg = prev.get("REGULAR") or []
            if reg:
                prior_close = reg[-1][1]
            else:
                flat = [p for b in prev.values() for _, p in b]
                if flat:
                    prior_close = flat[-1]

        def _ret(a: float | None, b: float | None) -> float | None:
            if a is None or b is None or a <= 0:
                return None
            return round((b / a - 1.0) * 100.0, 2)

        pre_bars = sessions.get("PRE") or []
        reg_bars = sessions.get("REGULAR") or []
        aft_bars = sessions.get("AFTER") or []
        pre_end = pre_bars[-1][1] if pre_bars else None
        reg_open = reg_bars[0][1] if reg_bars else (pre_end or prior_close)
        reg_end = reg_bars[-1][1] if reg_bars else None
        aft_end = aft_bars[-1][1] if aft_bars else None

        base.update(
            {
                "ok": True,
                "trading_date": day,
                "pre_pct": _ret(prior_close, pre_end) if pre_bars else None,
                "regular_pct": _ret(reg_open, reg_end) if reg_bars else None,
                "after_pct": _ret(reg_end or prior_close, aft_end) if aft_bars else None,
            }
        )
        return base

    def fetch_session_calendar(
        self, tickers: list[str] | tuple[str, ...], *, lookback_days: int = 20
    ) -> dict[str, dict[str, dict[str, float | None]]]:
        """
        Per ticker → trading_date → {pre, regular, after} percent moves.
        Only fills sessions that have real extended-hours bars (never invents AH).
        """
        symbols = [(t or "").strip().upper() for t in tickers if (t or "").strip()]
        if not symbols:
            return {}
        try:
            return asyncio.run(
                self._session_calendar_async(
                    symbols, lookback_days=max(3, int(lookback_days))
                )
            )
        except Exception:
            log.exception("fetch_session_calendar failed")
            return {}

    async def _session_calendar_async(
        self, symbols: list[str], *, lookback_days: int
    ) -> dict[str, dict[str, dict[str, float | None]]]:
        from ib_insync import IB, Stock

        out: dict[str, dict[str, dict[str, float | None]]] = {}
        ib = IB()
        err = await self._connect(ib)
        if err:
            log.warning("IBKR session calendar connect failed: %s", err)
            return out
        duration = f"{max(3, int(lookback_days) + 1)} D"
        try:
            for sym in symbols:
                out[sym] = await self._one_session_calendar(
                    ib, Stock, sym, duration=duration
                )
                await asyncio.sleep(0.35)
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass
        return out

    async def _one_session_calendar(
        self, ib: Any, Stock: Any, symbol: str, *, duration: str
    ) -> dict[str, dict[str, float | None]]:
        contract = await self._qualify(ib, Stock, symbol)
        if contract is None:
            return {}
        try:
            bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting="30 mins",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=1,
            )
        except Exception as exc:
            log.debug("session calendar hist failed for %s: %s", symbol, exc)
            return {}

        by_day: dict[str, dict[str, list[tuple[datetime, float]]]] = {}
        for bar in bars or []:
            dt = _bar_datetime_et(getattr(bar, "date", None))
            if dt is None:
                continue
            bucket = _session_bucket(dt)
            if not bucket:
                continue
            try:
                px = float(bar.close)
            except (TypeError, ValueError):
                continue
            if px <= 0:
                continue
            day = dt.date().isoformat()
            by_day.setdefault(day, {}).setdefault(bucket, []).append((dt, px))

        if not by_day:
            return {}

        def _ret(a: float | None, b: float | None) -> float | None:
            if a is None or b is None or a <= 0:
                return None
            return round((b / a - 1.0) * 100.0, 2)

        ordered = sorted(by_day.keys())
        result: dict[str, dict[str, float | None]] = {}
        for i, day in enumerate(ordered):
            sessions = by_day[day]
            prior_close = None
            if i > 0:
                prev = by_day[ordered[i - 1]]
                reg = prev.get("REGULAR") or []
                if reg:
                    prior_close = reg[-1][1]
                else:
                    flat = [p for b in prev.values() for _, p in b]
                    if flat:
                        prior_close = flat[-1]

            pre_bars = sessions.get("PRE") or []
            reg_bars = sessions.get("REGULAR") or []
            aft_bars = sessions.get("AFTER") or []
            pre_end = pre_bars[-1][1] if pre_bars else None
            reg_open = reg_bars[0][1] if reg_bars else (pre_end or prior_close)
            reg_end = reg_bars[-1][1] if reg_bars else None
            aft_end = aft_bars[-1][1] if aft_bars else None

            cell = {
                "pre": _ret(prior_close, pre_end) if pre_bars else None,
                "regular": _ret(reg_open, reg_end) if reg_bars else None,
                "after": _ret(reg_end or prior_close, aft_end) if aft_bars else None,
            }
            if cell["pre"] is not None or cell["regular"] is not None or cell["after"] is not None:
                result[day] = cell
        return result

    def fetch_earnings_events(
        self, tickers: list[str] | tuple[str, ...]
    ) -> dict[str, dict[str, Any]]:
        """IBKR calendar when subscribed; otherwise Yahoo should supplement."""
        symbols = [(t or "").strip().upper() for t in tickers if (t or "").strip()]
        return {
            s: {
                "ok": False,
                "ticker": s,
                "earnings_date": None,
                "events": [],
                "provider": "ibkr",
                "mode": self.profile.mode,
                "error": "IBKR earnings feed not configured / not subscribed",
                "supplement": "yahoo",
            }
            for s in symbols
        }

    def fetch_news_headlines(
        self, tickers: list[str] | tuple[str, ...], *, limit: int = 10
    ) -> dict[str, dict[str, Any]]:
        """IBKR news when providers enabled; otherwise Yahoo News supplement."""
        symbols = [(t or "").strip().upper() for t in tickers if (t or "").strip()]
        _ = limit
        return {
            s: {
                "ok": False,
                "ticker": s,
                "items": [],
                "provider": "ibkr",
                "mode": self.profile.mode,
                "error": "IBKR news providers not enabled",
                "supplement": "yahoo",
            }
            for s in symbols
        }

    def list_accounts(self) -> dict[str, Any]:
        try:
            return asyncio.run(self._list_accounts_async())
        except Exception as exc:
            return {
                "ok": False,
                "mode": self.profile.mode,
                "accounts": [],
                "error": str(exc),
            }

    async def _list_accounts_async(self) -> dict[str, Any]:
        from ib_insync import IB

        ib = IB()
        err = await self._connect(ib)
        if err:
            return {
                "ok": False,
                "mode": self.profile.mode,
                "accounts": [],
                "error": err,
            }
        try:
            accounts = list(ib.managedAccounts() or [])
            return {
                "ok": True,
                "mode": self.profile.mode,
                "accounts": accounts,
                "error": None,
                "live_trading_enabled": live_trading_enabled(),
            }
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass

    def place_order_stub(self, *_args: Any, **_kwargs: Any) -> None:
        assert_order_placement_allowed(self.profile.mode)
        raise NotImplementedError(
            f"IBKR order placement not implemented yet "
            f"(mode={self.profile.mode}, live_trading_enabled={live_trading_enabled()})"
        )


def get_adapter(mode: str | None = None) -> BrokerAdapter:
    if mode:
        return BrokerAdapter.for_mode(mode)
    return BrokerAdapter()


def fetch_daily_closes_via_adapter(
    tickers: list[str] | tuple[str, ...],
    *,
    period: str = "2y",
    mode: str | None = None,
) -> dict[str, dict[str, Any]]:
    return get_adapter(mode).fetch_daily_bars(tickers, period=period)
