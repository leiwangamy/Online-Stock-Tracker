"""Warm shared news cache for low_target names with Financial Pass Rate >= 60%."""

from __future__ import annotations

import json
import time
from pathlib import Path

from db import list_low_target_ratio
from market_data import ensure_news_cache, fund_pass_rate, fund_qualifies_for_news, get_fund_cached_only


def main() -> None:
    tickers = [r["ticker"] for r in list_low_target_ratio(0.8) if r.get("ticker")]
    funds = get_fund_cached_only(tickers)
    eligible = [t for t in tickers if fund_qualifies_for_news(funds.get(t))]
    print(f"screened={len(tickers)} pass_rate_ge_60={len(eligible)}")
    print("eligible:", ", ".join(eligible))

    t0 = time.perf_counter()
    result = ensure_news_cache(eligible, max_workers=3, force=False)
    result["elapsed_sec"] = round(time.perf_counter() - t0, 1)

    log = Path("data/logs/news_cache_warm_pass60_low_target.txt")
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"screened: {len(tickers)}",
        f"pass_rate_ge_60_count: {len(eligible)}",
        f"tickers: {', '.join(eligible)}",
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
    for t in eligible:
        fund = funds.get(t) or {}
        rate = fund_pass_rate(fund)
        rate_s = f"{rate:.0%}" if rate is not None else "n/a"
        n = (result.get("results") or {}).get(t) or {}
        lines.append(
            f"  {t}: fund={fund.get('ok')}/{fund.get('total_known')} ({rate_s}) "
            f"tone={n.get('tone')} label={n.get('label')}"
        )
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    out = {k: v for k, v in result.items() if k != "results"}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    print("log:", log)


if __name__ == "__main__":
    main()
