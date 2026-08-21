"""
LeiBot AI Paper Trading engine (simulation only).

Public research portfolio — never places IBKR / brokerage orders.
Uses existing AI Score / MOS T / fund / news caches without changing those formulas.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from db import (
    get_conn,
    get_setting,
    init_db,
    list_low_63d_pos,
    list_low_target_ratio,
    list_setup,
    set_setting,
)

log = logging.getLogger("leibot.paper")

PT = ZoneInfo("America/Los_Angeles")

# Rank → target USD allocation (adapts to share prices; need not sum exactly to limit).
ALLOC_LADDER = [300.0, 300.0, 250.0, 250.0, 200.0, 200.0]
INTEGER_SHARE_MAX_PRICE = 500.0
TOP_N = 10

EXIT_STOP = "STOP_LOSS"
EXIT_TAKE = "TAKE_PROFIT"
EXIT_MANUAL = "MANUAL_EXIT"
EXIT_OTHER = "OTHER"

# Legacy codes from earlier V1 rows (display / filter still accept these).
_EXIT_ALIASES = {
    "stop_loss": EXIT_STOP,
    "take_profit": EXIT_TAKE,
    "manual": EXIT_MANUAL,
    "other": EXIT_OTHER,
}


def normalize_exit_reason(code: str | None) -> str:
    raw = (code or "").strip()
    if not raw:
        return EXIT_OTHER
    if raw in (EXIT_STOP, EXIT_TAKE, EXIT_MANUAL, EXIT_OTHER):
        return raw
    return _EXIT_ALIASES.get(raw.lower(), raw.upper() if raw.isupper() else EXIT_OTHER)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def trading_day_pt(now: datetime | None = None) -> str:
    """Calendar date in Pacific Time (YYYY-MM-DD)."""
    dt = now or datetime.now(PT)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(PT)
    else:
        dt = dt.astimezone(PT)
    return dt.date().isoformat()


def _cfg() -> dict[str, float]:
    return {
        "starting_capital": float(get_setting("paper_starting_capital", 2000.0)),
        "trading_limit": float(get_setting("paper_trading_limit", 1500.0)),
        "reserve_cash": float(get_setting("paper_reserve_cash", 500.0)),
        "stop_loss_pct": float(get_setting("paper_stop_loss_pct", 5.0)),
        "take_profit_pct": float(get_setting("paper_take_profit_pct", 10.0)),
    }


def ensure_portfolio() -> dict[str, Any]:
    """Seed / return the single paper portfolio row."""
    init_db()
    cfg = _cfg()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM paper_portfolio WHERE id = 1").fetchone()
        if not row:
            now = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO paper_portfolio
                  (id, starting_capital, trading_limit, reserve_cash, cash, updated_at)
                VALUES (1, ?, ?, ?, ?, ?)
                """,
                (
                    cfg["starting_capital"],
                    cfg["trading_limit"],
                    cfg["reserve_cash"],
                    cfg["starting_capital"],
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM paper_portfolio WHERE id = 1").fetchone()
        return dict(row)


def sync_portfolio_limits_from_settings() -> dict[str, Any]:
    """Keep limit columns aligned with Settings (does not reset cash)."""
    port = ensure_portfolio()
    cfg = _cfg()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE paper_portfolio SET
              starting_capital = ?, trading_limit = ?, reserve_cash = ?, updated_at = ?
            WHERE id = 1
            """,
            (
                cfg["starting_capital"],
                cfg["trading_limit"],
                cfg["reserve_cash"],
                _utc_now_iso(),
            ),
        )
    return ensure_portfolio()


# ── Priority ───────────────────────────────────────────────────────────────


def list_priority_tickers() -> list[str]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ticker FROM paper_priority ORDER BY created_at ASC, ticker"
        ).fetchall()
    return [r["ticker"] for r in rows]


def set_priority(ticker: str, *, note: str | None = None) -> None:
    t = (ticker or "").strip().upper()
    if not t:
        raise ValueError("ticker required")
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO paper_priority (ticker, note, created_at) VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET note = excluded.note
            """,
            (t, note or "", _utc_now_iso()),
        )


def clear_priority(ticker: str) -> None:
    t = (ticker or "").strip().upper()
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM paper_priority WHERE ticker = ?", (t,))


# ── Sizing ─────────────────────────────────────────────────────────────────


def size_position(price: float, target_alloc: float) -> tuple[float, float, str]:
    """
    Returns (shares, cost, mode).
    price <= $500 → prefer integer shares; price > $500 → fractional allowed.
    """
    if price is None or price <= 0 or target_alloc is None or target_alloc <= 0:
        return 0.0, 0.0, "none"
    if price <= INTEGER_SHARE_MAX_PRICE:
        shares = max(1, int(round(target_alloc / price)))
        # Prefer not overshooting target too far when multiple shares.
        while shares > 1 and shares * price > target_alloc * 1.35:
            shares -= 1
        # Single share above target is OK (e.g. $257 vs $250).
        cost = round(shares * price, 4)
        return float(shares), cost, "integer"
    shares = round(target_alloc / price, 4)
    if shares <= 0:
        return 0.0, 0.0, "fractional"
    cost = round(shares * price, 4)
    return shares, cost, "fractional"


def stop_take_prices(
    entry: float, stop_pct: float, take_pct: float
) -> tuple[float, float]:
    stop = round(entry * (1.0 - stop_pct / 100.0), 4)
    take = round(entry * (1.0 + take_pct / 100.0), 4)
    return stop, take


def _fund_label(fund: dict | None) -> str:
    if not fund or fund.get("health") == "unknown":
        return "—"
    icon = {"good": "🟢", "ok": "🟡", "bad": "🔴"}.get(fund.get("health"), "")
    ok = fund.get("ok")
    known = fund.get("total_known")
    if ok is None or known is None:
        return icon or "—"
    return f"{icon} {ok}/{known}".strip()


