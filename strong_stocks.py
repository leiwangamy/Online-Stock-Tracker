"""
Strong Stock Monitor V1 — persistent near-high strength (COUNT20).

Reuses LeiBot's existing 63D Position formula (market_data._range_63d).
No Financials / News filters. No IBKR.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

from db import get_conn, get_setting, init_db, list_universe, set_setting
from market_data import RANGE_63D_LOOKBACK, _range_63d

log = logging.getLogger("leibot.strong")

# Configurable rule constants (observe counts before further tuning).
STRONG_63D_POSITION_THRESHOLD = 80  # Strong Day definition
STRONG_COUNT_WINDOW = 20
STRONG_COUNT_THRESHOLD = 13  # qualify when COUNT20 > 12
STRONG_RETENTION_DAYS = 20

# Enough history for 63D warm-up + COUNT20 + retention (~6 months + buffer).
STRONG_HISTORY_PERIOD = "1y"
STRONG_DOWNLOAD_CHUNK = 80
STRONG_META_AS_OF = "strong_monitor_as_of"
STRONG_META_BUILT_AT = "strong_monitor_built_at"
STRONG_META_THRESHOLD = "strong_monitor_count_threshold"
STRONG_META_POS_THRESHOLD = "strong_monitor_pos_threshold"
STRONG_META_RETENTION = "strong_monitor_retention_days"


def rule_summary() -> str:
    return (
        f"63D High >= {STRONG_63D_POSITION_THRESHOLD}% | "
        f"COUNT20 >= {STRONG_COUNT_THRESHOLD}/{STRONG_COUNT_WINDOW} | "
        f"Retention {STRONG_RETENTION_DAYS} trading days"
    )


def _pools_label(row: dict[str, Any]) -> str:
    """Index membership for display (same labels as Watchlist)."""
    labels = []
    if row.get("in_sp500"):
        labels.append("S&P500")
    if row.get("in_ndx100"):
        labels.append("Nasdaq100")
    if row.get("in_sp400"):
        labels.append("S&P400")
    if row.get("in_sp600"):
        labels.append("S&P600")
    if row.get("in_tsx"):
        labels.append("TSX")
    return " / ".join(labels) if labels else "MANUAL"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pos_at_end(closes: pd.Series) -> float | None:
    """63D Position% for the series ending on its last bar (existing LeiBot formula)."""
    _low, _high, pos = _range_63d(closes, lookback=RANGE_63D_LOOKBACK)
    return pos


def _compute_series_metrics(
    closes: pd.Series,
) -> list[tuple[str, float | None, int, int]]:
    """
    Chronological metrics for one ticker.
    Returns list of (date_iso, range_63d_pos|None, is_strong, count20).
    Warm-up dates before a valid 63D Position are omitted.
    """
    clean = closes.dropna()
    if clean.empty:
        return []
    dates = [pd.Timestamp(ts).strftime("%Y-%m-%d") for ts in clean.index]
    values = [float(x) for x in clean.tolist()]
    n = len(values)
    out: list[tuple[str, float | None, int, int]] = []
    strong_flags: list[int] = []  # aligned with `out` only (valid 63D days)

    for i in range(n):
        if i + 1 < RANGE_63D_LOOKBACK:
            continue
        window = pd.Series(values[i + 1 - RANGE_63D_LOOKBACK : i + 1])
        pos = _pos_at_end(window)
        if pos is None:
            # high==low or invalid — skip (not a countable warm observation)
            continue
        is_strong = 1 if pos >= STRONG_63D_POSITION_THRESHOLD else 0
        strong_flags.append(is_strong)
        window_flags = strong_flags[-STRONG_COUNT_WINDOW:]
        count20 = int(sum(window_flags))
        out.append((dates[i], pos, is_strong, count20))
    return out


def _membership_from_metrics(
    metrics: list[tuple[str, float | None, int, int]],
    as_of: str,
    calendar: list[str] | None = None,
) -> dict[str, str] | None:
    """
    Replay qualify/renew on one ticker's metric history.
    Returns {first_qualified_date, last_qualified_date} if still active as of `as_of`.
    Retention is measured on `calendar` trading days when provided.
    """
    if not metrics:
        return None
    metric_dates = [m[0] for m in metrics]
    if as_of not in {d for d in metric_dates}:
        eligible = [d for d in metric_dates if d <= as_of]
        if not eligible:
            return None
        as_of_eff = eligible[-1]
    else:
        as_of_eff = as_of

    first_q: str | None = None
    last_q: str | None = None
    for d, _pos, _strong, count20 in metrics:
        if d > as_of_eff:
            break
        if count20 >= STRONG_COUNT_THRESHOLD:
            if first_q is None:
                first_q = d
            last_q = d

    if last_q is None:
        return None

    cal = calendar if calendar else metric_dates
    cal_idx = {d: i for i, d in enumerate(cal)}
    if last_q not in cal_idx:
        return None
    # as_of on calendar (on or before)
    if as_of_eff in cal_idx:
        a_idx = cal_idx[as_of_eff]
    else:
        before = [d for d in cal if d <= as_of_eff]
        if not before:
            return None
        a_idx = cal_idx[before[-1]]
    last_idx = cal_idx[last_q]
    if a_idx > last_idx + STRONG_RETENTION_DAYS:
        return None
    return {
        "first_qualified_date": first_q or last_q,
        "last_qualified_date": last_q,
    }


def days_remaining(last_qualified_date: str, as_of: str, calendar: list[str]) -> int | None:
    """Trading days remaining until expiry (0 = last day on list)."""
    if not calendar:
        return None
    idx = {d: i for i, d in enumerate(calendar)}
    if last_qualified_date not in idx:
        return None
    # Map as_of to nearest calendar date on/before
    if as_of in idx:
        a_idx = idx[as_of]
    else:
        before = [d for d in calendar if d <= as_of]
        if not before:
            return None
        a_idx = idx[before[-1]]
    expiry_idx = idx[last_qualified_date] + STRONG_RETENTION_DAYS
    return max(0, expiry_idx - a_idx)


def upsert_daily_bars(ticker: str, closes: pd.Series) -> int:
    """Persist closes into daily_bars (idempotent)."""
    clean = closes.dropna()
    if clean.empty:
        return 0
    rows = [
        (ticker, pd.Timestamp(ts).strftime("%Y-%m-%d"), float(px))
        for ts, px in clean.items()
    ]
    init_db()
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO daily_bars (ticker, date, close) VALUES (?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET close = excluded.close
            """,
            rows,
        )
    return len(rows)


