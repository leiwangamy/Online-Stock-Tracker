"""
AI APPROVED membership helpers (Owner ADD / REMOVE).

Long-term universe qualification is owned by core_universe.py
(Core Universe Filter — deterministic PASS/FAIL).

This module no longer defines WHO enters the long-term pool via LLM/scoring.
Legacy compute_core_score / build_select_candidates remain for compatibility only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from db import get_conn, get_setting, init_db, set_setting
from watchlist_config import get_my_watchlist, validate_ticker_token

META_SELECT_AS_OF = "ai_select_as_of"
META_SELECT_BUILT = "ai_select_built_at"

# CORE SCORE weights (sum = 1.0)
W_TREND = 0.25
W_RS = 0.20
W_RISING = 0.15
W_LIQ = 0.15
W_STAB = 0.10
W_SECTOR = 0.10
W_QUALITY = 0.05


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _norm_ret(r: float | None, scale: float) -> float:
    if r is None:
        return 50.0
    return _clamp(50.0 + (float(r) / scale) * 50.0)


def compute_core_score(row: dict[str, Any]) -> dict[str, Any]:
    """
    CORE SCORE 0–100: suitability for long-term AI APPROVED pool.
    Independent of BUY SCORE / Dist SMA25 entry zones.
    """
    ret_20 = row.get("return_20d_pct")
    ret_63 = row.get("return_63d_pct")
    rs_spy = row.get("rs_spy_20d")
    rs_sec = row.get("rs_sector_20d")
    rising_freq = row.get("rising_freq_20")  # 0..1 or count/20
    avg_move = row.get("avg_move_pct")  # 63D avg daily |move| %
    knife = row.get("knife_score")
    sector_rot = row.get("sector_rotation_score")
    dist = row.get("dist_pct")
    trend = (row.get("trend") or "").upper()

    # 1) Trend quality
    t20 = _norm_ret(ret_20, 20.0)
    t63 = _norm_ret(ret_63, 40.0)
    trend_bonus = 70.0 if trend == "UP" else (50.0 if trend == "MIXED" else 30.0)
    c_trend = 0.4 * t20 + 0.4 * t63 + 0.2 * trend_bonus

    # 2) Relative strength
    c_rs = 0.6 * _norm_ret(rs_spy, 12.0) + 0.4 * _norm_ret(rs_sec, 12.0)

    # 3) Rising consistency (reuse Rising Now frequency when provided)
    if rising_freq is None:
        c_rising = 40.0
    else:
        rf = float(rising_freq)
        if rf > 1.0:
            rf = rf / 20.0
        c_rising = _clamp(rf * 100.0)

    # 4) Liquidity / activity — prefer moderate Avg Daily Move zone
    if avg_move is None:
        c_liq = 40.0
    else:
        m = float(avg_move)
        if m < 0.6:
            c_liq = m / 0.6 * 40.0
        elif m <= 2.5:
            c_liq = 55.0 + (m - 0.6) / 1.9 * 45.0
        elif m <= 4.0:
            c_liq = 100.0 - (m - 2.5) / 1.5 * 25.0
        else:
            c_liq = max(40.0, 75.0 - (m - 4.0) * 8.0)

    # 5) Stability — penalize extreme avg move + knife
    if avg_move is None:
        c_stab = 50.0
    else:
        m = float(avg_move)
        if m <= 2.0:
            c_stab = 85.0
        elif m <= 3.5:
            c_stab = 70.0
        elif m <= 5.0:
            c_stab = 50.0
        else:
            c_stab = 30.0
    if knife is not None and float(knife) >= 45:
        c_stab = min(c_stab, 25.0)

    # 6) Sector rotation context
    if sector_rot is None:
        c_sec = 50.0
    else:
        c_sec = _clamp(float(sector_rot))

    # 7) Quality / risk
    c_qual = 80.0
    if knife is not None:
        k = float(knife)
        if k >= 70:
            c_qual = 10.0
        elif k >= 45:
            c_qual = 35.0
        elif k >= 25:
            c_qual = 60.0
    if row.get("data_error") or (str(row.get("ai_note") or "").upper().startswith("DATA ERROR")):
        c_qual = min(c_qual, 20.0)

    raw = (
        W_TREND * c_trend
        + W_RS * c_rs
        + W_RISING * c_rising
        + W_LIQ * c_liq
        + W_STAB * c_stab
        + W_SECTOR * c_sec
        + W_QUALITY * c_qual
    )
    score = int(round(_clamp(raw)))
    return {
        "core_score": score,
        "components": {
            "trend": round(c_trend, 1),
            "relative_strength": round(c_rs, 1),
            "rising": round(c_rising, 1),
            "liquidity": round(c_liq, 1),
            "stability": round(c_stab, 1),
            "sector": round(c_sec, 1),
            "quality": round(c_qual, 1),
        },
    }


def _return_n(closes: list[float], n: int) -> float | None:
    need = n + 1
    if len(closes) < need:
        return None
    base, last = float(closes[-need]), float(closes[-1])
    if base <= 0 or last <= 0:
        return None
    return (last / base - 1.0) * 100.0


def _load_closes(tickers: list[str]) -> dict[str, list[float]]:
    init_db()
    if not tickers:
        return {}
    ph = ",".join("?" * len(tickers))
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT ticker, close FROM daily_bars
            WHERE ticker IN ({ph}) AND date >= date('now', '-200 days')
            ORDER BY ticker, date
            """,
            tickers,
        ).fetchall()
    out: dict[str, list[float]] = {}
    for r in rows:
        t = (r["ticker"] or "").upper()
        try:
            out.setdefault(t, []).append(float(r["close"]))
        except (TypeError, ValueError):
            continue
    return out