def _news_label(news: dict | None) -> str:
    if not news:
        return "—"
    if news.get("skipped") or news.get("status") == "SKIPPED" or news.get("tone") == "skipped":
        return "SKIPPED"
    label = (news.get("label") or "").strip()
    if label:
        return label[:48]
    tone = news.get("tone")
    if tone:
        return str(tone)
    return "—"


# Stable source codes (stored); display labels go through gettext.
SRC_OVERSOLD = "oversold"
SRC_TARGET = "target"
SRC_63D = "63d"
SRC_MANUAL = "manual"
_SOURCE_ORDER = (SRC_OVERSOLD, SRC_TARGET, SRC_63D, SRC_MANUAL)
_SOURCE_MSGID = {
    SRC_OVERSOLD: "Oversold",
    SRC_TARGET: "Target",
    SRC_63D: "63D",
    SRC_MANUAL: "Manual Priority",
}


def format_source_label(source_codes: str | list[str] | None) -> str:
    """Human-readable Source column, e.g. 'Oversold · 63D'."""
    if not source_codes:
        return "—"
    if isinstance(source_codes, str):
        codes = [c.strip() for c in source_codes.replace(";", ",").split(",") if c.strip()]
    else:
        codes = [str(c).strip() for c in source_codes if c]
    # Stable display order
    ordered = [c for c in _SOURCE_ORDER if c in codes]
    ordered.extend(c for c in codes if c not in ordered)

    def _t(msg: str) -> str:
        try:
            from i18n import gettext

            return gettext(msg)
        except Exception:
            return msg

    parts = [_t(_SOURCE_MSGID.get(c, c)) for c in ordered]
    return " · ".join(parts) if parts else "—"


def normalize_source_codes(codes: list[str] | set[str] | None) -> str:
    """Frozen storage form: 'oversold,63d'."""
    if not codes:
        return ""
    seen = {str(c).strip().lower() for c in codes if c}
    ordered = [c for c in _SOURCE_ORDER if c in seen]
    ordered.extend(sorted(c for c in seen if c not in _SOURCE_ORDER))
    return ",".join(ordered)


# ── Candidates ─────────────────────────────────────────────────────────────


def _score_universe_rows() -> list[dict[str, Any]]:
    """
    Score combined system screening pool (+ optional Priority names).

    Pool = Oversold ∪ Target Ratio < 80% ∪ 63D Position < 25%
    (My Watchlist / Temp are NOT included.)
    Deduplicate by ticker; track multi-group Source; rank by AI Score
    (Priority flag sorts first for allocation only — does not change AI Score).
    """
    from db import get_dashboard_by_tickers
    from market_data import (
        compute_ai_score,
        compute_target_proxy_mos,
        fund_qualifies_for_news,
        get_fund_cached_only,
        get_news_cached_only,
        make_news_skipped,
    )

    # Union of the three system Watchlist groups (reuse db list_* helpers).
    membership: dict[str, set[str]] = {}
    by_ticker: dict[str, dict[str, Any]] = {}

    def _ingest(rows: list[dict[str, Any]], source: str) -> None:
        for raw in rows:
            t = (raw.get("ticker") or "").upper()
            if not t:
                continue
            membership.setdefault(t, set()).add(source)
            if t not in by_ticker:
                by_ticker[t] = dict(raw)

    _ingest([dict(r) for r in list_setup(-10.0)], SRC_OVERSOLD)
    _ingest([dict(r) for r in list_low_target_ratio(0.8)], SRC_TARGET)
    _ingest([dict(r) for r in list_low_63d_pos(25.0)], SRC_63D)

    priority = set(list_priority_tickers())
    missing_pri = [t for t in priority if t not in by_ticker]
    if missing_pri:
        extra = get_dashboard_by_tickers(missing_pri)
        for t, row in extra.items():
            tu = (t or "").upper()
            if row and tu:
                by_ticker[tu] = dict(row)
                membership.setdefault(tu, set()).add(SRC_MANUAL)

    # Priority names already in system pool keep system sources; mark priority flag only.
    for t in priority:
        if t in by_ticker and SRC_MANUAL not in membership.get(t, set()):
            # Do not add "manual" source for system-screened names — Priority is separate.
            pass

    rows = list(by_ticker.values())
    tickers = [r["ticker"].upper() for r in rows if r.get("ticker")]
    fund_map = get_fund_cached_only(tickers)
    news_tickers = [t for t in tickers if fund_qualifies_for_news(fund_map.get(t))]
    news_map = get_news_cached_only(news_tickers) if news_tickers else {}

    scored: list[dict[str, Any]] = []
    for r in rows:
        t = (r.get("ticker") or "").upper()
        if not t or r.get("price") is None:
            continue
        f = fund_map.get(t)
        r["fund"] = f
        if fund_qualifies_for_news(f):
            r["news"] = news_map.get(t)
        else:
            r["news"] = make_news_skipped()
        r.update(compute_target_proxy_mos(r.get("price"), r.get("target_1y")))
        ai = compute_ai_score(r)
        r["ai"] = ai
        r["ai_score"] = float(ai.get("final") or 0)
        r["is_priority"] = 1 if t in priority else 0
        srcs = membership.get(t) or set()
        if t in priority and not (srcs - {SRC_MANUAL}):
            # Priority-only name (not in the three system groups).
            srcs = {SRC_MANUAL}
        r["source_codes"] = normalize_source_codes(srcs)
        r["source_label"] = format_source_label(r["source_codes"])
        r["financial_label"] = _fund_label(f)
        r["news_label"] = _news_label(r.get("news"))
        r["range_63d_pos"] = r.get("range_63d_pos")
        r["financial_ok"] = f.get("ok") if f else None
        r["financial_known"] = f.get("total_known") if f else None
        news = r.get("news") or {}
        r["news_tone"] = news.get("tone") if isinstance(news, dict) else None
        scored.append(r)

    # Rank purely by AI Score for Top-N selection (Priority does not change AI Score).
    scored.sort(
        key=lambda x: (-float(x.get("ai_score") or 0), x.get("ticker") or "")
    )
    return scored


