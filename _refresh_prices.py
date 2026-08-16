"""Refresh Watchlist + full Market Dashboard prices (incl. 1Y Target)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from app import MY_WATCHLIST, collect_watchlist_tickers
from db import get_all_settings, get_universe_flags, init_db, list_universe, save_dashboard_rows
from market_data import fetch_metrics_for_ticker, refresh_dashboard_cache


def _refresh_tickers(tickers: list[str], *, label: str, workers: int = 6) -> tuple[int, int]:
    settings = get_all_settings()
    sma = int(settings.get("sma_period", 25))
    reb = int(settings.get("rebound_lookback", sma))
    flags = get_universe_flags(tickers)
    meta_u = {r["ticker"]: r for r in list_universe()}
    rows: list[dict] = []
    errors = 0

    def one(t: str):
        meta = dict(meta_u.get(t) or flags.get(t) or {})
        return fetch_metrics_for_ticker(t, sma_period=sma, rebound_lookback=reb, meta=meta)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(one, t): t for t in tickers}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                r = fut.result()
            except Exception as exc:
                print(f"  FAIL {t}: {exc}")
                errors += 1
                continue
            if r is None:
                print(f"  FAIL {t}: no data")
                errors += 1
            else:
                rows.append(r)
                print(f"  OK   {t}: price={r.get('price')} target={r.get('target_1y')}")
    if rows:
        save_dashboard_rows(rows, replace_all=False)
    print(f"{label}: ok={len(rows)} errors={errors}")
    return len(rows), errors


def main() -> None:
    init_db()
    wl = collect_watchlist_tickers()
    # Ensure mine list is included even if oversold/pullback empty
    for t in MY_WATCHLIST:
        if t not in wl:
            wl.append(t)
    print(f"=== 1) Watchlist tickers ({len(wl)}) ===")
    _refresh_tickers(wl, label="Watchlist")

    print("=== 2) Full Market Dashboard universe ===")
    result = refresh_dashboard_cache(group=None, max_workers=8)
    print(
        f"Dashboard: ok={result['ok']} errors={result['errors']} "
        f"universe={result['universe']} SMA{result['sma_period']}"
    )
    print("Done.")


if __name__ == "__main__":
    main()
