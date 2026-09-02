"""
LeiBot IBKR Local Sync — Phase 4 (indicator compatibility, LOCAL ONLY).

Architecture (final design — not production-wired yet):

  Yahoo Primary
      ↓ validate per ticker
  Fresh / valid → keep Yahoo
      ↓
  Failed / missing / stale → IBKR-Fallback only for those tickers
      ↓
  Same LeiBot indicator engine (market_data.compute_indicators_from_closes)
      ↓
  Merge into LeiBot dataset with per-ticker source label

Safety: no production sync, no Yahoo scheduler changes, no orders, no DB migration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from ibkr_local import (
    DEFAULT_CLIENT_ID,
    DEFAULT_HOST,
    DEFAULT_PAPER_PORT,
    DEFAULT_TEST_TICKERS,
    _env_client_id,
    _env_host,
    _env_port,
)
from ibkr_local.bars import fetch_ibkr_daily_closes
from market_data import compute_indicators_from_closes, load_yahoo_daily_closes


SOURCE_YAHOO = "Yahoo"
SOURCE_IBKR_FALLBACK = "IBKR-Fallback"

STATUS_FRESH = "FRESH"
STATUS_FALLBACK = "FALLBACK"
STATUS_STALE = "STALE"
STATUS_FAILED = "FAILED"


@dataclass
class TickerSourceDesign:
    """Future per-ticker provenance (design/preview only — not persisted)."""

    ticker: str
    price: float | None
    data_source: str
    data_date: str | None
    status: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


PROPOSED_FALLBACK_RULES: list[dict[str, str]] = [
    {
        "id": "yahoo_request_failed",
        "when": "Yahoo history/request fails or returns empty after retries",
        "action": "Add ticker to IBKR fallback list only",
    },
    {
        "id": "price_missing_invalid",
        "when": "Latest price is null / NaN / <= 0 / non-finite",
        "action": "Add ticker to IBKR fallback list only",
    },
    {
        "id": "latest_daily_bar_missing",
        "when": "No usable latest daily bar/close after Yahoo load",
        "action": "Add ticker to IBKR fallback list only",
    },
    {
        "id": "expected_trading_date_missing",
        "when": (
            "Latest Yahoo bar date is before the last expected US equity session "
            "(lag >= 1 trading day vs expected calendar)"
        ),
        "action": "Mark STALE; add ticker to IBKR fallback list only",
    },
    {
        "id": "insufficient_bars",
        "when": "Fewer bars than required for configured SMA / rebound window",
        "action": "Add ticker to IBKR fallback list only",
    },
    {
        "id": "data_quality_block",
        "when": "Existing LeiBot quality gate blocks the Yahoo row",
        "action": "Try IBKR fallback if IBKR series passes the same gate",
    },
]

PROPOSED_NON_TRIGGERS: list[str] = [
    "Do NOT fallback only because IBKR and Yahoo prices differ by a small normal quote gap when both share the same latest trading date.",
    "Do NOT request IBKR for tickers whose Yahoo bars are already FRESH and valid.",
    "Do NOT replace Yahoo globally — IBKR fills gaps per ticker only.",
]


def _pct_rel(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round((float(a) / float(b) - 1.0) * 100.0, 4)


def _abs_diff(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(float(a) - float(b), 4)


def _classify_diff(
    *,
    ibkr_date: str | None,
    yahoo_date: str | None,
    price_diff_pct: float | None,
    sma_diffs: list[float | None],
) -> list[str]:
    reasons: list[str] = []
    if ibkr_date and yahoo_date and ibkr_date != yahoo_date:
        reasons.append("Different latest trading date")
    max_sma = max((abs(x) for x in sma_diffs if x is not None), default=0.0)
    if price_diff_pct is not None and abs(price_diff_pct) < 0.05 and max_sma < 0.05:
        reasons.append("Rounding / micro quote noise")
    elif price_diff_pct is not None and abs(price_diff_pct) >= 0.05:
        if max_sma > 0.5:
            reasons.append(
                "Possible adjusted vs unadjusted / dividend path divergence "
                "(TRADES vs ADJUSTED_LAST vs Yahoo split-only Close)"
            )
        else:
            reasons.append(
                "Small source price-path difference "
                "(session / last print / TRADES vs Yahoo Close)"
            )
    if not reasons:
        reasons.append("None material")
    return reasons


def _normalize_index(idx: pd.Index) -> pd.DatetimeIndex:
    di = pd.DatetimeIndex(idx)
    if di.tz is not None:
        di = di.tz_convert("UTC").tz_localize(None)
    return di.normalize()


def _align_closes(
    ibkr: pd.Series, yahoo: pd.Series
) -> tuple[pd.Series, pd.Series, int]:
    if ibkr is None or yahoo is None or ibkr.empty or yahoo.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float), 0
    a = ibkr.copy()
    b = yahoo.copy()
    a.index = _normalize_index(a.index)
    b.index = _normalize_index(b.index)
    common = a.index.intersection(b.index)
    if len(common) == 0:
        return pd.Series(dtype=float), pd.Series(dtype=float), 0
    return a.loc[common], b.loc[common], int(len(common))


def run_phase4_indicator_compare(
    tickers: list[str] | tuple[str, ...] | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    client_id: int | None = None,
    sma_period: int = 25,
    rebound_lookback: int = 25,
) -> dict[str, Any]:
    symbols = [
        (t or "").strip().upper()
        for t in (tickers or DEFAULT_TEST_TICKERS)
        if (t or "").strip()
    ] or list(DEFAULT_TEST_TICKERS)

    host = host or _env_host() or DEFAULT_HOST
    port = int(port if port is not None else (_env_port() or DEFAULT_PAPER_PORT))
    client_id = int(
        client_id if client_id is not None else (_env_client_id() or DEFAULT_CLIENT_ID)
    )

    ibkr_map = fetch_ibkr_daily_closes(
        symbols,
        host=host,
        port=port,
        client_id=client_id,
        duration="2 Y",
        what_to_show="TRADES",
    )

    rows: list[dict[str, Any]] = []
    design_rows: list[dict[str, Any]] = []

    for sym in symbols:
        ib_pack = ibkr_map.get(sym) or {}
        ib_closes: pd.Series = ib_pack.get("closes")
        if ib_closes is None:
            ib_closes = pd.Series(dtype=float)

        y_closes, _hist, _meta = load_yahoo_daily_closes(sym, period="2y")
        if y_closes is None:
            y_closes = pd.Series(dtype=float)

        ib_ind = compute_indicators_from_closes(
            ib_closes,
            sma_period=sma_period,
            rebound_lookback=rebound_lookback,
            source="IBKR",
        )
        y_ind = compute_indicators_from_closes(
            y_closes,
            sma_period=sma_period,
            rebound_lookback=rebound_lookback,
            source="Yahoo",
        )

        ib_c, y_c, n_common = _align_closes(ib_closes, y_closes)
        ib_aligned = compute_indicators_from_closes(
            ib_c,
            sma_period=sma_period,
            rebound_lookback=rebound_lookback,
            source="IBKR-aligned",
        )
        y_aligned = compute_indicators_from_closes(
            y_c,
            sma_period=sma_period,
            rebound_lookback=rebound_lookback,
            source="Yahoo-aligned",
        )

        price_diff = _pct_rel(ib_ind.get("latest_price"), y_ind.get("latest_price"))
        sma_diffs = [
            _pct_rel(ib_ind.get("sma25"), y_ind.get("sma25")),
            _pct_rel(ib_ind.get("sma50"), y_ind.get("sma50")),
            _pct_rel(ib_ind.get("sma63"), y_ind.get("sma63")),
            _pct_rel(ib_ind.get("sma90"), y_ind.get("sma90")),
        ]
        reasons = _classify_diff(
            ibkr_date=ib_ind.get("latest_bar_date"),
            yahoo_date=y_ind.get("latest_bar_date"),
            price_diff_pct=price_diff,
            sma_diffs=sma_diffs,
        )
        if ib_pack.get("error"):
            reasons.insert(0, f"IBKR fetch: {ib_pack.get('error')}")

        y_date = y_ind.get("latest_bar_date")
        ib_date = ib_ind.get("latest_bar_date")
        if y_ind.get("ok") and y_date and ib_date and y_date >= ib_date:
            design = TickerSourceDesign(
                ticker=sym,
                price=y_ind.get("latest_price"),
                data_source=SOURCE_YAHOO,
                data_date=y_date,
                status=STATUS_FRESH,
                reason="Yahoo fresh/valid — IBKR would NOT be requested",
            )
        elif y_ind.get("ok") and y_date and ib_date and y_date < ib_date:
            design = TickerSourceDesign(
                ticker=sym,
                price=y_ind.get("latest_price"),
                data_source=SOURCE_YAHOO,
                data_date=y_date,
                status=STATUS_STALE,
                reason="Yahoo bar date behind IBKR — candidate for IBKR-Fallback",
            )
        elif not y_ind.get("ok") and ib_ind.get("ok"):
            design = TickerSourceDesign(
                ticker=sym,
                price=ib_ind.get("latest_price"),
                data_source=SOURCE_IBKR_FALLBACK,
                data_date=ib_date,
                status=STATUS_FALLBACK,
                reason="Yahoo failed; IBKR indicators available",
            )
        else:
            design = TickerSourceDesign(
                ticker=sym,
                price=None,
                data_source=SOURCE_YAHOO,
                data_date=y_date,
                status=STATUS_FAILED,
                reason="Insufficient data on one or both sides",
            )

        rows.append(
            {
                "ticker": sym,
                "ibkr_fetch_ok": bool(ib_pack.get("ok")),
                "ibkr_what_to_show": ib_pack.get("what_to_show"),
                "ibkr_bars": int(ib_pack.get("bars") or 0),
                "yahoo_bars": int(len(y_closes)),
                "common_bars": n_common,
                "ibkr": ib_ind,
                "yahoo": y_ind,
                "ibkr_aligned": ib_aligned,
                "yahoo_aligned": y_aligned,
                "diff": {
                    "price_pct": price_diff,
                    "sma25_pct": sma_diffs[0],
                    "sma50_pct": sma_diffs[1],
                    "sma63_pct": sma_diffs[2],
                    "sma90_pct": sma_diffs[3],
                    "dist_sma25_pp": _abs_diff(
                        ib_ind.get("dist_sma25_pct"), y_ind.get("dist_sma25_pct")
                    ),
                    "rebound_pp": _abs_diff(
                        ib_ind.get("rebound_25d_pct"), y_ind.get("rebound_25d_pct")
                    ),
                    "pos_63d_pp": _abs_diff(
                        ib_ind.get("pos_63d_pct"), y_ind.get("pos_63d_pct")
                    ),
                    "aligned_sma25_pct": _pct_rel(
                        ib_aligned.get("sma25"), y_aligned.get("sma25")
                    ),
                    "aligned_price_pct": _pct_rel(
                        ib_aligned.get("latest_price"), y_aligned.get("latest_price")
                    ),
                },
                "difference_reasons": reasons,
                "source_design": design.as_dict(),
            }
        )
        design_rows.append(design.as_dict())

    material: list[str] = []
    for r in rows:
        d = r["diff"]
        flags = []
        for k in ("sma25_pct", "sma50_pct", "sma63_pct", "sma90_pct", "price_pct"):
            v = d.get(k)
            if v is not None and abs(v) >= 0.15:
                flags.append(f"{k}={v:+.4f}%")
        if flags:
            material.append(f"{r['ticker']}: " + ", ".join(flags))

    same_engine_ok = all(r["ibkr"].get("ok") and r["yahoo"].get("ok") for r in rows)
    if same_engine_ok and not material:
        compat = (
            "COMPATIBLE — same LeiBot formulas on IBKR TRADES and Yahoo closes "
            "produce near-identical indicators on this test set."
        )
    elif same_engine_ok:
        compat = (
            "MOSTLY COMPATIBLE — same engine runs on both feeds; see per-ticker "
            "difference reasons (dates / adjustment / quote path)."
        )
    else:
        compat = "INCOMPLETE — one or more feeds failed to produce indicators."

    return {
        "ok": bool(rows) and any(r["ibkr"].get("ok") for r in rows),
        "phase": 4,
        "mode": "indicator_compatibility_local",
        "architecture": {
            "primary": "Yahoo",
            "fallback": "IBKR-Fallback (gaps only)",
            "engine": "market_data.compute_indicators_from_closes",
            "dist_formula": "(price / SMA - 1) * 100",
        },
        "host": host,
        "port": port,
        "client_id": client_id,
        "readonly": True,
        "sma_period": sma_period,
        "rebound_lookback": rebound_lookback,
        "rows": rows,
        "source_design_preview": design_rows,
        "proposed_fallback_rules": PROPOSED_FALLBACK_RULES,
        "proposed_non_triggers": PROPOSED_NON_TRIGGERS,
        "compatibility": compat,
        "material_diffs": material,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Yahoo remains LeiBot's primary market-data source.",
            "IBKR whatToShow=TRADES (not ADJUSTED_LAST) to align with Yahoo split-adjusted Close.",
            "No production sync, no Yahoo workflow changes, no orders, no DB migration.",
            "Per-ticker source tracking is design/preview only in Phase 4.",
        ],
    }


def format_phase4_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=== LeiBot IBKR Phase 4 - Indicator compatibility (LOCAL) ===")
    lines.append(
        f"Host: {report.get('host')}  Port: {report.get('port')}  "
        f"ClientId: {report.get('client_id')}  ReadOnly: {report.get('readonly')}"
    )
    lines.append(
        f"Engine: {report.get('architecture', {}).get('engine')}  "
        f"Primary: Yahoo  Fallback: IBKR gaps only"
    )
    lines.append(f"OK: {report.get('ok')}")
    lines.append("")
    lines.append(
        "Ticker | IBKR Date | Yahoo Date | IBKR Price | Yahoo Price | "
        "IBKR SMA25 | Yahoo SMA25 | Diff% | "
        "IBKR SMA50 | Yahoo SMA50 | Diff% | "
        "IBKR SMA63 | Yahoo SMA63 | Diff% | "
        "IBKR SMA90 | Yahoo SMA90 | Diff% | "
        "IBKR Dist25 | Yahoo Dist25 | Diff | "
        "IBKR Rebound | Yahoo Rebound | Diff | "
        "IBKR Pos63 | Yahoo Pos63 | Diff"
    )
    lines.append("-" * 120)

    for r in report.get("rows") or []:
        ib, y, d = r["ibkr"], r["yahoo"], r["diff"]
        lines.append(
            f"{r['ticker']} | {ib.get('latest_bar_date')} | {y.get('latest_bar_date')} | "
            f"{ib.get('latest_price')} | {y.get('latest_price')} | "
            f"{ib.get('sma25')} | {y.get('sma25')} | {d.get('sma25_pct')} | "
            f"{ib.get('sma50')} | {y.get('sma50')} | {d.get('sma50_pct')} | "
            f"{ib.get('sma63')} | {y.get('sma63')} | {d.get('sma63_pct')} | "
            f"{ib.get('sma90')} | {y.get('sma90')} | {d.get('sma90_pct')} | "
            f"{ib.get('dist_sma25_pct')} | {y.get('dist_sma25_pct')} | {d.get('dist_sma25_pp')} | "
            f"{ib.get('rebound_25d_pct')} | {y.get('rebound_25d_pct')} | {d.get('rebound_pp')} | "
            f"{ib.get('pos_63d_pct')} | {y.get('pos_63d_pct')} | {d.get('pos_63d_pp')}"
        )

    lines.append("")
    lines.append("=== Detail / difference classification ===")
    for r in report.get("rows") or []:
        lines.append(f"--- {r['ticker']} ---")
        lines.append(
            f"  IBKR bars={r.get('ibkr_bars')} what={r.get('ibkr_what_to_show')}  "
            f"Yahoo bars={r.get('yahoo_bars')}  common={r.get('common_bars')}"
        )
        lines.append(
            f"  Aligned price diff%={r['diff'].get('aligned_price_pct')}  "
            f"aligned SMA25 diff%={r['diff'].get('aligned_sma25_pct')}"
        )
        lines.append(
            f"  IBKR low25={r['ibkr'].get('low_25d')} high63={r['ibkr'].get('high_63d')} "
            f"low63={r['ibkr'].get('low_63d')}"
        )
        lines.append(
            f"  Yahoo low25={r['yahoo'].get('low_25d')} high63={r['yahoo'].get('high_63d')} "
            f"low63={r['yahoo'].get('low_63d')}"
        )
        lines.append(f"  Reasons: {', '.join(r.get('difference_reasons') or [])}")
        sd = r.get("source_design") or {}
        lines.append(
            f"  Design source preview: {sd.get('data_source')}  "
            f"status={sd.get('status')}  date={sd.get('data_date')}  "
            f"({sd.get('reason')})"
        )

    lines.append("")
    lines.append("=== Proposed Yahoo stale/failure -> IBKR fallback rules (design) ===")
    for rule in report.get("proposed_fallback_rules") or []:
        lines.append(f"  [{rule['id']}] when: {rule['when']}")
        lines.append(f"           action: {rule['action']}")
    lines.append("  Non-triggers:")
    for t in report.get("proposed_non_triggers") or []:
        lines.append(f"    - {t}")

    lines.append("")
    lines.append(f"COMPATIBILITY: {report.get('compatibility')}")
    if report.get("material_diffs"):
        lines.append("Material diffs: " + "; ".join(report["material_diffs"]))
    for nte in report.get("notes") or []:
        lines.append(f"NOTE: {nte}")
    lines.append("Phase 4 complete - no production sync, no Yahoo changes, no orders.")
    return "\n".join(lines)