def build_candidates(*, as_of_date: str | None = None, persist: bool = True) -> list[dict[str, Any]]:
    """Build AI Candidates Top 10 with suggested allocation / shares / stops."""
    cfg = _cfg()
    day = as_of_date or trading_day_pt()
    scored = _score_universe_rows()
    top = scored[:TOP_N]
    # Within Top 10, Priority only affects allocation order (not AI Score / not membership).
    top.sort(
        key=lambda x: (
            -int(x.get("is_priority") or 0),
            -float(x.get("ai_score") or 0),
            x.get("ticker") or "",
        )
    )
    port = ensure_portfolio()
    invested = sum_open_invested()
    remaining_limit = max(0.0, float(port["trading_limit"]) - invested)

    out: list[dict[str, Any]] = []
    used = 0.0
    now = _utc_now_iso()
    for i, r in enumerate(top):
        rank = i + 1
        target = ALLOC_LADDER[i] if i < len(ALLOC_LADDER) else 0.0
        # Do not exceed remaining trading-fund capacity for suggestions.
        room = max(0.0, remaining_limit - used)
        target = min(target, room) if target > 0 else 0.0
        price = float(r["price"])
        shares, cost, mode = size_position(price, target) if target > 0 else (0.0, 0.0, "none")
        # If integer share cost exceeds remaining room, skip suggestion.
        if cost > room + 1e-6:
            shares, cost, mode = 0.0, 0.0, "none"
            target = 0.0
        stop, take = stop_take_prices(price, cfg["stop_loss_pct"], cfg["take_profit_pct"])
        if cost > 0:
            used += cost
        elif target > 0 and shares <= 0:
            target = 0.0
        row = {
            "as_of_date": day,
            "rank": rank,
            "ticker": r["ticker"].upper(),
            "name": r.get("name") or "",
            "ai_score": r.get("ai_score"),
            "mos_t": r.get("mos_t"),
            "financial_label": r.get("financial_label") or "—",
            "news_label": r.get("news_label") or "—",
            "price": price,
            "is_priority": int(r.get("is_priority") or 0),
            "suggested_alloc": round(target, 2),
            "suggested_shares": shares,
            "shares_mode": mode,
            "stop_price": stop,
            "take_profit_price": take,
            "stop_pct": cfg["stop_loss_pct"],
            "take_profit_pct": cfg["take_profit_pct"],
            "range_63d_pos": r.get("range_63d_pos"),
            "financial_ok": r.get("financial_ok"),
            "financial_known": r.get("financial_known"),
            "news_tone": r.get("news_tone"),
            "source_codes": r.get("source_codes") or "",
            "source_label": r.get("source_label") or format_source_label(r.get("source_codes") or ""),
            "updated_at": now,
        }
        out.append(row)

    if persist:
        init_db()
        with get_conn() as conn:
            conn.execute("DELETE FROM paper_candidates WHERE as_of_date = ?", (day,))
            for row in out:
                conn.execute(
                    """
                    INSERT INTO paper_candidates (
                      as_of_date, rank, ticker, name, ai_score, mos_t,
                      financial_label, news_label, price, is_priority,
                      suggested_alloc, suggested_shares, shares_mode,
                      stop_price, take_profit_price, meta_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["as_of_date"],
                        row["rank"],
                        row["ticker"],
                        row["name"],
                        row["ai_score"],
                        row["mos_t"],
                        row["financial_label"],
                        row["news_label"],
                        row["price"],
                        row["is_priority"],
                        row["suggested_alloc"],
                        row["suggested_shares"],
                        row["shares_mode"],
                        row["stop_price"],
                        row["take_profit_price"],
                        json.dumps(
                            {
                                "stop_pct": row["stop_pct"],
                                "take_profit_pct": row["take_profit_pct"],
                                "range_63d_pos": row.get("range_63d_pos"),
                                "financial_ok": row.get("financial_ok"),
                                "financial_known": row.get("financial_known"),
                                "news_tone": row.get("news_tone"),
                                "source_codes": row.get("source_codes") or "",
                            }
                        ),
                        row["updated_at"],
                    ),
                )
        set_setting("paper_candidates_updated_at", now)
        set_setting("paper_candidates_as_of", day)
    return out


def list_candidates(as_of_date: str | None = None) -> list[dict[str, Any]]:
    init_db()
    day = as_of_date or get_setting("paper_candidates_as_of") or trading_day_pt()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_candidates WHERE as_of_date = ? ORDER BY rank ASC",
            (day,),
        ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        codes = ""
        try:
            meta = json.loads(row.get("meta_json") or "{}")
            codes = meta.get("source_codes") or ""
        except Exception:
            codes = ""
        row["source_codes"] = codes
        row["source_label"] = format_source_label(codes)
        out.append(row)
    return out


# ── Trades ─────────────────────────────────────────────────────────────────


def sum_open_invested() -> float:
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost), 0) AS s FROM paper_trades WHERE status = 'open'"
        ).fetchone()
    return float(row["s"] or 0)


def list_open_trades() -> list[dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY entry_date DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def list_closed_trades(*, limit: int = 200) -> list[dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_trades WHERE status = 'closed' "
            "ORDER BY exit_date DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def create_paper_orders_from_candidates(
    *, as_of_date: str | None = None, tickers: list[str] | None = None
) -> dict[str, Any]:
    """
    Create simulated open positions from today's candidates with suggested_shares > 0.
    Does NOT auto-run — must be invoked explicitly (admin).
    """
    day = as_of_date or get_setting("paper_candidates_as_of") or trading_day_pt()
    cands = list_candidates(day)
    if not cands:
        cands = build_candidates(as_of_date=day, persist=True)

    want = None
    if tickers:
        want = {t.strip().upper() for t in tickers if t and t.strip()}

    port = ensure_portfolio()
    trading_limit = float(port["trading_limit"])
    cash = float(port["cash"])
    invested = sum_open_invested()
    open_tickers = {t["ticker"].upper() for t in list_open_trades()}

    created = []
    skipped = []
    now = _utc_now_iso()

    for c in cands:
        t = c["ticker"].upper()
        if want is not None and t not in want:
            continue
        shares = float(c.get("suggested_shares") or 0)
        if shares <= 0:
            skipped.append({"ticker": t, "reason": "no_allocation"})
            continue
        if t in open_tickers:
            skipped.append({"ticker": t, "reason": "already_open"})
            continue
        price = float(c["price"])
        cost = round(shares * price, 4)
        if invested + cost > trading_limit + 1e-6:
            skipped.append({"ticker": t, "reason": "trading_limit"})
            continue
        if cost > cash + 1e-6:
            skipped.append({"ticker": t, "reason": "insufficient_cash"})
            continue

        stop = float(c["stop_price"])
        take = float(c["take_profit_price"])
        meta = {}
        try:
            meta = json.loads(c.get("meta_json") or "{}")
        except Exception:
            meta = {}
        stop_pct = float(meta.get("stop_pct", get_setting("paper_stop_loss_pct", 5.0)))
        take_pct = float(meta.get("take_profit_pct", get_setting("paper_take_profit_pct", 10.0)))
        range_63d = meta.get("range_63d_pos")
        if range_63d is None:
            range_63d = c.get("range_63d_pos")
        fin_ok = meta.get("financial_ok", c.get("financial_ok"))
        fin_known = meta.get("financial_known", c.get("financial_known"))
        news_tone = meta.get("news_tone", c.get("news_tone"))
        source_at_entry = (
            meta.get("source_codes")
            or c.get("source_codes")
            or ""
        )

        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO paper_trades (
                  ticker, name, status, entry_date, entry_price, shares, shares_mode,
                  cost, stop_price, take_profit_price, stop_pct, take_profit_pct,
                  ai_score_entry, mos_t_entry, financial_entry, news_entry,
                  range_63d_pos_entry, financial_ok_entry, financial_known_entry,
                  news_tone_entry, source_at_entry,
                  is_priority, rank_at_entry, current_price, market_value,
                  unrealized_pnl, unrealized_pnl_pct, ai_score_current,
                  created_at, updated_at
                ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
                """,
                (
                    t,
                    c.get("name") or "",
                    day,
                    price,
                    shares,
                    c.get("shares_mode") or "integer",
                    cost,
                    stop,
                    take,
                    stop_pct,
                    take_pct,
                    c.get("ai_score"),
                    c.get("mos_t"),
                    c.get("financial_label"),
                    c.get("news_label"),
                    range_63d,
                    fin_ok,
                    fin_known,
                    news_tone,
                    source_at_entry,
                    int(c.get("is_priority") or 0),
                    int(c.get("rank") or 0),
                    price,
                    cost,
                    c.get("ai_score"),
                    now,
                    now,
                ),
            )
            cash -= cost
            invested += cost
            conn.execute(
                "UPDATE paper_portfolio SET cash = ?, updated_at = ? WHERE id = 1",
                (round(cash, 4), now),
            )
        open_tickers.add(t)
        created.append({"ticker": t, "shares": shares, "cost": cost, "entry_price": price})

    set_setting("paper_last_order_at", now)
    try:
        save_equity_snapshot(as_of_date=day)
    except Exception:
        log.exception("equity snapshot after create_orders failed")
    return {"created": created, "skipped": skipped, "cash": cash, "invested": invested}


