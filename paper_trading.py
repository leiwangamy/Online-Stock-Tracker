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
# Paper trading always allows fractional shares so small $ budgets can fill exactly.
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
    Always uses fractional shares so allocation $ can be filled exactly.
    """
    if price is None or price <= 0 or target_alloc is None or target_alloc <= 0:
        return 0.0, 0.0, "none"
    shares = round(float(target_alloc) / float(price), 4)
    if shares <= 0:
        return 0.0, 0.0, "fractional"
    cost = round(shares * float(price), 4)
    return shares, cost, "fractional"


def stop_take_prices(
    entry: float, stop_pct: float, take_pct: float
) -> tuple[float, float]:
    stop = round(entry * (1.0 - stop_pct / 100.0), 4)
    take = round(entry * (1.0 + take_pct / 100.0), 4)
    return stop, take


def risk_reward_metrics(
    entry: float | None, stop: float | None, take: float | None
) -> dict[str, float | None]:
    """Implied LONG risk / reward from entry vs stop / take prices."""
    try:
        e = float(entry) if entry is not None else None
        s = float(stop) if stop is not None else None
        t = float(take) if take is not None else None
    except (TypeError, ValueError):
        return {"stop_risk_pct": None, "reward_pct": None, "rr_ratio": None}
    if e is None or e <= 0:
        return {"stop_risk_pct": None, "reward_pct": None, "rr_ratio": None}
    risk = round((e - s) / e * 100.0, 2) if s is not None else None
    reward = round((t - e) / e * 100.0, 2) if t is not None else None
    rr = None
    if risk is not None and reward is not None and risk > 0:
        rr = round(reward / risk, 2)
    return {"stop_risk_pct": risk, "reward_pct": reward, "rr_ratio": rr}


def validate_long_levels(entry: float, stop: float, take: float) -> str | None:
    """Return error message if LONG levels are invalid; else None."""
    if not (stop < entry < take):
        return (
            f"Stop Loss ({stop:.2f}) must be below entry ({entry:.2f}) "
            f"and Take Profit ({take:.2f}) above entry"
        )
    return None


def get_level_overrides(
    tickers: list[str] | None = None,
) -> dict[str, dict[str, float | None]]:
    """Return {TICKER: {manual_stop, manual_take}} (None = auto for that side)."""
    init_db()
    with get_conn() as conn:
        if tickers:
            clean = [((t or "").strip().upper()) for t in tickers if (t or "").strip()]
            if not clean:
                return {}
            ph = ",".join("?" * len(clean))
            rows = conn.execute(
                f"SELECT ticker, manual_stop, manual_take FROM paper_level_overrides "
                f"WHERE ticker IN ({ph})",
                clean,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ticker, manual_stop, manual_take FROM paper_level_overrides"
            ).fetchall()
    out: dict[str, dict[str, float | None]] = {}
    for r in rows:
        out[str(r["ticker"]).upper()] = {
            "manual_stop": (
                float(r["manual_stop"]) if r["manual_stop"] is not None else None
            ),
            "manual_take": (
                float(r["manual_take"]) if r["manual_take"] is not None else None
            ),
        }
    return out


def upsert_level_override(
    ticker: str,
    *,
    manual_stop: float | None = None,
    manual_take: float | None = None,
    reset_stop: bool = False,
    reset_take: bool = False,
) -> dict[str, float | None]:
    """
    Set / clear manual Stop or Take for a candidate ticker.
    Pass reset_stop/reset_take to clear that side back to AUTO.
    Omitting a side leaves the existing override unchanged.
    """
    t = (ticker or "").strip().upper()
    if not t:
        raise ValueError("ticker required")
    init_db()
    now = _utc_now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT manual_stop, manual_take FROM paper_level_overrides WHERE ticker = ?",
            (t,),
        ).fetchone()
        stop = float(cur["manual_stop"]) if cur and cur["manual_stop"] is not None else None
        take = float(cur["manual_take"]) if cur and cur["manual_take"] is not None else None
        if reset_stop:
            stop = None
        elif manual_stop is not None:
            s = float(manual_stop)
            if s <= 0 or s != s:
                raise ValueError("invalid stop")
            stop = round(s, 2)
        if reset_take:
            take = None
        elif manual_take is not None:
            tp = float(manual_take)
            if tp <= 0 or tp != tp:
                raise ValueError("invalid take profit")
            take = round(tp, 2)
        if stop is None and take is None:
            conn.execute("DELETE FROM paper_level_overrides WHERE ticker = ?", (t,))
        else:
            conn.execute(
                """
                INSERT INTO paper_level_overrides (ticker, manual_stop, manual_take, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                  manual_stop = excluded.manual_stop,
                  manual_take = excluded.manual_take,
                  updated_at = excluded.updated_at
                """,
                (t, stop, take, now),
            )
    return {"manual_stop": stop, "manual_take": take}


def clear_level_overrides(tickers: list[str]) -> None:
    clean = [((t or "").strip().upper()) for t in tickers if (t or "").strip()]
    if not clean:
        return
    init_db()
    ph = ",".join("?" * len(clean))
    with get_conn() as conn:
        conn.execute(
            f"DELETE FROM paper_level_overrides WHERE ticker IN ({ph})", clean
        )


def apply_level_override_to_row(
    row: dict[str, Any],
    override: dict[str, float | None] | None,
    *,
    default_stop: float | None = None,
    default_take: float | None = None,
) -> dict[str, Any]:
    """Mutate candidate row with AUTO defaults + MANUAL active levels + risk metrics."""
    price = row.get("price")
    try:
        entry = float(price) if price is not None else None
    except (TypeError, ValueError):
        entry = None
    d_stop = default_stop if default_stop is not None else row.get("default_stop")
    d_take = default_take if default_take is not None else row.get("default_take")
    if d_stop is None:
        d_stop = row.get("stop_price")
    if d_take is None:
        d_take = row.get("take_profit_price")
    try:
        d_stop = float(d_stop) if d_stop is not None else None
    except (TypeError, ValueError):
        d_stop = None
    try:
        d_take = float(d_take) if d_take is not None else None
    except (TypeError, ValueError):
        d_take = None

    ov = override or {}
    m_stop = ov.get("manual_stop")
    m_take = ov.get("manual_take")
    stop = m_stop if m_stop is not None else d_stop
    take = m_take if m_take is not None else d_take
    row["default_stop"] = None if d_stop is None else round(d_stop, 2)
    row["default_take"] = None if d_take is None else round(d_take, 2)
    row["manual_stop"] = None if m_stop is None else round(float(m_stop), 2)
    row["manual_take"] = None if m_take is None else round(float(m_take), 2)
    row["stop_price"] = None if stop is None else round(float(stop), 2)
    row["take_profit_price"] = None if take is None else round(float(take), 2)
    row["stop_source"] = "manual" if m_stop is not None else "default"
    row["take_source"] = "manual" if m_take is not None else "default"
    metrics = risk_reward_metrics(entry, row["stop_price"], row["take_profit_price"])
    row.update(metrics)
    row["levels_valid"] = (
        entry is not None
        and row["stop_price"] is not None
        and row["take_profit_price"] is not None
        and validate_long_levels(entry, row["stop_price"], row["take_profit_price"]) is None
    )
    return row


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

    # Knife Risk hard gate is applied in build_candidates (keep scores visible here).
    try:
        from knife_risk import attach_knife_risk

        attach_knife_risk(scored, ensure_bench=True)
    except Exception:
        for r in scored:
            r.setdefault("knife", None)

    # Rank purely by AI Score for Top-N selection (Priority does not change AI Score).
    scored.sort(
        key=lambda x: (-float(x.get("ai_score") or 0), x.get("ticker") or "")
    )
    return scored


def build_candidates(*, as_of_date: str | None = None, persist: bool = True) -> list[dict[str, Any]]:
    """Build AI Candidates Top 10 with suggested allocation / shares / stops."""
    from knife_risk import KNIFE_AUTO_BLOCK_THRESHOLD, knife_auto_blocked

    cfg = _cfg()
    day = as_of_date or trading_day_pt()
    scored = _score_universe_rows()
    # Hard safety gate: Knife Risk >= threshold → never enter Auto Trading pool.
    # AI Score cannot override. Blocked names stay on Watchlist / Research.
    eligible: list[dict[str, Any]] = []
    blocked_n = 0
    for r in scored:
        k = r.get("knife") or {}
        score = k.get("score") if isinstance(k, dict) else None
        if knife_auto_blocked(score):
            blocked_n += 1
            continue
        eligible.append(r)
    top = eligible[:TOP_N]
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
    # Preload manual SL/TP overrides (survive candidate rebuild / price refresh).
    top_tickers = [(r.get("ticker") or "").upper() for r in top if r.get("ticker")]
    overrides = get_level_overrides(top_tickers)
    for i, r in enumerate(top):
        rank = i + 1
        target = ALLOC_LADDER[i] if i < len(ALLOC_LADDER) else 0.0
        # Do not exceed remaining trading-fund capacity for suggestions.
        room = max(0.0, remaining_limit - used)
        target = min(target, room) if target > 0 else 0.0
        price = float(r["price"])
        shares, cost, mode = size_position(price, target) if target > 0 else (0.0, 0.0, "none")
        # If share cost exceeds remaining room, skip suggestion.
        if cost > room + 1e-6:
            shares, cost, mode = 0.0, 0.0, "none"
            target = 0.0
        auto_stop, auto_take = stop_take_prices(
            price, cfg["stop_loss_pct"], cfg["take_profit_pct"]
        )
        if cost > 0:
            used += cost
        elif target > 0 and shares <= 0:
            target = 0.0
        knife = r.get("knife") if isinstance(r.get("knife"), dict) else {}
        ticker = r["ticker"].upper()
        row = {
            "as_of_date": day,
            "rank": rank,
            "ticker": ticker,
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
            "stop_price": auto_stop,
            "take_profit_price": auto_take,
            "stop_pct": cfg["stop_loss_pct"],
            "take_profit_pct": cfg["take_profit_pct"],
            "range_63d_pos": r.get("range_63d_pos"),
            "financial_ok": r.get("financial_ok"),
            "financial_known": r.get("financial_known"),
            "news_tone": r.get("news_tone"),
            "source_codes": r.get("source_codes") or "",
            "source_label": r.get("source_label") or format_source_label(r.get("source_codes") or ""),
            "knife_score": knife.get("score"),
            "knife_level": knife.get("level"),
            "updated_at": now,
        }
        apply_level_override_to_row(
            row,
            overrides.get(ticker),
            default_stop=auto_stop,
            default_take=auto_take,
        )
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
                                "default_stop": row.get("default_stop"),
                                "default_take": row.get("default_take"),
                                "manual_stop": row.get("manual_stop"),
                                "manual_take": row.get("manual_take"),
                                "stop_source": row.get("stop_source"),
                                "take_source": row.get("take_source"),
                                "range_63d_pos": row.get("range_63d_pos"),
                                "financial_ok": row.get("financial_ok"),
                                "financial_known": row.get("financial_known"),
                                "news_tone": row.get("news_tone"),
                                "source_codes": row.get("source_codes") or "",
                                "knife_score": row.get("knife_score"),
                                "knife_level": row.get("knife_level"),
                                "knife_auto_block_threshold": KNIFE_AUTO_BLOCK_THRESHOLD,
                                "knife_blocked_pool_count": blocked_n,
                            }
                        ),
                        row["updated_at"],
                    ),
                )
        set_setting("paper_candidates_updated_at", now)
        set_setting("paper_candidates_as_of", day)
        set_setting("paper_knife_blocked_count", blocked_n)
    return out


def list_candidates(as_of_date: str | None = None) -> list[dict[str, Any]]:
    init_db()
    day = as_of_date or get_setting("paper_candidates_as_of") or trading_day_pt()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_candidates WHERE as_of_date = ? ORDER BY rank ASC",
            (day,),
        ).fetchall()
    tickers = [str(r["ticker"]).upper() for r in rows if r["ticker"]]
    overrides = get_level_overrides(tickers)
    cfg = _cfg()
    out = []
    for r in rows:
        row = dict(r)
        codes = ""
        meta: dict[str, Any] = {}
        try:
            meta = json.loads(row.get("meta_json") or "{}")
            codes = meta.get("source_codes") or ""
        except Exception:
            codes = ""
        row["source_codes"] = codes
        row["source_label"] = format_source_label(codes)
        row["knife_score"] = meta.get("knife_score")
        row["knife_level"] = meta.get("knife_level")
        row["stop_pct"] = meta.get("stop_pct", cfg["stop_loss_pct"])
        row["take_profit_pct"] = meta.get("take_profit_pct", cfg["take_profit_pct"])
        # Recompute AUTO defaults from current candidate price; re-apply manual overrides.
        try:
            px = float(row["price"]) if row.get("price") is not None else None
        except (TypeError, ValueError):
            px = None
        if px is not None and px > 0:
            auto_stop, auto_take = stop_take_prices(
                px, float(row["stop_pct"]), float(row["take_profit_pct"])
            )
        else:
            auto_stop = meta.get("default_stop")
            auto_take = meta.get("default_take")
        apply_level_override_to_row(
            row,
            overrides.get(str(row["ticker"]).upper()),
            default_stop=auto_stop,
            default_take=auto_take,
        )
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
    return [annotate_open_trade_levels(dict(r)) for r in rows]


def annotate_open_trade_levels(tr: dict[str, Any]) -> dict[str, Any]:
    """Attach AUTO defaults, manual flags, and R/R metrics for an open trade."""
    try:
        entry = float(tr["entry_price"])
    except (TypeError, ValueError, KeyError):
        entry = None
    cfg = _cfg()
    try:
        stop_pct = float(
            tr["stop_pct"] if tr.get("stop_pct") is not None else cfg["stop_loss_pct"]
        )
    except (TypeError, ValueError):
        stop_pct = float(cfg["stop_loss_pct"])
    try:
        take_pct = float(
            tr["take_profit_pct"]
            if tr.get("take_profit_pct") is not None
            else cfg["take_profit_pct"]
        )
    except (TypeError, ValueError):
        take_pct = float(cfg["take_profit_pct"])
    tr["stop_pct"] = stop_pct
    tr["take_profit_pct"] = take_pct
    if entry is not None and entry > 0:
        d_stop, d_take = stop_take_prices(entry, stop_pct, take_pct)
    else:
        d_stop = d_take = None
    tr["default_stop"] = None if d_stop is None else round(float(d_stop), 2)
    tr["default_take"] = None if d_take is None else round(float(d_take), 2)
    try:
        cur_stop = float(tr["stop_price"]) if tr.get("stop_price") is not None else None
    except (TypeError, ValueError):
        cur_stop = None
    try:
        cur_take = (
            float(tr["take_profit_price"])
            if tr.get("take_profit_price") is not None
            else None
        )
    except (TypeError, ValueError):
        cur_take = None
    tr["stop_source"] = (
        "manual"
        if (
            cur_stop is not None
            and tr["default_stop"] is not None
            and abs(cur_stop - tr["default_stop"]) > 0.009
        )
        else "default"
    )
    tr["take_source"] = (
        "manual"
        if (
            cur_take is not None
            and tr["default_take"] is not None
            and abs(cur_take - tr["default_take"]) > 0.009
        )
        else "default"
    )
    tr.update(risk_reward_metrics(entry, cur_stop, cur_take))
    tr["levels_valid"] = (
        entry is not None
        and cur_stop is not None
        and cur_take is not None
        and validate_long_levels(entry, cur_stop, cur_take) is None
    )
    return tr


def update_open_trade_shares(trade_id: int, shares: float) -> dict[str, Any]:
    """
    Admin: change share count on an open paper trade.
    Recalculates cost at entry price and adjusts portfolio cash.
    Blocks if insufficient cash or trading limit would be exceeded.
    """
    tid = int(trade_id)
    try:
        new_shares = float(shares)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid shares") from exc
    if new_shares <= 0 or new_shares != new_shares:
        raise ValueError("shares must be > 0")

    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM paper_trades WHERE id = ? AND status = 'open'",
            (tid,),
        ).fetchone()
        if not row:
            raise ValueError("open trade not found")
        tr = dict(row)
        # Always allow fractional shares on paper positions.
        new_shares = round(new_shares, 4)

        entry = float(tr["entry_price"])
        old_shares = float(tr["shares"])
        old_cost = float(tr["cost"])
        new_cost = round(new_shares * entry, 4)
        delta = round(new_cost - old_cost, 4)

        port = conn.execute(
            "SELECT cash, trading_limit FROM paper_portfolio WHERE id = 1"
        ).fetchone()
        if not port:
            raise ValueError("portfolio missing")
        cash = float(port["cash"])
        trading_limit = float(port["trading_limit"])
        inv_row = conn.execute(
            "SELECT COALESCE(SUM(cost), 0) AS s FROM paper_trades WHERE status = 'open'"
        ).fetchone()
        invested = float(inv_row["s"] or 0)

        if delta > 1e-6:
            if delta > cash + 1e-6:
                raise ValueError(
                    f"insufficient cash: need ${delta:.2f} more, cash ${cash:.2f}"
                )
            if invested + delta > trading_limit + 1e-6:
                raise ValueError(
                    f"trading limit reached: invested ${invested:.2f}, "
                    f"need +${delta:.2f}, limit ${trading_limit:.2f}"
                )

        try:
            current = (
                float(tr["current_price"])
                if tr.get("current_price") is not None
                else entry
            )
        except (TypeError, ValueError):
            current = entry
        mv = round(current * new_shares, 4)
        upnl = round((current - entry) * new_shares, 4)
        upct = round((current - entry) / entry * 100.0, 4) if entry else 0.0
        now = _utc_now_iso()
        conn.execute(
            """
            UPDATE paper_trades
            SET shares = ?, cost = ?, market_value = ?,
                unrealized_pnl = ?, unrealized_pnl_pct = ?, updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (new_shares, new_cost, mv, upnl, upct, now, tid),
        )
        conn.execute(
            "UPDATE paper_portfolio SET cash = ?, updated_at = ? WHERE id = 1",
            (round(cash - delta, 4), now),
        )
        updated = conn.execute(
            "SELECT * FROM paper_trades WHERE id = ?", (tid,)
        ).fetchone()
    out = annotate_open_trade_levels(dict(updated))
    out["cash_delta"] = delta
    out["cash_after"] = round(cash - delta, 4)
    return out