def _sector_rotation_map() -> dict[str, int]:
    try:
        from sector_rotation import load_latest_sector_rotation, normalize_sector_name

        data = load_latest_sector_rotation(recompute_if_missing=False)
        return {
            normalize_sector_name(r["sector"]) or r["sector"]: int(r["rotation_score"])
            for r in (data.get("rows") or [])
            if r.get("rotation_score") is not None
        }
    except Exception:
        return {}


def build_select_candidates(*, limit: int = 120) -> dict[str, Any]:
    """
    Quantitative AI SELECT candidate list from research sources.
    Does not auto-approve. Owner (or later AI review) APPROVE / REJECT.
    """
    from rising_now import list_rising_now, rising_metrics_for_tickers
    from strong_stocks import list_active_strong_watchlist, strong_status_for_tickers
    from sector_rotation import normalize_sector_name
    from db import get_dashboard_by_tickers
    from knife_risk import attach_knife_risk, ensure_benchmark_returns

    rising_rows = list_rising_now()
    rising_set = {(r.get("ticker") or "").upper() for r in rising_rows if r.get("ticker")}
    strong_wl = list_active_strong_watchlist() or {}
    strong_set = {
        (r.get("symbol") or "").upper()
        for r in (strong_wl.get("rows") or [])
        if r.get("symbol")
    }

    # Discovery inbox tickers
    disc_set: set[str] = set()
    try:
        with get_conn() as conn:
            drows = conn.execute(
                """
                SELECT DISTINCT ticker FROM ai_discovery_candidates
                WHERE status IN ('DISCOVERED','WATCH','TRADE_CANDIDATE','ANALYZING')
                  AND ticker IS NOT NULL AND ticker != ''
                LIMIT 200
                """
            ).fetchall()
            disc_set = {(r["ticker"] or "").upper() for r in drows}
    except Exception:
        pass

    sources: dict[str, set[str]] = {}
    for t in rising_set:
        sources.setdefault(t, set()).add("RISING")
    for t in strong_set:
        sources.setdefault(t, set()).add("STRONG")
    for t in disc_set:
        sources.setdefault(t, set()).add("AI_DISCOVERY")
    for t in get_my_watchlist():
        sources.setdefault(t, set()).add("MY_WATCHLIST")

    # Already approved → skip as new candidates (still scored for refresh)
    approved = set(list_ai_approved_tickers())
    tickers = sorted(sources.keys())
    if not tickers:
        return {"as_of": "", "rows": [], "count": 0}

    dash = get_dashboard_by_tickers(tickers)
    closes = _load_closes(tickers)
    rising_m = rising_metrics_for_tickers(tickers)
    strong_m = strong_status_for_tickers(tickers)
    sec_map = _sector_rotation_map()
    spy_closes = closes.get("SPY") or _load_closes(["SPY"]).get("SPY") or []
    spy_20 = _return_n(spy_closes, 20)

    rows_in = []
    for t in tickers:
        d = dict(dash.get(t) or {"ticker": t})
        d["ticker"] = t
        cl = closes.get(t) or []
        d["return_20d_pct"] = _return_n(cl, 20)
        d["return_63d_pct"] = _return_n(cl, 63)
        d["return_5d_pct"] = (rising_m.get(t) or {}).get("return_5d_pct") or _return_n(cl, 5)
        if d["return_20d_pct"] is not None and spy_20 is not None:
            d["rs_spy_20d"] = round(d["return_20d_pct"] - spy_20, 2)
        # Rising frequency proxy: in Rising Now now → 1.0 else from up_days
        if t in rising_set:
            d["rising_freq_20"] = 0.55
        else:
            ud = (rising_m.get(t) or {}).get("up_days_5")
            d["rising_freq_20"] = (ud / 5.0) if ud is not None else 0.2
        st = strong_m.get(t) or {}
        d["in_strong"] = bool(st.get("in_membership"))
        d["in_rising"] = t in rising_set
        sec = normalize_sector_name(d.get("sector"))
        if sec and sec in sec_map:
            d["sector_rotation_score"] = sec_map[sec]
        d["sources"] = sorted(sources.get(t) or [])
        d["already_approved"] = t in approved
        rows_in.append(d)

    try:
        ensure_benchmark_returns(force=False)
        attach_knife_risk(rows_in, ensure_bench=False)
    except Exception:
        for r in rows_in:
            r.setdefault("knife", None)

    out_rows = []
    for r in rows_in:
        k = r.get("knife") or {}
        r["knife_score"] = k.get("score")
        # Basic eligibility: need price + not data error
        if r.get("price") is None:
            continue
        if str(r.get("ai_note") or "").upper().startswith("DATA ERROR"):
            r["data_error"] = True
        scored = compute_core_score(r)
        r["core_score"] = scored["core_score"]
        r["core_components"] = scored["components"]
        r["ai_quality"] = None
        r["select_status"] = "CANDIDATE"
        out_rows.append(r)

    out_rows.sort(key=lambda x: (-(x.get("core_score") or 0), x.get("ticker") or ""))
    # Prefer not-yet-approved for the inbox view
    inbox = [r for r in out_rows if not r.get("already_approved")][:limit]
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    set_setting(META_SELECT_AS_OF, as_of)
    set_setting(META_SELECT_BUILT, _utcnow())
    _persist_select_candidates(inbox, as_of=as_of)
    return {"as_of": as_of, "rows": inbox, "all_scored": out_rows, "count": len(inbox)}


