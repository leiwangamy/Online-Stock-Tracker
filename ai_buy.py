"""
AI BUY — Stage 2: WHEN to consider a buy (short-term only).

V1 pool (Owner definition):
  Observation = My Watchlist ∪ Nasdaq-100 ∪ AI Approved
  Buy candidates = names currently marked by Watchlist SMA Alert
                   (🟡 WATCH / 🟢 ALERT / 🟢 DEEP)

PRICE = opportunity. BLOCK = permission.
CORE SCORE is informational only — never merged into BUY SCORE.
No auto real orders in V1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from db import (
    build_watchlist_alert,
    get_alert_prices,
    get_conn,
    get_setting,
    init_db,
    list_universe,
    set_setting,
)
from watchlist_config import get_my_watchlist

META_BUY_AS_OF = "ai_buy_as_of"
META_BUY_BUILT = "ai_buy_built_at"

# Watchlist SMA Alert states that mean "marked for buy consideration"
WL_ALERT_BUY_STATES = frozenset({"watch", "alert", "deep"})

# Dist SMA25 anchors for continuous Price Score (more negative → higher score).
# Zone labels still use discrete bands; score interpolates between these points
# so names in the same zone (e.g. all GOOD) still rank high→low by Dist.
_PRICE_SCORE_ANCHORS: list[tuple[float, int]] = [
    (10.0, 0),
    (5.0, 10),
    (0.0, 30),
    (-5.0, 50),
    (-10.0, 65),
    (-15.0, 75),
    (-20.0, 85),
    (-30.0, 95),
    (-40.0, 100),
]


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _zone_from_dist(d: float) -> str:
    if d > 5.0:
        return "HIGH"
    if d > 0.0:
        return "WAIT"
    if d > -5.0:
        return "WATCH"
    if d > -10.0:
        return "GOOD"
    if d > -20.0:
        return "DEEP"
    if d > -30.0:
        return "VERY_DEEP"
    return "EXTREME"


def _price_score_continuous(d: float) -> int:
    """Piecewise-linear Dist → Price Score (0–100)."""
    anchors = _PRICE_SCORE_ANCHORS
    if d >= anchors[0][0]:
        return anchors[0][1]
    if d <= anchors[-1][0]:
        return anchors[-1][1]
    for i in range(len(anchors) - 1):
        d0, s0 = anchors[i]
        d1, s1 = anchors[i + 1]
        # anchors go from high Dist → low Dist
        if d1 <= d <= d0:
            if d0 == d1:
                return s0
            t = (d0 - d) / (d0 - d1)
            return int(round(s0 + t * (s1 - s0)))
    return 50


def price_score_from_dist(dist_pct: float | None) -> tuple[int | None, str | None]:
    """Map Dist SMA25 % → (continuous Price Score 0–100, discrete zone label)."""
    if dist_pct is None:
        return None, None
    d = float(dist_pct)
    return _price_score_continuous(d), _zone_from_dist(d)


def compute_recovery_score(row: dict[str, Any]) -> int:
    """
    Timing confirmation after price enters ALERT/DEEP zones.
    V1 heuristic from available fields (Rising / Knife / short rebound).
    """
    score = 40
    if row.get("in_rising"):
        score += 25
    rs = (row.get("rising") or {}).get("score") if isinstance(row.get("rising"), dict) else row.get("rising_score")
    if rs is not None:
        score += min(20, int(float(rs) / 5))
    knife = row.get("knife_score")
    if knife is not None:
        k = float(knife)
        if k < 25:
            score += 15
        elif k >= 45:
            score -= 25
    reb = row.get("rebound_pct")
    if reb is not None and float(reb) > 0:
        score += min(15, int(float(reb)))
    chg = row.get("change_pct")
    if chg is not None and float(chg) > 0:
        score += 5
    return int(round(_clamp(score)))


def compute_buy_score(*, price_score: int | None, recovery_score: int | None) -> int | None:
    """50% Price Opportunity + 30% Recovery + 20% reserved context (neutral 50)."""
    if price_score is None:
        return None
    rec = 50 if recovery_score is None else int(recovery_score)
    raw = 0.50 * float(price_score) + 0.30 * float(rec) + 0.20 * 50.0
    return int(round(_clamp(raw)))


def eval_blocks(row: dict[str, Any]) -> dict[str, Any]:
    """Hard gates — never soft-subtract into a still-allowed buy."""
    # Prefer validation already attached on the row (DATA QUALITY ≠ trading risk).
    if row.get("data_quality_status") is None:
        try:
            from market_data_validator import attach_data_quality_to_row

            attach_data_quality_to_row(row)
        except Exception:
            try:
                from market_data import is_data_quality_error

                row["data_block"] = bool(is_data_quality_error(row))
            except Exception:
                row.setdefault("data_block", False)

    data_block = bool(row.get("data_block"))
    dist = row.get("dist_pct")
    high_block = dist is not None and float(dist) > 10.0
    knife = row.get("knife_score")
    if knife is None and isinstance(row.get("knife"), dict):
        knife = row["knife"].get("score")
    knife_block = knife is not None and float(knife) >= 45
    news_status = (row.get("news_status") or "PASS").upper()
    news_block = news_status in ("BLOCK", "BLOCKED")
    buy_allowed = not (data_block or high_block or knife_block or news_block)
    reasons = []
    if data_block:
        reasons.append("DATA")
    if high_block:
        reasons.append("HIGH")
    if knife_block:
        reasons.append("KNIFE")
    if news_block:
        reasons.append("NEWS")
    return {
        "high_block": high_block,
        "knife_block": knife_block,
        "news_block": news_block,
        "data_block": data_block,
        "buy_allowed": buy_allowed,
        "block_reasons": reasons,
    }


def derive_buy_status(
    *,
    buy_allowed: bool,
    block_reasons: list[str],
    price_zone: str | None,
    buy_score: int | None,
    recovery_score: int | None,
    review_flag: bool = False,
    is_held: bool = False,
) -> str:
    """
    Primary status. If held → HOLD (see timing_status_without_hold for pre-HOLD label).
    """
    timing = timing_status_without_hold(
        buy_allowed=buy_allowed,
        price_zone=price_zone,
        buy_score=buy_score,
        recovery_score=recovery_score,
        review_flag=review_flag,
    )
    if is_held:
        return "HOLD"
    return timing


def timing_status_without_hold(
    *,
    buy_allowed: bool,
    price_zone: str | None,
    buy_score: int | None,
    recovery_score: int | None,
    review_flag: bool = False,
) -> str:
    """Buy timing status as if the ticker were not already held (READY / …)."""
    if review_flag:
        return "REVIEW"
    if not buy_allowed:
        return "BLOCKED"
    zone = (price_zone or "WAIT").upper()
    rec = recovery_score if recovery_score is not None else 0
    bs = buy_score if buy_score is not None else 0
    if zone in ("GOOD", "DEEP", "VERY_DEEP", "EXTREME", "WATCH", "ALERT") and rec >= 55 and bs >= 55:
        if zone not in ("WATCH", "ALERT") and rec >= 60 and bs >= 60:
            return "READY"
        if zone in ("WATCH", "ALERT") and rec >= 65 and bs >= 62:
            return "READY"
        return "STABILIZING"
    if zone in ("WATCH", "ALERT", "GOOD", "DEEP", "VERY_DEEP", "EXTREME"):
        if rec >= 45:
            return "STABILIZING"
        return "APPROACHING"
    if zone == "HIGH":
        return "WAIT"
    return "WAIT"


def status_emoji(status: str | None) -> str:
    return {
        "BLOCKED": "🔴",
        "WAIT": "🟦",
        "APPROACHING": "🟡",
        "WATCH": "🟡",  # legacy buy_status alias
        "ALERT": "🟡",  # legacy buy_status alias
        "STABILIZING": "🟡",
        "READY": "🟢",
        "HOLD": "💼",
        "REVIEW": "⚠",
    }.get((status or "").upper(), "🟦")


def ndx100_tickers() -> list[str]:
    """Nasdaq-100 observation pool from universe flags."""
    out: list[str] = []
    seen: set[str] = set()
    for r in list_universe(group="ndx100") or []:
        u = (r.get("ticker") or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def buy_observation_tickers() -> list[str]:
    """My Watchlist ∪ Nasdaq-100 ∪ AI Approved (deduped). Pool before Alert filter."""
    from ai_select import list_ai_approved_tickers

    seen: set[str] = set()
    out: list[str] = []
    for t in get_my_watchlist() + ndx100_tickers() + list_ai_approved_tickers():
        u = (t or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def buy_universe_tickers() -> list[str]:
    """Alias: observation pool (My ∪ NDX100 ∪ AI Approved). Alert filter in snapshot."""
    return buy_observation_tickers()


def _source_label(*, in_mine: bool, in_ndx: bool, in_ai: bool = False) -> str:
    parts: list[str] = []
    if in_mine:
        parts.append("MY")
    if in_ai:
        parts.append("AI")
    if in_ndx:
        parts.append("NDX")
    return "+".join(parts) if parts else "—"


def _wl_alert_emoji(state: str | None) -> str:
    st = (state or "").lower()
    if st in ("alert", "deep"):
        return "🟢"
    if st == "watch":
        return "🟡"
    return ""


def build_ai_buy_snapshot(*, persist: bool = True) -> dict[str, Any]:
    """
    AI BUY table: My ∪ NDX100 ∪ AI Approved names currently in SMA Alert zone.

    Watchlist Alert (same rules as Watchlist tabs) gates WHO enters the BUY table.
    Dist SMA25 / Recovery / blocks decide WHEN status (READY / …).
    """
    from ai_select import list_ai_approved_rows, list_ai_approved_tickers, membership_flags
    from db import get_dashboard_by_tickers
    from rising_now import list_rising_now
    from knife_risk import attach_knife_risk, ensure_benchmark_returns
    from rising_score import attach_rising_score

    mine_set = {t.strip().upper() for t in get_my_watchlist() if t}
    ndx_set = set(ndx100_tickers())
    ai_set = {t.strip().upper() for t in list_ai_approved_tickers() if t}
    tickers = buy_observation_tickers()
    pool_count = len(tickers)

    approved_rows = {r["ticker"]: r for r in list_ai_approved_rows()}
    flags = membership_flags(tickers)
    dash = get_dashboard_by_tickers(tickers) if tickers else {}
    alert_map = get_alert_prices(tickers) if tickers else {}
    rising_set = {(r.get("ticker") or "").upper() for r in list_rising_now()}

    # Open paper holdings → HOLD (still only if in alert zone)
    held: set[str] = set()
    try:
        with get_conn() as conn:
            opens = conn.execute(
                "SELECT DISTINCT ticker FROM paper_trades WHERE status='open'"
            ).fetchall()
            held = {(r["ticker"] or "").upper() for r in opens}
    except Exception:
        pass

    rows: list[dict[str, Any]] = []
    for t in tickers:
        d = dict(dash.get(t) or {"ticker": t})
        d["ticker"] = t
        ap = approved_rows.get(t) or {}
        fl = flags.get(t) or {}
        in_mine = t in mine_set or bool(fl.get("in_my_watchlist"))
        in_ndx = t in ndx_set
        in_ai = t in ai_set or bool(fl.get("ai_approved"))
        d["in_my_watchlist"] = in_mine
        d["in_ndx100"] = in_ndx
        d["ai_approved"] = in_ai
        d["core_score"] = ap.get("core_score")
        d["sources"] = [_source_label(in_mine=in_mine, in_ndx=in_ndx, in_ai=in_ai)]
        d["review_flag"] = bool(ap.get("review_flag"))
        d["in_rising"] = t in rising_set
        d["news_status"] = "PASS"  # News Recheck fills later when ALERT+

        bundle = build_watchlist_alert(d.get("price"), d.get("sma"), alert_map.get(t))
        wl_state = ((bundle.get("alert") or {}).get("state") or None)
        d["wl_alert_state"] = wl_state
        d["wl_alert_source"] = bundle.get("alert_source")
        d["wl_active_alert"] = bundle.get("active_alert")
        d["wl_alert_emoji"] = _wl_alert_emoji(wl_state)
        rows.append(d)

    # Gate: only Watchlist-Alert-marked names are buy candidates
    rows = [r for r in rows if (r.get("wl_alert_state") or "").lower() in WL_ALERT_BUY_STATES]
    alert_marked = len(rows)

    if rows:
        try:
            ensure_benchmark_returns(force=False)
            attach_knife_risk(rows, ensure_bench=False)
        except Exception:
            for r in rows:
                r.setdefault("knife", None)
        try:
            attach_rising_score(rows, ensure_bench=False)
        except Exception:
            for r in rows:
                r.setdefault("rising", None)

    out: list[dict[str, Any]] = []
    counts = {
        "READY": 0,
        "STABILIZING": 0,
        "APPROACHING": 0,
        "WAIT": 0,
        "BLOCKED": 0,
        "HOLD": 0,
        "REVIEW": 0,
    }
    for r in rows:
        k = r.get("knife") or {}
        r["knife_score"] = k.get("score")
        # Alias: SMA field is daily SMA25 (SMA25_D) for AI BUY documentation.
        if r.get("sma") is not None:
            r["sma25_d"] = r.get("sma")

        # Validate BEFORE price score / ranking (DATA QUALITY ≠ TRADING QUALITY).
        try:
            from market_data_validator import attach_data_quality_to_row

            attach_data_quality_to_row(r)
        except Exception:
            r.setdefault("data_block", False)
            r.setdefault("data_quality_status", "WARNING")

        blocks = eval_blocks(r)
        r.update(blocks)

        if blocks.get("data_block"):
            # Do not invent attractive Price/Buy scores from bad Dist SMA25.
            r["price_score"] = None
            r["price_zone"] = None
            r["recovery_score"] = None
            r["buy_score"] = None
            timing = "BLOCKED"
            if bool(r.get("review_flag")):
                timing = "REVIEW"
            r["timing_status"] = timing
            status = "HOLD" if r["ticker"] in held else timing
            r["buy_status"] = status
            r["status_emoji"] = status_emoji(status)
            reason_parts = []
            wl = (r.get("wl_alert_state") or "").upper()
            if wl:
                reason_parts.append(f"WL={wl}")
            reason_parts.append("BLOCK:DATA")
            if status == "HOLD":
                reason_parts.append(f"was={timing}")
            dq = r.get("data_quality_reason") or []
            if dq:
                reason_parts.append("DATA:" + "/".join(str(x) for x in dq[:3]))
            r["reason"] = " · ".join(reason_parts)
            counts[status] = counts.get(status, 0) + 1
            out.append(r)
            continue

        ps, zone = price_score_from_dist(r.get("dist_pct"))
        r["price_score"] = ps
        r["price_zone"] = zone
        rec = compute_recovery_score(r)
        r["recovery_score"] = rec
        if blocks["buy_allowed"]:
            bs = compute_buy_score(price_score=ps, recovery_score=rec)
        else:
            bs = None
        r["buy_score"] = bs
        timing = timing_status_without_hold(
            buy_allowed=blocks["buy_allowed"],
            price_zone=zone,
            buy_score=bs,
            recovery_score=rec,
            review_flag=bool(r.get("review_flag")),
        )
        # Final READY gate on underlying timing (even if currently held).
        if timing == "READY":
            try:
                from market_data_validator import validate_buy_data

                final = validate_buy_data(r.get("ticker") or "", r)
                r["data_quality_status"] = final["data_quality_status"]
                r["data_quality_reason"] = final["data_quality_reason"]
                r["buy_data_ok"] = final.get("buy_data_ok")
                if final.get("data_block") or not final.get("buy_data_ok"):
                    timing = "BLOCKED"
                    r["data_block"] = True
                    r["buy_allowed"] = False
                    r["buy_score"] = None
                    br = list(r.get("block_reasons") or [])
                    if "DATA" not in br:
                        br.append("DATA")
                    r["block_reasons"] = br
            except Exception:
                pass
        r["timing_status"] = timing
        status = "HOLD" if r["ticker"] in held else timing
        r["buy_status"] = status
        r["status_emoji"] = status_emoji(status)
        reason_parts = []
        wl = (r.get("wl_alert_state") or "").upper()
        if wl:
            reason_parts.append(f"WL={wl}")
        if not r.get("buy_allowed", blocks["buy_allowed"]):
            reason_parts.append(
                "BLOCK:" + "/".join(r.get("block_reasons") or blocks["block_reasons"])
            )
        if status == "HOLD" and timing:
            reason_parts.append(f"was={timing}")
        if zone:
            reason_parts.append(f"zone={zone}")
        if ps is not None:
            reason_parts.append(f"P={ps}")
        if rec is not None:
            reason_parts.append(f"R={rec}")
        r["reason"] = " · ".join(reason_parts)
        counts[status] = counts.get(status, 0) + 1
        out.append(r)

    # Default view priority: READY → STABILIZING → APPROACHING → others
    order = {
        "READY": 0,
        "STABILIZING": 1,
        "APPROACHING": 2,
        "WATCH": 2,
        "ALERT": 2,
        "REVIEW": 3,
        "WAIT": 4,
        "HOLD": 5,
        "BLOCKED": 6,
    }
    out.sort(
        key=lambda x: (
            # Primary: BUY score high → low (None / blocked sink)
            -(x.get("buy_score") if x.get("buy_score") is not None else -1),
            order.get(x.get("buy_status") or "", 9),
            x.get("ticker") or "",
        )
    )

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if persist:
        _persist_buy_snapshot(out, as_of=as_of)
        set_setting(META_BUY_AS_OF, as_of)
        set_setting(META_BUY_BUILT, _utcnow())

    return {
        "as_of": as_of,
        "built_at": get_setting(META_BUY_BUILT, "") or _utcnow(),
        "universe_count": len(out),
        "pool_count": pool_count,
        "alert_marked_count": alert_marked,
        "counts": counts,
        "rows": out,
        "definition": "my_ndx_ai_approved_sma_alert",
    }


def _persist_buy_snapshot(rows: list[dict[str, Any]], *, as_of: str) -> None:
    init_db()
    now = _utcnow()
    with get_conn() as conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO ai_buy_snapshots (
                    as_of_date, ticker, core_score, buy_score, price_score,
                    recovery_score, dist_pct, price_zone, knife_score,
                    high_block, knife_block, news_block, buy_allowed,
                    buy_status, in_my_watchlist, ai_approved, reason, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(as_of_date, ticker) DO UPDATE SET
                    core_score=excluded.core_score,
                    buy_score=excluded.buy_score,
                    price_score=excluded.price_score,
                    recovery_score=excluded.recovery_score,
                    dist_pct=excluded.dist_pct,
                    price_zone=excluded.price_zone,
                    knife_score=excluded.knife_score,
                    high_block=excluded.high_block,
                    knife_block=excluded.knife_block,
                    news_block=excluded.news_block,
                    buy_allowed=excluded.buy_allowed,
                    buy_status=excluded.buy_status,
                    in_my_watchlist=excluded.in_my_watchlist,
                    ai_approved=excluded.ai_approved,
                    reason=excluded.reason,
                    updated_at=excluded.updated_at
                """,
                (
                    as_of,
                    r["ticker"],
                    r.get("core_score"),
                    r.get("buy_score"),
                    r.get("price_score"),
                    r.get("recovery_score"),
                    r.get("dist_pct"),
                    r.get("price_zone"),
                    r.get("knife_score"),
                    1 if r.get("high_block") else 0,
                    1 if r.get("knife_block") else 0,
                    1 if r.get("news_block") else 0,
                    1 if r.get("buy_allowed") else 0,
                    r.get("buy_status"),
                    1 if r.get("in_my_watchlist") else 0,
                    1 if r.get("ai_approved") else 0,
                    r.get("reason"),
                    now,
                ),
            )


