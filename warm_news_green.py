"""Warm shared news cache for Financial-green names in low_target screen only."""

from __future__ import annotations

import json
import time
from pathlib import Path

from db import list_low_target_ratio
from market_data import ensure_news_cache, get_fund_cached_only


def main() -> None:
    tickers = [r["ticker"] for r in list_low_target_ratio(0.8) if r.get("ticker")]
    funds = get_fund_cached_only(tickers)
    green = [t for t in tickers if (funds.get(t) or {}).get("health") == "good"]
    print(f"screened={len(tickers)} green={len(green)}")
    print("green:", ", ".join(green))

    t0 = time.perf_counter()
    result = ensure_news_cache(green, max_workers=3, force=False)
    result["elapsed_sec"] = round(time.perf_counter() - t0, 1)

    log = Path("data/logs/news_cache_warm_green_low_target.txt")
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"screened: {len(tickers)}",
        f"green_count: {len(green)}",
        f"tickers: {', '.join(green)}",
        f"already_cached: {result['already_cached']}",
        f"fetched: {result['fetched']}",
        f"ok_new: {result['ok_new']}",
        f"failed: {result['failed']}",
        f"counts: {result['counts']}",
        f"elapsed_sec: {result['elapsed_sec']}",
        f"cache_path: {result['cache_path']}",
        "",
        "failures:",
    ]
    for f in result.get("failures") or []:
        lines.append(f"  {f.get('ticker')}: {f.get('reason')}")
    lines.append("")
    lines.append("per_ticker:")
    for t in green:
        n = (result.get("results") or {}).get(t) or {}
        lines.append(f"  {t}: tone={n.get('tone')} label={n.get('label')}")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = {k: v for k, v in result.items() if k != "results"}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print("log:", log)


if __name__ == "__main__":
    main()