def _persist_select_candidates(rows: list[dict[str, Any]], *, as_of: str) -> None:
    init_db()
    now = _utcnow()
    with get_conn() as conn:
        conn.execute("DELETE FROM ai_select_candidates WHERE as_of_date != ?", (as_of,))
        for i, r in enumerate(rows, start=1):
            conn.execute(
                """
                INSERT INTO ai_select_candidates (
                    as_of_date, ticker, rank, core_score, sources_json,
                    sector, industry, price, knife_score, select_status, meta_json, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(as_of_date, ticker) DO UPDATE SET
                    rank=excluded.rank,
                    core_score=excluded.core_score,
                    sources_json=excluded.sources_json,
                    sector=excluded.sector,
                    industry=excluded.industry,
                    price=excluded.price,
                    knife_score=excluded.knife_score,
                    select_status=excluded.select_status,
                    meta_json=excluded.meta_json,
                    updated_at=excluded.updated_at
                """,
                (
                    as_of,
                    r["ticker"],
                    i,
                    r.get("core_score"),
                    ",".join(r.get("sources") or []),
                    r.get("sector"),
                    r.get("industry"),
                    r.get("price"),
                    r.get("knife_score"),
                    "CANDIDATE",
                    None,
                    now,
                ),
            )


def list_select_candidates() -> list[dict[str, Any]]:
    init_db()
    as_of = (get_setting(META_SELECT_AS_OF, "") or "").strip()
    if not as_of:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM ai_select_candidates
            WHERE as_of_date = ? AND select_status = 'CANDIDATE'
            ORDER BY rank ASC
            """,
            (as_of,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_ai_approved_tickers() -> list[str]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT ticker FROM ai_approved
            WHERE status = 'APPROVED'
            ORDER BY approved_at ASC
            """
        ).fetchall()
    return [(r["ticker"] or "").upper() for r in rows]


