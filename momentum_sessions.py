"""
MOMENTUM raw session observations (PRE / REGULAR / AFTER).

NIGHT is never inferred — always MISSING / N/A.

Yahoo default (intraday + prepost). IBKR not required.
Completed COMPLETE rows are never overwritten.
Scoring (UP/DOWN/SIDEWAYS) is intentionally NOT computed here.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from db import get_conn, init_db

log = logging.getLogger("leibot.momentum_sessions")

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

SESSION_PRE = "PRE"
SESSION_REGULAR = "REGULAR"
SESSION_AFTER = "AFTER"
SESSION_NIGHT = "NIGHT"
SESSION_GAP = "GAP"

STATUS_COMPLETE = "COMPLETE"
STATUS_LIVE = "LIVE"
STATUS_MISSING = "MISSING"

SOURCE_YAHOO = "YAHOO"

# ET boundaries (approved).
_PRE_START = time(4, 0)
_PRE_END = time(9, 30)
_RTH_START = time(9, 30)
_RTH_END = time(16, 0)
_AFTER_START = time(16, 0)
_AFTER_END = time(20, 0)

TRADING_SESSIONS = (SESSION_PRE, SESSION_REGULAR, SESSION_AFTER)


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _to_iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ET)
    return ts.astimezone(UTC).replace(microsecond=0).isoformat()


def _session_window(day, session: str) -> tuple[datetime, datetime]:
    """Inclusive start, exclusive end in ET for a calendar trading day."""
    if session == SESSION_PRE:
        start = datetime.combine(day, _PRE_START, tzinfo=ET)
        end = datetime.combine(day, _PRE_END, tzinfo=ET)
    elif session == SESSION_REGULAR:
        start = datetime.combine(day, _RTH_START, tzinfo=ET)
        end = datetime.combine(day, _RTH_END, tzinfo=ET)
    elif session == SESSION_AFTER:
        start = datetime.combine(day, _AFTER_START, tzinfo=ET)
        end = datetime.combine(day, _AFTER_END, tzinfo=ET)
    else:
        raise ValueError(f"unsupported trading session: {session}")
    return start, end


def _active_session(now_et: datetime) -> tuple[Any, str] | None:
    """Return (trading_date, session) if now is inside PRE/REGULAR/AFTER."""
    day = now_et.date()
    t = now_et.time().replace(tzinfo=None)
    if _PRE_START <= t < _PRE_END:
        return day, SESSION_PRE
    if _RTH_START <= t < _RTH_END:
        return day, SESSION_REGULAR
    if _AFTER_START <= t < _AFTER_END:
        return day, SESSION_AFTER
    return None


def _is_session_complete(day, session: str, now_et: datetime) -> bool:
    _, end = _session_window(day, session)
    return now_et >= end


def ensure_momentum_session_table() -> None:
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS momentum_session_obs (
                symbol TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                session TEXT NOT NULL,
                start_ts TEXT,
                end_ts TEXT,
                start_price REAL,
                end_price REAL,
                return_pct REAL,
                source TEXT NOT NULL DEFAULT 'YAHOO',
                data_status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, trading_date, session, source)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_momentum_session_sym_date
            ON momentum_session_obs(symbol, trading_date)
            """
        )


