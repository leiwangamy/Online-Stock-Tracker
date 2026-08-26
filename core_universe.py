"""
CORE UNIVERSE FILTER — deterministic long-term stock qualification.

RAW MARKET DATA → NUMERIC FILTERS → QUALIFIED CORE UNIVERSE

Same data + same rules → same result.
No LLM / subjective ranking. No short-term timing (Rising / Knife / SMA25 / Sector Rotation NOW).

Long-term characteristics determine WHO we watch.
Short-term conditions (AI BUY) determine WHEN we buy.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from db import get_conn, get_setting, init_db, set_setting

META_AS_OF = "core_universe_as_of"
META_BUILT = "core_universe_built_at"
META_FUNNEL = "core_universe_last_funnel"
SETTINGS_KEY = "core_universe_thresholds"

# Defaults for testing — Owner/Admin may tune after seeing real funnel counts.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "min_market_cap": 10_000_000_000.0,  # $10B
    "min_avg_dollar_volume": 50_000_000.0,  # $50M/day (proxy: price × avg_vol_20d)
    "min_avg_daily_move_63d": 1.0,  # percent
    "min_return_126d": 0.0,
    "min_return_252d": 0.0,
    "min_rs_252d_vs_spy": -5.0,
    "min_revenue_growth": 0.0,  # fraction (0.0 = 0%)
    # Industry Path A — established leader
    "min_industry_mcap_percentile": 70.0,
    # Industry Path B — emerging leader (stricter growth / RS)
    "path_b_min_revenue_growth": 0.20,
    "path_b_min_rs_252d": 10.0,
}

FAIL_CODES = (
    "FAIL_MARKET_CAP",
    "FAIL_DOLLAR_VOLUME",
    "FAIL_ACTIVITY",
    "FAIL_126D_RETURN",
    "FAIL_252D_RETURN",
    "FAIL_RELATIVE_STRENGTH",
    "FAIL_REVENUE_GROWTH",
    "FAIL_INDUSTRY_POSITION",
    "FAIL_DATA_QUALITY",
)

_REV_PCT_RE = re.compile(
    r"Revenue Growth YoY:\s*([+-]?\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_thresholds() -> dict[str, float]:
    raw = get_setting(SETTINGS_KEY, None)
    out = dict(DEFAULT_THRESHOLDS)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in out and v is not None:
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    pass
    return out


def set_thresholds(updates: dict[str, Any]) -> dict[str, float]:
    cur = get_thresholds()
    for k, v in (updates or {}).items():
        if k not in DEFAULT_THRESHOLDS:
            continue
        try:
            cur[k] = float(v)
        except (TypeError, ValueError):
            continue
    set_setting(SETTINGS_KEY, cur)
    return cur


def _return_n(closes: list[float], n: int) -> float | None:
    need = n + 1
    if len(closes) < need:
        return None
    base, last = float(closes[-need]), float(closes[-1])
    if base <= 0 or last <= 0:
        return None
    return (last / base - 1.0) * 100.0


def _return_flex(closes: list[float], target_n: int, *, min_frac: float = 0.9) -> tuple[float | None, int | None]:
    """
    Prefer exact target_n trading days; if history is slightly short (common for ~252),
    use the longest available lookback provided it is >= min_frac * target_n.
    Returns (return_pct, lookback_used).
    """
    if not closes or len(closes) < 2:
        return None, None
    max_n = len(closes) - 1
    if max_n >= target_n:
        return _return_n(closes, target_n), target_n
    min_n = int(target_n * min_frac)
    if max_n >= min_n:
        return _return_n(closes, max_n), max_n
    return None, None


def _aligned_pair_return(
    stock: list[float],
    spy: list[float],
    target_n: int,
) -> tuple[float | None, float | None, float | None, int | None]:
    """Same lookback for stock and SPY so RS is meaningful."""
    if not stock or not spy:
        return None, None, None, None
    max_n = min(len(stock) - 1, len(spy) - 1, target_n)
    min_n = int(target_n * 0.9)
    if max_n < min_n:
        return None, None, None, None
    n = max_n if max_n < target_n else target_n
    rs_stock = _return_n(stock, n)
    rs_spy = _return_n(spy, n)
    if rs_stock is None or rs_spy is None:
        return None, None, None, None
    return rs_stock, rs_spy, round(rs_stock - rs_spy, 2), n


def _load_closes(tickers: list[str]) -> dict[str, list[float]]:
    init_db()
    if not tickers:
        return {}
    # Chunk to avoid huge IN clauses
    out: dict[str, list[float]] = {}
    with get_conn() as conn:
        for i in range(0, len(tickers), 400):
            chunk = tickers[i : i + 400]
            ph = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"""
                SELECT ticker, close FROM daily_bars
                WHERE ticker IN ({ph}) AND date >= date('now', '-550 days')
                ORDER BY ticker, date
                """,
                chunk,
            ).fetchall()
            for r in rows:
                t = (r["ticker"] or "").upper()
                try:
                    out.setdefault(t, []).append(float(r["close"]))
                except (TypeError, ValueError):
                    continue
    return out


def _parse_revenue_growth_pct(fund: dict[str, Any] | None) -> float | None:
    """
    Return revenue growth as percent (e.g. 6.9), or None if missing.
    Prefer numeric fund.revenue_growth_yoy (fraction); else parse detail text.
    """
    if not fund or not isinstance(fund, dict):
        return None
    raw = fund.get("revenue_growth_yoy")
    if raw is not None:
        try:
            return float(raw) * 100.0
        except (TypeError, ValueError):
            pass
    detail = fund.get("detail") or ""
    m = _REV_PCT_RE.search(str(detail))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _industry_mcap_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Within each industry, rank by market_cap among names that have mcap."""
    by_ind: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        ind = (r.get("industry") or "").strip() or "_UNKNOWN_"
        mc = r.get("market_cap")
        t = r.get("ticker")
        if not t or mc is None:
            continue
        try:
            mcf = float(mc)
        except (TypeError, ValueError):
            continue
        if mcf <= 0:
            continue
        by_ind.setdefault(ind, []).append((t, mcf))

    out: dict[str, dict[str, Any]] = {}
    for ind, pairs in by_ind.items():
        pairs.sort(key=lambda x: -x[1])
        n = len(pairs)
        for i, (t, _mc) in enumerate(pairs):
            rank = i + 1
            # Percentile: 100 = largest. rank 1 of 10 → 100; rank 10 of 10 → 10
            pctile = 100.0 * (n - rank + 1) / n if n else None
            out[t] = {
                "industry_market_cap_rank": rank,
                "industry_market_cap_n": n,
                "industry_market_cap_percentile": round(pctile, 1) if pctile is not None else None,
            }
    return out