def update_open_trade_levels(
    trade_id: int,
    *,
    manual_stop: float | None = None,
    manual_take: float | None = None,
    reset_stop: bool = False,
    reset_take: bool = False,
) -> dict[str, Any]:
    """
    Admin: update Stop / Take on an open paper trade.
    Reset restores AUTO from entry × frozen stop_pct / take_profit_pct.
    Does not change entry price or shares.
    """
    tid = int(trade_id)
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM paper_trades WHERE id = ? AND status = 'open'",
            (tid,),
        ).fetchone()
        if not row:
            raise ValueError("open trade not found")
        tr = dict(row)
        entry = float(tr["entry_price"])
        cfg = _cfg()
        stop_pct = float(
            tr["stop_pct"] if tr.get("stop_pct") is not None else cfg["stop_loss_pct"]
        )
        take_pct = float(
            tr["take_profit_pct"]
            if tr.get("take_profit_pct") is not None
            else cfg["take_profit_pct"]
        )
        d_stop, d_take = stop_take_prices(entry, stop_pct, take_pct)
        stop = float(tr["stop_price"])
        take = float(tr["take_profit_price"])
        if reset_stop:
            stop = float(d_stop)
        elif manual_stop is not None:
            s = float(manual_stop)
            if s <= 0 or s != s:
                raise ValueError("invalid stop")
            stop = round(s, 2)
        if reset_take:
            take = float(d_take)
        elif manual_take is not None:
            tp = float(manual_take)
            if tp <= 0 or tp != tp:
                raise ValueError("invalid take profit")
            take = round(tp, 2)
        err = validate_long_levels(entry, stop, take)
        if err:
            raise ValueError(err)
        now = _utc_now_iso()
        conn.execute(
            """
            UPDATE paper_trades
            SET stop_price = ?, take_profit_price = ?, updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (round(stop, 4), round(take, 4), now, tid),
        )
        updated = conn.execute(
            "SELECT * FROM paper_trades WHERE id = ?", (tid,)
        ).fetchone()
    return annotate_open_trade_levels(dict(updated))


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
            skipped.append(
                {
                    "ticker": t,
                    "reason": "trading_limit",
                    "detail": (
                        f"need ${cost:.2f}, invested ${invested:.2f}, "
                        f"limit ${trading_limit:.2f}"
                    ),
                    "cost": cost,
                    "cash": cash,
                    "invested": invested,
                    "trading_limit": trading_limit,
                }
            )
            continue
        if cost > cash + 1e-6:
            skipped.append(
                {
                    "ticker": t,
                    "reason": "insufficient_cash",
                    "detail": f"need ${cost:.2f}, cash ${cash:.2f}",
                    "cost": cost,
                    "cash": cash,
                }
            )
            continue

        stop = float(c["stop_price"])
        take = float(c["take_profit_price"])
        level_err = validate_long_levels(price, stop, take)
        if level_err:
            skipped.append(
                {
                    "ticker": t,
                    "reason": "invalid_levels",
                    "detail": level_err,
                }
            )
            continue
        meta = {}
        try:
            meta = json.loads(c.get("meta_json") or "{}")
        except Exception:
            meta = {}
        # Prefer implied % from active levels; fall back to settings defaults.
        if price > 0:
            stop_pct = round((price - stop) / price * 100.0, 4)
            take_pct = round((take - price) / price * 100.0, 4)
        else:
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
                    c.get("shares_mode") or "fractional",
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
        # Manual levels are consumed once an order is created.
        clear_level_overrides([t])

    set_setting("paper_last_order_at", now)
    try:
        save_equity_snapshot(as_of_date=day)
    except Exception:
        log.exception("equity snapshot after create_orders failed")
    return {"created": created, "skipped": skipped, "cash": cash, "invested": invested}


def manual_buy_candidate(
    ticker: str,
    *,
    amount: float | None = None,
    shares: float | None = None,
) -> dict[str, Any]:
    """
    Admin: manually buy / add shares for a Top-list ticker.
    - Not open → create a new paper position
    - Already open → add shares (加仓) at current candidate/open price
    Prefer explicit shares; else amount ÷ price. Cash + trading limit still apply.
    """
    t = (ticker or "").strip().upper()
    if not t:
        raise ValueError("ticker required")

    cands = {str(c["ticker"]).upper(): c for c in list_candidates()}
    c = cands.get(t)
    if not c:
        # Fall back: allow buy if we can still price it (e.g. after refresh lag).
        ohlc = _fetch_daily_ohlc(t)
        if not ohlc or ohlc.get("close") is None:
            raise ValueError(f"{t} not in today's AI candidates and no price")
        price = float(ohlc["close"])
        c = {
            "ticker": t,
            "name": "",
            "price": price,
            "stop_price": None,
            "take_profit_price": None,
            "ai_score": None,
            "mos_t": None,
            "financial_label": None,
            "news_label": None,
            "meta_json": "{}",
            "is_priority": 0,
            "rank": 0,
            "shares_mode": "fractional",
        }
    try:
        price = float(c["price"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid price") from exc
    if price <= 0:
        raise ValueError("invalid price")

    cfg = _cfg()
    mode = "fractional"

    add_shares: float | None = None
    if shares is not None and str(shares).strip() != "":
        try:
            add_shares = float(shares)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid shares") from exc
    elif amount is not None and str(amount).strip() != "":
        try:
            amt = float(amount)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid amount") from exc
        if amt <= 0:
            raise ValueError("amount must be > 0")
        add_shares = round(amt / price, 4)
    else:
        raise ValueError("enter buy amount ($) or shares")

    if add_shares is None or add_shares <= 0:
        raise ValueError("shares must be > 0")
    add_shares = round(float(add_shares), 4)

    opens = {o["ticker"].upper(): o for o in list_open_trades()}
    if t in opens:
        # 加仓 existing position
        tr = opens[t]
        old = float(tr["shares"])
        new_total = old + add_shares
        # Buy add-on at current candidate price for cash, but cost basis uses entry*
        # For paper simplicity: add cost at current price, blend into cost field.
        # update_open_trade_shares recalculates cost = shares * entry_price which
        # would mis-state cash for average-up. So do an explicit add-at-market.
        return _add_to_open_trade(tr, add_shares=add_shares, fill_price=price)

    # New position from candidate
    cost = round(add_shares * price, 4)
    stop = c.get("stop_price")
    take = c.get("take_profit_price")
    try:
        stop_pct = float(c.get("stop_pct") or cfg["stop_loss_pct"])
    except (TypeError, ValueError):
        stop_pct = float(cfg["stop_loss_pct"])
    try:
        take_pct = float(c.get("take_profit_pct") or cfg["take_profit_pct"])
    except (TypeError, ValueError):
        take_pct = float(cfg["take_profit_pct"])
    if stop is None or take is None:
        stop, take = stop_take_prices(price, stop_pct, take_pct)
    else:
        stop, take = float(stop), float(take)
    level_err = validate_long_levels(price, stop, take)
    if level_err:
        raise ValueError(level_err)

    port = ensure_portfolio()
    cash = float(port["cash"])
    trading_limit = float(port["trading_limit"])
    invested = sum_open_invested()
    if cost > cash + 1e-6:
        raise ValueError(f"insufficient cash: need ${cost:.2f}, cash ${cash:.2f}")
    if invested + cost > trading_limit + 1e-6:
        raise ValueError(
            f"trading limit reached: invested ${invested:.2f}, "
            f"need ${cost:.2f}, limit ${trading_limit:.2f}"
        )

    meta: dict[str, Any] = {}
    try:
        meta = json.loads(c.get("meta_json") or "{}")
    except Exception:
        meta = {}
    day = trading_day_pt()
    now = _utc_now_iso()
    with get_conn() as conn:
        cur = conn.execute(
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
                add_shares,
                mode,
                cost,
                stop,
                take,
                stop_pct,
                take_pct,
                c.get("ai_score"),
                c.get("mos_t"),
                c.get("financial_label"),
                c.get("news_label"),
                meta.get("range_63d_pos", c.get("range_63d_pos")),
                meta.get("financial_ok", c.get("financial_ok")),
                meta.get("financial_known", c.get("financial_known")),
                meta.get("news_tone", c.get("news_tone")),
                meta.get("source_codes") or c.get("source_codes") or "",
                int(c.get("is_priority") or 0),
                int(c.get("rank") or 0),
                price,
                cost,
                c.get("ai_score"),
                now,
                now,
            ),
        )
        new_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE paper_portfolio SET cash = ?, updated_at = ? WHERE id = 1",
            (round(cash - cost, 4), now),
        )
    try:
        save_equity_snapshot(as_of_date=day)
    except Exception:
        log.exception("equity snapshot after manual_buy failed")
    return {
        "mode": "new",
        "id": new_id,
        "ticker": t,
        "shares": add_shares,
        "entry_price": price,
        "cost": cost,
        "cash_after": round(cash - cost, 4),
    }


def _add_to_open_trade(
    tr: dict[str, Any], *, add_shares: float, fill_price: float
) -> dict[str, Any]:
    """Add shares to an open trade at fill_price (average into cost)."""
    tid = int(tr["id"])
    add_shares = float(add_shares)
    fill_price = float(fill_price)
    if add_shares <= 0 or fill_price <= 0:
        raise ValueError("invalid add size/price")
    add_cost = round(add_shares * fill_price, 4)

    port = ensure_portfolio()
    cash = float(port["cash"])
    trading_limit = float(port["trading_limit"])
    invested = sum_open_invested()
    if add_cost > cash + 1e-6:
        raise ValueError(f"insufficient cash: need ${add_cost:.2f}, cash ${cash:.2f}")
    if invested + add_cost > trading_limit + 1e-6:
        raise ValueError(
            f"trading limit reached: invested ${invested:.2f}, "
            f"need ${add_cost:.2f}, limit ${trading_limit:.2f}"
        )

    old_shares = float(tr["shares"])
    old_cost = float(tr["cost"])
    new_shares = round(old_shares + add_shares, 4)
    new_cost = round(old_cost + add_cost, 4)
    new_entry = round(new_cost / new_shares, 4) if new_shares else fill_price
    current = float(tr.get("current_price") or fill_price)
    mv = round(current * new_shares, 4)
    upnl = round(mv - new_cost, 4)
    upct = round((current - new_entry) / new_entry * 100.0, 4) if new_entry else 0.0
    # Keep stop/take as absolute prices; optionally leave unchanged on add.
    now = _utc_now_iso()
    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE paper_trades
            SET shares = ?, cost = ?, entry_price = ?,
                current_price = ?, market_value = ?,
                unrealized_pnl = ?, unrealized_pnl_pct = ?, updated_at = ?
            WHERE id = ? AND status = 'open'
            """,
            (new_shares, new_cost, new_entry, current, mv, upnl, upct, now, tid),
        )
        conn.execute(
            "UPDATE paper_portfolio SET cash = ?, updated_at = ? WHERE id = 1",
            (round(cash - add_cost, 4), now),
        )
    return {
        "mode": "add",
        "id": tid,
        "ticker": tr["ticker"],
        "shares_added": add_shares,
        "shares": new_shares,
        "fill_price": fill_price,
        "cost_added": add_cost,
        "cost": new_cost,
        "entry_price": new_entry,
        "cash_after": round(cash - add_cost, 4),
    }


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


