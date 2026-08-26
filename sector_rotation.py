"""
Sector Rotation — Research module (V1).

Answers: which GICS sectors are becoming stronger/weaker, and where is
market strength rotating?

Produces research signals only (score / status / rank / changes).
Does NOT place orders, alter Rising/Strong/Knife rules, or auto-add to AI Trading.

Uses:
  - Sector ETFs (XLK/XLF/…) as representative sector price series
  - Existing Rising Now qualification for Rising %
  - Existing Strong membership for Strong %
  - SPY as relative-strength benchmark
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from db import get_conn, get_setting, init_db, set_setting
from knife_risk import MARKET_ETF, sector_etf

META_AS_OF = "sector_rotation_as_of"
META_BUILT_AT = "sector_rotation_built_at"

# Display order = standard 11 GICS sectors (ETF = representative series).
GICS_SECTORS: list[tuple[str, str]] = [
    ("Information Technology", "XLK"),
    ("Financials", "XLF"),
    ("Health Care", "XLV"),
    ("Consumer Discretionary", "XLY"),
    ("Communication Services", "XLC"),
    ("Industrials", "XLI"),
    ("Consumer Staples", "XLP"),
    ("Energy", "XLE"),
    ("Utilities", "XLU"),
    ("Real Estate", "XLRE"),
    ("Materials", "XLB"),
]

ETF_TO_SECTOR: dict[str, str] = {etf: name for name, etf in GICS_SECTORS}
CANONICAL_SECTORS: list[str] = [name for name, _ in GICS_SECTORS]

# Rotation Score weights (sum = 1.0).
W_TREND = 0.30
W_RS = 0.25
W_RISING = 0.20
W_STRONG = 0.15
W_SMA = 0.10

_SMA_PERIOD = 25
_SMA_SLOPE_UP = 0.05  # %/day of SMA ≈ UP
_SMA_SLOPE_DOWN = -0.05


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def normalize_sector_name(raw: str | None) -> str | None:
    """Map free-text sector labels → canonical GICS name via existing ETF map."""
    if not raw:
        return None
    etf = sector_etf(raw)
    if not etf:
        # Direct canonical / alias match
        key = str(raw).strip().lower()
        for name, e in GICS_SECTORS:
            if name.lower() == key:
                return name
        return None
    return ETF_TO_SECTOR.get(etf)


def _return_n_sessions(closes: list[float], n: int) -> float | None:
    """(last / close[-(n+1)]) - 1 in %; needs n+1 closes."""
    need = n + 1
    if len(closes) < need:
        return None
    base = float(closes[-need])
    last = float(closes[-1])
    if base <= 0 or last <= 0:
        return None
    return (last / base - 1.0) * 100.0


def _sma_series(closes: list[float], period: int) -> list[float] | None:
    if len(closes) < period:
        return None
    out: list[float] = []
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        out.append(sum(window) / period)
    return out


def _sma25_trend(closes: list[float]) -> tuple[str, float | None]:
    """
    UP / FLAT / DOWN from recent SMA25 slope (%/day over last ~5 SMA points).
    """
    smas = _sma_series(closes, _SMA_PERIOD)
    if not smas or len(smas) < 3:
        return "FLAT", None
    look = min(5, len(smas) - 1)
    a = float(smas[-(look + 1)])
    b = float(smas[-1])
    if a <= 0:
        return "FLAT", None
    slope_pct_day = ((b / a) - 1.0) * 100.0 / look
    if slope_pct_day >= _SMA_SLOPE_UP:
        return "UP", round(slope_pct_day, 4)
    if slope_pct_day <= _SMA_SLOPE_DOWN:
        return "DOWN", round(slope_pct_day, 4)
    return "FLAT", round(slope_pct_day, 4)


def _norm_return_component(ret_5d: float | None, ret_20d: float | None) -> float:
    """Map 5D/20D returns → 0–100 (blend)."""
    def _one(r: float | None, scale: float) -> float:
        if r is None:
            return 50.0
        # ±scale% → 0..100 centered at 50
        return _clamp(50.0 + (r / scale) * 50.0, 0.0, 100.0)

    a = _one(ret_5d, 8.0)
    b = _one(ret_20d, 15.0)
    return 0.4 * a + 0.6 * b


def _norm_rs(rs: float | None) -> float:
    if rs is None:
        return 50.0
    return _clamp(50.0 + (rs / 10.0) * 50.0, 0.0, 100.0)


def _norm_pct(p: float | None) -> float:
    if p is None:
        return 0.0
    return _clamp(float(p), 0.0, 100.0)


def _norm_sma_trend(trend: str | None) -> float:
    t = (trend or "FLAT").upper()
    if t == "UP":
        return 100.0
    if t == "DOWN":
        return 0.0
    return 50.0


def compute_rotation_score(
    *,
    ret_5d: float | None,
    ret_20d: float | None,
    relative_strength: float | None,
    rising_pct: float | None,
    strong_pct: float | None,
    sma25_trend: str | None,
) -> dict[str, Any]:
    c_trend = _norm_return_component(ret_5d, ret_20d)
    c_rs = _norm_rs(relative_strength)
    c_rising = _norm_pct(rising_pct)
    c_strong = _norm_pct(strong_pct)
    c_sma = _norm_sma_trend(sma25_trend)
    raw = (
        W_TREND * c_trend
        + W_RS * c_rs
        + W_RISING * c_rising
        + W_STRONG * c_strong
        + W_SMA * c_sma
    )
    score = int(round(_clamp(raw, 0.0, 100.0)))
    return {
        "score": score,
        "components": {
            "trend": round(c_trend, 1),
            "relative_strength": round(c_rs, 1),
            "rising_pct": round(c_rising, 1),
            "strong_pct": round(c_strong, 1),
            "sma25_trend": round(c_sma, 1),
        },
    }


def _momentum_label(scores: list[int | None]) -> str:
    """ACCELERATING / STABLE / DECELERATING from oldest→newest scores."""
    vals = [int(s) for s in scores if s is not None]
    if len(vals) < 2:
        return "STABLE"
    delta = vals[-1] - vals[0]
    if delta >= 4:
        return "ACCELERATING"
    if delta <= -4:
        return "DECELERATING"
    return "STABLE"


def derive_status(
    *,
    score: int | None,
    score_change: float | None,
    momentum: str | None,
) -> str:
    """
    Status uses score AND direction (not score alone).
    LEADING / RISING / NEUTRAL / WEAKENING / FALLING
    """
    if score is None:
        return "NEUTRAL"
    sc = float(score)
    ch = 0.0 if score_change is None else float(score_change)
    mom = (momentum or "STABLE").upper()

    if sc < 30 or (sc < 40 and ch <= -4):
        return "FALLING"
    if ch <= -3 and sc >= 40:
        return "WEAKENING"
    if mom == "DECELERATING" and sc >= 65 and ch < 0:
        return "WEAKENING"
    if ch >= 3 and sc >= 40:
        return "RISING"
    if mom == "ACCELERATING" and sc >= 45:
        return "RISING"
    if sc >= 70 and ch >= -2:
        return "LEADING"
    if sc >= 60 and mom != "DECELERATING":
        return "LEADING"
    return "NEUTRAL"


def status_emoji(status: str | None) -> str:
    s = (status or "").upper()
    return {
        "LEADING": "🟢",
        "RISING": "🟢",
        "NEUTRAL": "🟡",
        "WEAKENING": "🟠",
        "FALLING": "🔴",
    }.get(s, "🟡")


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _ensure_etf_closes(tickers: list[str], *, min_bars: int = 40) -> dict[str, list[float]]:
    """Load closes from daily_bars; Yahoo-fill missing sector ETFs / SPY."""
    init_db()
    need = {t.upper() for t in tickers}
    out: dict[str, list[float]] = {}
    if not need:
        return out
    ph = ",".join("?" * len(need))
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT ticker, date, close FROM daily_bars
            WHERE ticker IN ({ph}) AND date >= date('now', '-180 days')
            ORDER BY ticker ASC, date ASC
            """,
            list(need),
        ).fetchall()
    by: dict[str, list[float]] = {}
    for r in rows:
        t = (r["ticker"] or "").upper()
        try:
            by.setdefault(t, []).append(float(r["close"]))
        except (TypeError, ValueError):
            continue
    missing = [t for t in need if len(by.get(t) or []) < min_bars]
    if missing:
        try:
            import yfinance as yf
            from strong_stocks import upsert_daily_bars

            for t in missing:
                try:
                    hist = yf.Ticker(t).history(period="1y", auto_adjust=True)
                    if hist is None or hist.empty or "Close" not in hist.columns:
                        continue
                    closes = hist["Close"].dropna()
                    if closes.empty:
                        continue
                    upsert_daily_bars(t, closes)
                    by[t] = [float(x) for x in closes.tolist()]
                except Exception:
                    continue
        except Exception:
            pass
    for t in need:
        if t in by and by[t]:
            out[t] = by[t]
    return out