def build_metrics_rows(*, group: str | None = None) -> list[dict[str, Any]]:
    """
    Assemble long-term numeric metrics for the raw universe from existing caches.
    Does not call Yahoo. Missing fields stay None (not zero).
    """
    from db import get_dashboard_by_tickers, list_universe
    from market_data import get_fund_cached_only

    uni = list_universe(group=group)
    tickers = [(r.get("ticker") or "").upper() for r in uni if r.get("ticker")]
    tickers = [t for t in tickers if t]
    dash = get_dashboard_by_tickers(tickers) if tickers else {}
    closes = _load_closes(tickers + ["SPY"])
    spy = closes.get("SPY") or []
    fund_map = get_fund_cached_only(tickers) if tickers else {}

    rows: list[dict[str, Any]] = []
    for u in uni:
        t = (u.get("ticker") or "").upper()
        if not t:
            continue
        d = dict(dash.get(t) or {})
        d["ticker"] = t
        d["name"] = d.get("name") or u.get("name")
        d["sector"] = d.get("sector") or u.get("sector")
        d["industry"] = d.get("industry") or u.get("industry")
        cl = closes.get(t) or []
        r126, _, rs126, n126 = _aligned_pair_return(cl, spy, 126)
        r252, _, rs252, n252 = _aligned_pair_return(cl, spy, 252)
        d["return_126d_pct"] = r126
        d["return_252d_pct"] = r252
        d["rs_126d_vs_spy"] = rs126
        d["rs_252d_vs_spy"] = rs252
        d["return_lookback_126"] = n126
        d["return_lookback_252"] = n252
        price = d.get("price")
        avg_vol = d.get("avg_vol_20d")
        # V1 liquidity proxy until 63D volume history is stored on daily_bars.
        if price is not None and avg_vol is not None:
            try:
                d["avg_dollar_volume"] = float(price) * float(avg_vol)
                d["avg_dollar_volume_note"] = "proxy_price_x_avg_vol_20d"
            except (TypeError, ValueError):
                d["avg_dollar_volume"] = None
        else:
            d["avg_dollar_volume"] = None
        fund = fund_map.get(t)
        d["revenue_growth_pct"] = _parse_revenue_growth_pct(fund)
        d["fund"] = fund
        rows.append(d)

    ind_stats = _industry_mcap_stats(rows)
    for r in rows:
        st = ind_stats.get(r["ticker"]) or {}
        r.update(st)
    return rows


