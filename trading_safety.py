"""
Hard Safety Guard — NOT user-editable.

Trading Procedure / Watchlist Notes are soft documentation only.
These checks must stay in code and must never be disabled by UI text edits.
"""

from __future__ import annotations

from typing import Any

# Read-only copy for My Watchlist UI (display). Enforcement lives in functions below.
SAFETY_RULES_DISPLAY: list[str] = [
    "Position Qty >= Exit Qty",
    "Exit must not reverse the position",
    "Old stop cancellation must be confirmed before replacement",
]

SAFETY_RULES_DETAIL: list[str] = [
    "A. Exit quantity must never exceed current position quantity.",
    "B. Closing a LONG: SELL qty ≤ LONG position; never reverse into a SHORT.",
    "C. Closing a SHORT: BUY-to-cover qty ≤ SHORT position; never reverse into a LONG.",
    "D. Before replacing Fixed Stop with Trail: cancel → verify CANCELLED → then submit Trail.",
    "E. Never rely on UI text as a safety control.",
    "F. Re-check immediately before sending an order to IBKR.",
]


class SafetyViolation(ValueError):
    """Raised when a hard safety rule would be violated."""


def validate_exit_quantity(
    *,
    side: str,
    position_qty: float | int | None,
    exit_qty: float | int | None,
) -> None:
    """
    Enforce A–C for closing exits.

    side: intended exit action — "SELL" closes a LONG; "BUY" covers a SHORT.
    position_qty: signed or absolute open size (abs used). None skips (caller
    must pass position when known — never invent from UI text).
    """
    action = (side or "").strip().upper()
    if action not in ("BUY", "SELL"):
        raise SafetyViolation("side must be BUY or SELL")
    try:
        eq = float(exit_qty) if exit_qty is not None else None
    except (TypeError, ValueError) as exc:
        raise SafetyViolation("exit quantity must be a number") from exc
    if eq is None or eq <= 0:
        raise SafetyViolation("exit quantity must be > 0")

    if position_qty is None:
        # No position context — cannot certify A–C; callers that know size must pass it.
        return

    try:
        pq = abs(float(position_qty))
    except (TypeError, ValueError) as exc:
        raise SafetyViolation("position quantity must be a number") from exc
    if pq <= 0:
        raise SafetyViolation("no open position to exit")
    if eq > pq + 1e-9:
        raise SafetyViolation(
            f"exit qty {eq:g} exceeds position qty {pq:g} (rule A)"
        )
    # B/C: exit must not exceed position — same numeric gate prevents accidental reverse
    # when the broker treats excess SELL/BUY as opening the opposite side.


def require_stop_cancelled_before_replace(*, old_stop_cancelled: bool) -> None:
    """
    Rule D: only submit a replacement Trail after broker reports old stop CANCELLED.
    Call immediately before sending the new Trail to IBKR.
    """
    if not old_stop_cancelled:
        raise SafetyViolation(
            "old fixed stop must be CANCELLED before submitting Trail (rule D)"
        )


def pre_send_ibkr_checks(payload: dict[str, Any]) -> None:
    """
    Rule F: final gate before any IBKR submit.

    Expected optional keys:
      exit_side, position_qty, exit_qty — for closing exits
      replacing_stop, old_stop_cancelled — when replacing Fixed Stop with Trail
      mode — PAPER | LIVE (LIVE also requires LIVE_TRADING_ENABLED)
    """
    if not isinstance(payload, dict):
        raise SafetyViolation("invalid order payload")

    # Hard env gate — selecting LIVE connection mode never enables this.
    try:
        from ibkr_local.config import assert_order_placement_allowed

        assert_order_placement_allowed(payload.get("mode"))
    except PermissionError as exc:
        raise SafetyViolation(str(exc)) from exc

    if payload.get("is_exit") or payload.get("position_qty") is not None:
        validate_exit_quantity(
            side=str(payload.get("exit_side") or payload.get("action") or ""),
            position_qty=payload.get("position_qty"),
            exit_qty=payload.get("exit_qty") or payload.get("quantity"),
        )

    if payload.get("replacing_stop"):
        require_stop_cancelled_before_replace(
            old_stop_cancelled=bool(payload.get("old_stop_cancelled"))
        )