def _upsert_obs(row: dict[str, Any]) -> str:
    """
    Insert or update one observation.
    Never overwrite an existing COMPLETE row.
    Returns: inserted | updated | skipped_complete | skipped_invalid
    """
    ensure_momentum_session_table()
    sym = (row.get("symbol") or "").strip().upper()
    day = (row.get("trading_date") or "").strip()
    session = (row.get("session") or "").strip().upper()
    source = (row.get("source") or SOURCE_YAHOO).strip().upper()
    if not sym or not day or not session:
        return "skipped_invalid"

    with get_conn() as conn:
        existing = conn.execute(
            """
            SELECT data_status FROM momentum_session_obs
            WHERE symbol=? AND trading_date=? AND session=? AND source=?
            """,
            (sym, day, session, source),
        ).fetchone()
        if existing and str(existing["data_status"] or "").upper() == STATUS_COMPLETE:
            return "skipped_complete"

        conn.execute(
            """
            INSERT INTO momentum_session_obs (
              symbol, trading_date, session,
              start_ts, end_ts, start_price, end_price, return_pct,
              source, data_status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, trading_date, session, source) DO UPDATE SET
              start_ts=excluded.start_ts,
              end_ts=excluded.end_ts,
              start_price=excluded.start_price,
              end_price=excluded.end_price,
              return_pct=excluded.return_pct,
              data_status=excluded.data_status,
              updated_at=excluded.updated_at
            WHERE momentum_session_obs.data_status != 'COMPLETE'
            """,
            (
                sym,
                day,
                session,
                row.get("start_ts"),
                row.get("end_ts"),
                row.get("start_price"),
                row.get("end_price"),
                row.get("return_pct"),
                source,
                row.get("data_status") or STATUS_MISSING,
                row.get("updated_at") or _utcnow_iso(),
            ),
        )
    return "updated" if existing else "inserted"


def _slice_session(bars, day, session: str, *, now_et: datetime) -> dict[str, Any]:
    """
    bars: DataFrame indexed in ET with Open/High/Low/Close.
    Returns observation dict (may be MISSING).
    """
    import pandas as pd

    start, end = _session_window(day, session)
    day_s = day.isoformat()
    base = {
        "trading_date": day_s,
        "session": session,
        "source": SOURCE_YAHOO,
        "updated_at": _utcnow_iso(),
    }
    if bars is None or getattr(bars, "empty", True):
        return {
            **base,
            "start_ts": None,
            "end_ts": None,
            "start_price": None,
            "end_price": None,
            "return_pct": None,
            "data_status": STATUS_MISSING,
        }

    # Half-open [start, end)
    mask = (bars.index >= start) & (bars.index < end)
    chunk = bars.loc[mask]
    if chunk is None or chunk.empty:
        # Only mark MISSING if the session should already have started.
        if now_et < start:
            return {
                **base,
                "start_ts": None,
                "end_ts": None,
                "start_price": None,
                "end_price": None,
                "return_pct": None,
                "data_status": STATUS_MISSING,
            }
        return {
            **base,
            "start_ts": None,
            "end_ts": None,
            "start_price": None,
            "end_price": None,
            "return_pct": None,
            "data_status": STATUS_MISSING,
        }

    try:
        start_px = float(chunk.iloc[0]["Open"])
    except Exception:
        start_px = float(chunk.iloc[0]["Close"])
    end_px = float(chunk.iloc[-1]["Close"])
    if start_px <= 0 or end_px <= 0:
        return {
            **base,
            "start_ts": _to_iso(chunk.index[0].to_pydatetime()),
            "end_ts": _to_iso(chunk.index[-1].to_pydatetime()),
            "start_price": None,
            "end_price": None,
            "return_pct": None,
            "data_status": STATUS_MISSING,
        }

    ret = round((end_px / start_px - 1.0) * 100.0, 4)
    complete = _is_session_complete(day, session, now_et)
    status = STATUS_COMPLETE if complete else STATUS_LIVE
    # Live only if this is the active session today.
    active = _active_session(now_et)
    if not complete:
        if active and active[0] == day and active[1] == session:
            status = STATUS_LIVE
        elif now_et < start:
            status = STATUS_MISSING
        else:
            # Started but not active (e.g. weekend edge) — treat as COMPLETE if past end
            status = STATUS_COMPLETE if now_et >= end else STATUS_LIVE

    return {
        **base,
        "start_ts": _to_iso(chunk.index[0].to_pydatetime()),
        "end_ts": _to_iso(chunk.index[-1].to_pydatetime()),
        "start_price": round(start_px, 6),
        "end_price": round(end_px, 6),
        "return_pct": ret,
        "data_status": status,
    }


def _yahoo_intraday_prepost(ticker: str, *, period: str = "7d", interval: str = "5m"):
    import yfinance as yf
    import pandas as pd

    hist = yf.Ticker(ticker).history(
        period=period, interval=interval, prepost=True, auto_adjust=False
    )
    if hist is None or hist.empty:
        return None
    if hist.index.tz is None:
        hist.index = hist.index.tz_localize(ET)
    else:
        hist.index = hist.index.tz_convert(ET)
    need = {"Open", "Close"}
    if not need.issubset(set(hist.columns)):
        return None
    return hist