def upsert_strong_daily(rows: list[tuple[str, str, float | None, int, int]]) -> int:
    """rows: (as_of_date, symbol, range_63d_pos, is_strong, count20)"""
    if not rows:
        return 0
    init_db()
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO strong_daily (
                as_of_date, symbol, range_63d_pos, is_strong, count20
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(as_of_date, symbol) DO UPDATE SET
                range_63d_pos = excluded.range_63d_pos,
                is_strong = excluded.is_strong,
                count20 = excluded.count20
            """,
            rows,
        )
    return len(rows)


def replace_membership(members: dict[str, dict[str, str]]) -> int:
    """Replace Strong Watchlist membership snapshot (active names only)."""
    init_db()
    now = _utc_now_iso()
    with get_conn() as conn:
        conn.execute("DELETE FROM strong_membership")
        if members:
            conn.executemany(
                """
                INSERT INTO strong_membership (
                    symbol, first_qualified_date, last_qualified_date, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        sym,
                        m["first_qualified_date"],
                        m["last_qualified_date"],
                        now,
                    )
                    for sym, m in members.items()
                ],
            )
    return len(members)


def _extract_close_from_download(data: pd.DataFrame, ticker: str, multi: bool) -> pd.Series | None:
    try:
        if multi:
            if ticker not in data.columns.get_level_values(0):
                return None
            sub = data[ticker]
            if isinstance(sub, pd.DataFrame):
                if "Close" in sub.columns:
                    return sub["Close"].dropna()
                return None
            return None
        if "Close" in data.columns:
            return data["Close"].dropna()
    except Exception:
        return None
    return None


def _download_chunk(tickers: list[str], period: str) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    if not tickers:
        return out
    # Prefer shared split-repaired closes (auto_adjust=True alone can mix scales).
    from market_data import load_yahoo_daily_closes

    if len(tickers) == 1:
        t = tickers[0]
        try:
            closes, _hist, _meta = load_yahoo_daily_closes(t, period=period)
            if closes is not None and not closes.empty:
                out[t] = closes
        except Exception as exc:
            log.warning("history failed %s: %s", t, exc)
        return out

    try:
        data = yf.download(
            tickers,
            period=period,
            group_by="ticker",
            auto_adjust=False,
            actions=True,
            threads=True,
            progress=False,
        )
    except Exception as exc:
        log.warning("download chunk failed (%s names): %s", len(tickers), exc)
        # Fall back per-ticker repaired path.
        for t in tickers:
            try:
                closes, _h, _m = load_yahoo_daily_closes(t, period=period)
                if closes is not None and len(closes) >= RANGE_63D_LOOKBACK:
                    out[t] = closes
            except Exception:
                pass
        return out

    if data is None or data.empty:
        return out

    multi = isinstance(data.columns, pd.MultiIndex)
    from market_data import apply_yahoo_split_factors, repair_close_scale_jumps

    for t in tickers:
        series = _extract_close_from_download(data, t, multi=multi)
        if series is None or len(series) < RANGE_63D_LOOKBACK:
            # Per-name repair fetch if batch extract failed / too short.
            try:
                closes, _h, _m = load_yahoo_daily_closes(t, period=period)
                if closes is not None and len(closes) >= RANGE_63D_LOOKBACK:
                    out[t] = closes
            except Exception:
                pass
            continue
        splits = None
        try:
            if multi and "Stock Splits" in data[t].columns:
                splits = data[t]["Stock Splits"]
            elif (not multi) and "Stock Splits" in data.columns:
                splits = data["Stock Splits"]
        except Exception:
            splits = None
        series = apply_yahoo_split_factors(series, splits)
        series, _fixes = repair_close_scale_jumps(series)
        series = series.dropna()
        if len(series) >= RANGE_63D_LOOKBACK:
            out[t] = series
    return out