def _rebuy_cutoff_date(*, trading_days: int = 63) -> str:
    """
    Earliest exit_date (YYYY-MM-DD) still inside the last N trading days.
    Prefer cached market calendar (daily_bars / strong_daily / equity snapshots);
    fall back to a calendar-day estimate. Never deletes history.
    """
    n = max(1, int(trading_days))
    today = trading_day_pt()
    init_db()
    dates: list[str] = []
    with get_conn() as conn:
        for sql, args in (
            (
                "SELECT DISTINCT date AS d FROM daily_bars "
                "WHERE ticker IN ('SPY','spy') AND date <= ? "
                "ORDER BY date DESC LIMIT ?",
                (today, n),
            ),
            (
                "SELECT DISTINCT as_of_date AS d FROM strong_daily "
                "WHERE as_of_date <= ? ORDER BY as_of_date DESC LIMIT ?",
                (today, n),
            ),
            (
                "SELECT as_of_date AS d FROM paper_equity_snapshots "
                "WHERE as_of_date <= ? ORDER BY as_of_date DESC LIMIT ?",
                (today, n),
            ),
        ):
            try:
                rows = conn.execute(sql, args).fetchall()
            except Exception:
                rows = []
            if rows:
                dates = [str(r["d"])[:10] for r in rows if r["d"]]
                break
    if len(dates) >= n:
        return dates[n - 1]
    if dates:
        return dates[-1]
    # ~N trading days ≈ N * 7/5 calendar days (+ buffer).
    from datetime import date, timedelta

    try:
        base = date.fromisoformat(today)
    except ValueError:
        base = datetime.now(PT).date()
    return (base - timedelta(days=int(n * 1.5) + 7)).isoformat()


