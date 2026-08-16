"""
One-shot: warm shared Financial / 财报 cache for current Target Ratio < 80% screen.

Reuses valid fund_cache entries; fetches only missing tickers.
Does NOT run News / AI / DCF / CLV.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from db import list_low_target_ratio
from market_data import ensure_fund_cache, fund_cache_path


def main() -> None:
    tickers = [r["ticker"] for r in list_low_target_ratio(0.8) if r.get("ticker")]
    t0 = time.perf_counter()
    result = ensure_fund_cache(tickers, max_workers=3, force=False)
    elapsed = time.perf_counter() - t0
    result["elapsed_sec"] = round(elapsed, 1)
    result["screen"] = "low_target (ratio<0.80, 63D pos<=70)"

    log_path = Path("data/logs/fund_cache_warm_low_target.txt")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"screen: {result['screen']}",
        f"total screened: {result['total']}",
        f"already cached: {result['already_cached']}",
        f"fetched (missing): {result['fetched']}",
        f"ok new: {result['ok_new']}",
        f"failed: {result['failed']}",
        f"final cached: {result['final_cached']}",
        f"coverage: {result['final_cached']}/{result['total']} ({result['coverage']*100:.1f}%)",
        f"elapsed_sec: {result['elapsed_sec']}",
        f"cache_path: {result['cache_path']}",
        "",
        "failures:",
    ]
    for f in result.get("failures") or []:
        lines.append(f"  {f.get('ticker')}: {f.get('reason')}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({k: v for k, v in result.items() if k != "ok_new_tickers"}, indent=2, ensure_ascii=False))
    print(f"log: {log_path}")


if __name__ == "__main__":
    main()