def _fetch_daily_ohlc(ticker: str) -> dict[str, Any] | None:
    """Last available Yahoo daily bar (open/high/low/close/date)."""
    try:
        import yfinance as yf

        hist = yf.Ticker(ticker).history(period="10d", auto_adjust=True)
        if hist is None or hist.empty:
            return None
        # Prefer last row with High/Low
        for i in range(len(hist) - 1, -1, -1):
            row = hist.iloc[i]
            high = float(row.get("High")) if "High" in hist.columns else None
            low = float(row.get("Low")) if "Low" in hist.columns else None
            close = float(row.get("Close")) if "Close" in hist.columns else None
            if close is None:
                continue
            if high is None:
                high = close
            if low is None:
                low = close
            idx = hist.index[i]
            try:
                d = idx.date().isoformat()
            except Exception:
                d = str(idx)[:10]
            return {
                "date": d,
                "open": float(row["Open"]) if "Open" in hist.columns else close,
                "high": high,
                "low": low,
                "close": close,
            }
    except Exception as exc:
        log.warning("OHLC fetch failed for %s: %s", ticker, exc)
    return None


def _close_trade(
    trade_id: int,
    *,
    exit_price: float,
    exit_date: str,
    exit_reason: str,
    exit_note: str = "",
    day_high: float | None = None,
    day_low: float | None = None,
) -> dict[str, Any]:
    init_db()
    now = _utc_now_iso()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            raise ValueError("trade not found")
        tr = dict(row)
        if tr["status"] != "open":
            raise ValueError("trade already closed")
        shares = float(tr["shares"])
        entry = float(tr["entry_price"])
        proceeds = round(exit_price * shares, 4)
        realized = round((exit_price - entry) * shares, 4)
        ret_pct = round((exit_price - entry) / entry * 100.0, 4) if entry else 0.0
        conn.execute(
            """
            UPDATE paper_trades SET
              status = 'closed',
              exit_date = ?, exit_price = ?, realized_pnl = ?, return_pct = ?,
              exit_reason = ?, exit_note = ?,
              day_high = COALESCE(?, day_high),
              day_low = COALESCE(?, day_low),
              current_price = ?, market_value = 0,
              unrealized_pnl = 0, unrealized_pnl_pct = 0,
              updated_at = ?
            WHERE id = ?
            """,
            (
                exit_date,
                exit_price,
                realized,
                ret_pct,
                exit_reason,
                exit_note,
                day_high,
                day_low,
                exit_price,
                now,
                trade_id,
            ),
        )
        port = conn.execute("SELECT cash FROM paper_portfolio WHERE id = 1").fetchone()
        cash = float(port["cash"]) + proceeds
        conn.execute(
            "UPDATE paper_portfolio SET cash = ?, updated_at = ? WHERE id = 1",
            (round(cash, 4), now),
        )
    return {
        "id": trade_id,
        "ticker": tr["ticker"],
        "exit_price": exit_price,
        "realized_pnl": realized,
        "exit_reason": exit_reason,
        "exit_note": exit_note,
    }


