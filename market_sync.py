"""
LeiBot IBKR market sync — Phase 5 (secure HTTPS fallback upload).

Yahoo remains primary. IBKR rows are partial per-ticker merges only.
Never replace_all. Never place orders. API key from env only.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import secrets
from datetime import date, datetime, timezone
from typing import Any

from db import get_conn, get_setting, init_db, set_setting

log = logging.getLogger("leibot.market_sync")

ENV_API_KEY = "LEIBOT_MARKET_SYNC_API_KEY"

SOURCE_YAHOO = "Yahoo"
SOURCE_IBKR_FALLBACK = "IBKR-Fallback"

STATUS_FRESH = "FRESH"
STATUS_FALLBACK = "FALLBACK"

SYNC_META_KEY = "ibkr_sync_meta"

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
_REQUIRED_INDICATORS = ("sma", "dist_pct", "rebound_pct")


def get_market_sync_api_key() -> str:
    return (os.environ.get(ENV_API_KEY) or "").strip()


def market_sync_api_key_configured() -> bool:
    return len(get_market_sync_api_key()) >= 16


def verify_market_sync_bearer(auth_header: str | None) -> bool:
    expected = get_market_sync_api_key()
    if not expected or len(expected) < 16:
        log.warning("market sync auth failed: key not configured")
        return False
    if not auth_header or not isinstance(auth_header, str):
        log.warning("market sync auth failed: missing Authorization header")
        return False
    parts = auth_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        log.warning("market sync auth failed: invalid Authorization scheme")
        return False
    ok = secrets.compare_digest(parts[1].strip(), expected)
    if ok:
        log.info("market sync auth ok (key_len=%s)", len(expected))
    else:
        log.warning("market sync auth failed: token mismatch")
    return ok


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _finite_positive(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def _finite_number(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def validate_ibkr_sync_row(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(raw, dict):
        return None, "row is not an object"

    ticker = str(raw.get("ticker") or "").strip().upper()
    if not ticker or not _TICKER_RE.match(ticker):
        return None, "invalid ticker"

    price = _finite_positive(raw.get("price"))
    if price is None:
        return None, "invalid price (null/NaN/<=0)"

    data_date = _parse_date(raw.get("data_date") or raw.get("bar_date"))
    if data_date is None:
        return None, "invalid or missing data_date"

    data_source = str(raw.get("data_source") or SOURCE_IBKR_FALLBACK).strip()
    if data_source != SOURCE_IBKR_FALLBACK:
        return None, f"data_source must be {SOURCE_IBKR_FALLBACK}"

    status = str(raw.get("status") or STATUS_FALLBACK).strip().upper()
    if status != STATUS_FALLBACK:
        return None, f"status must be {STATUS_FALLBACK}"

    nums: dict[str, float] = {}
    for key in _REQUIRED_INDICATORS:
        v = _finite_number(raw.get(key))
        if v is None:
            return None, f"missing/invalid indicator: {key}"
        nums[key] = v

    def _opt(key: str) -> float | None:
        if raw.get(key) is None or raw.get(key) == "":
            return None
        v = _finite_number(raw.get(key))
        return v

    for key in (
        "sma63",
        "dist_sma63_pct",
        "range_63d_low",
        "range_63d_high",
        "range_63d_pos",
        "change_pct",
        "avg_move_pct",
    ):
        if raw.get(key) is None or raw.get(key) == "":
            continue
        if _finite_number(raw.get(key)) is None:
            return None, f"invalid optional field: {key}"

    try:
        sma_period = int(raw.get("sma_period") or 25)
    except (TypeError, ValueError):
        return None, "invalid sma_period"

    row = {
        "ticker": ticker,
        "name": str(raw.get("name") or ticker).strip()[:120],
        "industry": str(raw.get("industry") or ""),
        "sector": str(raw.get("sector") or ""),
        "price": round(price, 4),
        "change_pct": _opt("change_pct"),
        "avg_move_pct": _opt("avg_move_pct"),
        "sma": round(nums["sma"], 4),
        "dist_pct": round(nums["dist_pct"], 4),
        "rebound_pct": round(nums["rebound_pct"], 4),
        "sma_period": sma_period,
        "range_63d_low": _opt("range_63d_low"),
        "range_63d_high": _opt("range_63d_high"),
        "range_63d_pos": _opt("range_63d_pos"),
        "sma63": _opt("sma63"),
        "dist_sma63_pct": _opt("dist_sma63_pct"),
        "trend": raw.get("trend"),
        "asset_type": str(raw.get("asset_type") or "STOCK"),
        "data_source": SOURCE_IBKR_FALLBACK,
        "data_date": data_date.isoformat(),
        "data_status": STATUS_FALLBACK,
        "updated_at": str(raw.get("updated_at") or _utc_now_iso()),
        "ai_note": raw.get("ai_note"),
        "earnings_date": raw.get("earnings_date"),
        "market_cap": _opt("market_cap"),
        "avg_vol_20d": _opt("avg_vol_20d"),
        "rvol": _opt("rvol"),
        "target_1y": _opt("target_1y"),
        "ret_20d": _opt("ret_20d"),
        "ret_63d": _opt("ret_63d"),
        "ret_126d": _opt("ret_126d"),
        "ret_252d": _opt("ret_252d"),
        "avg_dollar_vol": _opt("avg_dollar_vol"),
        "data_quality_status": raw.get("data_quality_status") or "ok",
    }
    return row, None


def _existing_row(conn, ticker: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT ticker, price, updated_at, data_source, data_date, data_status "
        "FROM dashboard_cache WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    return dict(row) if row else None


def _existing_data_date(existing: dict[str, Any] | None) -> date | None:
    if not existing:
        return None
    d = _parse_date(existing.get("data_date"))
    if d is not None:
        return d
    return _parse_date(existing.get("updated_at"))


def decide_merge_action(
    incoming: dict[str, Any],
    existing: dict[str, Any] | None,
) -> tuple[str, str]:
    in_date = _parse_date(incoming.get("data_date"))
    if in_date is None:
        return "REJECT", "incoming data_date unparseable"

    if existing is None:
        return "UPDATE", "ticker missing in production — insert fallback row"

    ex_date = _existing_data_date(existing)
    ex_source = (existing.get("data_source") or SOURCE_YAHOO).strip() or SOURCE_YAHOO

    if ex_date is not None and in_date < ex_date:
        return (
            "SKIP",
            f"incoming data_date {in_date} older than production {ex_date} "
            f"(source={ex_source}) — freshness protection",
        )

    if (
        ex_date is not None
        and in_date == ex_date
        and ex_source == SOURCE_YAHOO
        and _finite_positive(existing.get("price")) is not None
    ):
        return (
            "SKIP",
            f"production already has fresh Yahoo on {ex_date} — IBKR must not replace",
        )

    if ex_date is not None and in_date == ex_date and ex_source == SOURCE_IBKR_FALLBACK:
        return "UPDATE", "replace same-date IBKR-Fallback row"

    if ex_date is None:
        return "UPDATE", "production row lacks data_date — allow IBKR fallback fill"

    if in_date > ex_date:
        return (
            "UPDATE",
            f"incoming {in_date} newer than production {ex_date} (was {ex_source})",
        )

    return "UPDATE", "default allow"


def _count_dashboard(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM dashboard_cache").fetchone()["n"])


def _upsert_ibkr_row(conn, row: dict[str, Any]) -> None:
    """Per-ticker upsert only — never DELETE FROM dashboard_cache."""
    conn.execute(
        """
        INSERT INTO dashboard_cache (
            ticker, name, industry, sector, price, change_pct, avg_move_pct,
            range_63d_low, range_63d_high, range_63d_pos, target_1y,
            sma, dist_pct, rebound_pct, trend, market_cap, avg_vol_20d, rvol,
            sma_period, earnings_date, ai_note, updated_at,
            asset_type, sma63, dist_sma63_pct,
            ret_20d, ret_63d, ret_126d, ret_252d,
            avg_dollar_vol, data_quality_status,
            data_source, data_date, data_status
        ) VALUES (
            :ticker, :name, :industry, :sector, :price, :change_pct, :avg_move_pct,
            :range_63d_low, :range_63d_high, :range_63d_pos, :target_1y,
            :sma, :dist_pct, :rebound_pct, :trend, :market_cap, :avg_vol_20d, :rvol,
            :sma_period, :earnings_date, :ai_note, :updated_at,
            :asset_type, :sma63, :dist_sma63_pct,
            :ret_20d, :ret_63d, :ret_126d, :ret_252d,
            :avg_dollar_vol, :data_quality_status,
            :data_source, :data_date, :data_status
        )
        ON CONFLICT(ticker) DO UPDATE SET
            name = excluded.name,
            industry = excluded.industry,
            sector = excluded.sector,
            price = excluded.price,
            change_pct = excluded.change_pct,
            avg_move_pct = excluded.avg_move_pct,
            range_63d_low = excluded.range_63d_low,
            range_63d_high = excluded.range_63d_high,
            range_63d_pos = excluded.range_63d_pos,
            target_1y = excluded.target_1y,
            sma = excluded.sma,
            dist_pct = excluded.dist_pct,
            rebound_pct = excluded.rebound_pct,
            trend = excluded.trend,
            market_cap = excluded.market_cap,
            avg_vol_20d = excluded.avg_vol_20d,
            rvol = excluded.rvol,
            sma_period = excluded.sma_period,
            earnings_date = excluded.earnings_date,
            ai_note = excluded.ai_note,
            updated_at = excluded.updated_at,
            asset_type = COALESCE(excluded.asset_type, dashboard_cache.asset_type),
            sma63 = excluded.sma63,
            dist_sma63_pct = excluded.dist_sma63_pct,
            ret_20d = excluded.ret_20d,
            ret_63d = excluded.ret_63d,
            ret_126d = excluded.ret_126d,
            ret_252d = excluded.ret_252d,
            avg_dollar_vol = excluded.avg_dollar_vol,
            data_quality_status = excluded.data_quality_status,
            data_source = excluded.data_source,
            data_date = excluded.data_date,
            data_status = excluded.data_status
        """,
        row,
    )


def get_ibkr_sync_meta() -> dict[str, Any]:
    raw = get_setting(SYNC_META_KEY, None)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def process_ibkr_sync(
    payload: dict[str, Any],
    *,
    dry_run: bool | None = None,
    force_fail_after_n: int | None = None,
) -> dict[str, Any]:
    """
    Validate + merge IBKR fallback rows into dashboard_cache.

    Never replace_all / never deletes unrelated tickers.
    Writes run in one SQLite transaction (rollback on failure).
    """
    init_db()
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "sync_status": "FAILED",
            "error": "payload must be a JSON object",
            "decisions": [],
        }

    rows_in = payload.get("rows")
    if not isinstance(rows_in, list):
        return {
            "ok": False,
            "sync_status": "FAILED",
            "error": "payload.rows must be a list",
            "decisions": [],
        }

    if len(rows_in) > 50:
        return {
            "ok": False,
            "sync_status": "FAILED",
            "error": "Phase 5 limit: max 50 rows per request",
            "decisions": [],
        }

    if dry_run is None:
        dry_run = bool(payload.get("dry_run"))

    decisions: list[dict[str, Any]] = []
    to_update: list[dict[str, Any]] = []
    rejected = 0
    skipped = 0

    with get_conn() as conn:
        before_count = _count_dashboard(conn)

        for raw in rows_in:
            normalized, err = validate_ibkr_sync_row(raw)
            if err or normalized is None:
                rejected += 1
                ticker = ""
                if isinstance(raw, dict):
                    ticker = str(raw.get("ticker") or "").upper()
                decisions.append(
                    {
                        "ticker": ticker or None,
                        "decision": "REJECT",
                        "reason": err or "invalid",
                        "existing_data_source": None,
                        "existing_data_date": None,
                        "incoming_data_source": None,
                        "incoming_data_date": None,
                    }
                )
                continue

            existing = _existing_row(conn, normalized["ticker"])
            action, reason = decide_merge_action(normalized, existing)
            decisions.append(
                {
                    "ticker": normalized["ticker"],
                    "decision": action,
                    "reason": reason,
                    "existing_data_source": (existing or {}).get("data_source")
                    or (SOURCE_YAHOO if existing else None),
                    "existing_data_date": (
                        None
                        if not existing
                        else (
                            existing.get("data_date")
                            or str(existing.get("updated_at") or "")[:10]
                        )
                    ),
                    "incoming_data_source": normalized["data_source"],
                    "incoming_data_date": normalized["data_date"],
                    "incoming_price": normalized["price"],
                    "existing_price": None if not existing else existing.get("price"),
                }
            )
            if action == "UPDATE":
                to_update.append(normalized)
            elif action == "SKIP":
                skipped += 1
            else:
                rejected += 1

        if dry_run:
            sync_status = "SUCCESS" if rejected == 0 else "PARTIAL"
            meta = {
                "last_ibkr_update": None,
                "last_server_sync": _utc_now_iso(),
                "sync_status": sync_status,
                "records_received": len(rows_in),
                "records_validated": len(rows_in) - rejected,
                "records_updated": 0,
                "records_rejected": rejected,
                "records_skipped": skipped,
                "dry_run": True,
            }
            return {
                "ok": True,
                "dry_run": True,
                "sync_status": sync_status,
                "rows_before": before_count,
                "rows_after": before_count,
                "records_received": len(rows_in),
                "records_validated": len(rows_in) - rejected,
                "records_updated": 0,
                "records_skipped": skipped,
                "records_rejected": rejected,
                "decisions": decisions,
                "sync_meta": meta,
                "error": None,
            }

        updated = 0
        try:
            if force_fail_after_n is not None and force_fail_after_n >= 0:
                for i, row in enumerate(to_update):
                    if i >= force_fail_after_n:
                        raise RuntimeError(
                            "forced failure for rollback test "
                            f"(after {force_fail_after_n} upserts)"
                        )
                    _upsert_ibkr_row(conn, row)
                    updated += 1
            else:
                for row in to_update:
                    _upsert_ibkr_row(conn, row)
                    updated += 1

            after_count = _count_dashboard(conn)
            if updated and rejected:
                sync_status = "PARTIAL"
            elif not updated and rejected and not skipped:
                sync_status = "FAILED"
            elif not updated and rejected:
                sync_status = "PARTIAL"
            else:
                sync_status = "SUCCESS"

            meta = {
                "last_ibkr_update": _utc_now_iso() if updated else get_ibkr_sync_meta().get(
                    "last_ibkr_update"
                ),
                "last_server_sync": _utc_now_iso(),
                "sync_status": sync_status,
                "records_received": len(rows_in),
                "records_validated": len(rows_in) - rejected,
                "records_updated": updated,
                "records_rejected": rejected,
                "records_skipped": skipped,
                "dry_run": False,
            }
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SYNC_META_KEY, json.dumps(meta)),
            )
            return {
                "ok": sync_status in ("SUCCESS", "PARTIAL"),
                "dry_run": False,
                "sync_status": sync_status,
                "rows_before": before_count,
                "rows_after": after_count,
                "records_received": len(rows_in),
                "records_validated": len(rows_in) - rejected,
                "records_updated": updated,
                "records_skipped": skipped,
                "records_rejected": rejected,
                "decisions": decisions,
                "sync_meta": meta,
                "error": None,
            }
        except Exception as exc:
            log.exception("IBKR sync transaction failed — rolling back")
            try:
                conn.rollback()
            except Exception:
                pass
            after_count = _count_dashboard(conn)
            fail_meta = {
                "last_ibkr_update": get_ibkr_sync_meta().get("last_ibkr_update"),
                "last_server_sync": _utc_now_iso(),
                "sync_status": "FAILED",
                "records_received": len(rows_in),
                "records_validated": len(rows_in) - rejected,
                "records_updated": 0,
                "records_rejected": rejected,
                "records_skipped": skipped,
                "dry_run": False,
                "error": "transaction rolled back",
            }
            try:
                set_setting(SYNC_META_KEY, fail_meta)
            except Exception:
                pass
            return {
                "ok": False,
                "dry_run": False,
                "sync_status": "FAILED",
                "rows_before": before_count,
                "rows_after": after_count,
                "records_received": len(rows_in),
                "records_validated": len(rows_in) - rejected,
                "records_updated": 0,
                "records_skipped": skipped,
                "records_rejected": rejected,
                "decisions": decisions,
                "sync_meta": fail_meta,
                "error": str(exc),
            }