def evaluate_row(row: dict[str, Any], thr: dict[str, float]) -> dict[str, Any]:
    """PASS/FAIL for one ticker. Multiple failure reasons allowed."""
    fails: list[str] = []

    mcap = row.get("market_cap")
    adv = row.get("avg_dollar_volume")
    adm = row.get("avg_move_pct")
    r126 = row.get("return_126d_pct")
    r252 = row.get("return_252d_pct")
    rs252 = row.get("rs_252d_vs_spy")
    rev = row.get("revenue_growth_pct")
    ind_pct = row.get("industry_market_cap_percentile")
    price = row.get("price")

    # Data quality — critical fields must be present (not coerced to 0)
    if price is None or mcap is None:
        fails.append("FAIL_DATA_QUALITY")
    if adm is None:
        fails.append("FAIL_DATA_QUALITY")
    if adv is None:
        fails.append("FAIL_DATA_QUALITY")
    if r126 is None or r252 is None:
        fails.append("FAIL_DATA_QUALITY")
    if rs252 is None:
        fails.append("FAIL_DATA_QUALITY")
    if rev is None:
        fails.append("FAIL_DATA_QUALITY")
    # Industry unknown → cannot Path A; Path B may still apply if growth/RS strong
    if (row.get("industry") or "").strip() in ("", "_UNKNOWN_") and ind_pct is None:
        # still allow Path B later; do not auto-fail data quality solely for missing industry
        pass

    if mcap is not None and float(mcap) < thr["min_market_cap"]:
        fails.append("FAIL_MARKET_CAP")
    if adv is not None and float(adv) < thr["min_avg_dollar_volume"]:
        fails.append("FAIL_DOLLAR_VOLUME")
    if adm is not None and float(adm) < thr["min_avg_daily_move_63d"]:
        fails.append("FAIL_ACTIVITY")
    if r126 is not None and float(r126) <= thr["min_return_126d"]:
        fails.append("FAIL_126D_RETURN")
    if r252 is not None and float(r252) <= thr["min_return_252d"]:
        fails.append("FAIL_252D_RETURN")
    if rs252 is not None and float(rs252) < thr["min_rs_252d_vs_spy"]:
        fails.append("FAIL_RELATIVE_STRENGTH")
    if rev is not None and float(rev) <= thr["min_revenue_growth"] * 100.0:
        # thr stores fraction; rev is percent
        fails.append("FAIL_REVENUE_GROWTH")

    # Industry Path A / Path B — only if base long-term filters otherwise ok so far
    base_ok_codes = {
        "FAIL_MARKET_CAP",
        "FAIL_DOLLAR_VOLUME",
        "FAIL_ACTIVITY",
        "FAIL_126D_RETURN",
        "FAIL_252D_RETURN",
        "FAIL_RELATIVE_STRENGTH",
        "FAIL_REVENUE_GROWTH",
        "FAIL_DATA_QUALITY",
    }
    base_fail = any(f in base_ok_codes for f in fails)
    path = None
    if not base_fail:
        path_a = (
            ind_pct is not None
            and float(ind_pct) >= thr["min_industry_mcap_percentile"]
        )
        path_b = (
            rev is not None
            and float(rev) >= thr["path_b_min_revenue_growth"] * 100.0
            and rs252 is not None
            and float(rs252) >= thr["path_b_min_rs_252d"]
            and adv is not None
            and float(adv) >= thr["min_avg_dollar_volume"]
            and adm is not None
            and float(adm) >= thr["min_avg_daily_move_63d"]
        )
        if path_a and path_b:
            path = "ESTABLISHED+EMERGING"
        elif path_a:
            path = "ESTABLISHED"
        elif path_b:
            path = "EMERGING"
        else:
            fails.append("FAIL_INDUSTRY_POSITION")

    # Deduplicate fails while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for f in fails:
        if f not in seen:
            seen.add(f)
            uniq.append(f)

    qualified = len(uniq) == 0
    return {
        "qualified": qualified,
        "failure_reasons": uniq,
        "qualification_path": path if qualified else None,
    }