def list_rebuy_candidates(
    *, top_n: int = 8, lookback_trading_days: int = 63
) -> dict[str, Any]:
    """
    Re-entry pool: closed within last N trading days, not currently open,
    one row per ticker (most recent close). Ranked by current relevance
    (watchlist / research / current AI Score / knife / exit date).

    Returns:
      {
        "all": [...],          # full ranked pool (never permanently dropped)
        "top": [...],          # first top_n of all
        "total": int,
        "top_n": int,
        "lookback_trading_days": int,
        "cutoff_date": str,
      }
    History rows are never deleted.
    """
    from knife_risk import knife_auto_blocked
    from watchlist_config import get_my_watchlist

    top_n = max(1, int(top_n))
    cutoff = _rebuy_cutoff_date(trading_days=lookback_trading_days)
    open_tickers = {t["ticker"].upper() for t in list_open_trades()}
    # Pull enough closed rows; filter by exit_date >= cutoff.
    closed = list_closed_trades(limit=2000)

    latest_by_ticker: dict[str, dict[str, Any]] = {}
    for t in closed:
        ticker = str(t.get("ticker") or "").upper()
        if not ticker or ticker in open_tickers:
            continue
        exit_date = str(t.get("exit_date") or "")[:10]
        if not exit_date or exit_date < cutoff:
            continue
        prev = latest_by_ticker.get(ticker)
        if prev is None or exit_date > str(prev.get("exit_date") or "")[:10]:
            latest_by_ticker[ticker] = dict(t)
        elif exit_date == str(prev.get("exit_date") or "")[:10]:
            # Same day: prefer higher id (more recent insert).
            try:
                if int(t.get("id") or 0) > int(prev.get("id") or 0):
                    latest_by_ticker[ticker] = dict(t)
            except (TypeError, ValueError):
                pass

    mine = {str(x).strip().upper() for x in (get_my_watchlist() or []) if x}
    # Current research / AI Trading caches (no new Yahoo calls).
    cand_map: dict[str, dict[str, Any]] = {}
    try:
        for c in list_candidates():
            tk = str(c.get("ticker") or "").upper()
            if tk:
                cand_map[tk] = c
    except Exception:
        log.exception("rebuy: list_candidates failed")

    research_tickers = set(cand_map.keys())
    # Optional: dashboard_cache tickers already in LeiBot pools count as research-ish.
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT ticker FROM dashboard_cache WHERE ticker IS NOT NULL"
            ).fetchall()
        research_tickers |= {
            str(r["ticker"]).strip().upper() for r in rows if r["ticker"]
        }
    except Exception:
        pass

    ranked: list[dict[str, Any]] = []
    for ticker, src in latest_by_ticker.items():
        row = dict(src)
        row["exit_reason_norm"] = normalize_exit_reason(src.get("exit_reason"))
        c = cand_map.get(ticker) or {}
        cur_ai = c.get("ai_score")
        if cur_ai is None:
            cur_ai = c.get("ai_score_current")
        try:
            cur_ai_f = float(cur_ai) if cur_ai is not None else None
        except (TypeError, ValueError):
            cur_ai_f = None
        knife = c.get("knife_score")
        if knife is None and isinstance(c.get("knife"), dict):
            knife = c["knife"].get("score")
        try:
            knife_f = float(knife) if knife is not None else None
        except (TypeError, ValueError):
            knife_f = None
        in_mine = 1 if ticker in mine else 0
        in_research = 1 if ticker in research_tickers else 0
        knife_ok = 0 if knife_auto_blocked(knife_f) else 1
        row["current_ai_score"] = cur_ai_f
        row["current_knife_score"] = knife_f
        row["in_my_watchlist"] = in_mine
        row["in_research"] = in_research
        row["knife_ok"] = knife_ok
        ranked.append(row)

    # Watchlist / research → current AI Score → knife safety → recent exit (tie-break).
    ranked.sort(
        key=lambda r: (
            int(r.get("in_my_watchlist") or 0),
            int(r.get("in_research") or 0),
            float(r["current_ai_score"])
            if r.get("current_ai_score") is not None
            else -1.0,
            int(r.get("knife_ok") or 0),
            str(r.get("exit_date") or ""),
            int(r.get("id") or 0),
        ),
        reverse=True,
    )

    total = len(ranked)
    return {
        "all": ranked,
        "top": ranked[:top_n],
        "total": total,
        "top_n": top_n,
        "lookback_trading_days": lookback_trading_days,
        "cutoff_date": cutoff,
    }


