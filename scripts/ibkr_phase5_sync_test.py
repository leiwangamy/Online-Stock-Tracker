"""
Phase 5 local self-test for IBKR market sync (Flask test client).

Covers auth, dry_run, controlled synthetic write, Yahoo preservation,
older-data SKIP, and transaction rollback. Keeps DB backup.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_KEY = "leibot-phase5-local-test-key-32chars"
os.environ["LEIBOT_MARKET_SYNC_API_KEY"] = TEST_KEY
os.environ["LEIBOT_MARKET_SYNC_ALLOW_FORCE_FAIL"] = "1"


def _sample_row(ticker: str, data_date: str, price: float = 123.45) -> dict[str, Any]:
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
        "change_pct": 0.5,
        "avg_move_pct": 1.2,
        "asset_type": "STOCK",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    from db import DB_PATH, get_conn, get_dashboard_by_tickers, init_db

    init_db()

    backup_dir = ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"leibot_pre_phase5_{stamp}.db"
    shutil.copy2(DB_PATH, backup_path)
    print(f"BACKUP: {backup_path}")
    print(f"BACKUP_SIZE: {backup_path.stat().st_size}")

    with get_conn() as conn:
        before_count = int(
            conn.execute("SELECT COUNT(*) AS n FROM dashboard_cache").fetchone()["n"]
        )
        aapl = conn.execute(
            "SELECT ticker, price, updated_at, data_source, data_date FROM dashboard_cache "
            "WHERE ticker = 'AAPL'"
        ).fetchone()
        spy = conn.execute(
            "SELECT ticker, price, updated_at, data_source, data_date FROM dashboard_cache "
            "WHERE ticker = 'SPY'"
        ).fetchone()

    print(f"ROWS_BEFORE: {before_count}")
    if aapl:
        print(
            f"AAPL_BEFORE: price={aapl['price']} updated={aapl['updated_at']} "
            f"source={aapl['data_source']} date={aapl['data_date']}"
        )

    today = date.today().isoformat()
    if aapl:
        with get_conn() as conn:
            conn.execute(
                "UPDATE dashboard_cache SET data_source = COALESCE(data_source, 'Yahoo'), "
                "data_date = COALESCE(data_date, ?), "
                "data_status = COALESCE(data_status, 'FRESH') "
                "WHERE ticker = 'AAPL'",
                (str(aapl["updated_at"] or today)[:10],),
            )

    from app import app

    client = app.test_client()
    results: dict[str, Any] = {}

    # 1) Auth reject
    r = client.post(
        "/api/market/ibkr-sync",
        json={"dry_run": True, "rows": []},
        headers={"Authorization": "Bearer wrong-key-xxxxxxxxxxxx"},
    )
    results["auth_reject_status"] = r.status_code
    print(f"AUTH_REJECT: HTTP {r.status_code}")

    # 2) Auth ok + dry_run
    r = client.post(
        "/api/market/ibkr-sync",
        json={"dry_run": True, "rows": [_sample_row("ZZIBKR1", today, 111.0)]},
        headers={"Authorization": f"Bearer {TEST_KEY}"},
    )
    body = r.get_json() or {}
    results["auth_dry_run_status"] = r.status_code
    results["auth_dry_run_ok"] = body.get("ok")
    print(f"AUTH_DRY_RUN: HTTP {r.status_code} ok={body.get('ok')} dry_run={body.get('dry_run')}")

    # 3) Controlled write
    print("--- DECISIONS (write ZZIBKR1) ---")
    r = client.post(
        "/api/market/ibkr-sync",
        json={"dry_run": False, "rows": [_sample_row("ZZIBKR1", today, 111.11)]},
        headers={"Authorization": f"Bearer {TEST_KEY}"},
    )
    body = r.get_json() or {}
    results["write"] = body
    for d in body.get("decisions") or []:
        print(
            f"  {d.get('ticker')}: {d.get('decision')} | "
            f"exist_src={d.get('existing_data_source')} exist_date={d.get('existing_data_date')} | "
            f"in_src={d.get('incoming_data_source')} in_date={d.get('incoming_data_date')} | "
            f"{d.get('reason')}"
        )
    print(
        f"WRITE_SYNTHETIC: HTTP {r.status_code} updated={body.get('records_updated')} "
        f"rows {body.get('rows_before')} -> {body.get('rows_after')}"
    )

    # 4) Older-data protection
    if aapl:
        older = (date.today() - timedelta(days=5)).isoformat()
        r = client.post(
            "/api/market/ibkr-sync",
            json={"dry_run": False, "rows": [_sample_row("AAPL", older, 1.0)]},
            headers={"Authorization": f"Bearer {TEST_KEY}"},
        )
        body = r.get_json() or {}
        results["older"] = body
        for d in body.get("decisions") or []:
            print(f"OLDER_TEST {d.get('ticker')}: {d.get('decision')} — {d.get('reason')}")
        aapl_after = get_dashboard_by_tickers(["AAPL"]).get("AAPL") or {}
        print(
            f"AAPL_AFTER: price={aapl_after.get('price')} "
            f"source={aapl_after.get('data_source')} date={aapl_after.get('data_date')}"
        )
        price_ok = abs(float(aapl_after.get("price") or 0) - float(aapl["price"] or 0)) < 1e-6
        results["aapl_preserved"] = price_ok
        print(f"AAPL_PRESERVED: {price_ok}")
    else:
        results["aapl_preserved"] = True
        print("OLDER_TEST: skipped (AAPL not in local cache)")

    # 5) Rollback
    r = client.post(
        "/api/market/ibkr-sync",
        json={
            "dry_run": False,
            "force_fail_after_n": 0,
            "rows": [_sample_row("ZZIBKR2", today, 222.22)],
        },
        headers={"Authorization": f"Bearer {TEST_KEY}"},
    )
    body = r.get_json() or {}
    results["rollback"] = body
    zz2 = get_dashboard_by_tickers(["ZZIBKR2"]).get("ZZIBKR2")
    print(
        f"ROLLBACK: HTTP {r.status_code} sync={body.get('sync_status')} "
        f"error={body.get('error')} ZZIBKR2_exists={zz2 is not None}"
    )
    results["rollback_ok"] = zz2 is None and body.get("sync_status") == "FAILED"

    with get_conn() as conn:
        after_count = int(
            conn.execute("SELECT COUNT(*) AS n FROM dashboard_cache").fetchone()["n"]
        )
        zz1 = conn.execute(
            "SELECT ticker, price, data_source, data_date, data_status FROM dashboard_cache "
            "WHERE ticker='ZZIBKR1'"
        ).fetchone()
        if spy:
            spy_after = conn.execute(
                "SELECT price FROM dashboard_cache WHERE ticker='SPY'"
            ).fetchone()
            spy_ok = abs(float(spy_after["price"]) - float(spy["price"])) < 1e-6
        else:
            spy_ok = True

    print(f"ROWS_AFTER: {after_count} (delta={after_count - before_count})")
    print(f"ZZIBKR1: {dict(zz1) if zz1 else None}")
    print(f"SPY_PRESERVED: {spy_ok}")
    print(f"SYNC_META: {json.dumps((results.get('write') or {}).get('sync_meta'), default=str)}")

    with get_conn() as conn:
        conn.execute("DELETE FROM dashboard_cache WHERE ticker IN ('ZZIBKR1', 'ZZIBKR2')")
    with get_conn() as conn:
        final_count = int(
            conn.execute("SELECT COUNT(*) AS n FROM dashboard_cache").fetchone()["n"]
        )
    print(f"CLEANUP_ROWS: {final_count} (expect ~{before_count})")

    ok = (
        results.get("auth_reject_status") == 401
        and results.get("auth_dry_run_status") == 200
        and (results.get("write") or {}).get("records_updated", 0) >= 1
        and results.get("rollback_ok") is True
        and spy_ok
        and results.get("aapl_preserved") is True
        and (
            not aapl
            or ((results.get("older") or {}).get("records_skipped", 0) >= 1)
            or any(
                d.get("decision") == "SKIP"
                for d in ((results.get("older") or {}).get("decisions") or [])
            )
        )
    )
    print(f"PHASE5_LOCAL_OK: {ok}")
    print(f"BACKUP_KEPT: {backup_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