def _funnel_counts(rows: list[dict[str, Any]], thr: dict[str, float]) -> list[dict[str, Any]]:
    """
    Sequential filter funnel (explainability). Each stage = still passing all prior stages.
    """
    stages: list[tuple[str, Any]] = [
        ("raw", None),
        ("market_cap", lambda r: r.get("market_cap") is not None and float(r["market_cap"]) >= thr["min_market_cap"]),
        (
            "liquidity",
            lambda r: r.get("avg_dollar_volume") is not None
            and float(r["avg_dollar_volume"]) >= thr["min_avg_dollar_volume"],
        ),
        (
            "activity",
            lambda r: r.get("avg_move_pct") is not None
            and float(r["avg_move_pct"]) >= thr["min_avg_daily_move_63d"],
        ),
        (
            "growth",
            lambda r: r.get("revenue_growth_pct") is not None
            and float(r["revenue_growth_pct"]) > thr["min_revenue_growth"] * 100.0,
        ),
        (
            "strength_126_252",
            lambda r: r.get("return_126d_pct") is not None
            and r.get("return_252d_pct") is not None
            and float(r["return_126d_pct"]) > thr["min_return_126d"]
            and float(r["return_252d_pct"]) > thr["min_return_252d"],
        ),
        (
            "relative_strength",
            lambda r: r.get("rs_252d_vs_spy") is not None
            and float(r["rs_252d_vs_spy"]) >= thr["min_rs_252d_vs_spy"],
        ),
        (
            "industry",
            lambda r: (
                (
                    r.get("industry_market_cap_percentile") is not None
                    and float(r["industry_market_cap_percentile"])
                    >= thr["min_industry_mcap_percentile"]
                )
                or (
                    r.get("revenue_growth_pct") is not None
                    and float(r["revenue_growth_pct"])
                    >= thr["path_b_min_revenue_growth"] * 100.0
                    and r.get("rs_252d_vs_spy") is not None
                    and float(r["rs_252d_vs_spy"]) >= thr["path_b_min_rs_252d"]
                )
            ),
        ),
    ]

    remaining = list(rows)
    funnel: list[dict[str, Any]] = []
    for name, pred in stages:
        if pred is not None:
            remaining = [r for r in remaining if pred(r)]
        funnel.append({"stage": name, "count": len(remaining)})
    return funnel