def run_backfill(
    *,
    period: str = STRONG_HISTORY_PERIOD,
    max_tickers: int | None = None,
    chunk_size: int = STRONG_DOWNLOAD_CHUNK,
) -> dict[str, Any]:
    """
    One-time (or refresh) historical reconstruction:
    download history → 63D → Strong Day → COUNT20 → qualify/renew → membership.
    Idempotent upserts.
    """
    init_db()
    t0 = time.time()
    universe = list_universe()
    tickers = [str(r["ticker"]).upper() for r in universe if r.get("ticker")]
    if max_tickers is not None:
        tickers = tickers[: max(0, int(max_tickers))]

    log.info("Strong backfill start: %s tickers period=%s", len(tickers), period)

    members: dict[str, dict[str, str]] = {}
    obs_rows = 0
    ok = 0
    errors = 0
    latest_as_of = ""

    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i : i + chunk_size]
        series_map = _download_chunk(chunk, period)
        for t in chunk:
            series = series_map.get(t)
            if series is None or series.empty:
                errors += 1
                continue
            try:
                upsert_daily_bars(t, series)
                metrics = _compute_series_metrics(series)
                if not metrics:
                    errors += 1
                    continue
                batch = [
                    (d, t, pos, is_strong, count20)
                    for d, pos, is_strong, count20 in metrics
                ]
                obs_rows += upsert_strong_daily(batch)
                as_of = metrics[-1][0]
                if as_of > latest_as_of:
                    latest_as_of = as_of
                ok += 1
            except Exception as exc:
                errors += 1
                log.warning("strong process %s failed: %s", t, exc)

        log.info(
            "Strong backfill progress %s/%s ok=%s errors=%s",
            min(i + chunk_size, len(tickers)),
            len(tickers),
            ok,
            errors,
        )

    # Replay membership against a common as_of (latest observed trading day).
    if latest_as_of:
        members = _rebuild_membership_from_db(latest_as_of)

    n_mem = replace_membership(members)
    set_setting(STRONG_META_AS_OF, latest_as_of or "")
    set_setting(STRONG_META_BUILT_AT, _utc_now_iso())
    set_setting(STRONG_META_THRESHOLD, STRONG_COUNT_THRESHOLD)
    set_setting(STRONG_META_POS_THRESHOLD, STRONG_63D_POSITION_THRESHOLD)
    set_setting(STRONG_META_RETENTION, STRONG_RETENTION_DAYS)
    elapsed = round(time.time() - t0, 1)
    result = {
        "tickers": len(tickers),
        "ok": ok,
        "errors": errors,
        "observations_upserted": obs_rows,
        "active_members": n_mem,
        "as_of": latest_as_of,
        "elapsed_sec": elapsed,
        "rules": rule_summary(),
    }
    log.info("Strong backfill done: %s", result)
    return result


def _rebuild_membership_from_db(as_of: str) -> dict[str, dict[str, str]]:
    """Replay qualify/renew for all symbols from strong_daily up to as_of."""
    init_db()
    with get_conn() as conn:
        cal_rows = conn.execute(
            """
            SELECT DISTINCT as_of_date FROM strong_daily
            WHERE as_of_date <= ?
            ORDER BY as_of_date
            """,
            (as_of,),
        ).fetchall()
        calendar = [r["as_of_date"] for r in cal_rows]
        rows = conn.execute(
            """
            SELECT symbol, as_of_date, range_63d_pos, is_strong, count20
            FROM strong_daily
            WHERE as_of_date <= ?
            ORDER BY symbol, as_of_date
            """,
            (as_of,),
        ).fetchall()

    by_sym: dict[str, list[tuple[str, float | None, int, int]]] = {}
    for r in rows:
        sym = r["symbol"]
        by_sym.setdefault(sym, []).append(
            (
                r["as_of_date"],
                r["range_63d_pos"],
                int(r["is_strong"] or 0),
                int(r["count20"] or 0),
            )
        )

    members: dict[str, dict[str, str]] = {}
    for sym, metrics in by_sym.items():
        mem = _membership_from_metrics(metrics, as_of, calendar=calendar)
        if mem:
            members[sym] = mem
    return members


