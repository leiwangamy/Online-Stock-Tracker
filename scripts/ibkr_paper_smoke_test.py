"""
Phase 2 smoke test: Paper TWS only.

Usage (Windows, after Paper TWS is logged in + API enabled):

  pip install -r requirements-ibkr.txt
  python scripts/ibkr_paper_smoke_test.py

Optional env (no credentials — host/port/client only):

  set IBKR_HOST=127.0.0.1
  set IBKR_PAPER_PORT=7497
  set IBKR_CLIENT_ID=71
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
    format_report,
    run_paper_smoke_test,
)


def main() -> int:
    p = argparse.ArgumentParser(description="LeiBot IBKR Phase 2 — Paper TWS smoke test")
    p.add_argument(
        "--tickers",
        default=",".join(DEFAULT_TEST_TICKERS),
        help="Comma-separated tickers (default: AAPL,MSFT,SPY)",
    )
    p.add_argument("--host", default=None, help=f"Default {DEFAULT_HOST}")
    p.add_argument("--port", type=int, default=None, help=f"Paper TWS port (default {DEFAULT_PAPER_PORT})")
    p.add_argument("--client-id", type=int, default=None, help=f"Default {DEFAULT_CLIENT_ID}")
    p.add_argument(
        "--json",
        action="store_true",
        help="Also print raw JSON report after the human summary",
    )
    args = p.parse_args()
    tickers = [t.strip().upper() for t in str(args.tickers).split(",") if t.strip()]

    report = run_paper_smoke_test(
        tickers,
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        readonly=True,
    )
    print(format_report(report))
    if args.json:
        print("--- JSON ---")
        print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
