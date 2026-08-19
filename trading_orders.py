"""
Admin Order Requests + private Local Trading Agent API (V0).

No IBKR / brokerage execution. Human Admin creates requests;
a local agent may read pending requests and update processing status.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from db import get_conn, get_setting, init_db

log = logging.getLogger("leibot.trading_api")

MODE_PAPER = "PAPER"
ACTIONS = ("BUY", "SELL")
STATUSES = ("PENDING", "RECEIVED", "REPORTED", "ERROR")

# V0 processing lifecycle only (no brokerage FILLED/SUBMITTED).
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"PENDING", "RECEIVED", "REPORTED", "ERROR"},
    "RECEIVED": {"RECEIVED", "REPORTED", "ERROR"},
    "REPORTED": {"REPORTED"},  # terminal for V0
    "ERROR": {"ERROR", "RECEIVED", "REPORTED", "PENDING"},
}

ENV_API_KEY = "LEIBOT_PRIVATE_AGENT_API_KEY"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_private_agent_api_key() -> str:
    """Read API key from environment only (never from DB/HTML)."""
    return (os.environ.get(ENV_API_KEY) or "").strip()


def api_key_configured() -> bool:
    return len(get_private_agent_api_key()) >= 16


def verify_bearer_token(auth_header: str | None) -> bool:
    """
    Validate Authorization: Bearer <token> with constant-time compare.
    Returns False if key unset or mismatch — never logs the secret.
    """
    expected = get_private_agent_api_key()
    if not expected or len(expected) < 16:
        log.warning("private agent API auth failed: key not configured")
        return False
    if not auth_header or not isinstance(auth_header, str):
        log.warning("private agent API auth failed: missing Authorization header")
        return False
    parts = auth_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        log.warning("private agent API auth failed: invalid Authorization scheme")
        return False
    provided = parts[1].strip()
    ok = secrets.compare_digest(provided, expected)
    if ok:
        log.info("private agent API auth ok")
    else:
        log.warning("private agent API auth failed: token mismatch")
    return ok


def _row_to_api(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": int(row["id"]),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "symbol": row.get("symbol"),
        "action": row.get("action"),
        "quantity": row.get("quantity"),
        "expected_price": row.get("expected_price"),
        "allocation_amount": row.get("allocation_amount"),
        "stop_price": row.get("stop_price"),
        "take_profit_price": row.get("take_profit_price"),
        "ai_score": row.get("ai_score_at_request"),
        "mos_t": row.get("mos_t_at_request"),
        "source": row.get("source_at_request"),
        "mode": row.get("mode"),
        "status": row.get("status"),
        "status_message": row.get("status_message"),
    }


def validate_create_payload(data: dict[str, Any]) -> dict[str, Any]:
    """
    Validate Admin create fields. Raises ValueError on malformed input
    (does not silently coerce invalid values).
    """
    symbol = (data.get("symbol") or "").strip().upper()
    if not symbol or len(symbol) > 12:
        raise ValueError("symbol is required")
    # Same loose ticker shape as watchlist (letters/digits/.-)
    import re

    if not re.match(r"^[A-Z0-9.\-]{1,12}$", symbol):
        raise ValueError("invalid symbol format")

    action = (data.get("action") or "").strip().upper()
    if action not in ACTIONS:
        raise ValueError("action must be BUY or SELL")

    try:
        quantity = float(data.get("quantity"))
    except (TypeError, ValueError):
        raise ValueError("quantity must be a number") from None
    if quantity <= 0:
        raise ValueError("quantity must be > 0")

    mode = (data.get("mode") or MODE_PAPER).strip().upper()
    if mode != MODE_PAPER:
        raise ValueError("mode must be PAPER for V0")

    def _opt_float(key: str) -> float | None:
        raw = data.get(key)
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number") from None

    expected_price = _opt_float("expected_price")
    allocation_amount = _opt_float("allocation_amount")
    stop_price = _opt_float("stop_price")
    take_profit_price = _opt_float("take_profit_price")
    ai_score = _opt_float("ai_score")
    mos_t = _opt_float("mos_t")
    source = (data.get("source") or "").strip() or None

    if expected_price is not None and expected_price <= 0:
        raise ValueError("expected_price must be > 0 when provided")
    if allocation_amount is not None and allocation_amount <= 0:
        raise ValueError("allocation_amount must be > 0 when provided")
    if stop_price is not None and stop_price <= 0:
        raise ValueError("stop_price must be > 0 when provided")
    if take_profit_price is not None and take_profit_price <= 0:
        raise ValueError("take_profit_price must be > 0 when provided")

    # Long-oriented sanity when all three prices present.
    if (
        action == "BUY"
        and expected_price is not None
        and stop_price is not None
        and take_profit_price is not None
    ):
        if not (stop_price < expected_price < take_profit_price):
            raise ValueError("for BUY, require stop_price < expected_price < take_profit_price")

    # Soft check against Paper Trading Fund limit (do not mutate portfolio).
    if allocation_amount is not None:
        try:
            limit = float(get_setting("paper_trading_limit", 1500.0))
        except Exception:
            limit = 1500.0
        if allocation_amount > limit + 1e-6:
            raise ValueError(f"allocation_amount exceeds trading limit ({limit})")

    return {
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "expected_price": expected_price,
        "allocation_amount": allocation_amount,
        "stop_price": stop_price,
        "take_profit_price": take_profit_price,
        "ai_score_at_request": ai_score,
        "mos_t_at_request": mos_t,
        "source_at_request": source,
        "mode": mode,
        "status": "PENDING",
        "status_message": None,
    }


def create_order_request(data: dict[str, Any]) -> dict[str, Any]:
    payload = validate_create_payload(data)
    now = _utc_now_iso()
    init_db()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO trading_order_requests (
              created_at, updated_at, symbol, action, quantity,
              expected_price, allocation_amount, stop_price, take_profit_price,
              ai_score_at_request, mos_t_at_request, source_at_request,
              mode, status, status_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                now,
                payload["symbol"],
                payload["action"],
                payload["quantity"],
                payload["expected_price"],
                payload["allocation_amount"],
                payload["stop_price"],
                payload["take_profit_price"],
                payload["ai_score_at_request"],
                payload["mos_t_at_request"],
                payload["source_at_request"],
                payload["mode"],
                payload["status"],
                payload["status_message"],
            ),
        )
        rid = int(cur.lastrowid)
    log.info(
        "order request created id=%s symbol=%s action=%s qty=%s mode=%s",
        rid,
        payload["symbol"],
        payload["action"],
        payload["quantity"],
        payload["mode"],
    )
    return get_order_request(rid)  # type: ignore[return-value]


def get_order_request(request_id: int) -> dict[str, Any] | None:
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM trading_order_requests WHERE id = ?",
            (int(request_id),),
        ).fetchone()
    if not row:
        return None
    return _row_to_api(dict(row))


def list_order_requests(*, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    init_db()
    limit = max(1, min(int(limit), 500))
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM trading_order_requests WHERE status = ? "
                "ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trading_order_requests ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_api(dict(r)) for r in rows]


def list_pending_order_requests(*, limit: int = 100) -> list[dict[str, Any]]:
    return list_order_requests(status="PENDING", limit=limit)


def update_order_status(
    request_id: int, *, status: str, message: str | None = None
) -> dict[str, Any]:
    status = (status or "").strip().upper()
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")

    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM trading_order_requests WHERE id = ?",
            (int(request_id),),
        ).fetchone()
        if not row:
            raise LookupError("order request not found")
        current = dict(row)
        cur_status = current.get("status") or "PENDING"
        allowed = ALLOWED_TRANSITIONS.get(cur_status, set())
        if status not in allowed:
            raise ValueError(
                f"invalid status transition {cur_status} → {status}"
            )
        now = _utc_now_iso()
        msg = (message or "").strip() or current.get("status_message")
        conn.execute(
            """
            UPDATE trading_order_requests
            SET status = ?, status_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, msg, now, int(request_id)),
        )
    log.info(
        "order request status updated id=%s %s → %s",
        request_id,
        cur_status,
        status,
    )
    out = get_order_request(request_id)
    assert out is not None
    return out
