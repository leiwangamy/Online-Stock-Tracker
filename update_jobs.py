"""
Scheduled data refresh jobs for LeiBot / Online-Stock-Tracker.

Defaults:
  - Weekly: rebuild company names / index membership (Wikipedia)
  - Weekday after US close (~13:15 Pacific): refresh Yahoo dashboard cache,
    Watchlist tickers (incl. MANUAL), Paper Trading daily, and Research
    (Strong Monitor + daily_bars for Rising Now / Multi-Signal)

CLI:
  python update_jobs.py --universe
  python update_jobs.py --prices
  python update_jobs.py --watchlist
  python update_jobs.py --all
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# US equities close 16:00 America/New_York; run shortly after so Yahoo bars settle.
PRICE_TZ = ZoneInfo("America/Los_Angeles")
# Default: weekdays 13:15 Pacific ≈ 16:15 Eastern
DEFAULT_PRICE_CRON_HOUR = 13
DEFAULT_PRICE_CRON_MINUTE = 15
# Default: Sunday 10:00 Pacific — company names / index membership
DEFAULT_UNIVERSE_WEEKDAY = "sun"  # mon=0 … sun=6 in APScheduler
DEFAULT_UNIVERSE_HOUR = 10
DEFAULT_UNIVERSE_MINUTE = 0


def _setup_logging() -> logging.Logger:
    log = logging.getLogger("leibot.update")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(LOG_DIR / "update_jobs.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)
    return log


def job_refresh_universe() -> dict:
    """Weekly: Wikipedia → company names + index flags."""
    log = _setup_logging()
    log.info("Starting universe (company names) refresh…")
    from db import init_db
    from universe import refresh_universe

    init_db()
    result = refresh_universe()
    log.info(
        "Universe done: SP500=%s NDX=%s SP400=%s SP600=%s TSX=%s unique=%s",
        result.get("sp500"),
        result.get("ndx100"),
        result.get("sp400"),
        result.get("sp600"),
        result.get("tsx"),
        result.get("unique"),
    )
    return result


def job_refresh_watchlist(*, max_workers: int = 4) -> dict:
    """
    Refresh all current Watchlist tickers (setup ∪ mine),
    including MANUAL names that are not in index universe pools.
    """
    log = _setup_logging()
    from db import get_setting, get_universe_flags, init_db, list_universe, save_dashboard_rows
    from market_data import fetch_metrics_for_ticker
    from watchlist_config import collect_watchlist_tickers, get_my_watchlist

    init_db()
    tickers = collect_watchlist_tickers()
    for t in get_my_watchlist():
        if t not in tickers:
            tickers.append(t)

    sma_period = int(get_setting("sma_period", 25))
    rebound_lookback = int(get_setting("rebound_lookback", sma_period))
    meta_u = {r["ticker"]: r for r in list_universe()}
    flags = get_universe_flags(tickers)

    log.info("Starting Watchlist refresh (%s tickers)…", len(tickers))
    rows: list[dict] = []
    errors = 0

    def one(t: str):
        meta = dict(meta_u.get(t) or flags.get(t) or {})
        return fetch_metrics_for_ticker(
            t, sma_period=sma_period, rebound_lookback=rebound_lookback, meta=meta
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(one, t): t for t in tickers}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                r = fut.result()
            except Exception as exc:
                log.warning("Watchlist %s failed: %s", t, exc)
                errors += 1
                continue
            if r is None:
                errors += 1
            else:
                rows.append(r)

    if rows:
        save_dashboard_rows(rows, replace_all=False)
    result = {"ok": len(rows), "errors": errors, "tickers": len(tickers)}
    log.info(
        "Watchlist done: ok=%s errors=%s tickers=%s",
        result["ok"],
        result["errors"],
        result["tickers"],
    )
    return result


def job_refresh_prices(*, max_workers: int = 4) -> dict:
    """
    Weekday EOD: Yahoo → full dashboard cache, Watchlist (incl. MANUAL),
    Paper Trading daily, then Research (Strong + daily_bars used by Rising Now).
    """
    log = _setup_logging()
    now_pt = datetime.now(PRICE_TZ)
    log.info("Starting dashboard price refresh (PT %s)…", now_pt.strftime("%Y-%m-%d %H:%M %Z"))
    from db import init_db, universe_count
    from market_data import refresh_dashboard_cache
    from universe import ensure_universe

    init_db()
    ensure_universe()
    if universe_count() == 0:
        log.warning("Universe empty after ensure; running Wikipedia refresh first")
        job_refresh_universe()

    # Slightly slower workers to reduce Yahoo rate-limit failures
    result = refresh_dashboard_cache(group=None, max_workers=max_workers)
    log.info(
        "Prices done: ok=%s errors=%s universe=%s SMA=%s",
        result.get("ok"),
        result.get("errors"),
        result.get("universe"),
        result.get("sma_period"),
    )
    # Always refresh Watchlist after pool prices (covers MANUAL ETFs / mine list)
    time.sleep(1.0)
    wl = job_refresh_watchlist(max_workers=max_workers)
    result["watchlist_ok"] = wl.get("ok")
    result["watchlist_errors"] = wl.get("errors")
    # LeiBot ETF Universe — same Yahoo daily pipeline; not sent to AI BUY
    try:
        time.sleep(1.0)
        from market_data import refresh_etf_dashboard_cache

        etf = refresh_etf_dashboard_cache(max_workers=max_workers)
        result["etf_ok"] = etf.get("ok")
        result["etf_errors"] = etf.get("errors")
        result["etf_universe"] = etf.get("universe")
        log.info(
            "ETF prices done: ok=%s errors=%s universe=%s",
            etf.get("ok"),
            etf.get("errors"),
            etf.get("universe"),
        )
    except Exception:
        log.exception("ETF dashboard refresh failed (non-fatal)")
        result["etf_error"] = 1
    # Paper Trading daily mark / stop-target (simulation only — never brokerage)
    try:
        paper = job_paper_trading_daily()
        result["paper_closed"] = len(paper.get("closed") or [])
        result["paper_marked"] = paper.get("marked")
        result["paper_candidates"] = paper.get("candidates")
    except Exception:
        log.exception("Paper trading daily update failed (non-fatal)")
        result["paper_error"] = 1
    # Research: Strong Monitor (+ daily_bars → Rising Now / Multi-Signal)
    try:
        research = job_research_refresh()
        result["research"] = research
        result["strong_active"] = research.get("active_members")
        result["strong_as_of"] = research.get("as_of")
        result["rising_count"] = research.get("rising_count")
    except Exception:
        log.exception("Research (Strong/Rising) refresh failed (non-fatal)")
        result["research_error"] = 1
    return result


def job_research_refresh(*, full: bool | None = None) -> dict:
    """
    Refresh Research Center data used by Strong / Rising / Multi-Signal.

    - Upserts daily_bars + Strong Day / COUNT20 / membership
    - Rising Now is derived from daily_bars (no separate Yahoo pass)
    - If Strong was never initialized, runs a full 1y backfill automatically
    """
    log = _setup_logging()
    from db import get_setting
    from rising_now import list_rising_now
    from strong_stocks import (
        STRONG_HISTORY_PERIOD,
        STRONG_META_AS_OF,
        run_backfill,
        run_incremental_update,
    )

    as_of = (get_setting(STRONG_META_AS_OF, "") or "").strip()
    do_full = bool(full) if full is not None else (not as_of)
    if do_full and not as_of:
        log.info(
            "Research first-time init: full Strong backfill (%s) — also fills Rising bars",
            STRONG_HISTORY_PERIOD,
        )
    elif do_full:
        log.info("Research refresh: full Strong backfill (%s)…", STRONG_HISTORY_PERIOD)
    else:
        log.info("Research refresh: Strong incremental (3mo)…")

    if do_full:
        out = run_backfill(period=STRONG_HISTORY_PERIOD)
    else:
        out = run_incremental_update(period="3mo")

    try:
        rising_n = len(list_rising_now())
    except Exception:
        log.exception("Rising Now count failed after Research refresh")
        rising_n = 0
    out["rising_count"] = rising_n
    try:
        from sector_rotation import job_sector_rotation_update

        rot = job_sector_rotation_update(force=False)
        out["sector_rotation"] = rot
        log.info(
            "Sector Rotation: sectors=%s as_of=%s",
            rot.get("sectors"),
            rot.get("as_of"),
        )
    except Exception:
        log.exception("Sector Rotation update failed after Research refresh")
        out["sector_rotation"] = {"ok": False}

    # Core Universe Filter (deterministic) + light AI BUY snapshot.
    try:
        from core_universe import run_core_universe_filter

        core = run_core_universe_filter(persist=True)
        out["core_universe_qualified"] = core.get("qualified_count", 0)
        out["core_universe_funnel"] = core.get("funnel")
        log.info(
            "Core Universe Filter: qualified=%s funnel=%s",
            out["core_universe_qualified"],
            core.get("funnel"),
        )
    except Exception:
        log.exception("Core Universe Filter failed after Research (non-fatal)")
        out["core_universe_qualified"] = None
    try:
        from ai_buy import build_ai_buy_snapshot

        buy = build_ai_buy_snapshot(persist=True)
        out["ai_buy_universe"] = buy.get("universe_count", 0)
        out["ai_buy_ready"] = (buy.get("counts") or {}).get("READY", 0)
        log.info(
            "AI BUY snapshot: universe=%s READY=%s",
            out["ai_buy_universe"],
            out["ai_buy_ready"],
        )
    except Exception:
        log.exception("AI BUY refresh failed after Research (non-fatal)")
        out["ai_buy_universe"] = None

    log.info(
        "Research done: strong_active=%s as_of=%s rising=%s ok=%s errors=%s elapsed=%ss",
        out.get("active_members"),
        out.get("as_of"),
        rising_n,
        out.get("ok"),
        out.get("errors"),
        out.get("elapsed_sec"),
    )
    return out


def job_strong_monitor_update(*, full: bool = False) -> dict:
    """
    Strong Stock Monitor: incremental recent history (or full 1y backfill).
    Reconstructs COUNT20 + membership; no Financials/News filters.
    Also refreshes daily_bars used by Rising Now.
    """
    return job_research_refresh(full=True if full else None)


def job_paper_trading_daily() -> dict:
    """Once-per-day AI Paper Trading: Top 10 candidates + open-position OHLC settle."""
    log = _setup_logging()
    log.info("Starting AI Paper Trading daily update…")
    from paper_trading import run_daily_update

    out = run_daily_update(refresh_candidates=True)
    log.info(
        "Paper done: day=%s closed=%s marked=%s candidates=%s auto_bought=%s errors=%s",
        out.get("day"),
        len(out.get("closed") or []),
        out.get("marked"),
        out.get("candidates"),
        len(out.get("auto_created") or []),
        len(out.get("errors") or []),
    )
    return out


def job_paper_intraday_mark() -> dict:
    """
    Intraday: soft-mark open paper P&L + refresh AI BUY snapshot.
    Complements the weekday EOD full settle (stops/targets).
    """
    log = _setup_logging()
    log.info("Starting AI Trading intraday mark…")
    from ai_buy import build_ai_buy_snapshot
    from paper_trading import soft_mark_open_positions

    out: dict = {"soft": None, "ai_buy": None}
    try:
        out["soft"] = soft_mark_open_positions()
    except Exception:
        log.exception("Intraday soft mark failed")
        out["soft_error"] = 1
    try:
        buy = build_ai_buy_snapshot(persist=True)
        out["ai_buy"] = {
            "universe": buy.get("universe_count", 0),
            "ready": (buy.get("counts") or {}).get("READY", 0),
        }
    except Exception:
        log.exception("Intraday AI BUY refresh failed")
        out["ai_buy_error"] = 1
    log.info(
        "Intraday mark done: soft=%s ai_buy=%s",
        out.get("soft"),
        out.get("ai_buy"),
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LeiBot scheduled data updates")
    parser.add_argument("--universe", action="store_true", help="Refresh company names / index lists")
    parser.add_argument(
        "--prices",
        action="store_true",
        help="Refresh Yahoo dashboard list data + Watchlist",
    )
    parser.add_argument(
        "--etf",
        action="store_true",
        help="Refresh LeiBot ETF Universe prices only (shared Yahoo pipeline)",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="AI Paper Trading daily update (candidates + OHLC settle; simulation only)",
    )
    parser.add_argument(
        "--strong",
        action="store_true",
        help="Research refresh: Strong incremental (or full 1y if never initialized); fills Rising bars",
    )
    parser.add_argument(
        "--strong-backfill",
        action="store_true",
        help="Research full ~1y Strong backfill (also fills Rising daily_bars)",
    )
    parser.add_argument(
        "--research",
        action="store_true",
        help="Same as --strong: refresh Research (Strong + Rising bars)",
    )
    parser.add_argument("--all", action="store_true", help="Universe then prices (+ Watchlist + Research)")
    args = parser.parse_args(argv)

    if not (
        args.universe
        or args.prices
        or args.watchlist
        or args.etf
        or args.paper
        or args.strong
        or args.strong_backfill
        or args.research
        or args.all
    ):
        parser.print_help()
        return 2

    log = _setup_logging()
    try:
        if args.all or args.universe:
            job_refresh_universe()
        if args.all or args.prices:
            job_refresh_prices()
        elif args.watchlist:
            job_refresh_watchlist()
        elif args.etf:
            from market_data import refresh_etf_dashboard_cache

            etf = refresh_etf_dashboard_cache(max_workers=4)
            log.info(
                "ETF-only refresh: ok=%s errors=%s universe=%s failed=%s",
                etf.get("ok"),
                etf.get("errors"),
                etf.get("universe"),
                etf.get("failed"),
            )
        if args.paper and not (args.all or args.prices):
            # --prices already includes paper settle; standalone --paper for manual runs
            job_paper_trading_daily()
        if args.strong_backfill:
            job_strong_monitor_update(full=True)
        elif (args.strong or args.research) and not (args.all or args.prices):
            # --prices already includes Research; standalone --strong/--research for manual runs
            job_strong_monitor_update(full=False)
        log.info("Finished OK")
        return 0
    except Exception:
        log.exception("Update job failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
