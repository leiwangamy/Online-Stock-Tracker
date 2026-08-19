#!/usr/bin/env python3
"""
Local Trading Agent V0 smoke helper (no IBKR).

Usage:
  set LEIBOT_PRIVATE_AGENT_API_KEY=your-secret-at-least-16-chars
  python scripts/local_agent_v0_smoke.py [--base-url http://127.0.0.1:3000]

Steps:
  1) GET /api/trading/orders/pending  (requires Bearer token)
  2) For first pending order (if any), POST status=REPORTED
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _request(method: str, url: str, token: str, body: dict | None = None):
    data = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return e.code, payload


def main() -> int:
    parser = argparse.ArgumentParser(description="LeiBot Local Agent V0 smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    args = parser.parse_args()
    token = (os.environ.get("LEIBOT_PRIVATE_AGENT_API_KEY") or "").strip()
    if len(token) < 16:
        print("ERROR: set LEIBOT_PRIVATE_AGENT_API_KEY (min 16 chars)", file=sys.stderr)
        return 2

    base = args.base_url.rstrip("/")

    # Unauthenticated should fail
    req = urllib.request.Request(f"{base}/api/trading/orders/pending", method="GET")
    try:
        urllib.request.urlopen(req, timeout=15)
        print("FAIL: unauthenticated GET should return 401")
        return 1
    except urllib.error.HTTPError as e:
        if e.code != 401:
            print(f"FAIL: expected 401 without auth, got {e.code}")
            return 1
        print("OK: unauthenticated → 401")

    status, payload = _request("GET", f"{base}/api/trading/orders/pending", token)
    if status != 200:
        print(f"FAIL: pending GET status={status} body={payload}")
        return 1
    orders = payload.get("orders") or []
    print(f"OK: pending count={len(orders)}")
    if not orders:
        print("No PENDING orders — create one in Admin → Order Requests, then re-run.")
        return 0

    oid = orders[0]["request_id"]
    print(f"First pending request_id={oid} symbol={orders[0].get('symbol')}")
    status, updated = _request(
        "POST",
        f"{base}/api/trading/orders/{oid}/status",
        token,
        {"status": "REPORTED", "message": "Local order report generated successfully."},
    )
    if status != 200:
        print(f"FAIL: status update {status} {updated}")
        return 1
    print(f"OK: request_id={oid} → {updated.get('order', {}).get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