def rebuy_from_closed_trade(
    trade_id: int, *, shares: float | None = None, amount: float | None = None
) -> dict[str, Any]:
    """
    Admin: open a NEW independent paper trade for a previously closed ticker.
    Uses current price, current settings Stop/Take %, current sizing, and
    current AI Score / Knife from today's candidate cache when available.
    Never reuses the old trade's entry / SL / TP / AI / Knife.
    Old closed History row is left unchanged.
    """
    tid = int(trade_id)
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM paper_trades WHERE id = ? AND status = 'closed'",
            (tid,),
        ).fetchone()
        if not row:
            raise ValueError("closed trade not found")
        src = dict(row)

    ticker = str(src["ticker"]).upper()
    open_tickers = {t["ticker"].upper() for t in list_open_trades()}
    if ticker in open_tickers:
        raise ValueError(f"{ticker} already has an open position")

    # Prefer today's candidate row (cached); else live OHLC for price only.
    cand_map = {str(c["ticker"]).upper(): c for c in list_candidates()}
    c = cand_map.get(ticker) or {}
    price = None
    used_cand_price = False
    try:
        if c.get("price") is not None:
            price = float(c["price"])
            used_cand_price = price > 0
    except (TypeError, ValueError):
        price = None
        used_cand_price = False
    if price is None or price <= 0:
        used_cand_price = False
        ohlc = _fetch_daily_ohlc(ticker)
        if ohlc and ohlc.get("close") is not None:
            price = float(ohlc["close"])
    if price is None or price <= 0:
        raise ValueError("no current price available to re-enter")

    cfg = _cfg()
    stop_pct = float(cfg["stop_loss_pct"])
    take_pct = float(cfg["take_profit_pct"])
    # New SL/TP from current settings; reuse candidate/Admin levels only when
    # entry price came from the same candidate row (same price basis).
    if (
        used_cand_price
        and c.get("stop_price") is not None
        and c.get("take_profit_price") is not None
    ):
        try:
            stop = float(c["stop_price"])
            take = float(c["take_profit_price"])
            if price > 0:
                stop_pct = round((price - stop) / price * 100.0, 4)
                take_pct = round((take - price) / price * 100.0, 4)
        except (TypeError, ValueError):
            stop, take = stop_take_prices(price, stop_pct, take_pct)
    else:
        stop, take = stop_take_prices(price, stop_pct, take_pct)

    level_err = validate_long_levels(price, stop, take)
    if level_err:
        raise ValueError(level_err)

    mode = "fractional"
    new_shares: float
    if shares is not None and str(shares).strip() != "":
        new_shares = round(float(shares), 4)
    elif amount is not None and str(amount).strip() != "":
        new_shares, _cost, mode = size_position(price, float(amount))
    elif c.get("suggested_shares") and float(c.get("suggested_shares") or 0) > 0:
        new_shares = round(float(c["suggested_shares"]), 4)
        mode = c.get("shares_mode") or "fractional"
    else:
        # Current sizing: apply fractional size to prior dollar exposure (or ladder $300).
        try:
            prior_cost = float(src.get("cost") or 0)
        except (TypeError, ValueError):
            prior_cost = 0.0
        target = prior_cost if prior_cost > 0 else float(ALLOC_LADDER[0])
        new_shares, _cost, mode = size_position(price, target)

    if new_shares <= 0:
        raise ValueError("shares must be > 0")
    new_shares = round(float(new_shares), 4)
    cost = round(new_shares * price, 4)

    port = ensure_portfolio()
    cash = float(port["cash"])
    trading_limit = float(port["trading_limit"])
    invested = sum_open_invested()
    if cost > cash + 1e-6:
        raise ValueError(f"insufficient cash: need ${cost:.2f}, cash ${cash:.2f}")
    if invested + cost > trading_limit + 1e-6:
        raise ValueError(
            f"trading limit reached: invested ${invested:.2f}, "
            f"need ${cost:.2f}, limit ${trading_limit:.2f}"
        )

    # Current research fields from candidate cache only (not old trade).
    meta: dict[str, Any] = {}
    try:
        meta = json.loads(c.get("meta_json") or "{}")
    except Exception:
        meta = {}
    ai_now = c.get("ai_score")
    if ai_now is None:
        ai_now = meta.get("ai_score")
    knife_now = c.get("knife_score")
    if knife_now is None and isinstance(c.get("knife"), dict):
        knife_now = c["knife"].get("score")
    if knife_now is None:
        knife_now = meta.get("knife_score")

    day = trading_day_pt()
    now = _utc_now_iso()
    with get_conn() as conn:
        cur = conn.execute(
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
                ticker,
                c.get("name") or src.get("name") or "",
                day,
                price,
                new_shares,
                mode if mode in ("integer", "fractional") else "fractional",
                cost,
                stop,
                take,
                stop_pct,
                take_pct,
                ai_now,
                c.get("mos_t"),
                c.get("financial_label"),
                c.get("news_label"),
                meta.get("range_63d_pos", c.get("range_63d_pos")),
                meta.get("financial_ok", c.get("financial_ok")),
                meta.get("financial_known", c.get("financial_known")),
                meta.get("news_tone", c.get("news_tone")),
                meta.get("source_codes") or c.get("source_codes") or "",
                int(c.get("is_priority") or 0),
                int(c.get("rank") or 0) if c.get("rank") is not None else None,
                price,
                cost,
                ai_now,
                now,
                now,
            ),
        )
        new_id = int(cur.lastrowid)
        conn.execute(
            "UPDATE paper_portfolio SET cash = ?, updated_at = ? WHERE id = 1",
            (round(cash - cost, 4), now),
        )
    try:
        save_equity_snapshot(as_of_date=day)
    except Exception:
        log.exception("equity snapshot after rebuy failed")
    return {
        "id": new_id,
        "from_trade_id": tid,
        "ticker": ticker,
        "shares": new_shares,
        "entry_price": price,
        "cost": cost,
        "stop_price": stop,
        "take_profit_price": take,
        "ai_score_entry": ai_now,
        "knife_score": knife_now,
        "cash_after": round(cash - cost, 4),
    }


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
