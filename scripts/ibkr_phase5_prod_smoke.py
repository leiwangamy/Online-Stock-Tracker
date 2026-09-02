"""
Phase 5 production smoke (controlled).

Requires:
  LEIBOT_MARKET_SYNC_API_KEY in env or .env
  LEIBOT_SYNC_BASE_URL (default https://stock.lwsoc.com)

Examples:
  set LEIBOT_SYNC_BASE_URL=https://stock.lwsoc.com
  python scripts/ibkr_phase5_prod_smoke.py --dry-run
  python scripts/ibkr_phase5_prod_smoke.py --write-synthetic
  python scripts/ibkr_phase5_prod_smoke.py --test-older-aapl
  python scripts/ibkr_phase5_prod_smoke.py --cleanup-synthetic
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ibkr_local.sync_client import get_sync_api_key, post_ibkr_sync  # noqa: E402


def _row(ticker: str, data_date: str, price: float) -> dict:
    return {
        "ticker": ticker,
        "name": ticker,
        "price": price,
        "data_date": data_date,
        "data_source": "IBKR-Fallback",
        "status": "FALLBACK",
        "sma": round(price * 0.98, 4),
        "dist_pct": 2.04,
        "rebound_pct": 5.5,
        "sma_period": 25,
        "range_63d_low": round(price * 0.9, 4),
        "range_63d_high": round(price * 1.1, 4),
        "range_63d_pos": 55.0,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _print_decisions(body: dict) -> None:
    for d in body.get("decisions") or []:
        print(
            f"  {d.get('ticker')}: {d.get('decision')} | "
            f"exist={d.get('existing_data_source')}/{d.get('existing_data_date')} | "
            f"in={d.get('incoming_data_source')}/{d.get('incoming_data_date')} | "
            f"{d.get('reason')}"
        )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--base-url",
        default=os.environ.get("LEIBOT_SYNC_BASE_URL", "https://stock.lwsoc.com"),
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--write-synthetic", action="store_true")
    p.add_argument("--test-older-aapl", action="store_true")
    p.add_argument("--cleanup-synthetic", action="store_true")
    p.add_argument("--auth-fail-check", action="store_true")
    args = p.parse_args()

    key = get_sync_api_key()
    if len(key) < 16:
        print("ERROR: set LEIBOT_MARKET_SYNC_API_KEY", file=sys.stderr)
        return 2
    print(f"BASE={args.base_url} KEY_LEN={len(key)} KEY_PREFIX={key[:4]}...")

    today = date.today().isoformat()

    if args.auth_fail_check:
        status, body = post_ibkr_sync(
            args.base_url,
            {"dry_run": True, "rows": []},
            api_key="definitely-wrong-key-xxxx",
        )
        print(f"AUTH_FAIL_CHECK HTTP {status}: {json.dumps(body)[:300]}")
        return 0 if status == 401 else 1

    if args.dry_run:
        status, body = post_ibkr_sync(
            args.base_url,
            {"dry_run": True, "rows": [_row("ZZIBKR1", today, 111.11)]},
        )
        print(f"DRY_RUN HTTP {status}")
        print(json.dumps(body, indent=2, default=str)[:2000])
        _print_decisions(body)
        return 0 if status == 200 and body.get("ok") else 1

    if args.write_synthetic:
        status, body = post_ibkr_sync(
            args.base_url,
            {"dry_run": False, "rows": [_row("ZZIBKR1", today, 111.11)]},
        )
        print(f"WRITE_SYNTHETIC HTTP {status}")
        _print_decisions(body)
        print(
            f"updated={body.get('records_updated')} "
            f"rows {body.get('rows_before')}->{body.get('rows_after')} "
            f"sync={body.get('sync_status')}"
        )
        return 0 if body.get("records_updated") else 1

    if args.test_older_aapl:
        older = (date.today() - timedelta(days=5)).isoformat()
        status, body = post_ibkr_sync(
            args.base_url,
            {"dry_run": False, "rows": [_row("AAPL", older, 1.0)]},
        )
        print(f"OLDER_AAPL HTTP {status}")
        _print_decisions(body)
        skipped = any(d.get("decision") == "SKIP" for d in (body.get("decisions") or []))
        print(f"SKIPPED={skipped}")
        return 0 if skipped else 1

    if args.cleanup_synthetic:
        # Cleanup is intentionally not exposed via sync API (no delete endpoint).
        # Document: remove ZZIBKR1 via SQL on server after test.
        print(
            "Cleanup via server SQL:\n"
            "  sudo docker exec -i stock_web_prod python -c "
            "\"from db import get_conn; "
            "c=get_conn(); c.execute(\\\"DELETE FROM dashboard_cache WHERE ticker='ZZIBKR1'\\\"); "
            "c.commit(); print('deleted')\""
        )
        return 0

    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
