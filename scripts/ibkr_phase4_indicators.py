"""
Phase 4: IBKR vs Yahoo indicator compatibility (LOCAL ONLY).

Yahoo remains primary. IBKR is tested as a same-engine fallback feed.

  python scripts/ibkr_phase4_indicators.py

No production sync, no Yahoo workflow changes, no orders.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ibkr_local import (  # noqa: E402
    DEFAULT_CLIENT_ID,
    DEFAULT_HOST,
    DEFAULT_PAPER_PORT,
    DEFAULT_TEST_TICKERS,
)
from ibkr_local.phase4 import format_phase4_report, run_phase4_indicator_compare  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(
        description="LeiBot IBKR Phase 4 — indicator compatibility (local)"
    )
    p.add_argument(
        "--tickers",
        default=",".join(DEFAULT_TEST_TICKERS),
        help="Comma-separated tickers (default: AAPL,MSFT,SPY)",
    )
    p.add_argument("--host", default=None, help=f"Default {DEFAULT_HOST}")
    p.add_argument(
        "--port", type=int, default=None, help=f"Paper TWS port (default {DEFAULT_PAPER_PORT})"
    )
    p.add_argument(
        "--client-id", type=int, default=None, help=f"Default {DEFAULT_CLIENT_ID}"
    )
    p.add_argument("--json", action="store_true", help="Also print raw JSON")
    args = p.parse_args()
    tickers = [t.strip().upper() for t in str(args.tickers).split(",") if t.strip()]

    report = run_phase4_indicator_compare(
        tickers,
        host=args.host,
        port=args.port,
        client_id=args.client_id,
    )
    print(format_phase4_report(report))
    if args.json:
        print("--- JSON ---")
        print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