def manual_close_trade(trade_id: int, *, exit_price: float | None = None) -> dict[str, Any]:
    trades = {t["id"]: t for t in list_open_trades()}
    tr = trades.get(trade_id)
    if not tr:
        raise ValueError("open trade not found")
    px = exit_price
    if px is None:
        ohlc = _fetch_daily_ohlc(tr["ticker"])
        px = float(ohlc["close"]) if ohlc else float(tr.get("current_price") or tr["entry_price"])
    return _close_trade(
        trade_id,
        exit_price=float(px),
        exit_date=trading_day_pt(),
        exit_reason=EXIT_MANUAL,
        exit_note="manual_exit",
    )


def evaluate_open_trade_vs_ohlc(tr: dict[str, Any], ohlc: dict[str, Any]) -> dict[str, Any]:
    """
    Apply V1 daily stop/target rules using OHLC.
    Same-day both hit → conservative: Stop Loss first.
    """
    stop = float(tr["stop_price"])
    take = float(tr["take_profit_price"])
    high = float(ohlc["high"])
    low = float(ohlc["low"])
    close = float(ohlc["close"])
    hit_stop = low <= stop
    hit_take = high >= take
    if hit_stop and hit_take:
        return {
            "action": "close",
            "exit_price": stop,
            "exit_reason": EXIT_STOP,
            "exit_note": "same_day_stop_and_target_assumed_stop_first",
            "day_high": high,
            "day_low": low,
            "current_price": close,
        }
    if hit_stop:
        return {
            "action": "close",
            "exit_price": stop,
            "exit_reason": EXIT_STOP,
            "exit_note": "daily_low_hit_stop",
            "day_high": high,
            "day_low": low,
            "current_price": close,
        }
    if hit_take:
        return {
            "action": "close",
            "exit_price": take,
            "exit_reason": EXIT_TAKE,
            "exit_note": "daily_high_hit_take_profit",
            "day_high": high,
            "day_low": low,
            "current_price": close,
        }
    entry = float(tr["entry_price"])
    shares = float(tr["shares"])
    mv = round(close * shares, 4)
    upnl = round((close - entry) * shares, 4)
    upct = round((close - entry) / entry * 100.0, 4) if entry else 0.0
    return {
        "action": "mark",
        "current_price": close,
        "day_high": high,
        "day_low": low,
        "market_value": mv,
        "unrealized_pnl": upnl,
        "unrealized_pnl_pct": upct,
    }


def run_daily_update(*, refresh_candidates: bool = True) -> dict[str, Any]:
    """
    Once-per-trading-day paper update:
    refresh Top 10 candidates, mark open P&L, close stops/targets via OHLC.
    """
    ensure_portfolio()
    day = trading_day_pt()
    closed = []
    marked = 0
    errors = []

    # Current AI map for open positions (optional enrichment)
    ai_map: dict[str, float] = {}
    try:
        scored = _score_universe_rows()
        ai_map = {r["ticker"].upper(): float(r.get("ai_score") or 0) for r in scored}
    except Exception as exc:
        log.warning("AI refresh for open positions failed: %s", exc)

    for tr in list_open_trades():
        try:
            ohlc = _fetch_daily_ohlc(tr["ticker"])
            if not ohlc:
                # Fall back to dashboard cache close only (no stop check without OHLC).
                from db import get_dashboard_by_tickers

                cached = get_dashboard_by_tickers([tr["ticker"]]).get(tr["ticker"].upper())
                px = float(cached["price"]) if cached and cached.get("price") is not None else None
                if px is None:
                    errors.append({"ticker": tr["ticker"], "error": "no_price"})
                    continue
                entry = float(tr["entry_price"])
                shares = float(tr["shares"])
                mv = round(px * shares, 4)
                upnl = round((px - entry) * shares, 4)
                upct = round((px - entry) / entry * 100.0, 4) if entry else 0.0
                with get_conn() as conn:
                    conn.execute(
                        """
                        UPDATE paper_trades SET
                          current_price = ?, market_value = ?,
                          unrealized_pnl = ?, unrealized_pnl_pct = ?,
                          ai_score_current = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            px,
                            mv,
                            upnl,
                            upct,
                            ai_map.get(tr["ticker"].upper()),
                            _utc_now_iso(),
                            tr["id"],
                        ),
                    )
                marked += 1
                continue

            decision = evaluate_open_trade_vs_ohlc(tr, ohlc)
            if decision["action"] == "close":
                closed.append(
                    _close_trade(
                        tr["id"],
                        exit_price=decision["exit_price"],
                        exit_date=ohlc.get("date") or day,
                        exit_reason=decision["exit_reason"],
                        exit_note=decision.get("exit_note") or "",
                        day_high=decision.get("day_high"),
                        day_low=decision.get("day_low"),
                    )
                )
            else:
                with get_conn() as conn:
                    conn.execute(
                        """
                        UPDATE paper_trades SET
                          current_price = ?, day_high = ?, day_low = ?,
                          market_value = ?, unrealized_pnl = ?, unrealized_pnl_pct = ?,
                          ai_score_current = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            decision["current_price"],
                            decision.get("day_high"),
                            decision.get("day_low"),
                            decision["market_value"],
                            decision["unrealized_pnl"],
                            decision["unrealized_pnl_pct"],
                            ai_map.get(tr["ticker"].upper()),
                            _utc_now_iso(),
                            tr["id"],
                        ),
                    )
                marked += 1
        except Exception as exc:
            log.exception("daily update failed for trade %s", tr.get("id"))
            errors.append({"ticker": tr.get("ticker"), "error": str(exc)})

    candidates = []
    if refresh_candidates:
        try:
            candidates = build_candidates(as_of_date=day, persist=True)
        except Exception as exc:
            log.exception("candidate rebuild failed")
            errors.append({"ticker": "*", "error": f"candidates: {exc}"})

    now = _utc_now_iso()
    set_setting("paper_last_daily_update", now)
    set_setting("paper_last_daily_update_day", day)
    try:
        snap = save_equity_snapshot(as_of_date=day)
    except Exception as exc:
        log.exception("equity snapshot failed")
        snap = None
        errors.append({"ticker": "*", "error": f"snapshot: {exc}"})
    return {
        "day": day,
        "closed": closed,
        "marked": marked,
        "candidates": len(candidates),
        "errors": errors,
        "updated_at": now,
        "snapshot": snap,
    }