def _store_night_missing(symbol: str, day, *, source: str = SOURCE_YAHOO) -> None:
    """Explicit NIGHT placeholder — never invent prices."""
    _upsert_obs(
        {
            "symbol": symbol,
            "trading_date": day.isoformat(),
            "session": SESSION_NIGHT,
            "start_ts": None,
            "end_ts": None,
            "start_price": None,
            "end_price": None,
            "return_pct": None,
            "source": source,
            "data_status": STATUS_MISSING,
            "updated_at": _utcnow_iso(),
        }
    )


def _store_gap(
    symbol: str,
    trading_date: str,
    *,
    start_ts: str | None,
    end_ts: str | None,
    start_price: float | None,
    end_price: float | None,
    source: str = SOURCE_YAHOO,
) -> None:
    """Research-only GAP — not a trading session; never used for scoring."""
    ret = None
    status = STATUS_MISSING
    if (
        start_price is not None
        and end_price is not None
        and float(start_price) > 0
        and float(end_price) > 0
    ):
        ret = round((float(end_price) / float(start_price) - 1.0) * 100.0, 4)
        status = STATUS_COMPLETE
    _upsert_obs(
        {
            "symbol": symbol,
            "trading_date": trading_date,
            "session": SESSION_GAP,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "start_price": start_price,
            "end_price": end_price,
            "return_pct": ret,
            "source": source,
            "data_status": status,
            "updated_at": _utcnow_iso(),
        }
    )


def refresh_symbol_sessions(
    ticker: str, *, period: str = "7d", interval: str = "5m"
) -> dict[str, Any]:
    """Fetch Yahoo prepost bars and persist P/D/A (+ NIGHT MISSING, optional GAP)."""
    t = (ticker or "").strip().upper()
    now_et = datetime.now(ET)
    out: dict[str, Any] = {
        "ticker": t,
        "ok": False,
        "days": [],
        "counts": {"inserted": 0, "updated": 0, "skipped_complete": 0, "missing": 0},
        "error": None,
    }
    if not t:
        out["error"] = "empty_ticker"
        return out

    try:
        bars = _yahoo_intraday_prepost(t, period=period, interval=interval)
    except Exception as exc:
        log.warning("Yahoo intraday failed for %s: %s", t, exc)
        out["error"] = str(exc)
        return out
    if bars is None or bars.empty:
        out["error"] = "no_bars"
        return out

    days = sorted({ts.date() for ts in bars.index})
    # Keep last ~5 trading days for display window, but persist all returned.
    prev_after: dict[str, Any] | None = None
    for day in days:
        day_recs: dict[str, Any] = {}
        for session in TRADING_SESSIONS:
            obs = _slice_session(bars, day, session, now_et=now_et)
            obs["symbol"] = t
            action = _upsert_obs(obs)
            if action in out["counts"]:
                out["counts"][action] += 1
            if obs.get("data_status") == STATUS_MISSING:
                out["counts"]["missing"] += 1
            day_recs[session] = obs

        _store_night_missing(t, day)
        # Optional GAP: previous AFTER end → this PRE start (overnight calendar gap).
        if prev_after and day_recs.get(SESSION_PRE):
            pre = day_recs[SESSION_PRE]
            if (
                prev_after.get("end_price") is not None
                and pre.get("start_price") is not None
            ):
                _store_gap(
                    t,
                    day.isoformat(),
                    start_ts=prev_after.get("end_ts"),
                    end_ts=pre.get("start_ts"),
                    start_price=prev_after.get("end_price"),
                    end_price=pre.get("start_price"),
                )
        if day_recs.get(SESSION_AFTER) and day_recs[SESSION_AFTER].get("end_price"):
            prev_after = day_recs[SESSION_AFTER]
        out["days"].append(day.isoformat())

    out["ok"] = True
    return out


