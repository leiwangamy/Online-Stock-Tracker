"""Phase 5: run Core Universe Filter and print funnel + distribution."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import init_db  # noqa: E402
from core_universe import (  # noqa: E402
    get_thresholds,
    metric_distribution,
    run_core_universe_filter,
)

init_db()
print("THRESHOLDS", json.dumps(get_thresholds(), indent=2))
result = run_core_universe_filter(persist=True)
print("\n=== FILTER FUNNEL ===")
for st in result.get("funnel") or []:
    print(f"  {st['stage']:24s} {st['count']}")
print(f"\nFINAL QUALIFIED: {result.get('qualified_count')}")
print(f"RAW: {result.get('raw_count')}")
print(f"NEWLY: {len(result.get('newly_qualified') or [])}")
print(f"STILL: {len(result.get('still_qualified') or [])}")
print(f"NO LONGER: {len(result.get('no_longer_qualified') or [])}")
print(f"\nliquidity_note: {result.get('liquidity_note')}")

dist = metric_distribution(result.get("all_rows") or [])
print("\n=== DISTRIBUTION (all scanned) ===")
print(json.dumps(dist, indent=2))

q = result.get("qualified") or []
print("\n=== SAMPLE QUALIFIED (top 25 by mcap) ===")
for r in q[:25]:
    print(
        f"{r['ticker']:8s} path={r.get('qualification_path')} "
        f"mcap={r.get('market_cap')} rev%={r.get('revenue_growth_pct')} "
        f"rs252={r.get('rs_252d_vs_spy')} adm={r.get('avg_move_pct')}"
    )

# Failure reason histogram
from collections import Counter

c = Counter()
for r in result.get("all_rows") or []:
    for f in r.get("failure_reasons") or []:
        c[f] += 1
print("\n=== FAILURE REASON COUNTS ===")
for k, v in c.most_common():
    print(f"  {k:28s} {v}")
