"""
MARKET INDEX research — FULL / local only.

Visual comparison of major U.S. cash indexes over 20D / 40D / 63D windows.
IBKR Index contracts only (no Yahoo). Reuses Group Movement window + TOTAL
semantics and column-pref pattern.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from db import get_conn, get_setting, init_db, set_setting
from market_index_config import list_index_defs, storage_tickers
from strong_stocks import upsert_daily_bars

log = logging.getLogger("leibot.market_index")
ET = ZoneInfo("America/New_York")

# Same window set as Group Movement; default 63D per product spec.
WINDOW_CHOICES = (20, 40, 63)
DEFAULT_WINDOW = 63

SETTINGS_COLUMNS_KEY = "market_index_columns"
SETTINGS_META_KEY = "market_index_refresh_meta"

DEFAULT_COLUMNS: dict[str, bool] = {
    "daily": True,
    "ret_5d": True,
    "ret_20d": True,
    "ret_40d": True,
    "ret_63d": True,
    "total": True,
    "rank": True,
    "source": True,
    "time": True,
}

PERIOD_LOOKBACKS = (5, 20, 40, 63)


def get_column_prefs() -> dict[str, bool]:
    raw = get_setting(SETTINGS_COLUMNS_KEY, None)
    out = dict(DEFAULT_COLUMNS)
    if isinstance(raw, dict):
        for k in DEFAULT_COLUMNS:
            if k in raw:
                out[k] = bool(raw[k])
    return out


def set_column_prefs(prefs: dict[str, Any]) -> dict[str, bool]:
    out = dict(DEFAULT_COLUMNS)
    if isinstance(prefs, dict):
        for k in DEFAULT_COLUMNS:
            if k in prefs:
                out[k] = bool(prefs[k])
    set_setting(SETTINGS_COLUMNS_KEY, out)
    return out


def get_refresh_meta() -> dict[str, Any]:
    raw = get_setting(SETTINGS_META_KEY, None)
    return dict(raw) if isinstance(raw, dict) else {}


def _set_refresh_meta(meta: dict[str, Any]) -> None:
    set_setting(SETTINGS_META_KEY, meta)


def _fmt_time(raw: Any) -> str:
    if not raw:
        return "—"
    s = str(raw).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")
    except Exception:
        return s[:19] if len(s) >= 19 else s


def _load_dated_closes(
    tickers: list[str], *, lookback_calendar_days: int = 400
) -> dict[str, list[tuple[str, float]]]:
    init_db()
    clean = sorted({(t or "").strip() for t in tickers if t})
    if not clean:
        return {}
    ph = ",".join("?" * len(clean))
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT ticker, date, close FROM daily_bars
            WHERE ticker IN ({ph})
              AND date >= date('now', ?)
            ORDER BY ticker ASC, date ASC
            """,
            [*clean, f"-{int(lookback_calendar_days)} days"],
        ).fetchall()
    out: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        t = r["ticker"] or ""
        d = str(r["date"] or "")[:10]
        try:
            px = float(r["close"])
        except (TypeError, ValueError):
            continue
        if not d or px <= 0:
            continue
        out.setdefault(t, []).append((d, px))
    return out


