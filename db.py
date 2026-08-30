"""SQLite storage for LeiBot platform (universe, settings, cache, later watchlist/AI)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
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
        "ndx100": f"{p}in_ndx100 = 1",
        "sp400": f"{p}in_sp400 = 1",
        "sp600": f"{p}in_sp600 = 1",
        "tsx": f"{p}in_tsx = 1",
    }.get(group)

DEFAULT_SETTINGS = {
    "sma_period": 25,
    "sma_presets": [25, 50, 63, 90],
    "rebound_lookback": 25,  # days used for recent low (phase 2 / reserved)
    "data_source": "yahoo",  # later: ibkr
    # Auto-update (Pacific). Prices: weekdays after US close (~16:15 ET).
    "schedule_universe_weekday": "sun",
    "schedule_universe_hour": 10,
    "schedule_universe_minute": 0,
    "schedule_price_hour": 13,
    "schedule_price_minute": 15,
    # AI Paper Trading (simulation only — never IBKR)
    "paper_starting_capital": 2000.0,
    "paper_trading_limit": 1500.0,
    "paper_reserve_cash": 500.0,
    "paper_stop_loss_pct": 5.0,  # percent; stop = entry × (1 - pct/100)
    "paper_take_profit_pct": 10.0,
    # After Stop/Take: auto-buy top unused AI-ranked names (1:1 with exits).
    "paper_auto_replace_on_exit": "1",
    # On AI BUY refresh: auto-open READY/STABILIZING while cash + limit remain.
    "paper_auto_buy_on_refresh": "1",
    # AI Discovery pool visibility (unique events). No Top-N by default.
    "ai_discovery_min_event_score": 70.0,
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

            -- LeiBot ETF Universe (separate from equity Wikipedia universe so
            -- weekly stock refresh cannot wipe ETF membership).
            CREATE TABLE IF NOT EXISTS etf_universe (
                ticker TEXT PRIMARY KEY,
                name TEXT,
                etf_category TEXT NOT NULL,
                etf_subcategory TEXT,
                market TEXT NOT NULL DEFAULT 'US',
                currency TEXT NOT NULL DEFAULT 'USD',
                tags_json TEXT,
                asset_type TEXT NOT NULL DEFAULT 'ETF',
                updated_at TEXT
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
                range_63d_low REAL,
                range_63d_high REAL,
                range_63d_pos REAL,
                target_1y REAL,
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

            -- Cached estimated intrinsic value (valuation layer; independent of AI Score).
            -- Recompute when fundamentals / model update — not on every price tick.
            CREATE TABLE IF NOT EXISTS intrinsic_value (
                ticker TEXT PRIMARY KEY,
                est_value REAL,
                currency TEXT,
                model TEXT,
                as_of TEXT,
                notes TEXT,
                updated_at TEXT,
                wacc REAL,
                terminal_growth REAL,
                confidence TEXT,
                failure_reason TEXT,
                growth_path TEXT,
                financial_period TEXT,
                meta_json TEXT
            );

            -- Manual watchlist Alert Price (human observation only; never auto-overwritten).
            CREATE TABLE IF NOT EXISTS watchlist_alerts (
                ticker TEXT PRIMARY KEY,
                alert_price REAL NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- AI Paper Trading (simulation only; never brokerage orders).
            CREATE TABLE IF NOT EXISTS paper_portfolio (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                starting_capital REAL NOT NULL,
                trading_limit REAL NOT NULL,
                reserve_cash REAL NOT NULL,
                cash REAL NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- Per-strategy paper accounts (independent experimental capital).
            CREATE TABLE IF NOT EXISTS paper_strategy_accounts (
                strategy_id TEXT PRIMARY KEY,
                starting_capital REAL NOT NULL,
                trading_limit REAL NOT NULL,
                reserve_cash REAL NOT NULL,
                cash REAL NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- Daily candidate snapshots (includes BLOCKED / not purchased).
            CREATE TABLE IF NOT EXISTS strategy_candidates (
                as_of_date TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT,
                primary_rank INTEGER,
                primary_metric_name TEXT,
                primary_metric_value REAL,
                trade_status TEXT,
                block_reasons_json TEXT,
                purchased INTEGER NOT NULL DEFAULT 0,
                price REAL,
                dist_pct REAL,
                market_cap REAL,
                cap_category TEXT,
                knife_score REAL,
                recovery_score REAL,
                rising_score REAL,
                buy_score REAL,
                news_status TEXT,
                financial_status TEXT,
                data_quality_status TEXT,
                side TEXT,
                meta_json TEXT,
                updated_at TEXT,
                PRIMARY KEY (as_of_date, strategy_id, ticker)
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_candidates_day
                ON strategy_candidates(strategy_id, as_of_date, primary_rank);

            CREATE TABLE IF NOT EXISTS paper_priority (
                ticker TEXT PRIMARY KEY,
                note TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_candidates (
                as_of_date TEXT NOT NULL,
                rank INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT,
                ai_score REAL,
                mos_t REAL,
                financial_label TEXT,
                news_label TEXT,
                price REAL,
                is_priority INTEGER NOT NULL DEFAULT 0,
                suggested_alloc REAL,
                suggested_shares REAL,
                shares_mode TEXT,
                stop_price REAL,
                take_profit_price REAL,
                meta_json TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (as_of_date, ticker)
            );

            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                name TEXT,
                status TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                shares REAL NOT NULL,
                shares_mode TEXT,
                cost REAL NOT NULL,
                stop_price REAL NOT NULL,
                take_profit_price REAL NOT NULL,
                stop_pct REAL,
                take_profit_pct REAL,
                ai_score_entry REAL,
                mos_t_entry REAL,
                financial_entry TEXT,
                news_entry TEXT,
                range_63d_pos_entry REAL,
                financial_ok_entry INTEGER,
                financial_known_entry INTEGER,
                news_tone_entry TEXT,
                source_at_entry TEXT,
                is_priority INTEGER NOT NULL DEFAULT 0,
                rank_at_entry INTEGER,
                current_price REAL,
                day_high REAL,
                day_low REAL,
                market_value REAL,
                unrealized_pnl REAL,
                unrealized_pnl_pct REAL,
                ai_score_current REAL,
                exit_date TEXT,
                exit_price REAL,
                realized_pnl REAL,
                return_pct REAL,
                exit_reason TEXT,
                exit_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- Daily portfolio equity (one row per trading date; upsert on re-run).
            CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
                as_of_date TEXT PRIMARY KEY,
                cash REAL NOT NULL,
                open_market_value REAL NOT NULL,
                total_equity REAL NOT NULL,
                daily_unrealized_pnl REAL,
                cumulative_realized_pnl REAL,
                trades_closed INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                realized_pnl_day REAL NOT NULL DEFAULT 0,
                daily_return_pct REAL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_level_overrides (
                ticker TEXT PRIMARY KEY,
                manual_stop REAL,
                manual_take REAL,
                updated_at TEXT NOT NULL
            );

            -- News-Driven AI Discovery (event → ticker → pool → optional paper order).
            CREATE TABLE IF NOT EXISTS ai_discovery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_fingerprint TEXT NOT NULL UNIQUE,
                ticker TEXT,
                company_name TEXT,
                event_category TEXT NOT NULL,
                event_summary TEXT NOT NULL,
                source_name TEXT,
                source_url TEXT,
                event_score REAL,
                reliability TEXT,
                discovered_at TEXT NOT NULL,
                meta_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_discovery_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                company_name TEXT,
                status TEXT NOT NULL,
                discovery_date TEXT NOT NULL,
                event_id INTEGER,
                event_category TEXT,
                event_summary TEXT,
                event_score REAL,
                source_name TEXT,
                ai_score REAL,
                financial_label TEXT,
                news_label TEXT,
                knife_score REAL,
                knife_level TEXT,
                price REAL,
                dist_pct REAL,
                target_ratio REAL,
                range_63d_pos REAL,
                trade_eligible INTEGER NOT NULL DEFAULT 0,
                block_reason TEXT,
                paper_trade_id INTEGER,
                analysis_json TEXT,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (ticker, event_id)
            );

            CREATE INDEX IF NOT EXISTS idx_ai_discovery_events_fp
                ON ai_discovery_events(event_fingerprint);
            CREATE INDEX IF NOT EXISTS idx_ai_discovery_cand_status
                ON ai_discovery_candidates(status);
            CREATE INDEX IF NOT EXISTS idx_ai_discovery_cand_ticker
                ON ai_discovery_candidates(ticker);

            CREATE TABLE IF NOT EXISTS ai_discovery_unresolved (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                headline_fingerprint TEXT NOT NULL UNIQUE,
                headline TEXT NOT NULL,
                source_name TEXT,
                source_url TEXT,
                event_category TEXT,
                event_score REAL,
                resolve_notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades(status);
            CREATE INDEX IF NOT EXISTS idx_paper_candidates_date ON paper_candidates(as_of_date);
            CREATE INDEX IF NOT EXISTS idx_paper_equity_date ON paper_equity_snapshots(as_of_date);

            -- Admin Order Requests for Local Trading Agent (V0: no IBKR).
            CREATE TABLE IF NOT EXISTS trading_order_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity REAL NOT NULL,
                expected_price REAL,
                allocation_amount REAL,
                stop_price REAL,
                take_profit_price REAL,
                ai_score_at_request REAL,
                mos_t_at_request REAL,
                source_at_request TEXT,
                mode TEXT NOT NULL DEFAULT 'PAPER',
                status TEXT NOT NULL DEFAULT 'PENDING',
                status_message TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_trading_order_requests_status
                ON trading_order_requests(status);
            CREATE INDEX IF NOT EXISTS idx_trading_order_requests_created
                ON trading_order_requests(created_at);

            CREATE INDEX IF NOT EXISTS idx_daily_bars_ticker ON daily_bars(ticker);

            -- Strong Stock Monitor: normalized daily strength observations (no wide date columns).
            CREATE TABLE IF NOT EXISTS strong_daily (
                as_of_date TEXT NOT NULL,
                symbol TEXT NOT NULL,
                range_63d_pos REAL,
                is_strong INTEGER NOT NULL DEFAULT 0,
                count20 INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (as_of_date, symbol)
            );
            CREATE INDEX IF NOT EXISTS idx_strong_daily_symbol
                ON strong_daily(symbol, as_of_date);
            CREATE INDEX IF NOT EXISTS idx_strong_daily_count
                ON strong_daily(as_of_date, count20);

            -- Active / recently tracked Strong Watchlist membership (replayed from history).
            CREATE TABLE IF NOT EXISTS strong_membership (
                symbol TEXT PRIMARY KEY,
                first_qualified_date TEXT NOT NULL,
                last_qualified_date TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            -- Sector Rotation daily history (one row per sector per trading date).
            CREATE TABLE IF NOT EXISTS sector_rotation_history (
                as_of_date TEXT NOT NULL,
                sector TEXT NOT NULL,
                etf TEXT,
                rank INTEGER,
                previous_rank INTEGER,
                rank_change INTEGER,
                rotation_score INTEGER,
                previous_score INTEGER,
                score_change REAL,
                momentum TEXT,
                return_5d REAL,
                return_20d REAL,
                relative_strength REAL,
                rising_pct REAL,
                strong_pct REAL,
                n_stocks INTEGER,
                n_rising INTEGER,
                n_strong INTEGER,
                sma25_trend TEXT,
                sma25_slope REAL,
                status TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (as_of_date, sector)
            );
            CREATE INDEX IF NOT EXISTS idx_sector_rotation_sector
                ON sector_rotation_history(sector, as_of_date);

            -- AI SELECT / AI BUY architecture (long-term approved + daily buy snapshot).
            CREATE TABLE IF NOT EXISTS ai_approved (
                ticker TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'APPROVED',
                approved_at TEXT,
                approved_price REAL,
                sources_json TEXT,
                core_score INTEGER,
                sector TEXT,
                industry TEXT,
                knife_score INTEGER,
                ai_quality TEXT,
                ai_reason TEXT,
                buy_status TEXT,
                review_flag INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_select_candidates (
                as_of_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                rank INTEGER,
                core_score INTEGER,
                sources_json TEXT,
                sector TEXT,
                industry TEXT,
                price REAL,
                knife_score INTEGER,
                select_status TEXT NOT NULL DEFAULT 'CANDIDATE',
                meta_json TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (as_of_date, ticker)
            );

            CREATE TABLE IF NOT EXISTS ai_buy_snapshots (
                as_of_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                core_score INTEGER,
                buy_score INTEGER,
                price_score INTEGER,
                recovery_score INTEGER,
                dist_pct REAL,
                price_zone TEXT,
                knife_score INTEGER,
                high_block INTEGER NOT NULL DEFAULT 0,
                knife_block INTEGER NOT NULL DEFAULT 0,
                news_block INTEGER NOT NULL DEFAULT 0,
                buy_allowed INTEGER NOT NULL DEFAULT 0,
                buy_status TEXT,
                in_my_watchlist INTEGER NOT NULL DEFAULT 0,
                ai_approved INTEGER NOT NULL DEFAULT 0,
                reason TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (as_of_date, ticker)
            );
            CREATE INDEX IF NOT EXISTS idx_ai_buy_status
                ON ai_buy_snapshots(as_of_date, buy_status);

            -- Core Universe Filter (deterministic PASS/FAIL long-term qualification).
            CREATE TABLE IF NOT EXISTS core_universe_runs (
                as_of_date TEXT PRIMARY KEY,
                built_at TEXT NOT NULL,
                funnel_json TEXT,
                thresholds_json TEXT,
                qualified_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS core_universe_results (
                as_of_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT,
                sector TEXT,
                industry TEXT,
                market_cap REAL,
                industry_mcap_rank INTEGER,
                industry_mcap_percentile REAL,
                avg_dollar_volume REAL,
                avg_daily_move_63d REAL,
                revenue_growth_pct REAL,
                return_126d REAL,
                return_252d REAL,
                rs_126d REAL,
                rs_252d REAL,
                qualified INTEGER NOT NULL DEFAULT 0,
                qualification_path TEXT,
                failure_reasons_json TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (as_of_date, ticker)
            );
            CREATE INDEX IF NOT EXISTS idx_core_universe_qualified
                ON core_universe_results(as_of_date, qualified);
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
        for col, decl in (
            ("change_pct", "REAL"),
            ("trend", "TEXT"),
            ("avg_move_pct", "REAL"),
            ("market_cap", "REAL"),
            ("avg_vol_20d", "REAL"),
            ("rvol", "REAL"),
            ("range_63d_low", "REAL"),
            ("range_63d_high", "REAL"),
            ("range_63d_pos", "REAL"),
            ("target_1y", "REAL"),
            ("asset_type", "TEXT"),
            ("sma63", "REAL"),
            ("dist_sma63_pct", "REAL"),
            ("ret_20d", "REAL"),
            ("ret_63d", "REAL"),
            ("ret_126d", "REAL"),
            ("ret_252d", "REAL"),
            ("avg_dollar_vol", "REAL"),
            ("data_quality_status", "TEXT"),
        ):
            if col not in cache_cols:
                conn.execute(f"ALTER TABLE dashboard_cache ADD COLUMN {col} {decl}")
        # Ensure ETF universe table exists on older DBs that ran init before this migration.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS etf_universe (
                ticker TEXT PRIMARY KEY,
                name TEXT,
                etf_category TEXT NOT NULL,
                etf_subcategory TEXT,
                market TEXT NOT NULL DEFAULT 'US',
                currency TEXT NOT NULL DEFAULT 'USD',
                tags_json TEXT,
                asset_type TEXT NOT NULL DEFAULT 'ETF',
                updated_at TEXT
            )
            """
        )
        # Migrate intrinsic_value for Valuation Engine V1 metadata.
        iv_cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(intrinsic_value)")
        }
        if iv_cols:  # table exists
            for col, decl in (
                ("wacc", "REAL"),
                ("terminal_growth", "REAL"),
                ("confidence", "TEXT"),
                ("failure_reason", "TEXT"),
                ("growth_path", "TEXT"),
                ("financial_period", "TEXT"),
                ("meta_json", "TEXT"),
            ):
                if col not in iv_cols:
                    conn.execute(f"ALTER TABLE intrinsic_value ADD COLUMN {col} {decl}")
        # Migrate paper_trades entry-time research columns (do not overwrite existing values).
        paper_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(paper_trades)")
        }
        if paper_cols:
            for col, decl in (
                ("range_63d_pos_entry", "REAL"),
                ("financial_ok_entry", "INTEGER"),
                ("financial_known_entry", "INTEGER"),
                ("news_tone_entry", "TEXT"),
                ("source_at_entry", "TEXT"),
                ("strategy_id", "TEXT"),
                ("primary_rank_entry", "INTEGER"),
                ("primary_metric_name_entry", "TEXT"),
                ("primary_metric_value_entry", "REAL"),
                ("side", "TEXT"),
                ("peak_price", "REAL"),
                ("trailing_stop", "INTEGER"),
            ):
                if col not in paper_cols:
                    conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} {decl}")
            # Backfill legacy rows → ALERT_BUY (do not wipe history).
            conn.execute(
                """
                UPDATE paper_trades
                SET strategy_id = 'ALERT_BUY'
                WHERE strategy_id IS NULL OR TRIM(strategy_id) = ''
                """
            )
            conn.execute(
                """
                UPDATE paper_trades
                SET side = 'long'
                WHERE side IS NULL OR TRIM(side) = ''
                """
            )
        # Ensure multi-strategy account + candidate tables exist on older DBs.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_strategy_accounts (
                strategy_id TEXT PRIMARY KEY,
                starting_capital REAL NOT NULL,
                trading_limit REAL NOT NULL,
                reserve_cash REAL NOT NULL,
                cash REAL NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_candidates (
                as_of_date TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                name TEXT,
                primary_rank INTEGER,
                primary_metric_name TEXT,
                primary_metric_value REAL,
                trade_status TEXT,
                block_reasons_json TEXT,
                purchased INTEGER NOT NULL DEFAULT 0,
                price REAL,
                dist_pct REAL,
                market_cap REAL,
                cap_category TEXT,
                knife_score REAL,
                recovery_score REAL,
                rising_score REAL,
                buy_score REAL,
                news_status TEXT,
                financial_status TEXT,
                data_quality_status TEXT,
                side TEXT,
                meta_json TEXT,
                updated_at TEXT,
                PRIMARY KEY (as_of_date, strategy_id, ticker)
            )
            """
        )
        # Migrate AI Discovery for underlying-event dedupe + Discovery Alpha snapshots.
        disc_ev_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(ai_discovery_events)")
        }
        if disc_ev_cols:
            for col, decl in (
                ("event_period", "TEXT"),
                ("primary_source", "TEXT"),
                ("supporting_sources_json", "TEXT"),
                ("supporting_count", "INTEGER NOT NULL DEFAULT 0"),
                ("earliest_discovered_at", "TEXT"),
                ("latest_confirmed_at", "TEXT"),
            ):
                if col not in disc_ev_cols:
                    conn.execute(f"ALTER TABLE ai_discovery_events ADD COLUMN {col} {decl}")
        disc_cand_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(ai_discovery_candidates)")
        }
        if disc_cand_cols:
            for col, decl in (
                ("event_period", "TEXT"),
                ("primary_source", "TEXT"),
                ("supporting_count", "INTEGER NOT NULL DEFAULT 0"),
                ("discovery_price", "REAL"),
                ("discovery_ai_score", "REAL"),
                ("discovery_financial_label", "TEXT"),
                ("discovery_knife_score", "REAL"),
                ("discovery_status", "TEXT"),
                ("discovery_block_reason", "TEXT"),
                ("became_trade_candidate", "INTEGER NOT NULL DEFAULT 0"),
                ("ret_1d", "REAL"),
                ("ret_5d", "REAL"),
                ("ret_20d", "REAL"),
                ("ret_63d", "REAL"),
                ("ret_1d_vs_spy", "REAL"),
                ("ret_5d_vs_spy", "REAL"),
                ("ret_20d_vs_spy", "REAL"),
                ("ret_63d_vs_spy", "REAL"),
                ("returns_updated_at", "TEXT"),
                ("sentiment", "TEXT"),
                ("impact_score", "REAL"),
                ("event_date", "TEXT"),
                ("source_tags", "TEXT"),
                ("source_sites", "TEXT"),
                ("is_recent", "INTEGER NOT NULL DEFAULT 1"),
                ("is_news_priority", "INTEGER NOT NULL DEFAULT 0"),
                ("news_priority_at", "TEXT"),
            ):
                if col not in disc_cand_cols:
                    conn.execute(
                        f"ALTER TABLE ai_discovery_candidates ADD COLUMN {col} {decl}"
                    )
        if disc_ev_cols:
            for col, decl in (
                ("sentiment", "TEXT"),
                ("impact_score", "REAL"),
                ("event_date", "TEXT"),
                ("source_tags", "TEXT"),
                ("source_sites", "TEXT"),
                ("is_recent", "INTEGER NOT NULL DEFAULT 1"),
            ):
                if col not in disc_ev_cols:
                    conn.execute(f"ALTER TABLE ai_discovery_events ADD COLUMN {col} {decl}")
        # Unresolved discovery log (ticker confidence insufficient).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_discovery_unresolved (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                headline_fingerprint TEXT NOT NULL UNIQUE,
                headline TEXT NOT NULL,
                source_name TEXT,
                source_url TEXT,
                event_category TEXT,
                event_score REAL,
                resolve_notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        unres_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(ai_discovery_unresolved)")
        }
        if unres_cols:
            for col, decl in (
                ("status", "TEXT NOT NULL DEFAULT 'open'"),
                ("resolved_ticker", "TEXT"),
                ("resolved_at", "TEXT"),
                ("resolved_event_id", "INTEGER"),
                ("last_retry_at", "TEXT"),
            ):
                if col not in unres_cols:
                    conn.execute(
                        f"ALTER TABLE ai_discovery_unresolved ADD COLUMN {col} {decl}"
                    )
        for key, value in DEFAULT_SETTINGS.items():
            existing = conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    (key, json.dumps(value)),
                )


# My Watchlist alerts: DB stores MANUAL overrides only.
# Default Alert = SMA × 0.95 (dynamic); Deep Alert = SMA × 0.90 (info only).
# Never auto-buy from alert status.


def get_alert_prices(tickers: list[str] | None = None) -> dict[str, float]:
    """Return {TICKER: manual_alert}. If tickers given, only those keys (when present)."""
    init_db()
    with get_conn() as conn:
        if tickers:
            clean = [((t or "").strip().upper()) for t in tickers if (t or "").strip()]
            if not clean:
                return {}
            placeholders = ",".join("?" * len(clean))
            rows = conn.execute(
                f"SELECT ticker, alert_price FROM watchlist_alerts WHERE ticker IN ({placeholders})",
                clean,
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ticker, alert_price FROM watchlist_alerts"
            ).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        try:
            out[str(r["ticker"]).upper()] = float(r["alert_price"])
        except (TypeError, ValueError):
            continue
    return out


def upsert_alert_price(ticker: str, alert_price: float | None) -> float | None:
    """
    Set or clear Manual Alert Price for a ticker.
    alert_price=None deletes the row → Active Alert falls back to Default (SMA×0.95).
    Returns stored manual price or None after clear.
    """
    t = (ticker or "").strip().upper()
    if not t:
        raise ValueError("ticker required")
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        if alert_price is None:
            conn.execute("DELETE FROM watchlist_alerts WHERE ticker = ?", (t,))
            return None
        px = float(alert_price)
        if px <= 0 or px != px:  # NaN check
            raise ValueError("alert_price must be a positive number")
        conn.execute(
            "INSERT INTO watchlist_alerts (ticker, alert_price, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "alert_price = excluded.alert_price, updated_at = excluded.updated_at",
            (t, round(px, 2), now),
        )
    return round(px, 2)


def default_alert_from_sma(sma: float | None) -> float | None:
    """Default Alert = SMA × 0.95 (rounded to 2 decimals)."""
    if sma is None:
        return None
    try:
        s = float(sma)
    except (TypeError, ValueError):
        return None
    if s <= 0 or s != s:
        return None
    return round(s * 0.95, 2)


def deep_alert_from_sma(sma: float | None) -> float | None:
    """Deep Alert = SMA × 0.90 (informational)."""
    if sma is None:
        return None
    try:
        s = float(sma)
    except (TypeError, ValueError):
        return None
    if s <= 0 or s != s:
        return None
    return round(s * 0.90, 2)


def build_watchlist_alert(
    price: float | None,
    sma: float | None,
    manual_alert: float | None,
) -> dict[str, Any]:
    """
    Watchlist / AI BUY Dist-SMA25 alert zones (research only — never creates orders).

    Dist_SMA25% = (Price − SMA25) / SMA25 × 100

      > −5%            — none (normal)
      −5% ~ −10%       WATCH   🟡
      −10% ~ −15%      LOW     🟢
      −15% ~ −20%      DEEP    🟠
      ≤ −20%           EXTREME 🔵

    Manual alert price is still stored for Owner “Active Alert” display, but
    the colored state always comes from Dist vs SMA (not from Manual).
    """
    default_alert = default_alert_from_sma(sma)
    deep_alert = deep_alert_from_sma(sma)
    manual: float | None = None
    if manual_alert is not None:
        try:
            m = float(manual_alert)
            if m > 0 and m == m:
                manual = round(m, 2)
        except (TypeError, ValueError):
            manual = None

    if manual is not None:
        active = manual
        source = "manual"
    else:
        active = default_alert
        source = "default" if default_alert is not None else None

    try:
        px = float(price) if price is not None else None
    except (TypeError, ValueError):
        px = None
    try:
        s = float(sma) if sma is not None else None
    except (TypeError, ValueError):
        s = None

    dist_sma_pct = None
    if px is not None and s is not None and s > 0:
        dist_sma_pct = round((px - s) / s * 100.0, 2)

    # Half-open bands: (−10,−5] WATCH · (−15,−10] LOW · (−20,−15] DEEP · (−∞,−20] EXTREME
    state: str | None = None
    if dist_sma_pct is not None:
        if dist_sma_pct <= -20.0:
            state = "extreme"
        elif dist_sma_pct <= -15.0:
            state = "deep"
        elif dist_sma_pct <= -10.0:
            state = "low"
        elif dist_sma_pct <= -5.0:
            state = "watch"

    dist_pct = None
    if px is not None and active is not None and active > 0:
        dist_pct = round((px - active) / active * 100.0, 2)

    return {
        "sma": None if s is None else round(s, 2),
        "default_alert": default_alert,
        "deep_alert": deep_alert,
        "manual_alert": manual,
        "active_alert": active,
        "alert_source": source,
        "dist_sma_pct": dist_sma_pct,
        "alert": {
            "state": state,
            "price": px,
            "sma": None if s is None else round(s, 2),
            "active_alert": active,
            "default_alert": default_alert,
            "deep_alert": deep_alert,
            "manual_alert": manual,
            "alert_source": source,
            "dist_pct": dist_pct,
            "dist_sma_pct": dist_sma_pct,
        },
        # Backward-compatible alias used by older templates/JS
        "alert_price": manual,
    }


def alert_status(
    price: float | None,
    alert_price: float | None,
    *,
    sma: float | None = None,
) -> dict[str, Any] | None:
    """Return alert status dict (or None when no zone). Prefer build_watchlist_alert."""
    bundle = build_watchlist_alert(price, sma, alert_price)
    st = bundle.get("alert") or {}
    if not st.get("state"):
        return None
    return st


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


def upsert_etf_universe(rows: list[dict[str, Any]], *, replace_all: bool = False) -> int:
    """Insert/update ETF metadata. replace_all clears the table first (seed)."""
    init_db()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload: list[dict[str, Any]] = []
    for row in rows:
        t = str(row.get("ticker") or "").strip().upper()
        if not t:
            continue
        tags = row.get("tags")
        if isinstance(tags, str):
            tags_json = tags
        else:
            tags_json = json.dumps(list(tags or []), ensure_ascii=False)
        payload.append(
            {
                "ticker": t,
                "name": row.get("name") or "",
                "etf_category": (row.get("etf_category") or "OTHER").upper(),
                "etf_subcategory": row.get("etf_subcategory") or "",
                "market": (row.get("market") or "US").upper(),
                "currency": (row.get("currency") or "USD").upper(),
                "tags_json": tags_json,
                "asset_type": "ETF",
                "updated_at": now,
            }
        )
    with get_conn() as conn:
        if replace_all:
            conn.execute("DELETE FROM etf_universe")
        if payload:
            conn.executemany(
                """
                INSERT INTO etf_universe (
                    ticker, name, etf_category, etf_subcategory,
                    market, currency, tags_json, asset_type, updated_at
                ) VALUES (
                    :ticker, :name, :etf_category, :etf_subcategory,
                    :market, :currency, :tags_json, :asset_type, :updated_at
                )
                ON CONFLICT(ticker) DO UPDATE SET
                    name = excluded.name,
                    etf_category = excluded.etf_category,
                    etf_subcategory = excluded.etf_subcategory,
                    market = excluded.market,
                    currency = excluded.currency,
                    tags_json = excluded.tags_json,
                    asset_type = 'ETF',
                    updated_at = excluded.updated_at
                """,
                payload,
            )
    return len(payload)


def etf_universe_count(*, market: str | None = None) -> int:
    init_db()
    sql = "SELECT COUNT(*) AS n FROM etf_universe"
    args: list[Any] = []
    if market:
        sql += " WHERE UPPER(market) = ?"
        args.append(market.upper())
    with get_conn() as conn:
        row = conn.execute(sql, args).fetchone()
    return int(row["n"] if row else 0)


def list_etf_universe(
    *,
    category: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    """List ETF metadata. category matches primary category OR tags_json."""
    init_db()
    sql = (
        "SELECT ticker, name, etf_category, etf_subcategory, market, currency, "
        "tags_json, asset_type, updated_at FROM etf_universe"
    )
    where: list[str] = []
    args: list[Any] = []
    cat = (category or "").strip().upper()
    if cat and cat != "ALL":
        where.append("(UPPER(etf_category) = ? OR UPPER(tags_json) LIKE ?)")
        args.extend([cat, f'%"{cat}"%'])
    query = (q or "").strip()
    if query:
        like = f"%{query}%"
        where.append(
            "(UPPER(ticker) LIKE UPPER(?) OR UPPER(name) LIKE UPPER(?) "
            "OR UPPER(etf_category) LIKE UPPER(?) OR UPPER(etf_subcategory) LIKE UPPER(?) "
            "OR UPPER(tags_json) LIKE UPPER(?))"
        )
        args.extend([like, like, like, like, like])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ticker"
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["tags"] = json.loads(d.get("tags_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            d["tags"] = []
        out.append(d)
    return out


def list_etf_dashboard(
    *,
    category: str | None = None,
    q: str | None = None,
    order: str = "dist_asc",
) -> list[dict[str, Any]]:
    """ETF universe joined with dashboard_cache price metrics."""
    meta = list_etf_universe(category=category, q=q)
    if not meta:
        return []
    tickers = [r["ticker"] for r in meta]
    placeholders = ",".join("?" for _ in tickers)
    with get_conn() as conn:
        cache_rows = conn.execute(
            f"SELECT * FROM dashboard_cache WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
    by_t = {str(r["ticker"]).upper(): dict(r) for r in cache_rows}
    merged: list[dict[str, Any]] = []
    for m in meta:
        t = m["ticker"]
        row = dict(by_t.get(t) or {})
        row.update(
            {
                "ticker": t,
                "name": m.get("name") or row.get("name") or "",
                "etf_category": m.get("etf_category"),
                "etf_subcategory": m.get("etf_subcategory"),
                "market": m.get("market"),
                "currency": m.get("currency"),
                "tags": m.get("tags") or [],
                "asset_type": "ETF",
            }
        )
        if m.get("name"):
            row["name"] = m["name"]
        merged.append(row)

    def _key(r: dict[str, Any]):
        if order == "ret_63d_desc":
            v = r.get("ret_63d")
            return (v is None, -(v or 0), r.get("ticker") or "")
        if order == "ret_126d_desc":
            v = r.get("ret_126d")
            return (v is None, -(v or 0), r.get("ticker") or "")
        if order == "ret_252d_desc":
            v = r.get("ret_252d")
            return (v is None, -(v or 0), r.get("ticker") or "")
        if order == "avg_move_desc":
            v = r.get("avg_move_pct")
            return (v is None, -(v or 0), r.get("ticker") or "")
        if order == "dollar_vol_desc":
            v = r.get("avg_dollar_vol")
            return (v is None, -(v or 0), r.get("ticker") or "")
        if order == "ticker":
            return (r.get("ticker") or "",)
        v = r.get("dist_pct")
        return (v is None, v if v is not None else 0, r.get("ticker") or "")

    merged.sort(key=_key)
    return merged


def search_market_tickers(q: str, *, limit: int = 30) -> list[dict[str, Any]]:
    """Search stock universe + ETF universe (GLD / Gold, etc.)."""
    query = (q or "").strip()
    if not query:
        return []
    like = f"%{query}%"
    init_db()
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    with get_conn() as conn:
        for r in conn.execute(
            """
            SELECT ticker, name, etf_category AS category, market, 'ETF' AS asset_type
            FROM etf_universe
            WHERE UPPER(ticker) LIKE UPPER(?)
               OR UPPER(name) LIKE UPPER(?)
               OR UPPER(etf_category) LIKE UPPER(?)
               OR UPPER(etf_subcategory) LIKE UPPER(?)
               OR UPPER(tags_json) LIKE UPPER(?)
            ORDER BY ticker LIMIT ?
            """,
            (like, like, like, like, like, limit),
        ):
            t = str(r["ticker"]).upper()
            if t in seen:
                continue
            seen.add(t)
            hits.append(dict(r))
        for r in conn.execute(
            """
            SELECT ticker, name, industry AS category, NULL AS market, 'STOCK' AS asset_type
            FROM universe
            WHERE UPPER(ticker) LIKE UPPER(?) OR UPPER(name) LIKE UPPER(?)
            ORDER BY ticker LIMIT ?
            """,
            (like, like, limit),
        ):
            t = str(r["ticker"]).upper()
            if t in seen:
                continue
            seen.add(t)
            hits.append(dict(r))
    return hits[:limit]


def save_dashboard_rows(rows: list[dict[str, Any]], replace_all: bool = False) -> None:
    init_db()
    optional_keys = (
        "range_63d_low",
        "range_63d_high",
        "range_63d_pos",
        "target_1y",
        "asset_type",
        "sma63",
        "dist_sma63_pct",
        "ret_20d",
        "ret_63d",
        "ret_126d",
        "ret_252d",
        "avg_dollar_vol",
        "data_quality_status",
    )
    normalized: list[dict[str, Any]] = []
    for row in rows:
        r = dict(row)
        for k in optional_keys:
            r.setdefault(k, None)
        if not r.get("asset_type"):
            r["asset_type"] = "STOCK"
        normalized.append(r)
    with get_conn() as conn:
        if replace_all:
            conn.execute("DELETE FROM dashboard_cache")
        if normalized:
            conn.executemany(
                """
                INSERT INTO dashboard_cache (
                    ticker, name, industry, sector, price, change_pct, avg_move_pct,
                    range_63d_low, range_63d_high, range_63d_pos, target_1y,
                    sma, dist_pct,
                    rebound_pct, trend, market_cap, avg_vol_20d, rvol, sma_period, earnings_date,
                    ai_note, updated_at,
                    asset_type, sma63, dist_sma63_pct,
                    ret_20d, ret_63d, ret_126d, ret_252d,
                    avg_dollar_vol, data_quality_status
                ) VALUES (
                    :ticker, :name, :industry, :sector, :price, :change_pct, :avg_move_pct,
                    :range_63d_low, :range_63d_high, :range_63d_pos, :target_1y,
                    :sma, :dist_pct,
                    :rebound_pct, :trend, :market_cap, :avg_vol_20d, :rvol, :sma_period, :earnings_date,
                    :ai_note, :updated_at,
                    :asset_type, :sma63, :dist_sma63_pct,
                    :ret_20d, :ret_63d, :ret_126d, :ret_252d,
                    :avg_dollar_vol, :data_quality_status
                )
                ON CONFLICT(ticker) DO UPDATE SET
                    name = excluded.name,
                    industry = excluded.industry,
                    sector = excluded.sector,
                    price = excluded.price,
                    change_pct = excluded.change_pct,
                    avg_move_pct = excluded.avg_move_pct,
                    range_63d_low = excluded.range_63d_low,
                    range_63d_high = excluded.range_63d_high,
                    range_63d_pos = excluded.range_63d_pos,
                    target_1y = excluded.target_1y,
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
                    updated_at = excluded.updated_at,
                    asset_type = COALESCE(excluded.asset_type, dashboard_cache.asset_type),
                    sma63 = excluded.sma63,
                    dist_sma63_pct = excluded.dist_sma63_pct,
                    ret_20d = excluded.ret_20d,
                    ret_63d = excluded.ret_63d,
                    ret_126d = excluded.ret_126d,
                    ret_252d = excluded.ret_252d,
                    avg_dollar_vol = excluded.avg_dollar_vol,
                    data_quality_status = excluded.data_quality_status
                """,
                normalized,
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

# Exclude Yahoo scale / SMA corruption flags from research pools + auto trading.
_DATA_OK_SQL = (
    " AND (d.ai_note IS NULL OR UPPER(d.ai_note) NOT LIKE 'DATA ERROR%') "
)


def list_setup(threshold: float = -10.0) -> list[dict[str, Any]]:
    """
    Combined auto Watchlist group (超卖 / 强势回调):
    dist_pct < threshold (default -10%), any trend UP / MIXED / DOWN.
    Ranked UP > MIXED > DOWN, then deepest discount first.
    """
    sql = (
        f"{_POOLS_SELECT} WHERE d.dist_pct IS NOT NULL AND d.dist_pct < ? "
        f"{_DATA_OK_SQL}"
        f"ORDER BY {_TREND_RANK_SQL}, d.dist_pct ASC, d.ticker"
    )
    init_db()
    with get_conn() as conn:
        rows = conn.execute(sql, (threshold,)).fetchall()
    return [dict(r) for r in rows]


def list_low_target_ratio(max_ratio: float = 0.8) -> list[dict[str, Any]]:
    """
    Auto group: Target Ratio = price / target_1y < max_ratio (default 0.8 = 80%).
    Requires price and positive Yahoo 1Y target. Ordered by ratio ascending.
    (63D Position filter removed — all ratio hits included.)
    """
    sql = (
        f"{_POOLS_SELECT} WHERE d.price IS NOT NULL AND d.target_1y IS NOT NULL "
        "AND d.target_1y > 0 AND (d.price / d.target_1y) < ? "
        f"{_DATA_OK_SQL}"
        "ORDER BY (d.price / d.target_1y) ASC, d.ticker"
    )
    init_db()
    with get_conn() as conn:
        rows = conn.execute(sql, (max_ratio,)).fetchall()
    return [dict(r) for r in rows]


def list_low_63d_pos(max_pos: float = 25.0) -> list[dict[str, Any]]:
    """
    Auto group: 63D Position% = range_63d_pos < max_pos (default 25).
    Ordered by position ascending (nearest the 63D low first).
    """
    sql = (
        f"{_POOLS_SELECT} WHERE d.range_63d_pos IS NOT NULL AND d.range_63d_pos < ? "
        f"{_DATA_OK_SQL}"
        "ORDER BY d.range_63d_pos ASC, d.ticker"
    )
    init_db()
    with get_conn() as conn:
        rows = conn.execute(sql, (max_pos,)).fetchall()
    return [dict(r) for r in rows]


def list_oversold(threshold: float = -10.0) -> list[dict[str, Any]]:
    """Alias of list_setup (legacy name)."""
    return list_setup(threshold)


def list_pullback(threshold: float = -10.0) -> list[dict[str, Any]]:
    """Alias of list_setup (legacy name; former UP-only filter removed)."""
    return list_setup(threshold)


def get_intrinsic_values(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """Cached Est.Value rows keyed by ticker (Valuation Engine V1)."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT ticker, est_value, currency, model, as_of, notes, updated_at, "
            f"wacc, terminal_growth, confidence, failure_reason, growth_path, "
            f"financial_period, meta_json "
            f"FROM intrinsic_value WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def upsert_intrinsic_value(
    ticker: str,
    *,
    est_value: float | None,
    currency: str | None = None,
    model: str | None = None,
    as_of: str | None = None,
    notes: str | None = None,
    wacc: float | None = None,
    terminal_growth: float | None = None,
    confidence: str | None = None,
    failure_reason: str | None = None,
    growth_path: str | None = None,
    financial_period: str | None = None,
    meta_json: str | None = None,
) -> None:
    """Store / update Estimated Intrinsic Value (does not touch dashboard price cache)."""
    from datetime import datetime, timezone

    init_db()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO intrinsic_value (
                ticker, est_value, currency, model, as_of, notes, updated_at,
                wacc, terminal_growth, confidence, failure_reason, growth_path,
                financial_period, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                est_value = excluded.est_value,
                currency = excluded.currency,
                model = excluded.model,
                as_of = excluded.as_of,
                notes = excluded.notes,
                updated_at = excluded.updated_at,
                wacc = excluded.wacc,
                terminal_growth = excluded.terminal_growth,
                confidence = excluded.confidence,
                failure_reason = excluded.failure_reason,
                growth_path = excluded.growth_path,
                financial_period = excluded.financial_period,
                meta_json = excluded.meta_json
            """,
            (
                ticker.strip().upper(),
                est_value,
                currency,
                model,
                as_of,
                notes,
                datetime.now(timezone.utc).isoformat(),
                wacc,
                terminal_growth,
                confidence,
                failure_reason,
                growth_path,
                financial_period,
                meta_json,
            ),
        )


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