def _parse_ymd(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def holding_days_calendar(entry_date: str | None, exit_date: str | None) -> int | None:
    """Calendar days between entry and exit (inclusive of same-day = 0)."""
    a = _parse_ymd(entry_date)
    b = _parse_ymd(exit_date)
    if not a or not b:
        return None
    return max(0, (b - a).days)


def save_equity_snapshot(*, as_of_date: str | None = None) -> dict[str, Any]:
    """
    Upsert one daily portfolio equity snapshot for as_of_date (Pacific trading date).
    Safe to re-run the same day — overwrites that date's row only.
    """
    init_db()
    day = as_of_date or trading_day_pt()
    port = ensure_portfolio()
    opens = list_open_trades()
    cash = float(port["cash"])
    # Prefer marked market_value; fall back to current_price × shares or cost.
    open_mv = 0.0
    unrealized = 0.0
    for t in opens:
        mv = t.get("market_value")
        if mv is None:
            px = t.get("current_price")
            if px is None:
                px = t.get("entry_price")
            sh = float(t.get("shares") or 0)
            mv = float(px or 0) * sh
        open_mv += float(mv or 0)
        up = t.get("unrealized_pnl")
        if up is None:
            entry = float(t.get("entry_price") or 0)
            sh = float(t.get("shares") or 0)
            px = float(t.get("current_price") or entry)
            up = (px - entry) * sh
        unrealized += float(up or 0)

    total_equity = round(cash + open_mv, 4)
    with get_conn() as conn:
        closed_rows = conn.execute(
            "SELECT realized_pnl FROM paper_trades WHERE status = 'closed' AND exit_date = ?",
            (day,),
        ).fetchall()
        all_closed = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS s FROM paper_trades WHERE status = 'closed'"
        ).fetchone()
    trades_closed = len(closed_rows)
    wins = sum(1 for r in closed_rows if float(r["realized_pnl"] or 0) > 0)
    losses = sum(1 for r in closed_rows if float(r["realized_pnl"] or 0) < 0)
    realized_day = round(sum(float(r["realized_pnl"] or 0) for r in closed_rows), 4)
    cum_realized = round(float(all_closed["s"] or 0), 4)

    # Previous snapshot for daily return
    with get_conn() as conn:
        prev = conn.execute(
            "SELECT total_equity FROM paper_equity_snapshots "
            "WHERE as_of_date < ? ORDER BY as_of_date DESC LIMIT 1",
            (day,),
        ).fetchone()
    daily_ret = None
    if prev and float(prev["total_equity"] or 0) > 0:
        daily_ret = round(
            (total_equity - float(prev["total_equity"])) / float(prev["total_equity"]) * 100.0,
            4,
        )

    now = _utc_now_iso()
    row = {
        "as_of_date": day,
        "cash": round(cash, 4),
        "open_market_value": round(open_mv, 4),
        "total_equity": total_equity,
        "daily_unrealized_pnl": round(unrealized, 4),
        "cumulative_realized_pnl": cum_realized,
        "trades_closed": trades_closed,
        "wins": wins,
        "losses": losses,
        "realized_pnl_day": realized_day,
        "daily_return_pct": daily_ret,
        "updated_at": now,
    }
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO paper_equity_snapshots (
              as_of_date, cash, open_market_value, total_equity,
              daily_unrealized_pnl, cumulative_realized_pnl,
              trades_closed, wins, losses, realized_pnl_day, daily_return_pct, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(as_of_date) DO UPDATE SET
              cash = excluded.cash,
              open_market_value = excluded.open_market_value,
              total_equity = excluded.total_equity,
              daily_unrealized_pnl = excluded.daily_unrealized_pnl,
              cumulative_realized_pnl = excluded.cumulative_realized_pnl,
              trades_closed = excluded.trades_closed,
              wins = excluded.wins,
              losses = excluded.losses,
              realized_pnl_day = excluded.realized_pnl_day,
              daily_return_pct = excluded.daily_return_pct,
              updated_at = excluded.updated_at
            """,
            (
                row["as_of_date"],
                row["cash"],
                row["open_market_value"],
                row["total_equity"],
                row["daily_unrealized_pnl"],
                row["cumulative_realized_pnl"],
                row["trades_closed"],
                row["wins"],
                row["losses"],
                row["realized_pnl_day"],
                row["daily_return_pct"],
                row["updated_at"],
            ),
        )
    return row


def list_equity_snapshots(
    *, start_date: str | None = None, limit: int = 2000
) -> list[dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        if start_date:
            rows = conn.execute(
                "SELECT * FROM paper_equity_snapshots WHERE as_of_date >= ? "
                "ORDER BY as_of_date ASC LIMIT ?",
                (start_date, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM paper_equity_snapshots ORDER BY as_of_date ASC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def range_start_date(range_key: str, *, end: str | None = None) -> str | None:
    """Return YYYY-MM-DD start for History filter, or None for ALL."""
    key = (range_key or "ALL").upper()
    days_map = {"7D": 7, "30D": 30, "3M": 90, "6M": 180, "1Y": 365, "ALL": None}
    n = days_map.get(key, None)
    if n is None:
        return None
    end_d = _parse_ymd(end) or _parse_ymd(trading_day_pt())
    if not end_d:
        return None
    from datetime import timedelta

    return (end_d - timedelta(days=n - 1)).isoformat()


def _max_drawdown_pct(equities: list[float]) -> float | None:
    if not equities:
        return None
    peak = equities[0]
    max_dd = 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (eq - peak) / peak * 100.0
            if dd < max_dd:
                max_dd = dd
    return round(max_dd, 2)


def backfill_entry_research_from_candidates() -> int:
    """
    Fill missing entry research fields on existing trades from paper_candidates
    for the same entry_date + ticker. Never overwrites non-NULL entry values.
    """
    init_db()
    updated = 0
    with get_conn() as conn:
        trades = conn.execute(
            "SELECT id, ticker, entry_date, range_63d_pos_entry, financial_ok_entry, "
            "financial_known_entry, news_tone_entry FROM paper_trades"
        ).fetchall()
        for tr in trades:
            need = (
                tr["range_63d_pos_entry"] is None
                or tr["financial_ok_entry"] is None
                or tr["news_tone_entry"] is None
            )
            if not need:
                continue
            cand = conn.execute(
                "SELECT meta_json, financial_label, news_label FROM paper_candidates "
                "WHERE as_of_date = ? AND ticker = ?",
                (tr["entry_date"], tr["ticker"]),
            ).fetchone()
            if not cand:
                continue
            meta = {}
            try:
                meta = json.loads(cand["meta_json"] or "{}")
            except Exception:
                meta = {}
            sets = []
            vals = []
            if tr["range_63d_pos_entry"] is None and meta.get("range_63d_pos") is not None:
                sets.append("range_63d_pos_entry = ?")
                vals.append(meta.get("range_63d_pos"))
            if tr["financial_ok_entry"] is None and meta.get("financial_ok") is not None:
                sets.append("financial_ok_entry = ?")
                vals.append(meta.get("financial_ok"))
            if tr["financial_known_entry"] is None and meta.get("financial_known") is not None:
                sets.append("financial_known_entry = ?")
                vals.append(meta.get("financial_known"))
            if tr["news_tone_entry"] is None and meta.get("news_tone"):
                sets.append("news_tone_entry = ?")
                vals.append(meta.get("news_tone"))
            if not sets:
                continue
            vals.append(tr["id"])
            conn.execute(
                f"UPDATE paper_trades SET {', '.join(sets)} WHERE id = ?",
                vals,
            )
            updated += 1
    return updated


def history_report(*, range_key: str = "ALL") -> dict[str, Any]:
    """Build History tab payload for the selected time range."""
    ensure_portfolio()
    try:
        backfill_entry_research_from_candidates()
    except Exception:
        log.exception("entry research backfill skipped")
    # Ensure today's snapshot exists so the equity curve is not empty.
    try:
        save_equity_snapshot()
    except Exception:
        log.exception("history equity snapshot skipped")

    key = (range_key or "ALL").upper()
    if key not in ("7D", "30D", "3M", "6M", "1Y", "ALL"):
        key = "ALL"
    start = range_start_date(key)
    port = ensure_portfolio()
    starting = float(port["starting_capital"])

    snaps = list_equity_snapshots(start_date=start)
    closed_all = list_closed_trades(limit=5000)
    if start:
        closed = [t for t in closed_all if (t.get("exit_date") or "") >= start]
    else:
        closed = closed_all

    # Enrich trades for display (do not mutate DB entry fields).
    trades_out = []
    for t in closed:
        row = dict(t)
        row["exit_reason_norm"] = normalize_exit_reason(t.get("exit_reason"))
        row["holding_days"] = holding_days_calendar(t.get("entry_date"), t.get("exit_date"))
        # Financial score display: prefer ok/known; else label
        ok = t.get("financial_ok_entry")
        known = t.get("financial_known_entry")
        if ok is not None and known is not None:
            row["financial_score_entry"] = f"{ok}/{known}"
        else:
            row["financial_score_entry"] = t.get("financial_entry") or None
        row["news_grade_entry"] = t.get("news_entry") or t.get("news_tone_entry") or None
        row["source_label_entry"] = format_source_label(t.get("source_at_entry"))
        trades_out.append(row)

    wins = [t for t in closed if float(t.get("realized_pnl") or 0) > 0]
    losses = [t for t in closed if float(t.get("realized_pnl") or 0) < 0]
    n_closed = len(closed)
    win_rate = (len(wins) / n_closed * 100.0) if n_closed else None
    avg_gain = (
        sum(float(t.get("return_pct") or 0) for t in wins) / len(wins) if wins else None
    )
    avg_loss = (
        sum(float(t.get("return_pct") or 0) for t in losses) / len(losses) if losses else None
    )
    gross_profit = sum(float(t.get("realized_pnl") or 0) for t in wins)
    gross_loss_abs = abs(sum(float(t.get("realized_pnl") or 0) for t in losses))
    if gross_loss_abs > 0:
        profit_factor = round(gross_profit / gross_loss_abs, 2)
    elif gross_profit > 0:
        profit_factor = None  # no losses — display as — or ∞ later in UI
        profit_factor_inf = True
    else:
        profit_factor = None
        profit_factor_inf = False
    if gross_loss_abs > 0 or gross_profit <= 0:
        profit_factor_inf = False

    realized_total = sum(float(t.get("realized_pnl") or 0) for t in closed)
    # Ending equity: last snapshot in range, else live portfolio.
    if snaps:
        ending = float(snaps[-1]["total_equity"])
    else:
        ending = float(portfolio_summary()["current_equity"])
    total_return_pct = (
        (ending - starting) / starting * 100.0 if starting else None
    )
    max_dd = _max_drawdown_pct([float(s["total_equity"]) for s in snaps])

    # Exit analysis
    by_reason: dict[str, list] = {}
    for t in closed:
        reason = normalize_exit_reason(t.get("exit_reason"))
        by_reason.setdefault(reason, []).append(t)
    exit_analysis = []
    ordered = [EXIT_TAKE, EXIT_STOP, EXIT_MANUAL] + [
        r for r in by_reason if r not in (EXIT_TAKE, EXIT_STOP, EXIT_MANUAL)
    ]
    for reason in ordered:
        group = by_reason.get(reason) or []
        if not group:
            continue
        avg_ret = sum(float(x.get("return_pct") or 0) for x in group) / len(group)
        exit_analysis.append(
            {
                "exit_reason": reason,
                "trades": len(group),
                "total_pnl": round(sum(float(x.get("realized_pnl") or 0) for x in group), 2),
                "avg_return_pct": round(avg_ret, 2),
            }
        )

    # Daily table newest first
    daily = list(reversed(snaps))

    # Chart points (oldest → newest) + precomputed SVG polyline
    eq_vals = [round(float(s["total_equity"]), 2) for s in snaps]
    dates = [s["as_of_date"] for s in snaps]
    svg_points = ""
    if eq_vals:
        W, H, L, R, T, B = 640, 200, 48, 12, 12, 28
        pw, ph = W - L - R, H - T - B
        ymin, ymax = min(eq_vals), max(eq_vals)
        if ymin == ymax:
            ymin -= 1
            ymax += 1
        pad = (ymax - ymin) * 0.08
        y0, y1 = ymin - pad, ymax + pad
        n = len(eq_vals)
        pts = []
        for i, eq in enumerate(eq_vals):
            x = L + (pw * i / (n - 1 if n > 1 else 1))
            y = T + ph * (1 - ((eq - y0) / (y1 - y0)))
            pts.append(f"{x:.2f},{y:.2f}")
        svg_points = " ".join(pts)
        chart = {
            "dates": dates,
            "equity": eq_vals,
            "svg_points": svg_points,
            "y0": y0,
            "y1": y1,
            "n": n,
        }
    else:
        chart = {"dates": [], "equity": [], "svg_points": "", "y0": 0, "y1": 0, "n": 0}

    return {
        "range_key": key,
        "range_start": start,
        "perf": {
            "starting_capital": round(starting, 2),
            "ending_equity": round(ending, 2),
            "total_return_pct": round(total_return_pct, 2) if total_return_pct is not None else None,
            "total_realized_pnl": round(realized_total, 2),
            "closed_trades": n_closed,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round(win_rate, 1) if win_rate is not None else None,
            "avg_gain_pct": round(avg_gain, 2) if avg_gain is not None else None,
            "avg_loss_pct": round(avg_loss, 2) if avg_loss is not None else None,
            "profit_factor": profit_factor,
            "profit_factor_inf": profit_factor_inf,
            "max_drawdown_pct": max_dd,
        },
        "chart": chart,
        "daily": daily,
        "exit_analysis": exit_analysis,
        "trades": trades_out,
    }


def portfolio_summary() -> dict[str, Any]:
    port = ensure_portfolio()
    opens = list_open_trades()
    closed = list_closed_trades(limit=5000)
    invested = sum(float(t.get("cost") or 0) for t in opens)
    unrealized = sum(float(t.get("unrealized_pnl") or 0) for t in opens)
    realized_total = sum(float(t.get("realized_pnl") or 0) for t in closed)
    day = trading_day_pt()
    realized_today = sum(
        float(t.get("realized_pnl") or 0) for t in closed if (t.get("exit_date") or "") == day
    )
    cash = float(port["cash"])
    equity = cash + sum(float(t.get("market_value") or 0) for t in opens)
    starting = float(port["starting_capital"])
    total_return_pct = ((equity - starting) / starting * 100.0) if starting else 0.0
    wins = sum(1 for t in closed if float(t.get("realized_pnl") or 0) > 0)
    win_rate = (wins / len(closed) * 100.0) if closed else None

    return {
        "starting_capital": starting,
        "trading_limit": float(port["trading_limit"]),
        "reserve_cash": float(port["reserve_cash"]),
        "cash": round(cash, 2),
        "invested": round(invested, 2),
        "current_equity": round(equity, 2),
        "today_realized_pnl": round(realized_today, 2),
        "total_realized_pnl": round(realized_total, 2),
        "total_unrealized_pnl": round(unrealized, 2),
        "today_pnl": round(realized_today + unrealized, 2),
        "total_return_pct": round(total_return_pct, 2),
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "closed_trades": len(closed),
        "open_trades": len(opens),
        "updated_at": port.get("updated_at")
        or get_setting("paper_last_daily_update")
        or get_setting("paper_candidates_updated_at"),
        "candidates_as_of": get_setting("paper_candidates_as_of"),
        "last_daily_update": get_setting("paper_last_daily_update"),
    }