def _trading_calendar(
    dated: dict[str, list[tuple[str, float]]], *, window: int
) -> list[str]:
    """Union of index dates — prefer densest recent calendar."""
    from collections import Counter

    counts: Counter[str] = Counter()
    n = 0
    for series in dated.values():
        if not series:
            continue
        n += 1
        for d, _ in series[-(window + 15) :]:
            counts[d] += 1
    if not counts:
        return []
    threshold = max(1, n // 2)
    ordered = sorted(d for d, c in counts.items() if c >= threshold)
    if len(ordered) < window:
        ordered = sorted(counts.keys())
    if len(ordered) <= window:
        return ordered
    return ordered[-window:]


def _period_return_pct(series: list[tuple[str, float]], lookback: int) -> float | None:
    """Return over last `lookback` sessions: close[-1]/close[-(lookback+1)] - 1."""
    if lookback < 1 or len(series) < lookback + 1:
        return None
    base = series[-(lookback + 1)][1]
    last = series[-1][1]
    if base <= 0 or last <= 0:
        return None
    return round((last / base - 1.0) * 100.0, 2)


def _compound_total(daily_pcts: list[float | None]) -> list[float | None]:
    """
    TOTAL starts at 100 on day 0; then compounds Daily Change %.
    Same definition as Group Movement product spec.
    """
    out: list[float | None] = []
    total: float | None = None
    for i, pct in enumerate(daily_pcts):
        if i == 0:
            total = 100.0
            out.append(100.0)
            continue
        if total is None or pct is None:
            out.append(None)
            continue
        total = total * (1.0 + float(pct) / 100.0)
        out.append(round(total, 2))
    return out


def refresh_market_index_from_ibkr(*, period: str = "1y") -> dict[str, Any]:
    """
    Pull IBKR Index daily bars into daily_bars. Never uses Yahoo.
    On failure, leaves prior stored bars untouched.
    """
    defs = list_index_defs()
    result: dict[str, Any] = {
        "ok": False,
        "provider": "ibkr",
        "updated": 0,
        "failed": [],
        "errors": {},
        "as_of": "",
    }
    try:
        from ibkr_local.adapter import get_adapter
        from market_index_config import INDEX_CONTRACT_FALLBACKS

        adapter = get_adapter()
        specs = []
        for d in defs:
            specs.append(
                {
                    "key": d["storage_ticker"],
                    "ibkr_symbol": d["ibkr_symbol"],
                    "exchange": d["exchange"],
                    "currency": d["currency"],
                    "what_to_show": d.get("what_to_show") or "TRADES",
                    "fallbacks": list(INDEX_CONTRACT_FALLBACKS.get(d["key"]) or ()),
                }
            )
        fetched = adapter.fetch_index_daily_bars(specs, period=period)
    except Exception as exc:
        log.exception("market index IBKR fetch failed")
        result["errors"]["__all__"] = str(exc)
        return result

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    per_index: dict[str, Any] = {}
    latest_dates: list[str] = []
    updated = 0
    for d in defs:
        st = d["storage_ticker"]
        payload = (fetched or {}).get(st) or {}
        if not payload.get("ok"):
            err = payload.get("error") or "fetch failed"
            result["failed"].append(st)
            result["errors"][st] = err
            per_index[st] = {
                "ok": False,
                "error": err,
                "provider": "ibkr",
                "updated_at": now,
            }
            continue
        closes = payload.get("closes")
        if closes is None or getattr(closes, "empty", True):
            result["failed"].append(st)
            result["errors"][st] = "empty closes"
            per_index[st] = {
                "ok": False,
                "error": "empty closes",
                "provider": "ibkr",
                "updated_at": now,
            }
            continue
        n = upsert_daily_bars(st, closes)
        updated += int(n or 0)
        bar_day = str(payload.get("latest_bar_date") or "")[:10]
        if bar_day:
            latest_dates.append(bar_day)
        per_index[st] = {
            "ok": True,
            "provider": "ibkr",
            "bars": int(payload.get("bars") or 0),
            "latest_bar_date": bar_day,
            "updated_at": now,
            "mode": payload.get("mode"),
        }

    as_of = max(latest_dates) if latest_dates else ""
    meta = {
        "provider": "IBKR",
        "updated_at": now,
        "as_of": as_of,
        "indexes": per_index,
    }
    _set_refresh_meta(meta)
    result.update(
        {
            "ok": updated > 0,
            "updated": updated,
            "as_of": as_of,
            "updated_at": now,
            "meta": meta,
        }
    )
    return result


def _build_index_row(
    idef: dict[str, Any],
    *,
    dates: list[str],
    dated: dict[str, list[tuple[str, float]]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    st = idef["storage_ticker"]
    series = dated.get(st) or []
    cmap = {d: px for d, px in series}
    prior_by_date: dict[str, float] = {}
    for i, (d, px) in enumerate(series):
        if i > 0:
            prior_by_date[d] = series[i - 1][1]

    days_out: list[dict[str, Any]] = []
    daily_pcts: list[float | None] = []
    for d in dates:
        close = cmap.get(d)
        prior = prior_by_date.get(d)
        daily_pct: float | None = None
        if close is not None and prior is not None and prior > 0:
            daily_pct = round((close / prior - 1.0) * 100.0, 2)
        daily_pcts.append(daily_pct)
        days_out.append(
            {
                "date": d,
                "daily_pct": daily_pct,
                "close": round(close, 4) if close is not None else None,
            }
        )

    totals = _compound_total(daily_pcts)
    for i, day in enumerate(days_out):
        day["total"] = totals[i] if i < len(totals) else None

    # Period returns from full stored series (not limited to window length).
    rets = {f"ret_{n}d": _period_return_pct(series, n) for n in PERIOD_LOOKBACKS}

    idx_meta = (meta.get("indexes") or {}).get(st) or {}
    provider = "—"
    if series:
        # This view never writes Yahoo — stored bars are IBKR (or empty).
        provider = "IBKR"
    elif idx_meta.get("provider"):
        provider = str(idx_meta.get("provider") or "IBKR").upper()
        if provider == "IBKR" and not idx_meta.get("ok"):
            provider = "—"

    updated_at = idx_meta.get("updated_at") or meta.get("updated_at") or ""
    last_total = days_out[-1]["total"] if days_out else None
    window_ret = None
    if last_total is not None:
        window_ret = round(last_total - 100.0, 2)

    return {
        "key": idef["key"],
        "name": idef["name"],
        "display_symbol": idef["display_symbol"],
        "category": idef["category"],
        "storage_ticker": st,
        "days": days_out,
        "total": last_total,
        "window_return_pct": window_ret,
        **rets,
        "data_source": provider if series else "—",
        "downloaded_at": updated_at,
        "downloaded_at_display": _fmt_time(updated_at),
        "relative_rank": None,
        "strength": "—",
    }


def _assign_ranks(rows: list[dict[str, Any]]) -> None:
    """Relative Rank by window TOTAL (higher = stronger). No Leader labels."""
    scored = [
        (i, r.get("total"))
        for i, r in enumerate(rows)
        if r.get("total") is not None
    ]
    scored.sort(key=lambda x: (-float(x[1]), rows[x[0]].get("display_symbol") or ""))
    for rank, (idx, _tot) in enumerate(scored, start=1):
        rows[idx]["relative_rank"] = rank
        # Simple strength band vs median of scored totals
    if len(scored) >= 2:
        totals = [float(t) for _, t in scored]
        mid = sorted(totals)[len(totals) // 2]
        for idx, tot in scored:
            t = float(tot)
            if t >= mid + 1.0:
                rows[idx]["strength"] = "Improving"
            elif t <= mid - 1.0:
                rows[idx]["strength"] = "Weakening"
            else:
                rows[idx]["strength"] = "Neutral"


def load_market_index(
    *,
    window: int = DEFAULT_WINDOW,
    refresh: bool = False,
) -> dict[str, Any]:
    """
    Build MARKET INDEX payload.
    refresh=False: cache only (fast).
    refresh=True: IBKR Index bars only; keep prior DB on failure.
    """
    try:
        w = int(window)
    except (TypeError, ValueError):
        w = DEFAULT_WINDOW
    if w not in WINDOW_CHOICES:
        w = min(WINDOW_CHOICES, key=lambda x: abs(x - w))

    defs = list_index_defs()
    refresh_result: dict[str, Any] | None = None
    if refresh:
        refresh_result = refresh_market_index_from_ibkr(period="1y")

    meta = get_refresh_meta()
    tickers = storage_tickers()
    dated = _load_dated_closes(tickers, lookback_calendar_days=max(200, w * 5))
    dates = _trading_calendar(dated, window=w)

    rows = [
        _build_index_row(d, dates=dates, dated=dated, meta=meta) for d in defs
    ]
    _assign_ranks(rows)
    # Display order: Relative Rank asc, then config order
    rows.sort(
        key=lambda r: (
            r["relative_rank"] if r.get("relative_rank") is not None else 999,
            r.get("display_symbol") or "",
        )
    )

    date_labels = []
    for d in dates:
        try:
            _y, m, day = d.split("-")
            months = (
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            )
            label = f"{months[int(m) - 1]} {int(day)}"
        except Exception:
            label = d
        date_labels.append({"date": d, "label": label})

    as_of = dates[-1] if dates else (meta.get("as_of") or "")
    return {
        "window": w,
        "window_choices": list(WINDOW_CHOICES),
        "dates": dates,
        "date_labels": date_labels,
        "rows": rows,
        "row_count": len(rows),
        "columns": get_column_prefs(),
        "as_of": as_of,
        "refreshed": bool(refresh),
        "refresh_result": refresh_result,
        "provider": meta.get("provider") or ("IBKR" if any(dated.values()) else "—"),
        "downloaded_at_display": _fmt_time(meta.get("updated_at")),
        "notes": (
            "TOTAL starts at 100 on day 1 of the window, then compounds Daily Change %. "
            "Relative Rank orders indexes by TOTAL for the selected window. "
            "IBKR Index contracts only — no Yahoo. Use Refresh Indexes to update."
        ),
    }


def export_market_index_xlsx(*, window: int = DEFAULT_WINDOW) -> tuple[str, bytes]:
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    payload = load_market_index(window=window, refresh=False)
    dates = list(payload.get("dates") or [])
    rows = list(payload.get("rows") or [])
    as_of = payload.get("as_of") or "na"
    fname = f"market_index_{payload.get('window') or window}D_{as_of}.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "Market Index"
    header = [
        "Index",
        "Name",
        "Rank",
        "Strength",
        "5D %",
        "20D %",
        "40D %",
        "63D %",
        "TOTAL",
        "Window %",
        "Source",
        "Time",
    ]
    for d in dates:
        header.extend([f"{d} Daily %", f"{d} Close", f"{d} TOTAL"])
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for r in rows:
        line: list[Any] = [
            r.get("display_symbol") or "",
            r.get("name") or "",
            r.get("relative_rank"),
            r.get("strength") or "",
            r.get("ret_5d"),
            r.get("ret_20d"),
            r.get("ret_40d"),
            r.get("ret_63d"),
            r.get("total"),
            r.get("window_return_pct"),
            r.get("data_source") or "",
            r.get("downloaded_at_display") or "",
        ]
        by_date = {d.get("date"): d for d in (r.get("days") or []) if d.get("date")}
        for d in dates:
            cell = by_date.get(d) or {}
            line.extend([cell.get("daily_pct"), cell.get("close"), cell.get("total")])
        ws.append(line)

    for i, width in enumerate((14, 28, 8, 12, 10, 10, 10, 10, 10, 10, 10, 22), start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"

    bio = io.BytesIO()
    wb.save(bio)
    return fname, bio.getvalue()