def _load_sector_constituents() -> dict[str, list[dict[str, Any]]]:
    """
    Stocks with a known GICS sector from dashboard_cache (+ universe fallback).
    Keyed by canonical sector name.
    """
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT d.ticker, d.name, d.industry,
                   COALESCE(NULLIF(TRIM(d.sector), ''), u.sector) AS sector,
                   d.price, d.change_pct, d.dist_pct, d.range_63d_pos, d.sma, d.trend,
                   d.target_1y, d.ai_note, d.updated_at
            FROM dashboard_cache d
            LEFT JOIN universe u ON u.ticker = d.ticker
            WHERE d.price IS NOT NULL
            """
        ).fetchall()
    by_sector: dict[str, list[dict[str, Any]]] = {s: [] for s in CANONICAL_SECTORS}
    for r in rows:
        canon = normalize_sector_name(r["sector"])
        if not canon:
            continue
        by_sector[canon].append(
            {
                "ticker": (r["ticker"] or "").upper(),
                "name": r["name"] or "",
                "industry": r["industry"] or "",
                "sector": canon,
                "price": r["price"],
                "change_pct": r["change_pct"],
                "dist_pct": r["dist_pct"],
                "range_63d_pos": r["range_63d_pos"],
                "sma": r["sma"],
                "trend": r["trend"],
                "target_1y": r["target_1y"],
                "ai_note": r["ai_note"],
            }
        )
    return by_sector


def _load_stock_closes(tickers: list[str]) -> dict[str, list[float]]:
    """Recent closes from daily_bars for constituent stocks."""
    init_db()
    clean = [t.upper() for t in tickers if t]
    if not clean:
        return {}
    ph = ",".join("?" * len(clean))
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT ticker, date, close FROM daily_bars
            WHERE ticker IN ({ph}) AND date >= date('now', '-120 days')
            ORDER BY ticker ASC, date ASC
            """,
            clean,
        ).fetchall()
    out: dict[str, list[float]] = {}
    for r in rows:
        t = (r["ticker"] or "").upper()
        try:
            out.setdefault(t, []).append(float(r["close"]))
        except (TypeError, ValueError):
            continue
    return out


