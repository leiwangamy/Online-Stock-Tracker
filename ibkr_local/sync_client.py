"""
Local HTTPS client for Phase 5 IBKR → LeiBot market sync.

Credentials: LEIBOT_MARKET_SYNC_API_KEY from environment / .env only.
Never logs the full API key.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def _load_dotenv_if_present() -> None:
    """Minimal .env loader (no dependency). Does not override existing env."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    path = os.path.join(root, ".env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


def get_sync_api_key() -> str:
    _load_dotenv_if_present()
    return (os.environ.get("LEIBOT_MARKET_SYNC_API_KEY") or "").strip()


def post_ibkr_sync(
    base_url: str,
    payload: dict[str, Any],
    *,
    api_key: str | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict[str, Any]]:
    """
    POST /api/market/ibkr-sync with Bearer auth.
    Returns (http_status, parsed_json_or_error_dict).
    """
    key = (api_key if api_key is not None else get_sync_api_key()).strip()
    if len(key) < 16:
        return 0, {"ok": False, "error": "LEIBOT_MARKET_SYNC_API_KEY missing/too short"}

    url = base_url.rstrip("/") + "/api/market/ibkr-sync"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "LeiBot-IBKR-SyncClient/phase5",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                data = {"ok": False, "error": "non-json response", "raw": raw[:500]}
            return int(resp.status), data if isinstance(data, dict) else {"ok": False, "data": data}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"ok": False, "error": raw[:500] or str(exc)}
        if not isinstance(data, dict):
            data = {"ok": False, "error": str(data)}
        return int(exc.code), data
    except Exception as exc:
        return 0, {"ok": False, "error": str(exc)}