def list_ai_approved_rows() -> list[dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM ai_approved
            WHERE status = 'APPROVED'
            ORDER BY ticker ASC
            """
        ).fetchall()
    out = [dict(r) for r in rows]
    out.sort(
        key=lambda r: (
            -(r.get("core_score") if r.get("core_score") is not None else -1),
            r.get("ticker") or "",
        )
    )
    return out


def approve_ticker(
    ticker: str,
    *,
    source: str = "MANUAL",
    core_score: int | None = None,
    ai_quality: str | None = None,
    ai_reason: str | None = None,
    price: float | None = None,
) -> dict[str, Any]:
    t = (ticker or "").strip().upper()
    if not validate_ticker_token(t):
        raise ValueError("invalid ticker")
    init_db()
    now = _utcnow()
    day = now[:10]
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT ticker, sources_json FROM ai_approved WHERE ticker = ?", (t,)
        ).fetchone()
        srcs = set()
        if existing and existing["sources_json"]:
            srcs.update(s for s in str(existing["sources_json"]).split(",") if s)
        srcs.add((source or "MANUAL").upper())
        # Pull latest select candidate metrics if present
        cand = conn.execute(
            """
            SELECT * FROM ai_select_candidates
            WHERE ticker = ? ORDER BY as_of_date DESC LIMIT 1
            """,
            (t,),
        ).fetchone()
        if cand:
            if core_score is None:
                core_score = cand["core_score"]
            if price is None:
                price = cand["price"]
            if cand["sources_json"]:
                srcs.update(s for s in str(cand["sources_json"]).split(",") if s)
            sector = cand["sector"]
            industry = cand["industry"]
            knife = cand["knife_score"]
        else:
            sector = industry = knife = None
        conn.execute(
            """
            INSERT INTO ai_approved (
                ticker, status, approved_at, approved_price, sources_json,
                core_score, sector, industry, knife_score, ai_quality, ai_reason,
                buy_status, review_flag, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?)
            ON CONFLICT(ticker) DO UPDATE SET
                status='APPROVED',
                approved_at=COALESCE(ai_approved.approved_at, excluded.approved_at),
                approved_price=COALESCE(excluded.approved_price, ai_approved.approved_price),
                sources_json=excluded.sources_json,
                core_score=COALESCE(excluded.core_score, ai_approved.core_score),
                sector=COALESCE(excluded.sector, ai_approved.sector),
                industry=COALESCE(excluded.industry, ai_approved.industry),
                knife_score=excluded.knife_score,
                ai_quality=COALESCE(excluded.ai_quality, ai_approved.ai_quality),
                ai_reason=COALESCE(excluded.ai_reason, ai_approved.ai_reason),
                review_flag=0,
                updated_at=excluded.updated_at
            """,
            (
                t,
                "APPROVED",
                day,
                price,
                ",".join(sorted(srcs)),
                core_score,
                sector,
                industry,
                knife,
                ai_quality,
                ai_reason,
                "WAIT",
                now,
            ),
        )
        conn.execute(
            """
            UPDATE ai_select_candidates SET select_status='APPROVED', updated_at=?
            WHERE ticker=? AND select_status='CANDIDATE'
            """,
            (now, t),
        )
    return {"ticker": t, "status": "APPROVED", "sources": sorted(srcs)}


def reject_ticker(ticker: str, *, reason: str | None = None) -> dict[str, Any]:
    t = (ticker or "").strip().upper()
    init_db()
    now = _utcnow()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE ai_select_candidates
            SET select_status='REJECTED', meta_json=?, updated_at=?
            WHERE ticker=? AND select_status='CANDIDATE'
            """,
            (reason or "", now, t),
        )
    return {"ticker": t, "status": "REJECTED"}


def remove_ai_approved(ticker: str) -> dict[str, Any]:
    """Owner manual removal from long-term pool (not auto on score drop)."""
    t = (ticker or "").strip().upper()
    init_db()
    now = _utcnow()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE ai_approved SET status='REMOVED', updated_at=?, review_flag=0
            WHERE ticker=?
            """,
            (now, t),
        )
    return {"ticker": t, "status": "REMOVED"}


def mark_ai_approved_review(ticker: str, *, flag: bool = True) -> dict[str, Any]:
    t = (ticker or "").strip().upper()
    init_db()
    with get_conn() as conn:
        conn.execute(
            "UPDATE ai_approved SET review_flag=?, updated_at=? WHERE ticker=?",
            (1 if flag else 0, _utcnow(), t),
        )
    return {"ticker": t, "review_flag": flag}


def membership_flags(tickers: list[str]) -> dict[str, dict[str, bool]]:
    """Per-ticker MY / AI APPROVED badges (no duplicate stock rows)."""
    mine = set(get_my_watchlist())
    approved = set(list_ai_approved_tickers())
    out = {}
    for t in tickers:
        u = (t or "").upper()
        out[u] = {"in_my_watchlist": u in mine, "ai_approved": u in approved}
    return out