def refresh_momentum_watchlist_sessions(
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    from watchlist_config import get_momentum_watchlist

    pool = tickers if tickers is not None else get_momentum_watchlist()
    results = []
    for t in pool:
        results.append(refresh_symbol_sessions(t))
    return {
        "ok": all(r.get("ok") for r in results) if results else True,
        "count": len(results),
        "results": results,
        "updated_at": _utcnow_iso(),
    }


def list_session_obs(
    symbol: str,
    *,
    source: str = SOURCE_YAHOO,
    limit_days: int = 10,
) -> list[dict[str, Any]]:
    ensure_momentum_session_table()
    t = (symbol or "").strip().upper()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM momentum_session_obs
            WHERE symbol=? AND source=?
            ORDER BY trading_date DESC, session ASC
            """,
            (t, source),
        ).fetchall()
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        by_day.setdefault(str(d["trading_date"]), []).append(d)
    days = sorted(by_day.keys(), reverse=True)[: max(1, int(limit_days))]
    out: list[dict[str, Any]] = []
    for d in sorted(days):
        out.extend(by_day[d])
    return out


def latest_session_price(
    symbol: str, *, source: str = SOURCE_YAHOO
) -> float | None:
    """Most recent PRE/REGULAR/AFTER end_price (Yahoo session obs)."""
    ensure_momentum_session_table()
    t = (symbol or "").strip().upper()
    if not t:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT end_price FROM momentum_session_obs
            WHERE symbol=? AND source=?
              AND session IN ('PRE', 'REGULAR', 'AFTER')
              AND end_price IS NOT NULL AND end_price > 0
            ORDER BY trading_date DESC,
              CASE session
                WHEN 'AFTER' THEN 3
                WHEN 'REGULAR' THEN 2
                WHEN 'PRE' THEN 1
                ELSE 0
              END DESC,
              updated_at DESC
            LIMIT 1
            """,
            (t, source),
        ).fetchone()
    if not row or row["end_price"] is None:
        return None
    try:
        px = float(row["end_price"])
    except (TypeError, ValueError):
        return None
    return round(px, 2) if px > 0 else None


DAY_LABELS = ("D-4", "D-3", "D-2", "D-1", "D0")


def _session_return_decimal(obs: dict[str, Any] | None) -> float | None:
    """Return decimal session return, or None if unavailable (do not fabricate)."""
    if not obs or obs.get("return_pct") is None:
        return None
    try:
        return float(obs["return_pct"]) / 100.0
    except (TypeError, ValueError):
        return None