def rebuild_membership_only() -> dict[str, Any]:
    """
    Recompute Strong Watchlist membership from existing strong_daily rows
    (no Yahoo re-download). Uses current STRONG_COUNT_THRESHOLD + retention rules.
    """
    init_db()
    as_of = (get_setting(STRONG_META_AS_OF, "") or "").strip()
    if not as_of:
        dates = _latest_trading_dates(1)
        as_of = dates[0] if dates else ""
    if not as_of:
        return {"active_members": 0, "as_of": "", "rebuilt": False}
    members = _rebuild_membership_from_db(as_of)
    n = replace_membership(members)
    set_setting(STRONG_META_AS_OF, as_of)
    set_setting(STRONG_META_BUILT_AT, _utc_now_iso())
    set_setting(STRONG_META_THRESHOLD, STRONG_COUNT_THRESHOLD)
    set_setting(STRONG_META_POS_THRESHOLD, STRONG_63D_POSITION_THRESHOLD)
    set_setting(STRONG_META_RETENTION, STRONG_RETENTION_DAYS)
    log.info(
        "Strong membership rebuilt count=%s pos=%s retention=%s active=%s as_of=%s",
        STRONG_COUNT_THRESHOLD,
        STRONG_63D_POSITION_THRESHOLD,
        STRONG_RETENTION_DAYS,
        n,
        as_of,
    )
    return {
        "active_members": n,
        "as_of": as_of,
        "threshold": STRONG_COUNT_THRESHOLD,
        "pos_threshold": STRONG_63D_POSITION_THRESHOLD,
        "retention_days": STRONG_RETENTION_DAYS,
        "rebuilt": True,
    }


def recalculate_strong_from_stored_positions() -> dict[str, Any]:
    """
    Recompute is_strong + COUNT20 for every strong_daily row from stored
    range_63d_pos using the current STRONG_63D_POSITION_THRESHOLD, then
    wipe and rebuild Strong Watchlist membership (qualify/renew/retention).

    Does not re-download Yahoo history. Discards prior membership built under
    an older Strong Day rule.
    """
    init_db()
    t0 = time.time()
    with get_conn() as conn:
        symbols = [
            r["symbol"]
            for r in conn.execute(
                "SELECT DISTINCT symbol FROM strong_daily ORDER BY symbol"
            ).fetchall()
        ]

    updated = 0
    for sym in symbols:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT as_of_date, range_63d_pos
                FROM strong_daily
                WHERE symbol = ?
                ORDER BY as_of_date
                """,
                (sym,),
            ).fetchall()
        if not rows:
            continue
        # Only days with a valid 63D Position participate in COUNT20
        # (same rule as _compute_series_metrics).
        strong_flags: list[int] = []
        batch: list[tuple[int, int, float, str, str]] = []
        for r in rows:
            pos = r["range_63d_pos"]
            if pos is None:
                continue
            try:
                pos_f = float(pos)
            except (TypeError, ValueError):
                continue
            if pos_f != pos_f:  # NaN
                continue
            is_strong = 1 if pos_f >= STRONG_63D_POSITION_THRESHOLD else 0
            strong_flags.append(is_strong)
            count20 = int(sum(strong_flags[-STRONG_COUNT_WINDOW:]))
            batch.append((is_strong, count20, pos_f, r["as_of_date"], sym))

        if not batch:
            continue
        with get_conn() as conn:
            # Reset then rewrite — clears any stale is_strong from prior threshold.
            conn.execute(
                """
                UPDATE strong_daily
                SET is_strong = 0, count20 = 0
                WHERE symbol = ?
                """,
                (sym,),
            )
            conn.executemany(
                """
                UPDATE strong_daily
                SET is_strong = ?, count20 = ?, range_63d_pos = ?
                WHERE as_of_date = ? AND symbol = ?
                """,
                batch,
            )
        updated += len(batch)

    # Discard old membership entirely, then replay under new Strong Day + COUNT rules.
    mem = rebuild_membership_only()
    elapsed = round(time.time() - t0, 1)
    result = {
        "symbols": len(symbols),
        "observations_updated": updated,
        "active_members": mem.get("active_members", 0),
        "as_of": mem.get("as_of", ""),
        "pos_threshold": STRONG_63D_POSITION_THRESHOLD,
        "count_threshold": STRONG_COUNT_THRESHOLD,
        "elapsed_sec": elapsed,
        "rules": rule_summary(),
    }
    log.info("Strong recalculate from stored positions: %s", result)
    return result


def ensure_strong_rules_match() -> bool:
    """
    If Strong Day %, COUNT threshold, or retention constants differ from last build meta,
    recalculate as needed.
    """
    init_db()
    stored_count = get_setting(STRONG_META_THRESHOLD, None)
    stored_pos = get_setting(STRONG_META_POS_THRESHOLD, None)
    stored_ret = get_setting(STRONG_META_RETENTION, None)
    try:
        stored_count_i = int(stored_count) if stored_count not in (None, "") else None
    except (TypeError, ValueError):
        stored_count_i = None
    try:
        stored_pos_i = int(stored_pos) if stored_pos not in (None, "") else None
    except (TypeError, ValueError):
        stored_pos_i = None
    try:
        stored_ret_i = int(stored_ret) if stored_ret not in (None, "") else None
    except (TypeError, ValueError):
        stored_ret_i = None

    pos_changed = stored_pos_i != STRONG_63D_POSITION_THRESHOLD
    count_changed = stored_count_i != STRONG_COUNT_THRESHOLD
    ret_changed = stored_ret_i != STRONG_RETENTION_DAYS

    if not pos_changed and not count_changed and not ret_changed:
        return False

    if pos_changed:
        # Strong Day definition changed → rewrite is_strong/COUNT20, then membership.
        recalculate_strong_from_stored_positions()
    else:
        # COUNT and/or retention only → replay membership on existing Strong Day flags.
        rebuild_membership_only()
    return True


def ensure_membership_matches_threshold() -> bool:
    """Backward-compatible alias."""
    return ensure_strong_rules_match()


def run_incremental_update(*, period: str = "3mo") -> dict[str, Any]:
    """
    Daily incremental path: refresh recent history, upsert observations, rebuild membership.
    Idempotent for the same trading date.
    """
    return run_backfill(period=period, max_tickers=None)


def _latest_trading_dates(limit: int = STRONG_COUNT_WINDOW) -> list[str]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT as_of_date FROM strong_daily
            ORDER BY as_of_date DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    # newest first (left → right in Daily Strong Stocks UI)
    return [r["as_of_date"] for r in rows]


def _fmt_date_short(iso: str) -> str:
    """YYYY-MM-DD → 'Aug 19' style for headers."""
    try:
        dt = datetime.strptime(iso[:10], "%Y-%m-%d")
        return dt.strftime("%b %d").replace(" 0", " ")
    except Exception:
        return iso[5:] if len(iso) >= 10 else iso


def build_daily_strong_stocks(
    *, window: int = STRONG_COUNT_WINDOW
) -> dict[str, Any]:
    """
    Tab 1 — Daily Strong Stocks.
    Each date column lists ONLY stocks with 63D Position >= threshold that day,
    sorted by position descending. Column lengths may differ.
    Dates ordered newest → oldest (left → right).
    """
    init_db()
    dates = _latest_trading_dates(window)
    if not dates:
        return {
            "dates": [],
            "columns": [],
            "max_rows": 0,
            "window": window,
            "threshold_pos": STRONG_63D_POSITION_THRESHOLD,
        }

    placeholders = ",".join("?" * len(dates))
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT symbol, as_of_date, range_63d_pos
            FROM strong_daily
            WHERE as_of_date IN ({placeholders})
              AND is_strong = 1
              AND range_63d_pos IS NOT NULL
              AND range_63d_pos >= ?
            ORDER BY as_of_date DESC, range_63d_pos DESC, symbol
            """,
            [*dates, float(STRONG_63D_POSITION_THRESHOLD)],
        ).fetchall()

    by_date: dict[str, list[dict[str, Any]]] = {d: [] for d in dates}
    for r in rows:
        d = r["as_of_date"]
        if d not in by_date:
            continue
        by_date[d].append(
            {
                "symbol": r["symbol"],
                "range_63d_pos": float(r["range_63d_pos"]),
            }
        )

    columns: list[dict[str, Any]] = []
    for d in dates:
        stocks = by_date.get(d) or []
        columns.append(
            {
                "date": d,
                "label": _fmt_date_short(d),
                "count": len(stocks),
                "stocks": stocks,
            }
        )

    max_rows = max((c["count"] for c in columns), default=0)
    return {
        "dates": dates,
        "columns": columns,
        "max_rows": max_rows,
        "window": window,
        "threshold_pos": STRONG_63D_POSITION_THRESHOLD,
    }


