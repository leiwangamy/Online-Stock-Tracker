"""SQLite storage for LeiBot platform (universe, settings, cache, later watchlist/AI)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "leibot.db"

# Dashboard index groups (tabs). Each maps to a SQL membership condition.
DASHBOARD_GROUPS = ("core", "sp400", "sp600", "tsx")
DEFAULT_GROUP = "core"


def _group_condition(group: str, alias: str = "") -> str | None:
    """SQL WHERE fragment for an index group. `alias` prefixes column names for joins."""
    p = f"{alias}." if alias else ""
    return {
        "core": f"({p}in_sp500 = 1 OR {p}in_ndx100 = 1)",
        "sp400": f"{p}in_sp400 = 1",
        "sp600": f"{p}in_sp600 = 1",
        "tsx": f"{p}in_tsx = 1",
    }.get(group)

DEFAULT_SETTINGS = {
    "sma_period": 25,
    "sma_presets": [25, 50, 63, 90],
    "rebound_lookback": 25,  # days used for recent low (phase 2 / reserved)
    "data_source": "yahoo",  # later: ibkr
}


def get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Prefer leibot.db; migrate once from early name market.db
    legacy = DATA_DIR / "market.db"
    if legacy.exists() and not DB_PATH.exists():
        try:
            legacy.rename(DB_PATH)
        except OSError:
            # If rename fails (file locked), copy instead
            import shutil

            shutil.copy2(legacy, DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS universe (
                ticker TEXT PRIMARY KEY,
                name TEXT,
                industry TEXT,
                sector TEXT,
                in_sp500 INTEGER NOT NULL DEFAULT 0,
                in_ndx100 INTEGER NOT NULL DEFAULT 0,
                in_sp400 INTEGER NOT NULL DEFAULT 0,
                in_sp600 INTEGER NOT NULL DEFAULT 0,
                in_tsx INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS daily_bars (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                close REAL NOT NULL,
                PRIMARY KEY (ticker, date)
            );

            CREATE TABLE IF NOT EXISTS dashboard_cache (
                ticker TEXT PRIMARY KEY,
                name TEXT,
                industry TEXT,
                sector TEXT,
                price REAL,
                change_pct REAL,
                avg_move_pct REAL,
                sma REAL,
                dist_pct REAL,
                rebound_pct REAL,
                trend TEXT,
                market_cap REAL,
                avg_vol_20d REAL,
                rvol REAL,
                sma_period INTEGER,
                earnings_date TEXT,
                ai_note TEXT,
                updated_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_daily_bars_ticker ON daily_bars(ticker);
            """
        )
        # Migrate older databases that predate the S&P400 / S&P600 columns.
        existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(universe)")}
        for col in ("in_sp400", "in_sp600", "in_tsx"):
            if col not in existing_cols:
                conn.execute(
                    f"ALTER TABLE universe ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
                )
        # Migrate dashboard_cache for daily change % and long-term trend columns.
        cache_cols = {r["name"] for r in conn.execute("PRAGMA table_info(dashboard_cache)")}
        for col, decl in (("change_pct", "REAL"), ("trend", "TEXT"), ("avg_move_pct", "REAL"),
                          ("market_cap", "REAL"), ("avg_vol_20d", "REAL"), ("rvol", "REAL")):
            if col not in cache_cols:
                conn.execute(f"ALTER TABLE dashboard_cache ADD COLUMN {col} {decl}")
        for key, value in DEFAULT_SETTINGS.items():
            existing = conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )


def get_setting(key: str, default: Any = None) -> Any:
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return DEFAULT_SETTINGS.get(key, default)
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def get_all_settings() -> dict[str, Any]:
    init_db()
    out = dict(DEFAULT_SETTINGS)
    with get_conn() as conn:
        for row in conn.execute("SELECT key, value FROM settings"):
            try:
                out[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                out[row["key"]] = row["value"]
    return out


def upsert_universe(rows: list[dict[str, Any]]) -> int:
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM universe")
        conn.executemany(
            """
            INSERT INTO universe (
                ticker, name, industry, sector,
                in_sp500, in_ndx100, in_sp400, in_sp600, in_tsx
            )
            VALUES (
                :ticker, :name, :industry, :sector,
                :in_sp500, :in_ndx100, :in_sp400, :in_sp600, :in_tsx
            )
            """,
            rows,
        )
    return len(rows)


def list_universe(group: str | None = None) -> list[dict[str, Any]]:
    init_db()
    sql = (
        "SELECT ticker, name, industry, sector, "
        "in_sp500, in_ndx100, in_sp400, in_sp600, in_tsx FROM universe"
    )
    cond = _group_condition(group) if group else None
    if cond:
        sql += f" WHERE {cond}"
    sql += " ORDER BY ticker"
    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def universe_count(group: str | None = None) -> int:
    init_db()
    sql = "SELECT COUNT(*) AS n FROM universe"
    cond = _group_condition(group) if group else None
    if cond:
        sql += f" WHERE {cond}"
    with get_conn() as conn:
        row = conn.execute(sql).fetchone()
    return int(row["n"] if row else 0)


def save_dashboard_rows(rows: list[dict[str, Any]], replace_all: bool = False) -> None:
    init_db()
    with get_conn() as conn:
        if replace_all:
            conn.execute("DELETE FROM dashboard_cache")
        if rows:
            # Upsert so a group-scoped refresh only touches its own tickers,
            # leaving the other tabs' cached rows intact.
            conn.executemany(
                """
                INSERT INTO dashboard_cache (
                    ticker, name, industry, sector, price, change_pct, avg_move_pct, sma, dist_pct,
                    rebound_pct, trend, market_cap, avg_vol_20d, rvol, sma_period, earnings_date,
                    ai_note, updated_at
                ) VALUES (
                    :ticker, :name, :industry, :sector, :price, :change_pct, :avg_move_pct, :sma, :dist_pct,
                    :rebound_pct, :trend, :market_cap, :avg_vol_20d, :rvol, :sma_period, :earnings_date,
                    :ai_note, :updated_at
                )
                ON CONFLICT(ticker) DO UPDATE SET
                    name = excluded.name,
                    industry = excluded.industry,
                    sector = excluded.sector,
                    price = excluded.price,
                    change_pct = excluded.change_pct,
                    avg_move_pct = excluded.avg_move_pct,
                    sma = excluded.sma,
                    dist_pct = excluded.dist_pct,
                    rebound_pct = excluded.rebound_pct,
                    trend = excluded.trend,
                    market_cap = excluded.market_cap,
                    avg_vol_20d = excluded.avg_vol_20d,
                    rvol = excluded.rvol,
                    sma_period = excluded.sma_period,
                    earnings_date = excluded.earnings_date,
                    ai_note = excluded.ai_note,
                    updated_at = excluded.updated_at
                """,
                rows,
            )


def list_dashboard(order: str = "dist_asc", group: str | None = None) -> list[dict[str, Any]]:
    init_db()
    # Columns are qualified with the `d` alias so ORDER BY stays unambiguous
    # once we JOIN the universe table for group filtering.
    order_sql = {
        "dist_asc": "CASE WHEN d.dist_pct IS NULL THEN 1 ELSE 0 END, d.dist_pct ASC, d.ticker",
        "dist_desc": "CASE WHEN d.dist_pct IS NULL THEN 1 ELSE 0 END, d.dist_pct DESC, d.ticker",
        "rebound_desc": "CASE WHEN d.rebound_pct IS NULL THEN 1 ELSE 0 END, d.rebound_pct DESC, d.ticker",
        "ticker": "d.ticker",
    }.get(order, "CASE WHEN d.dist_pct IS NULL THEN 1 ELSE 0 END, d.dist_pct ASC, d.ticker")

    sql = "SELECT d.* FROM dashboard_cache d"
    cond = _group_condition(group, alias="u") if group else None
    if cond:
        sql += f" JOIN universe u ON u.ticker = d.ticker WHERE {cond}"
    sql += f" ORDER BY {order_sql}"

    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


# Order UP > MIXED > DOWN (then unknown) for watchlist priority.
_TREND_RANK_SQL = "CASE d.trend WHEN 'UP' THEN 0 WHEN 'MIXED' THEN 1 WHEN 'DOWN' THEN 2 ELSE 3 END"

_POOLS_SELECT = (
    "SELECT d.*, u.in_sp500, u.in_ndx100, u.in_sp400, u.in_sp600, u.in_tsx "
    "FROM dashboard_cache d LEFT JOIN universe u ON u.ticker = d.ticker"
)


def list_oversold(threshold: float = -20.0) -> list[dict[str, Any]]:
    """Auto group ①: distance from mean ≤ threshold, ranked UP > MIXED > DOWN."""
    sql = (
        f"{_POOLS_SELECT} WHERE d.dist_pct IS NOT NULL AND d.dist_pct <= ? "
        f"ORDER BY {_TREND_RANK_SQL}, d.dist_pct ASC, d.ticker"
    )
    init_db()
    with get_conn() as conn:
        rows = conn.execute(sql, (threshold,)).fetchall()
    return [dict(r) for r in rows]


def list_pullback(max_dist: float = 3.0) -> list[dict[str, Any]]:
    """Auto group ②: trend UP and distance from mean < max_dist (pullback to mean)."""
    sql = (
        f"{_POOLS_SELECT} WHERE d.trend = 'UP' AND d.dist_pct IS NOT NULL AND d.dist_pct < ? "
        "ORDER BY d.dist_pct ASC, d.ticker"
    )
    init_db()
    with get_conn() as conn:
        rows = conn.execute(sql, (max_dist,)).fetchall()
    return [dict(r) for r in rows]


def get_dashboard_by_tickers(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """Cached dashboard rows (with pool flags) keyed by ticker, for a specific list."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    sql = f"{_POOLS_SELECT} WHERE d.ticker IN ({placeholders})"
    init_db()
    with get_conn() as conn:
        rows = conn.execute(sql, tickers).fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def get_universe_flags(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """Index-pool membership flags keyed by ticker (for live-fetched names)."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ticker, name, industry, sector, "
            "in_sp500, in_ndx100, in_sp400, in_sp600, in_tsx "
            f"FROM universe WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def dashboard_meta(group: str | None = None) -> dict[str, Any]:
    init_db()
    cond = _group_condition(group, alias="u") if group else None
    join = " JOIN universe u ON u.ticker = d.ticker" if cond else ""
    where = f" WHERE {cond}" if cond else ""
    with get_conn() as conn:
        count = conn.execute(
            f"SELECT COUNT(*) AS n FROM dashboard_cache d{join}{where}"
        ).fetchone()["n"]
        updated = conn.execute(
            f"SELECT MAX(d.updated_at) AS t FROM dashboard_cache d{join}{where}"
        ).fetchone()["t"]
    return {"count": int(count or 0), "updated_at": updated}