def compound_daily_total_pct(
    day_sessions: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """
    Daily Total = (1+PRE)*(1+REGULAR)*(1+AFTER) - 1  (decimal internally).

    NIGHT / GAP excluded. If any of P/D/A is unavailable → total N/A.
    LIVE sessions with a return_pct are included (observed, not fabricated).
    """
    out: dict[str, Any] = {
        "total_pct": None,
        "status": STATUS_MISSING,
        "missing_sessions": [],
        "has_live": False,
    }
    if not day_sessions:
        out["missing_sessions"] = list(TRADING_SESSIONS)
        return out

    factors: list[float] = []
    missing: list[str] = []
    has_live = False
    for sess in TRADING_SESSIONS:
        obs = day_sessions.get(sess)
        dec = _session_return_decimal(obs)
        if dec is None:
            missing.append(sess)
            continue
        factors.append(1.0 + dec)
        if str((obs or {}).get("data_status") or "").upper() == STATUS_LIVE:
            has_live = True

    out["missing_sessions"] = missing
    out["has_live"] = has_live
    if missing:
        # Do not fabricate a partial day total when a required session is missing.
        return out

    prod = 1.0
    for f in factors:
        prod *= f
    out["total_pct"] = round((prod - 1.0) * 100.0, 4)
    out["status"] = STATUS_LIVE if has_live else STATUS_COMPLETE
    return out


def compound_5d_total_pct(daily_totals_pct: list[float | None]) -> float | None:
    """5D Total = product(1 + each Daily Total) - 1. Any missing day → N/A."""
    if len(daily_totals_pct) != 5:
        return None
    prod = 1.0
    for t in daily_totals_pct:
        if t is None:
            return None
        prod *= 1.0 + (float(t) / 100.0)
    return round((prod - 1.0) * 100.0, 4)


def _fmt_signed_pct(v: float | None, *, digits: int = 1) -> str:
    if v is None:
        return "N/A"
    return f"{v:+.{digits}f}"


def format_session_history(
    symbol: str,
    *,
    source: str = SOURCE_YAHOO,
    n_days: int = 5,
) -> dict[str, Any]:
    """
    Public: compounded D-4..D0 + 5D totals.
    Admin: same plus raw P/D/A SESSION HISTORY compact string.
    """
    n = max(1, int(n_days))
    rows = list_session_obs(symbol, source=source, limit_days=n + 2)
    by_day: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r.get("session") not in TRADING_SESSIONS:
            continue
        by_day.setdefault(str(r["trading_date"]), {})[str(r["session"])] = r

    available_days = sorted(by_day.keys())[-n:]
    # Always expose fixed D-4..D0 slots (pad leading N/A when history is short).
    labels = list(DAY_LABELS[-n:]) if n <= 5 else [
        f"D-{n - 1 - i}" if i < n - 1 else "D0" for i in range(n)
    ]
    pad = max(0, n - len(available_days))
    day_slots: list[str | None] = [None] * pad + list(available_days)

    parts_out: list[dict[str, Any]] = []
    compact_bits: list[str] = []
    daily_totals: list[float | None] = []
    day_details: list[dict[str, Any]] = []

    for i, label in enumerate(labels):
        d = day_slots[i] if i < len(day_slots) else None
        day_map = by_day.get(d or "", {}) if d else {}
        segs = []
        for sess, abbr in (
            (SESSION_PRE, "P"),
            (SESSION_REGULAR, "D"),
            (SESSION_AFTER, "A"),
        ):
            obs = day_map.get(sess)
            if not obs or obs.get("return_pct") is None:
                segs.append(f"{abbr}:N/A")
            else:
                rp = float(obs["return_pct"])
                st = str(obs.get("data_status") or "").upper()
                if st == STATUS_LIVE:
                    segs.append(f"{abbr}:LIVE {rp:+.1f}")
                else:
                    segs.append(f"{abbr}:{rp:+.1f}")
        inner = " ".join(segs)
        parts_out.append(
            {
                "label": label,
                "date": d,
                "text": f"{label} [{inner}]" if d else f"{label} [P:N/A D:N/A A:N/A]",
            }
        )
        compact_bits.append(parts_out[-1]["text"])

        derived = compound_daily_total_pct(day_map if d else None)
        daily_totals.append(derived["total_pct"])
        day_details.append(
            {
                "label": label,
                "date": d,
                "total_pct": derived["total_pct"],
                "status": derived["status"],
                "missing_sessions": derived["missing_sessions"],
                "has_live": derived["has_live"],
                "display": _fmt_signed_pct(derived["total_pct"]),
            }
        )

    total_5d = compound_5d_total_pct(daily_totals) if n == 5 else None
    public_bits = [_fmt_signed_pct(v) for v in daily_totals]
    public_bits.append(_fmt_signed_pct(total_5d))
    public_compact = " | ".join(public_bits)

    # Active-session LIVE for MOMENTUM (start → latest), not RTH-close LIVE.
    now_et = datetime.now(ET)
    active = _active_session(now_et)
    live_pct = None
    live_session = None
    live_detail = None
    if active:
        day, sess = active
        obs = by_day.get(day.isoformat(), {}).get(sess)
        if obs and obs.get("return_pct") is not None:
            live_pct = float(obs["return_pct"])
            live_session = sess
            live_detail = obs

    return {
        "symbol": (symbol or "").strip().upper(),
        "days": parts_out,
        "compact": "  ".join(compact_bits) if compact_bits else "—",
        "daily_totals_pct": daily_totals,
        "daily_total_details": day_details,
        "total_5d_pct": total_5d,
        "public_compact": public_compact,
        "labels": labels,
        "live_pct": live_pct,
        "live_session": live_session,
        "live_detail": live_detail,
        "n_days": n,
    }