def list_count20_ranking(
    *, window: int = STRONG_COUNT_WINDOW
) -> dict[str, Any]:
    """
    Tab 2 — COUNT20 Ranking (historical frequency in the latest window).
    Current Position may be below 80%; that is expected.
    """
    from db import get_dashboard_by_tickers
    from market_data import compute_target_proxy_mos

    init_db()
    as_of = get_setting(STRONG_META_AS_OF, "") or ""
    dates = _latest_trading_dates(window)
    distribution = {i: 0 for i in range(0, window + 1)}

    if not dates:
        return {
            "as_of": as_of,
            "window": window,
            "threshold": STRONG_COUNT_THRESHOLD,
            "distribution": distribution,
            "distribution_line": "",
            "rows": [],
            "count": 0,
        }

    placeholders = ",".join("?" * len(dates))
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT symbol, as_of_date, range_63d_pos, is_strong
            FROM strong_daily
            WHERE as_of_date IN ({placeholders})
            ORDER BY symbol, as_of_date
            """,
            dates,
        ).fetchall()
        members = {
            r["symbol"]: dict(r)
            for r in conn.execute(
                "SELECT symbol, first_qualified_date, last_qualified_date "
                "FROM strong_membership"
            ).fetchall()
        }

    # Aggregate per symbol within window
    agg: dict[str, dict[str, Any]] = {}
    date_set = set(dates)
    latest_date = dates[0] if dates else ""  # newest first
    for r in rows:
        sym = r["symbol"]
        d = r["as_of_date"]
        if d not in date_set:
            continue
        slot = agg.setdefault(
            sym,
            {
                "count20": 0,
                "last_strong_date": None,
                "pos_by_date": {},
            },
        )
        pos = r["range_63d_pos"]
        if pos is not None:
            slot["pos_by_date"][d] = float(pos)
        if int(r["is_strong"] or 0) == 1:
            slot["count20"] += 1
            if slot["last_strong_date"] is None or d > slot["last_strong_date"]:
                slot["last_strong_date"] = d

    # Only keep names that appeared at least once as Strong Day (COUNT>=1)
    # or show all with observations? User wants COUNT ranking — include COUNT>=1
    # for distribution include 0 as well for stocks that had observations but never strong?
    # Distribution purpose: observe counts at each level. Include all symbols with
    # any strong_daily row in window; COUNT can be 0.
    for slot in agg.values():
        c = int(slot["count20"])
        distribution[c] = distribution.get(c, 0) + 1

    symbols = list(agg.keys())
    dash = get_dashboard_by_tickers(symbols) if symbols else {}

    out_rows: list[dict[str, Any]] = []
    for sym, slot in agg.items():
        count20 = int(slot["count20"])
        if count20 <= 0:
            continue  # ranking table: only stocks that appeared in Daily Strong Stocks
        drow = dash.get(sym) or {}
        # Prefer latest window date's stored pos; fallback dashboard
        cur_pos = slot["pos_by_date"].get(latest_date)
        if cur_pos is None and drow.get("range_63d_pos") is not None:
            cur_pos = float(drow["range_63d_pos"])
        price = drow.get("price")
        name = drow.get("name") or ""
        mos_t = None
        try:
            mos_info = compute_target_proxy_mos(price, drow.get("target_1y"))
            if isinstance(mos_info, dict):
                mos_t = mos_info.get("mos_t")
        except Exception:
            mos_t = None

        mem = members.get(sym)
        if mem:
            wl_status = "on_watchlist"
        elif count20 >= STRONG_COUNT_THRESHOLD:
            wl_status = "qualifies"
        else:
            wl_status = "—"

        out_rows.append(
            {
                "symbol": sym,
                "name": name,
                "count20": count20,
                "range_63d_pos": cur_pos,
                "last_strong_date": slot["last_strong_date"],
                "price": price,
                "ai_score": None,
                "mos_t": mos_t,
                "watchlist_status": wl_status,
                "financials": None,
                "news": None,
            }
        )

    out_rows.sort(
        key=lambda r: (
            -(r.get("count20") or 0),
            -(r.get("range_63d_pos") if r.get("range_63d_pos") is not None else -1),
            r.get("symbol") or "",
        )
    )
    for i, r in enumerate(out_rows, start=1):
        r["rank"] = i

    # Compact distribution: 20 → 0 for threshold decisions
    parts = [f"{c}: {distribution.get(c, 0)}" for c in range(window, -1, -1)]
    distribution_line = " · ".join(parts)

    return {
        "as_of": as_of,
        "window": window,
        "threshold": STRONG_COUNT_THRESHOLD,
        "distribution": distribution,
        "distribution_line": distribution_line,
        "rows": out_rows,
        "count": len(out_rows),
        "n_ge_threshold": sum(
            1 for r in out_rows if r["count20"] >= STRONG_COUNT_THRESHOLD
        ),
    }


def list_active_strong_watchlist() -> dict[str, Any]:
    """
    Tab 3 — Strong Watchlist: COUNT20 >= threshold + 20-day retention.
    Current Position may be below 80% (intentional retention).
    """
    from db import get_dashboard_by_tickers
    from market_data import compute_target_proxy_mos

    init_db()
    as_of = get_setting(STRONG_META_AS_OF, "") or ""
    built_at = get_setting(STRONG_META_BUILT_AT, "") or ""
    dates = _latest_trading_dates(STRONG_COUNT_WINDOW)
    latest_date = dates[0] if dates else as_of

    with get_conn() as conn:
        members = conn.execute(
            """
            SELECT symbol, first_qualified_date, last_qualified_date
            FROM strong_membership
            ORDER BY symbol
            """
        ).fetchall()
        cal_rows = conn.execute(
            "SELECT DISTINCT as_of_date FROM strong_daily ORDER BY as_of_date"
        ).fetchall()
        calendar = [r["as_of_date"] for r in cal_rows]

    empty = {
        "as_of": as_of,
        "built_at": built_at,
        "rules": rule_summary(),
        "count": 0,
        "count_qualifying": 0,
        "count_retention": 0,
        "rows": [],
    }
    if not members:
        return empty

    symbols = [m["symbol"] for m in members]
    latest_obs: dict[str, dict[str, Any]] = {}
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.symbol, s.as_of_date, s.range_63d_pos, s.is_strong, s.count20
            FROM strong_daily s
            INNER JOIN (
                SELECT symbol, MAX(as_of_date) AS mx
                FROM strong_daily
                WHERE symbol IN ({})
                GROUP BY symbol
            ) t ON s.symbol = t.symbol AND s.as_of_date = t.mx
            """.format(
                ",".join("?" * len(symbols))
            ),
            symbols,
        ).fetchall()
        for row in rows:
            latest_obs[row["symbol"]] = dict(row)

    # COUNT20 within latest window (consistent with ranking tab)
    count_in_window: dict[str, int] = {s: 0 for s in symbols}
    if dates:
        ph = ",".join("?" * len(dates))
        with get_conn() as conn:
            crow = conn.execute(
                f"""
                SELECT symbol, COUNT(*) AS n
                FROM strong_daily
                WHERE symbol IN ({",".join("?" * len(symbols))})
                  AND as_of_date IN ({ph})
                  AND is_strong = 1
                GROUP BY symbol
                """,
                [*symbols, *dates],
            ).fetchall()
            for r in crow:
                count_in_window[r["symbol"]] = int(r["n"])

    dash = get_dashboard_by_tickers(symbols)
    rows_out: list[dict[str, Any]] = []
    n_qual = 0
    n_ret = 0
    for m in members:
        sym = m["symbol"]
        obs = latest_obs.get(sym) or {}
        drow = dash.get(sym) or {}
        count20 = int(count_in_window.get(sym) or 0)
        pos = obs.get("range_63d_pos")
        if pos is None and drow.get("range_63d_pos") is not None:
            pos = drow.get("range_63d_pos")
        price = drow.get("price")
        change_pct = drow.get("change_pct")
        try:
            change_pct = float(change_pct) if change_pct is not None else None
            if change_pct is not None and change_pct != change_pct:  # NaN
                change_pct = None
        except (TypeError, ValueError):
            change_pct = None
        avg_move_pct = drow.get("avg_move_pct")
        try:
            avg_move_pct = float(avg_move_pct) if avg_move_pct is not None else None
            if avg_move_pct is not None and avg_move_pct != avg_move_pct:
                avg_move_pct = None
        except (TypeError, ValueError):
            avg_move_pct = None
        name = drow.get("name") or ""
        pools = _pools_label(drow)
        mos_t = None
        try:
            mos_info = compute_target_proxy_mos(price, drow.get("target_1y"))
            if isinstance(mos_info, dict):
                mos_t = mos_info.get("mos_t")
        except Exception:
            mos_t = None

        rem = days_remaining(
            m["last_qualified_date"], as_of or latest_date or "", calendar
        )
        qualifying = count20 >= STRONG_COUNT_THRESHOLD
        status = "qualifying" if qualifying else "retention"
        if qualifying:
            n_qual += 1
        else:
            n_ret += 1

        rows_out.append(
            {
                "symbol": sym,
                "name": name,
                "pools": pools,
                "price": price,
                "change_pct": change_pct,
                "avg_move_pct": avg_move_pct,
                "range_63d_pos": pos,
                "count20": count20,
                "first_qualified_date": m["first_qualified_date"],
                "last_qualified_date": m["last_qualified_date"],
                "days_remaining": rem,
                "status": status,
                "ai_score": None,
                "mos_t": mos_t,
                "financials": None,
                "news": None,
            }
        )

    rows_out.sort(
        key=lambda r: (
            0 if r.get("status") == "qualifying" else 1,
            -(r.get("count20") or 0),
            -(r.get("range_63d_pos") if r.get("range_63d_pos") is not None else -1),
            r.get("symbol") or "",
        )
    )
    for i, r in enumerate(rows_out, start=1):
        r["rank"] = i

    return {
        "as_of": as_of,
        "built_at": built_at,
        "rules": rule_summary(),
        "count": len(rows_out),
        "count_qualifying": n_qual,
        "count_retention": n_ret,
        "threshold": STRONG_COUNT_THRESHOLD,
        "rows": rows_out,
    }