def run_core_universe_filter(
    *,
    group: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """
    Full PASS/FAIL scan of the raw universe.
    Does NOT auto-add/remove from Owner pool — only produces recommendations.
    """
    thr = get_thresholds()
    rows = build_metrics_rows(group=group)
    funnel = _funnel_counts(rows, thr)

    evaluated: list[dict[str, Any]] = []
    qualified_rows: list[dict[str, Any]] = []
    for r in rows:
        ev = evaluate_row(r, thr)
        r2 = dict(r)
        r2.update(ev)
        # Drop bulky fund blob from persisted rows
        r2.pop("fund", None)
        evaluated.append(r2)
        if ev["qualified"]:
            qualified_rows.append(r2)

    qualified_rows.sort(
        key=lambda x: (
            -(x.get("market_cap") or 0),
            x.get("ticker") or "",
        )
    )

    # Diff vs current AI APPROVED / Core Watch pool
    from ai_select import list_ai_approved_tickers

    pool = set(list_ai_approved_tickers())
    qset = {r["ticker"] for r in qualified_rows}
    newly = sorted(qset - pool)
    still = sorted(qset & pool)
    no_longer = sorted(pool - qset)

    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    built = _utcnow()
    result = {
        "as_of": as_of,
        "built_at": built,
        "thresholds": thr,
        "funnel": funnel,
        "raw_count": len(rows),
        "qualified_count": len(qualified_rows),
        "qualified": qualified_rows,
        "all_rows": evaluated,
        "newly_qualified": newly,
        "still_qualified": still,
        "no_longer_qualified": no_longer,
        "liquidity_note": "avg_dollar_volume = price × avg_vol_20d (63D volume history not yet on daily_bars)",
    }

    if persist:
        _persist_run(result)
        set_setting(META_AS_OF, as_of)
        set_setting(META_BUILT, built)
        set_setting(
            META_FUNNEL,
            {"as_of": as_of, "funnel": funnel, "qualified_count": len(qualified_rows)},
        )

    return result


def _persist_run(result: dict[str, Any]) -> None:
    init_db()
    as_of = result["as_of"]
    built = result["built_at"]
    now = _utcnow()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO core_universe_runs (as_of_date, built_at, funnel_json, thresholds_json, qualified_count, updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(as_of_date) DO UPDATE SET
                built_at=excluded.built_at,
                funnel_json=excluded.funnel_json,
                thresholds_json=excluded.thresholds_json,
                qualified_count=excluded.qualified_count,
                updated_at=excluded.updated_at
            """,
            (
                as_of,
                built,
                json.dumps(result.get("funnel") or []),
                json.dumps(result.get("thresholds") or {}),
                int(result.get("qualified_count") or 0),
                now,
            ),
        )
        conn.execute("DELETE FROM core_universe_results WHERE as_of_date = ?", (as_of,))
        for r in result.get("all_rows") or []:
            conn.execute(
                """
                INSERT INTO core_universe_results (
                    as_of_date, ticker, name, sector, industry,
                    market_cap, industry_mcap_rank, industry_mcap_percentile,
                    avg_dollar_volume, avg_daily_move_63d, revenue_growth_pct,
                    return_126d, return_252d, rs_126d, rs_252d,
                    qualified, qualification_path, failure_reasons_json, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    as_of,
                    r.get("ticker"),
                    r.get("name"),
                    r.get("sector"),
                    r.get("industry"),
                    r.get("market_cap"),
                    r.get("industry_market_cap_rank"),
                    r.get("industry_market_cap_percentile"),
                    r.get("avg_dollar_volume"),
                    r.get("avg_move_pct"),
                    r.get("revenue_growth_pct"),
                    r.get("return_126d_pct"),
                    r.get("return_252d_pct"),
                    r.get("rs_126d_vs_spy"),
                    r.get("rs_252d_vs_spy"),
                    1 if r.get("qualified") else 0,
                    r.get("qualification_path"),
                    json.dumps(r.get("failure_reasons") or []),
                    now,
                ),
            )


def observation_exclude_tickers() -> set[str]:
    """
    Tickers already covered by parallel observation pools.
    Core Universe Owner view keeps only names NOT in these sets.
    """
    from watchlist_config import get_my_watchlist
    from db import list_universe

    out: set[str] = set()
    for t in get_my_watchlist():
        u = (t or "").strip().upper()
        if u:
            out.add(u)
    for r in list_universe(group="ndx100") or []:
        u = (r.get("ticker") or "").strip().upper()
        if u:
            out.add(u)
    return out


def filter_focus_qualified(
    rows: list[dict[str, Any]],
    *,
    exclude: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Qualified rows minus My Watchlist and Nasdaq-100."""
    ex = exclude if exclude is not None else observation_exclude_tickers()
    out: list[dict[str, Any]] = []
    for r in rows:
        if not r.get("qualified"):
            continue
        t = (r.get("ticker") or "").upper()
        if not t or t in ex:
            continue
        out.append(r)
    return out


def load_latest_run(*, qualified_only: bool = False) -> dict[str, Any] | None:
    init_db()
    as_of = (get_setting(META_AS_OF, "") or "").strip()
    if not as_of:
        return None
    with get_conn() as conn:
        run = conn.execute(
            "SELECT * FROM core_universe_runs WHERE as_of_date = ?", (as_of,)
        ).fetchone()
        sql = "SELECT * FROM core_universe_results WHERE as_of_date = ?"
        if qualified_only:
            sql += " AND qualified = 1"
        sql += " ORDER BY ticker ASC"
        rows = conn.execute(sql, (as_of,)).fetchall()
    out_rows = [dict(r) for r in rows]
    for r in out_rows:
        try:
            r["failure_reasons"] = json.loads(r.get("failure_reasons_json") or "[]")
        except Exception:
            r["failure_reasons"] = []
        r["qualified"] = bool(r.get("qualified"))
    out_rows.sort(
        key=lambda x: (-(x.get("market_cap") or 0), x.get("ticker") or "")
    )
    funnel = []
    thr = {}
    if run:
        try:
            funnel = json.loads(run["funnel_json"] or "[]")
        except Exception:
            funnel = []
        try:
            thr = json.loads(run["thresholds_json"] or "{}")
        except Exception:
            thr = {}
    return {
        "as_of": as_of,
        "built_at": (run["built_at"] if run else get_setting(META_BUILT, "")),
        "funnel": funnel,
        "thresholds": thr or get_thresholds(),
        "qualified_count": sum(1 for r in out_rows if r.get("qualified")),
        "rows": out_rows,
    }


def metric_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Simple distribution summary for Owner tuning (Phase 5 report)."""

    def _stats(vals: list[float]) -> dict[str, float | int | None]:
        if not vals:
            return {"n": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None}
        vals = sorted(vals)
        n = len(vals)

        def pct(p: float) -> float:
            i = int(round((p / 100.0) * (n - 1)))
            return vals[max(0, min(n - 1, i))]

        return {
            "n": n,
            "min": vals[0],
            "p25": pct(25),
            "median": pct(50),
            "p75": pct(75),
            "max": vals[-1],
        }

    def collect(key: str) -> list[float]:
        out: list[float] = []
        for r in rows:
            v = r.get(key)
            if v is None:
                continue
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                pass
        return out

    return {
        "market_cap": _stats(collect("market_cap")),
        "avg_dollar_volume": _stats(collect("avg_dollar_volume")),
        "avg_move_pct": _stats(collect("avg_move_pct")),
        "revenue_growth_pct": _stats(collect("revenue_growth_pct")),
        "return_126d_pct": _stats(collect("return_126d_pct")),
        "return_252d_pct": _stats(collect("return_252d_pct")),
        "rs_252d_vs_spy": _stats(collect("rs_252d_vs_spy")),
        "industry_market_cap_percentile": _stats(collect("industry_market_cap_percentile")),
    }
