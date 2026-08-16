"""Retry dashboard tickers whose cache is missing/stale after a partial refresh."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from db import get_all_settings, get_conn, init_db, list_universe, save_dashboard_rows
from market_data import fetch_metrics_for_ticker
import valuation_config as cfg


def _stale(updated_at: str | None, limit_h: float) -> bool:
    if not updated_at:
        return True
    try:
        dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0 > limit_h
    except Exception:
        return True


def main() -> None:
    init_db()
    limit_h = float(cfg.MOS_PRICE_STALE_HOURS)
    settings = get_all_settings()
    sma = int(settings.get("sma_period", 25))
    reb = int(settings.get("rebound_lookback", sma))
    universe = list_universe()
    meta = {r["ticker"]: r for r in universe}

    with get_conn() as conn:
        cached = {
            r["ticker"]: r["updated_at"]
            for r in conn.execute("SELECT ticker, updated_at FROM dashboard_cache").fetchall()
        }

    need = [r["ticker"] for r in universe if _stale(cached.get(r["ticker"]), limit_h)]
    print(f"Stale/missing tickers to retry: {len(need)} (limit {limit_h}h)")
    if not need:
        print("Nothing to retry.")
        return

    rows: list[dict] = []
    errors = 0
    # Slower to reduce Yahoo rate limits
    workers = 4
    batch = 80
    for i in range(0, len(need), batch):
        chunk = need[i : i + batch]
        print(f"Batch {i // batch + 1}: {len(chunk)} tickers...")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(
                    fetch_metrics_for_ticker,
                    t,
                    sma_period=sma,
                    rebound_lookback=reb,
                    meta=meta.get(t) or {},
                ): t
                for t in chunk
            }
            for fut in as_completed(futs):
                t = futs[fut]
                try:
                    r = fut.result()
                except Exception:
                    r = None
                if r is None:
                    errors += 1
                else:
                    rows.append(r)
        if rows:
            save_dashboard_rows(rows, replace_all=False)
            print(f"  saved so far ok={len(rows)} errors={errors}")
            rows = []
    print(f"Retry done. errors={errors}")


if __name__ == "__main__":
    main()
