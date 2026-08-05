"""SQLite storage for LeiBot platform (universe, settings, cache, later watchlist/AI)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "leibot.db"

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
                in_ndx100 INTEGER NOT NULL DEFAULT 0
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
                sma REAL,
                dist_pct REAL,
                rebound_pct REAL,
                sma_period INTEGER,
                earnings_date TEXT,
                ai_note TEXT,
                updated_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_daily_bars_ticker ON daily_bars(ticker);
            """
        )
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
            INSERT INTO universe (ticker, name, industry, sector, in_sp500, in_ndx100)
            VALUES (:ticker, :name, :industry, :sector, :in_sp500, :in_ndx100)
            """,
            rows,
        )
    return len(rows)


def list_universe() -> list[dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ticker, name, industry, sector, in_sp500, in_ndx100 "
            "FROM universe ORDER BY ticker"
        ).fetchall()
    return [dict(r) for r in rows]


def universe_count() -> int:
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM universe").fetchone()
    return int(row["n"] if row else 0)


def save_dashboard_rows(rows: list[dict[str, Any]]) -> None:
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM dashboard_cache")
        if rows:
            conn.executemany(
                """
                INSERT INTO dashboard_cache (
                    ticker, name, industry, sector, price, sma, dist_pct,
                    rebound_pct, sma_period, earnings_date, ai_note, updated_at
                ) VALUES (
                    :ticker, :name, :industry, :sector, :price, :sma, :dist_pct,
                    :rebound_pct, :sma_period, :earnings_date, :ai_note, :updated_at
                )
                """,
                rows,
            )


def list_dashboard(order: str = "dist_asc") -> list[dict[str, Any]]:
    init_db()
    order_sql = {
        "dist_asc": "dist_pct ASC NULLS LAST, ticker",
        "dist_desc": "dist_pct DESC NULLS LAST, ticker",
        "rebound_desc": "rebound_pct DESC NULLS LAST, ticker",
        "ticker": "ticker",
    }.get(order, "dist_pct ASC NULLS LAST, ticker")

    # SQLite older versions may not support NULLS LAST — emulate
    if "NULLS LAST" in order_sql:
        if order == "dist_asc":
            order_sql = "CASE WHEN dist_pct IS NULL THEN 1 ELSE 0 END, dist_pct ASC, ticker"
        elif order == "dist_desc":
            order_sql = "CASE WHEN dist_pct IS NULL THEN 1 ELSE 0 END, dist_pct DESC, ticker"
        elif order == "rebound_desc":
            order_sql = "CASE WHEN rebound_pct IS NULL THEN 1 ELSE 0 END, rebound_pct DESC, ticker"

    with get_conn() as conn:
        rows = conn.execute(f"SELECT * FROM dashboard_cache ORDER BY {order_sql}").fetchall()
    return [dict(r) for r in rows]


def dashboard_meta() -> dict[str, Any]:
    init_db()
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM dashboard_cache").fetchone()["n"]
        updated = conn.execute(
            "SELECT MAX(updated_at) AS t FROM dashboard_cache"
        ).fetchone()["t"]
    return {"count": int(count or 0), "updated_at": updated}