def strong_status_for_tickers(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """
    Read-only Strong snapshot for an arbitrary ticker set (Candidate Analysis).

    Does not change Strong Monitor membership or thresholds.
    Returns count20 (window), membership flags, status, dates, days_remaining.
    """
    clean: list[str] = []
    seen: set[str] = set()
    for s in symbols:
        u = (s or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            clean.append(u)
    empty: dict[str, dict[str, Any]] = {}
    if not clean:
        return empty

    init_db()
    as_of = get_setting(STRONG_META_AS_OF, "") or ""
    dates = _latest_trading_dates(STRONG_COUNT_WINDOW)
    with get_conn() as conn:
        mem_rows = conn.execute(
            f"""
            SELECT symbol, first_qualified_date, last_qualified_date
            FROM strong_membership
            WHERE symbol IN ({",".join("?" * len(clean))})
            """,
            clean,
        ).fetchall()
        cal_rows = conn.execute(
            "SELECT DISTINCT as_of_date FROM strong_daily ORDER BY as_of_date"
        ).fetchall()
    calendar = [r["as_of_date"] for r in cal_rows]
    mem = {r["symbol"]: dict(r) for r in mem_rows}

    count_in_window: dict[str, int] = {s: 0 for s in clean}
    if dates:
        ph_d = ",".join("?" * len(dates))
        ph_s = ",".join("?" * len(clean))
        with get_conn() as conn:
            crow = conn.execute(
                f"""
                SELECT symbol, COUNT(*) AS n
                FROM strong_daily
                WHERE symbol IN ({ph_s})
                  AND as_of_date IN ({ph_d})
                  AND is_strong = 1
                GROUP BY symbol
                """,
                [*clean, *dates],
            ).fetchall()
            for r in crow:
                count_in_window[r["symbol"]] = int(r["n"])

    latest_date = dates[0] if dates else as_of
    out: dict[str, dict[str, Any]] = {}
    for sym in clean:
        count20 = int(count_in_window.get(sym) or 0)
        m = mem.get(sym)
        in_membership = m is not None
        status = None
        first_q = None
        last_q = None
        rem = None
        if in_membership:
            first_q = m.get("first_qualified_date")
            last_q = m.get("last_qualified_date")
            status = "qualifying" if count20 >= STRONG_COUNT_THRESHOLD else "retention"
            rem = days_remaining(last_q or "", as_of or latest_date or "", calendar)
        out[sym] = {
            "count20": count20,
            "count20_label": f"{count20}/{STRONG_COUNT_WINDOW}",
            "in_membership": in_membership,
            "status": status,
            "first_qualified_date": first_q,
            "last_qualified_date": last_q,
            "days_remaining": rem,
            "threshold": STRONG_COUNT_THRESHOLD,
            "window": STRONG_COUNT_WINDOW,
        }
    return out


def load_strong_monitor_page(tab: str = "daily") -> dict[str, Any]:
    """
    Bundle meta + tab payloads for the Strong Monitor page.
    Loads only the active tab's heavy payload (plus light meta) for speed/stability.

    tab='meta' — badge counts + rules only (for Candidate Analysis / Rising / Multi).
    """
    tab = (tab or "daily").strip().lower()
    if tab not in ("daily", "ranking", "watchlist", "meta"):
        tab = "daily"

    as_of = get_setting(STRONG_META_AS_OF, "") or ""
    built_at = get_setting(STRONG_META_BUILT_AT, "") or ""

    # Auto-recalc when Strong Day % or COUNT threshold constants change.
    # Skip on meta-only loads (Candidate Analysis) to keep Research opening fast.
    if tab != "meta":
        try:
            ensure_strong_rules_match()
        except Exception:
            log.exception("ensure_strong_rules_match failed")

    empty_daily = {
        "columns": [],
        "max_rows": 0,
        "window": STRONG_COUNT_WINDOW,
        "threshold_pos": STRONG_63D_POSITION_THRESHOLD,
    }
    empty_ranking = {
        "count": 0,
        "rows": [],
        "distribution_line": "",
        "n_ge_threshold": 0,
        "window": STRONG_COUNT_WINDOW,
        "threshold": STRONG_COUNT_THRESHOLD,
    }
    empty_watchlist = {
        "count": 0,
        "count_qualifying": 0,
        "count_retention": 0,
        "threshold": STRONG_COUNT_THRESHOLD,
        "rows": [],
    }

    daily = empty_daily
    ranking = empty_ranking
    watchlist = empty_watchlist

    if tab == "daily":
        daily = build_daily_strong_stocks()
    elif tab == "ranking":
        ranking = list_count20_ranking()
    elif tab == "watchlist":
        watchlist = list_active_strong_watchlist()
    # tab == "meta": leave empty payloads; badges below

    # Prefer as_of/built_at from whichever payload was loaded
    if tab == "watchlist":
        as_of = watchlist.get("as_of") or as_of
        built_at = watchlist.get("built_at") or built_at
    elif tab == "ranking":
        as_of = ranking.get("as_of") or as_of

    # Tab badge counts (lightweight): membership size + latest-day strong count
    badge_daily = 0
    badge_ranking = 0
    badge_watchlist = 0
    try:
        init_db()
        dates = _latest_trading_dates(1)
        with get_conn() as conn:
            badge_watchlist = int(
                conn.execute("SELECT COUNT(*) AS n FROM strong_membership").fetchone()["n"]
            )
            if dates:
                badge_daily = int(
                    conn.execute(
                        "SELECT COUNT(*) AS n FROM strong_daily "
                        "WHERE as_of_date = ? AND is_strong = 1",
                        (dates[0],),
                    ).fetchone()["n"]
                )
            # COUNT>=1 over window — approximate ranking size
            win = _latest_trading_dates(STRONG_COUNT_WINDOW)
            if win:
                ph = ",".join("?" * len(win))
                badge_ranking = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(DISTINCT symbol) AS n FROM strong_daily
                        WHERE as_of_date IN ({ph}) AND is_strong = 1
                        """,
                        win,
                    ).fetchone()["n"]
                )
    except Exception:
        log.exception("strong monitor badge counts failed")

    if tab == "daily" and daily.get("columns"):
        badge_daily = daily["columns"][0]["count"]
    if tab == "ranking":
        badge_ranking = ranking.get("count") or badge_ranking
    if tab == "watchlist":
        badge_watchlist = watchlist.get("count") or badge_watchlist

    return {
        "as_of": as_of,
        "built_at": built_at,
        "rules": rule_summary(),
        "daily": daily,
        "ranking": ranking,
        "watchlist": watchlist,
        "badge_daily": badge_daily,
        "badge_ranking": badge_ranking,
        "badge_watchlist": badge_watchlist,
    }
