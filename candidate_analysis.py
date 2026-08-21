"""
Candidate Analysis / 候选分析 — research aggregation layer.

Deduplicates symbols discovered by existing LeiBot signal systems and completes
cached research fields (Financial, News, AI Score, Strong COUNT20, Rising metrics).

Does NOT change AI Score, Strong Monitor, or source-group rules.
"""

from __future__ import annotations

import json
from typing import Any

from db import (
    get_dashboard_by_tickers,
    list_low_63d_pos,
    list_low_target_ratio,
    list_setup,
)
from market_data import (
    _score_news,
    compute_ai_score,
    compute_target_proxy_mos,
    fund_qualifies_for_news,
    get_fund_cached_only,
    get_news_cached_only,
    is_news_skipped,
    make_news_skipped,
)
from multi_signal import active_strong_symbols, build_multi_signal
from rising_now import list_rising_now, rising_metrics_for_tickers
from strong_stocks import strong_status_for_tickers
from watchlist_config import get_my_watchlist, get_trade_candidates


def _pools_label(row: dict[str, Any]) -> str:
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


def _ticker_set(rows: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for r in rows:
        t = (r.get("ticker") or "").strip().upper()
        if t:
            out.add(t)
    return out


def _index_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for r in rows:
        t = (r.get("ticker") or "").strip().upper()
        if t and t not in idx:
            idx[t] = dict(r)
    return idx


def _financial_display(fund: dict[str, Any] | None) -> dict[str, Any]:
    if not fund or fund.get("total_known") in (None, 0):
        return {
            "label": None,
            "ok": None,
            "known": None,
            "sort": None,
            "health": None,
        }
    ok = fund.get("ok")
    known = fund.get("total_known")
    health = fund.get("health") or "unknown"
    emoji = {"good": "🟢", "ok": "🟡", "bad": "🔴"}.get(health, "")
    label = f"{emoji} {ok}/{known}".strip() if ok is not None and known else None
    sort_v = None
    try:
        if known and int(known) > 0 and ok is not None:
            sort_v = float(ok) / float(known)
    except (TypeError, ValueError):
        sort_v = None
    return {
        "label": label,
        "ok": ok,
        "known": known,
        "sort": sort_v,
        "health": health,
    }


def _news_display(news: dict[str, Any] | None) -> dict[str, Any]:
    """News display: SKIPPED / ±5 score / analyzed labels. Never confuse SKIPPED with NEUTRAL."""
    if not news:
        return {"label": None, "score": None, "tone": None, "text": None, "status": None}
    if is_news_skipped(news):
        return {
            "label": "SKIPPED",
            "score": 0,
            "tone": "skipped",
            "status": "SKIPPED",
            "text": news.get("detail") or "News skipped — Financial Score < 60%",
        }
    score = int(_score_news(news))
    status = news.get("status") or news.get("tone")
    text = news.get("label")
    if score > 0:
        label = f"+{score}"
    elif score < 0:
        label = str(score)
    else:
        label = "0"
    return {
        "label": label,
        "score": score,
        "tone": news.get("tone"),
        "status": status,
        "text": text,
    }


def _target_ratio(price: Any, target: Any) -> float | None:
    try:
        p = float(price)
        t = float(target)
    except (TypeError, ValueError):
        return None
    if t <= 0 or p != p or t != t:
        return None
    return round(p / t, 4)


def _signal_tags(
    *,
    in_oversold: bool,
    in_target: bool,
    in_low63: bool,
    in_rising: bool,
    match_count: int | None,
    strong_status: str | None,
    in_strong: bool,
    count20: int | None = None,
    days_remaining: int | None = None,
) -> list[dict[str, str]]:
    """Compact tagged signals for Candidate Analysis (key + label + optional detail)."""
    tags: list[dict[str, str]] = []
    if in_oversold:
        tags.append({"key": "oversold", "label": "Oversold pullback", "detail": ""})
    if in_target:
        tags.append({"key": "target", "label": "Target Ratio < 80%", "detail": ""})
    if in_low63:
        tags.append({"key": "low63", "label": "63D Position < 25%", "detail": ""})
    if in_rising:
        tags.append({"key": "rising", "label": "Rising Now", "detail": ""})
    if match_count is not None and match_count >= 2:
        tags.append(
            {"key": "multi", "label": "Multi-Signal", "detail": f"{match_count}/4"}
        )
    if in_strong:
        if strong_status == "retention":
            detail = f"{days_remaining}d" if days_remaining is not None else ""
            tags.append({"key": "retention", "label": "Strong Retention", "detail": detail})
        else:
            detail = f"{count20}/20" if count20 is not None else ""
            tags.append({"key": "strong", "label": "Strong", "detail": detail})
    return tags


def _signals_label(tags: list[dict[str, str]]) -> str:
    parts = []
    for t in tags:
        lab = t.get("label") or ""
        det = (t.get("detail") or "").strip()
        parts.append(f"{lab} {det}".strip() if det else lab)
    return " · ".join(parts) if parts else "—"


def build_candidate_analysis(
    *,
    fill_ai: bool = True,
) -> dict[str, Any]:
    """
    Aggregate + dedupe signal sources; attach cached research fields.

    Returns {rows, counts, sources}.
    """
    setup = [dict(r) for r in list_setup(-10.0)]
    low_target = [dict(r) for r in list_low_target_ratio(0.8)]
    low_63d = [dict(r) for r in list_low_63d_pos(25.0)]
    rising = list_rising_now()
    multi_rows, multi_summary = build_multi_signal(setup, low_target, low_63d, rising)
    strong_syms = active_strong_symbols()

    oversold_s = _ticker_set(setup)
    target_s = _ticker_set(low_target)
    low63_s = _ticker_set(low_63d)
    rising_s = _ticker_set(rising)
    multi_idx = {
        (r.get("ticker") or "").upper(): int(r.get("match_count") or 0)
        for r in multi_rows
        if r.get("ticker")
    }

    by_ticker: dict[str, dict[str, Any]] = {}
    for src in (setup, low_target, low_63d, rising):
        for t, row in _index_rows(src).items():
            if t not in by_ticker:
                by_ticker[t] = row

    # Strong-only names not already in a screen still belong in Candidate Analysis.
    missing_strong = [s for s in strong_syms if s not in by_ticker]
    if missing_strong:
        dash = get_dashboard_by_tickers(missing_strong)
        for t in missing_strong:
            row = dict(dash.get(t) or {"ticker": t})
            row["ticker"] = t
            by_ticker[t] = row

    tickers = sorted(by_ticker.keys())
    strong_map = strong_status_for_tickers(tickers)
    rising_idx = _index_rows(rising)
    need_metrics = [t for t in tickers if t not in rising_s]
    metrics = rising_metrics_for_tickers(need_metrics) if need_metrics else {}

    fund_map = get_fund_cached_only(tickers)
    mine = set(get_my_watchlist())
    # My Watchlist names always show News (regular holdings); others need Financial≥60%.
    news_tickers = [
        t
        for t in tickers
        if t in mine or fund_qualifies_for_news(fund_map.get(t))
    ]
    news_map = get_news_cached_only(news_tickers) if news_tickers else {}

    trade = set(get_trade_candidates())

    rows_out: list[dict[str, Any]] = []
    for t in tickers:
        base = dict(by_ticker[t])
        base["ticker"] = t
        base["pools"] = _pools_label(base)

        in_oversold = t in oversold_s
        in_target = t in target_s
        in_low63 = t in low63_s
        in_rising = t in rising_s
        match_count = multi_idx.get(t)
        st = strong_map.get(t) or {}
        in_strong = bool(st.get("in_membership"))
        strong_status = st.get("status")

        fund = fund_map.get(t)
        if t in mine or fund_qualifies_for_news(fund):
            news = news_map.get(t)
        else:
            news = make_news_skipped() if fund else None
        fin = _financial_display(fund)
        nw = _news_display(news)

        base["fund"] = fund
        base["news"] = news
        base["financial_label"] = fin["label"]
        base["financial_ok"] = fin["ok"]
        base["financial_known"] = fin["known"]
        base["financial_sort"] = fin["sort"]
        base["news_label"] = nw["label"]
        base["news_score"] = nw["score"]
        base["news_text"] = nw["text"]
        base["news_status"] = nw.get("status")

        mos = compute_target_proxy_mos(base.get("price"), base.get("target_1y"))
        base.update(mos)
        base["target_ratio"] = _target_ratio(base.get("price"), base.get("target_1y"))

        if fill_ai:
            ai = compute_ai_score(base)
            base["ai"] = ai
            base["ai_score"] = ai.get("final")
        else:
            base["ai"] = None
            base["ai_score"] = None

        # Clear heavy payloads from row for template (labels already set).
        base["fund"] = None
        base["news"] = None

        rr = rising_idx.get(t) or {}
        met = metrics.get(t) or {}
        base["up_days_5"] = rr.get("up_days_5", met.get("up_days_5"))
        base["return_5d_pct"] = rr.get("return_5d_pct", met.get("return_5d_pct"))

        base["in_oversold"] = in_oversold
        base["in_target"] = in_target
        base["in_low_63d"] = in_low63
        base["in_rising"] = in_rising
        base["match_count"] = match_count
        base["in_strong"] = in_strong
        base["strong_status"] = strong_status
        base["count20"] = st.get("count20")
        base["count20_label"] = st.get("count20_label") or (
            f"{st.get('count20', 0)}/20" if st.get("count20") is not None else None
        )
        base["last_qualified_date"] = st.get("last_qualified_date")
        base["days_remaining"] = st.get("days_remaining")
        tags = _signal_tags(
            in_oversold=in_oversold,
            in_target=in_target,
            in_low63=in_low63,
            in_rising=in_rising,
            match_count=match_count,
            strong_status=strong_status,
            in_strong=in_strong,
            count20=st.get("count20"),
            days_remaining=st.get("days_remaining"),
        )
        base["signal_tags"] = tags
        base["signals"] = _signals_label(tags)
        base["in_my_watchlist"] = t in mine
        base["is_trade_candidate"] = t in trade

        rows_out.append(base)

    rows_out.sort(
        key=lambda r: (
            -(float(r.get("ai_score")) if r.get("ai_score") is not None else float("-inf")),
            (r.get("range_63d_pos") if r.get("range_63d_pos") is not None else 999),
            r.get("ticker") or "",
        )
    )

    counts = {
        "total": len(rows_out),
        "oversold": sum(1 for r in rows_out if r.get("in_oversold")),
        "low_63d": sum(1 for r in rows_out if r.get("in_low_63d")),
        "rising": sum(1 for r in rows_out if r.get("in_rising")),
        "multi": sum(1 for r in rows_out if (r.get("match_count") or 0) >= 2),
        "strong": sum(1 for r in rows_out if r.get("in_strong")),
        "retention": sum(1 for r in rows_out if r.get("strong_status") == "retention"),
        "fin_6": sum(
            1
            for r in rows_out
            if r.get("financial_ok") is not None
            and r.get("financial_known") == 6
            and r.get("financial_ok") == 6
        ),
        "fin_ge5": sum(
            1
            for r in rows_out
            if r.get("financial_ok") is not None
            and r.get("financial_known")
            and int(r["financial_known"]) > 0
            and (float(r["financial_ok"]) / float(r["financial_known"])) >= (5 / 6)
        ),
        "mine": sum(1 for r in rows_out if r.get("in_my_watchlist")),
        "trade": sum(1 for r in rows_out if r.get("is_trade_candidate")),
    }

    return {
        "rows": rows_out,
        "counts": counts,
        "multi_summary": multi_summary,
        "source_sizes": {
            "oversold": len(setup),
            "target": len(low_target),
            "low_63d": len(low_63d),
            "rising": len(rising),
            "multi": len(multi_rows),
            "strong": len(strong_syms),
        },
    }


def enrich_ai_trading_watchlist_rows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Attach timing/Strong reference fields to AI Trading Watchlist rows.
    Does not change AI Score or candidate selection.
    """
    if not candidates:
        return []
    tickers = [(c.get("ticker") or "").upper() for c in candidates if c.get("ticker")]
    dash = get_dashboard_by_tickers(tickers)
    rising = {r["ticker"]: r for r in list_rising_now() if r.get("ticker")}
    metrics = rising_metrics_for_tickers(tickers)
    strong_map = strong_status_for_tickers(tickers)
    trade = set(get_trade_candidates())
    out = []
    for c in candidates:
        row = dict(c)
        t = (row.get("ticker") or "").upper()
        drow = dash.get(t) or {}
        # Restore research fields from meta_json when present.
        try:
            meta = json.loads(row.get("meta_json") or "{}")
            if isinstance(meta, dict):
                if row.get("range_63d_pos") is None and meta.get("range_63d_pos") is not None:
                    row["range_63d_pos"] = meta.get("range_63d_pos")
        except Exception:
            pass
        if row.get("range_63d_pos") is None:
            row["range_63d_pos"] = drow.get("range_63d_pos")
        if row.get("change_pct") is None:
            row["change_pct"] = drow.get("change_pct")
        if row.get("target_1y") is None:
            row["target_1y"] = drow.get("target_1y")
        rr = rising.get(t) or {}
        met = metrics.get(t) or {}
        st = strong_map.get(t) or {}
        row["in_rising"] = t in rising
        row["up_days_5"] = rr.get("up_days_5", met.get("up_days_5"))
        row["return_5d_pct"] = rr.get("return_5d_pct", met.get("return_5d_pct"))
        row["count20"] = st.get("count20")
        row["count20_label"] = st.get("count20_label")
        row["strong_status"] = st.get("status")
        row["in_strong"] = bool(st.get("in_membership"))
        row["is_trade_candidate"] = t in trade
        out.append(row)
    return out