def _rising_set() -> set[str]:
    from rising_now import list_rising_now

    return {(r.get("ticker") or "").upper() for r in list_rising_now() if r.get("ticker")}


def _strong_set() -> set[str]:
    from strong_stocks import list_active_strong_watchlist

    try:
        wl = list_active_strong_watchlist() or {}
        rows = wl.get("rows") or []
        return {(r.get("symbol") or r.get("ticker") or "").upper() for r in rows if r.get("symbol") or r.get("ticker")}
    except Exception:
        return set()


def _prev_snapshots(as_of: str) -> dict[str, dict[str, Any]]:
    """Most recent history row per sector with date < as_of."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT h.*
            FROM sector_rotation_history h
            INNER JOIN (
                SELECT sector, MAX(as_of_date) AS d
                FROM sector_rotation_history
                WHERE as_of_date < ?
                GROUP BY sector
            ) p ON h.sector = p.sector AND h.as_of_date = p.d
            """,
            (as_of,),
        ).fetchall()
    return {r["sector"]: dict(r) for r in rows}


def _score_history(sector: str, *, limit: int = 8) -> list[dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT as_of_date, rank, rotation_score, status, score_change
            FROM sector_rotation_history
            WHERE sector = ?
            ORDER BY as_of_date DESC
            LIMIT ?
            """,
            (sector, limit),
        ).fetchall()
    out = [dict(r) for r in rows]
    out.reverse()
    return out


def save_sector_rotation_snapshot(rows: list[dict[str, Any]], *, as_of: str) -> int:
    """Insert/replace today's snapshot only (one row per sector per date)."""
    init_db()
    now = _utcnow_iso()
    n = 0
    with get_conn() as conn:
        for r in rows:
            conn.execute(
                """
                INSERT INTO sector_rotation_history (
                    as_of_date, sector, etf, rank, previous_rank, rank_change,
                    rotation_score, previous_score, score_change, momentum,
                    return_5d, return_20d, relative_strength,
                    rising_pct, strong_pct, n_stocks, n_rising, n_strong,
                    sma25_trend, sma25_slope, status, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(as_of_date, sector) DO UPDATE SET
                    etf=excluded.etf,
                    rank=excluded.rank,
                    previous_rank=excluded.previous_rank,
                    rank_change=excluded.rank_change,
                    rotation_score=excluded.rotation_score,
                    previous_score=excluded.previous_score,
                    score_change=excluded.score_change,
                    momentum=excluded.momentum,
                    return_5d=excluded.return_5d,
                    return_20d=excluded.return_20d,
                    relative_strength=excluded.relative_strength,
                    rising_pct=excluded.rising_pct,
                    strong_pct=excluded.strong_pct,
                    n_stocks=excluded.n_stocks,
                    n_rising=excluded.n_rising,
                    n_strong=excluded.n_strong,
                    sma25_trend=excluded.sma25_trend,
                    sma25_slope=excluded.sma25_slope,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (
                    as_of,
                    r["sector"],
                    r.get("etf"),
                    r.get("rank"),
                    r.get("previous_rank"),
                    r.get("rank_change"),
                    r.get("rotation_score"),
                    r.get("previous_score"),
                    r.get("score_change"),
                    r.get("momentum"),
                    r.get("return_5d"),
                    r.get("return_20d"),
                    r.get("relative_strength"),
                    r.get("rising_pct"),
                    r.get("strong_pct"),
                    r.get("n_stocks"),
                    r.get("n_rising"),
                    r.get("n_strong"),
                    r.get("sma25_trend"),
                    r.get("sma25_slope"),
                    r.get("status"),
                    now,
                ),
            )
            n += 1
    set_setting(META_AS_OF, as_of)
    set_setting(META_BUILT_AT, now)
    return n


def build_sector_rotation(*, persist: bool = True, force_etf: bool = False) -> dict[str, Any]:
    """
    Compute full Sector Rotation table + summary chips.
    When persist=True, writes today's history snapshot (does not delete older days).
    """
    etfs = [etf for _, etf in GICS_SECTORS] + [MARKET_ETF]
    closes_map = _ensure_etf_closes(etfs, min_bars=30)
    spy = closes_map.get(MARKET_ETF) or []
    spy_5d = _return_n_sessions(spy, 5)
    spy_20d = _return_n_sessions(spy, 20)

    rising = _rising_set()
    strong = _strong_set()
    constituents = _load_sector_constituents()

    # as_of = latest SPY bar date if available
    as_of = ""
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM daily_bars WHERE ticker = ?",
            (MARKET_ETF,),
        ).fetchone()
        as_of = (row["d"] if row else None) or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prev = _prev_snapshots(as_of)
    built: list[dict[str, Any]] = []

    for sector, etf in GICS_SECTORS:
        closes = closes_map.get(etf) or []
        ret_5d = _return_n_sessions(closes, 5)
        ret_20d = _return_n_sessions(closes, 20)
        rs = None
        if ret_20d is not None and spy_20d is not None:
            rs = round(ret_20d - spy_20d, 2)
        sma_trend, sma_slope = _sma25_trend(closes)

        members = constituents.get(sector) or []
        n = len(members)
        n_rising = sum(1 for m in members if m["ticker"] in rising)
        n_strong = sum(1 for m in members if m["ticker"] in strong)
        rising_pct = round(100.0 * n_rising / n, 1) if n else None
        strong_pct = round(100.0 * n_strong / n, 1) if n else None

        scored = compute_rotation_score(
            ret_5d=ret_5d,
            ret_20d=ret_20d,
            relative_strength=rs,
            rising_pct=rising_pct,
            strong_pct=strong_pct,
            sma25_trend=sma_trend,
        )
        score = scored["score"]

        hist = _score_history(sector, limit=5)
        hist_scores = [h.get("rotation_score") for h in hist] + [score]
        momentum = _momentum_label(hist_scores)

        p = prev.get(sector) or {}
        prev_score = p.get("rotation_score")
        prev_rank = p.get("rank")
        score_change = None
        if prev_score is not None and score is not None:
            score_change = round(float(score) - float(prev_score), 1)

        status = derive_status(score=score, score_change=score_change, momentum=momentum)

        built.append(
            {
                "sector": sector,
                "etf": etf,
                "return_5d": None if ret_5d is None else round(ret_5d, 2),
                "return_20d": None if ret_20d is None else round(ret_20d, 2),
                "relative_strength": rs,
                "rising_pct": rising_pct,
                "strong_pct": strong_pct,
                "n_stocks": n,
                "n_rising": n_rising,
                "n_strong": n_strong,
                "sma25_trend": sma_trend,
                "sma25_slope": sma_slope,
                "rotation_score": score,
                "components": scored["components"],
                "previous_score": prev_score,
                "score_change": score_change,
                "previous_rank": prev_rank,
                "rank_change": None,  # filled after ranking
                "momentum": momentum,
                "status": status,
                "status_emoji": status_emoji(status),
                "spy_5d": None if spy_5d is None else round(spy_5d, 2),
                "spy_20d": None if spy_20d is None else round(spy_20d, 2),
            }
        )

    # Rank by Rotation Score desc
    built.sort(
        key=lambda r: (
            -(r["rotation_score"] if r["rotation_score"] is not None else -1),
            -(r["relative_strength"] if r["relative_strength"] is not None else -999),
            r["sector"],
        )
    )
    for i, r in enumerate(built, start=1):
        r["rank"] = i
        pr = r.get("previous_rank")
        r["rank_change"] = (int(pr) - i) if pr is not None else None  # + = moved up

    if persist:
        save_sector_rotation_snapshot(built, as_of=as_of)

    summary = _build_summary(built)
    return {
        "as_of": as_of,
        "built_at": get_setting(META_BUILT_AT, "") or _utcnow_iso(),
        "rows": built,
        "summary": summary,
        "spy_5d": None if spy_5d is None else round(spy_5d, 2),
        "spy_20d": None if spy_20d is None else round(spy_20d, 2),
        "rules": (
            "Sector ETFs for returns / RS / SMA25 · Rising % & Strong % from constituents · "
            "Rotation Score = 30% 5D/20D + 25% RS vs SPY + 20% Rising + 15% Strong + 10% SMA25"
        ),
    }


def _build_summary(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    leading: list[str] = []
    rotating_in: list[str] = []
    weakening: list[str] = []
    falling: list[str] = []
    for r in rows:
        st = (r.get("status") or "").upper()
        name = r["sector"]
        if st == "LEADING":
            leading.append(name)
        elif st == "RISING":
            rotating_in.append(name)
        elif st == "WEAKENING":
            weakening.append(name)
        elif st == "FALLING":
            falling.append(name)
    return {
        "leading": leading[:4],
        "rotating_in": rotating_in[:4],
        "weakening": weakening[:4],
        "falling": falling[:4],
    }


def load_latest_sector_rotation(*, recompute_if_missing: bool = True) -> dict[str, Any]:
    """Prefer today's saved snapshot; recompute if empty."""
    init_db()
    as_of = (get_setting(META_AS_OF, "") or "").strip()
    if as_of:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sector_rotation_history
                WHERE as_of_date = ?
                ORDER BY rank ASC, sector ASC
                """,
                (as_of,),
            ).fetchall()
        if rows:
            out_rows = []
            for r in rows:
                d = dict(r)
                d["status_emoji"] = status_emoji(d.get("status"))
                out_rows.append(d)
            return {
                "as_of": as_of,
                "built_at": get_setting(META_BUILT_AT, "") or "",
                "rows": out_rows,
                "summary": _build_summary(out_rows),
                "spy_5d": None,
                "spy_20d": None,
                "rules": (
                    "Sector ETFs for returns / RS / SMA25 · Rising % & Strong % from constituents · "
                    "Rotation Score = 30% 5D/20D + 25% RS vs SPY + 20% Rising + 15% Strong + 10% SMA25"
                ),
                "from_cache": True,
            }
    if recompute_if_missing:
        return build_sector_rotation(persist=True)
    return {
        "as_of": "",
        "built_at": "",
        "rows": [],
        "summary": {"leading": [], "rotating_in": [], "weakening": [], "falling": []},
        "rules": "",
        "from_cache": False,
    }


def classify_line(
    *,
    rising_score: int | None,
    in_rising: bool,
    in_strong: bool,
    range_63d_pos: float | None,
    knife_score: int | None,
    return_5d: float | None,
) -> str:
    """
    Informational LEADER / SECOND LINE / THIRD LINE / WEAK within a sector.
    Not a trade signal.
    """
    if knife_score is not None and knife_score >= 45:
        return "WEAK"
    pos = range_63d_pos if range_63d_pos is not None else 50.0
    rs = rising_score if rising_score is not None else 0
    # Extremely extended + already strong movers → LEADER (informational)
    if (in_strong or rs >= 70) and pos >= 85 and (return_5d or 0) >= 5:
        return "LEADER"
    if in_rising and rs >= 55 and pos < 85:
        return "SECOND LINE"
    if in_strong and pos < 80:
        return "SECOND LINE"
    if in_rising or in_strong or rs >= 40:
        return "THIRD LINE"
    if (return_5d or 0) < -3 or (knife_score or 0) >= 25:
        return "WEAK"
    return "THIRD LINE"


def build_sector_detail(sector: str) -> dict[str, Any]:
    """Sector summary + constituent stocks with Rising / Strong / Knife / line class."""
    canon = normalize_sector_name(sector) or sector
    if canon not in CANONICAL_SECTORS:
        # try exact
        for name in CANONICAL_SECTORS:
            if name.lower() == str(sector).strip().lower():
                canon = name
                break
    overview = load_latest_sector_rotation(recompute_if_missing=True)
    sector_row = next((r for r in overview["rows"] if r["sector"] == canon), None)
    hist = _score_history(canon, limit=12)

    constituents = _load_sector_constituents().get(canon) or []
    tickers = [m["ticker"] for m in constituents]
    rising = _rising_set()
    from strong_stocks import strong_status_for_tickers
    from rising_now import rising_metrics_for_tickers

    strong_map = strong_status_for_tickers(tickers) if tickers else {}
    rising_m = rising_metrics_for_tickers(tickers) if tickers else {}
    closes = _load_stock_closes(tickers) if tickers else {}

    # Attach Rising Score + Knife for display
    rows = []
    for m in constituents:
        t = m["ticker"]
        cl = closes.get(t) or []
        ret_5d = _return_n_sessions(cl, 5)
        ret_20d = _return_n_sessions(cl, 20)
        if ret_5d is None and t in rising_m:
            ret_5d = rising_m[t].get("return_5d_pct")
        st = strong_map.get(t) or {}
        row = dict(m)
        row["return_5d_pct"] = None if ret_5d is None else round(ret_5d, 2)
        row["return_20d_pct"] = None if ret_20d is None else round(ret_20d, 2)
        row["in_rising"] = t in rising
        row["in_strong"] = bool(st.get("in_membership"))
        row["strong_status"] = st.get("status")
        row["count20_label"] = st.get("count20_label")
        rows.append(row)

    try:
        from knife_risk import attach_knife_risk

        attach_knife_risk(rows, ensure_bench=True)
    except Exception:
        for r in rows:
            r.setdefault("knife", None)
    try:
        from rising_score import attach_rising_score

        attach_rising_score(rows, ensure_bench=False)
    except Exception:
        for r in rows:
            r.setdefault("rising", None)

    for r in rows:
        k = r.get("knife") or {}
        rs = r.get("rising") or {}
        r["line"] = classify_line(
            rising_score=rs.get("score"),
            in_rising=bool(r.get("in_rising")),
            in_strong=bool(r.get("in_strong")),
            range_63d_pos=r.get("range_63d_pos"),
            knife_score=k.get("score"),
            return_5d=r.get("return_5d_pct"),
        )

    # Sort: SECOND LINE first (rotation opportunity), then LEADER, THIRD, WEAK
    line_order = {"SECOND LINE": 0, "LEADER": 1, "THIRD LINE": 2, "WEAK": 3}
    rows.sort(
        key=lambda r: (
            line_order.get(r.get("line") or "", 9),
            -(
                (r.get("rising") or {}).get("score")
                if (r.get("rising") or {}).get("score") is not None
                else -1
            ),
            r.get("ticker") or "",
        )
    )

    return {
        "sector": canon,
        "etf": next((e for n, e in GICS_SECTORS if n == canon), None),
        "overview": sector_row,
        "history": hist,
        "stocks": rows,
        "as_of": overview.get("as_of"),
        "n_stocks": len(rows),
    }


def job_sector_rotation_update(*, force: bool = False) -> dict[str, Any]:
    """Daily (or manual) Sector Rotation rebuild + history snapshot."""
    data = build_sector_rotation(persist=True, force_etf=force)
    return {
        "ok": True,
        "as_of": data.get("as_of"),
        "sectors": len(data.get("rows") or []),
        "built_at": data.get("built_at"),
    }