def load_ai_buy_view(*, recompute: bool = True) -> dict[str, Any]:
    if recompute:
        return build_ai_buy_snapshot(persist=True)
    as_of = (get_setting(META_BUY_AS_OF, "") or "").strip()
    if not as_of:
        return build_ai_buy_snapshot(persist=True)
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ai_buy_snapshots WHERE as_of_date=? ORDER BY ticker",
            (as_of,),
        ).fetchall()
    out = [dict(r) for r in rows]
    for r in out:
        st = (r.get("buy_status") or "WAIT").upper()
        if st in ("ALERT", "WATCH"):
            st = "APPROACHING"
            r["buy_status"] = "APPROACHING"
        r["status_emoji"] = status_emoji(st)
        r["high_block"] = bool(r.get("high_block"))
        r["knife_block"] = bool(r.get("knife_block"))
        r["news_block"] = bool(r.get("news_block"))
        r["buy_allowed"] = bool(r.get("buy_allowed"))
        r["in_my_watchlist"] = bool(r.get("in_my_watchlist"))
        r["ai_approved"] = bool(r.get("ai_approved"))
        if (r.get("price_zone") or "").upper() == "ALERT":
            r["price_zone"] = "WATCH"
    counts: dict[str, int] = {}
    for r in out:
        st = r.get("buy_status") or "WAIT"
        counts[st] = counts.get(st, 0) + 1
    order = {
        "READY": 0,
        "STABILIZING": 1,
        "APPROACHING": 2,
        "WATCH": 2,
        "ALERT": 2,
        "REVIEW": 3,
        "WAIT": 4,
        "HOLD": 5,
        "BLOCKED": 6,
    }
    out.sort(
        key=lambda x: (
            # Primary: BUY score high → low (None / blocked sink)
            -(x.get("buy_score") if x.get("buy_score") is not None else -1),
            order.get(x.get("buy_status") or "", 9),
            x.get("ticker") or "",
        )
    )
    return {
        "as_of": as_of,
        "built_at": get_setting(META_BUY_BUILT, "") or "",
        "universe_count": len(out),
        "pool_count": None,
        "alert_marked_count": len(out),
        "counts": counts,
        "rows": out,
        "from_cache": True,
        "definition": "my_ndx_ai_approved_sma_alert",
    }


def suggested_alert_price(sma: float | None, *, zone: str = "WATCH") -> float | None:
    """AUTO level near WATCH band (≈ SMA × 0.975 ≈ −2.5%)."""
    if sma is None or float(sma) <= 0:
        return None
    # Mid of WATCH band (0 to −5%): use −2.5%
    mult = {"WAIT": 1.0, "WATCH": 0.975, "ALERT": 0.975, "GOOD": 0.925, "DEEP": 0.875}.get(
        zone.upper(), 0.975
    )
    return round(float(sma) * mult, 2)
