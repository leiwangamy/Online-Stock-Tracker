"""
Batch-apply production DCF v1.3 + CLV to ALL Watchlist tickers.

Uses the same ensure_valuations / ensure_clvs engines as the Watchlist UI —
single source of truth. Does not alter frozen DCF knobs or CLV haircuts.
"""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

from watchlist_config import MY_WATCHLIST, collect_watchlist_tickers
from clv_engine import ensure_clvs
from db import get_dashboard_by_tickers, init_db
from market_data import compute_row_mos
from valuation_engine import ensure_valuations
import valuation_config as cfg

OUT = Path(__file__).resolve().parent / "data" / "logs" / "watchlist_batch_valuation.txt"

# Original verification set — sample must exclude these
TEST_SET = {"TSLA", "AAPL", "IBM", "DVA", "GOOG"}


def _norm_fail(reason: str | None, *, kind: str) -> str:
    if not reason:
        return f"{kind}: (no reason)"
    r = reason.strip()
    low = r.lower()
    if "share_count_mismatch" in low:
        return "share_count_mismatch"
    if "negative/unstable fcff" in low:
        return "negative/unstable FCFF"
    if "financial company" in low:
        return "financial company / unsupported model"
    if "unsupported quote" in low:
        return "financial company / unsupported model"
    if "missing balance sheet" in low or "missing cash" in low or "missing total liabilities" in low:
        return "missing balance-sheet fields"
    if "missing diluted shares" in low or "missing shares" in low:
        return "missing shares"
    if "insufficient financial" in low:
        return "insufficient financial history"
    if "pending" in low:
        return "pending (cache not filled)"
    if low.startswith("exception:"):
        return "exception"
    return r


def _fmt(v, money: bool = False) -> str:
    if v is None:
        return "—"
    if money:
        return f"${float(v):,.2f}"
    return str(v)


def main() -> None:
    init_db()
    tickers = collect_watchlist_tickers()
    lines: list[str] = []
    lines.append("=" * 88)
    lines.append("WATCHLIST BATCH — DCF v1.3 + CLV (same engines as Watchlist UI)")
    lines.append(
        f"VALUATION_VERSION={cfg.VALUATION_VERSION}  CLV_VERSION={cfg.CLV_VERSION}  "
        f"cache_days DCF={cfg.VALUATION_CACHE_DAYS} CLV={cfg.CLV_CACHE_DAYS}"
    )
    lines.append(f"Tickers ({len(tickers)}): {', '.join(tickers)}")
    lines.append("")

    print(f"Computing DCF for {len(tickers)} tickers (max_new=None)...")
    iv = ensure_valuations(tickers, force=False, max_new=None)
    print(f"Computing CLV for {len(tickers)} tickers (max_new=None)...")
    clv = ensure_clvs(tickers, force=False, max_new=None)
    dash = get_dashboard_by_tickers(tickers)

    dcf_ok = dcf_fail = 0
    clv_pos = clv_zero = clv_fail = 0
    dcf_reasons: Counter[str] = Counter()
    clv_reasons: Counter[str] = Counter()

    rows: list[dict] = []
    for t in tickers:
        vr = iv.get(t)
        cr = clv.get(t)
        drow = dash.get(t) or {"ticker": t}
        price = drow.get("price")
        mos_info = compute_row_mos(
            getattr(vr, "est_value", None) if (vr and getattr(vr, "ok", False)) else None,
            drow,
        )
        warn_fail = []
        if vr is not None and not getattr(vr, "ok", False):
            warn_fail.append(f"DCF:{vr.failure_reason or 'unavailable'}")
            dcf_fail += 1
            dcf_reasons[_norm_fail(vr.failure_reason, kind="DCF")] += 1
        elif vr is not None and vr.ok:
            dcf_ok += 1
        else:
            dcf_fail += 1
            dcf_reasons["DCF: missing result"] += 1
            warn_fail.append("DCF: missing result")

        if cr is not None and getattr(cr, "ok", False) and cr.clv_per_share is not None:
            if float(cr.clv_per_share) == 0.0:
                clv_zero += 1
            else:
                clv_pos += 1
            if cr.warnings:
                warn_fail.extend(cr.warnings)
        elif cr is not None:
            clv_fail += 1
            clv_reasons[_norm_fail(cr.failure_reason, kind="CLV")] += 1
            warn_fail.append(f"CLV:{cr.failure_reason or 'unavailable'}")
            if cr.warnings:
                warn_fail.extend(cr.warnings)
        else:
            clv_fail += 1
            clv_reasons["CLV: missing result"] += 1
            warn_fail.append("CLV: missing result")

        rows.append(
            {
                "ticker": t,
                "price": price,
                "clv": getattr(cr, "clv_per_share", None) if cr else None,
                "bear": getattr(vr, "bear_value", None) if vr else None,
                "base": getattr(vr, "est_value", None) if (vr and vr.ok) else None,
                "bull": getattr(vr, "bull_value", None) if vr else None,
                "mos": mos_info.get("mos_pct"),
                "conf": getattr(vr, "confidence", None) if (vr and vr.ok) else None,
                "bs_date": getattr(cr, "report_date", None) if cr else None,
                "bs_src": getattr(cr, "bs_period_type", None) if cr else None,
                "bs_age": getattr(cr, "bs_age_days", None) if cr else None,
                "warn": "; ".join(warn_fail) if warn_fail else "",
            }
        )

    clv_success_total = clv_pos + clv_zero
    lines.append("--- SUMMARY ---")
    lines.append(f"Total Watchlist tickers: {len(tickers)}")
    lines.append(f"DCF successful: {dcf_ok}")
    lines.append(f"DCF unavailable: {dcf_fail}")
    lines.append(f"CLV successful (incl. $0): {clv_success_total}")
    lines.append(f"CLV > $0: {clv_pos}")
    lines.append(f"CLV = $0: {clv_zero}")
    lines.append(f"CLV unavailable: {clv_fail}")
    lines.append("")
    lines.append("DCF failure_reason counts:")
    if dcf_reasons:
        for k, n in dcf_reasons.most_common():
            lines.append(f"  {n:3d}  {k}")
    else:
        lines.append("  (none)")
    lines.append("CLV failure_reason counts:")
    if clv_reasons:
        for k, n in clv_reasons.most_common():
            lines.append(f"  {n:3d}  {k}")
    else:
        lines.append("  (none)")
    lines.append("")

    # Sample ≥10 non-test tickers
    candidates = [r for r in rows if r["ticker"] not in TEST_SET]
    random.seed(20260814)
    sample_n = min(12, len(candidates))
    sample = random.sample(candidates, sample_n) if candidates else []
    sample.sort(key=lambda r: r["ticker"])

    lines.append(
        f"--- SAMPLE ({sample_n} non-test tickers; excluded {sorted(TEST_SET)}) ---"
    )
    hdr = (
        f"{'Ticker':<8} {'Price':>10} {'CLV':>8} {'Bear':>10} {'Base':>10} "
        f"{'Bull':>10} {'MOS':>8} {'Conf':<6} {'BS Date':<12} {'Src':<10} Warning/Failure"
    )
    lines.append(hdr)
    lines.append("-" * len(hdr) + "-" * 40)
    for r in sample:
        mos_s = "—" if r["mos"] is None else f"{r['mos']:.1f}%"
        lines.append(
            f"{r['ticker']:<8} {_fmt(r['price'], True):>10} {_fmt(r['clv'], True):>8} "
            f"{_fmt(r['bear'], True):>10} {_fmt(r['base'], True):>10} {_fmt(r['bull'], True):>10} "
            f"{mos_s:>8} {(r['conf'] or '—'):<6} {(r['bs_date'] or '—'):<12} "
            f"{(r['bs_src'] or '—'):<10} {r['warn'] or '—'}"
        )

    lines.append("")
    lines.append("--- FULL WATCHLIST TABLE ---")
    lines.append(hdr)
    lines.append("-" * len(hdr) + "-" * 40)
    for r in rows:
        mos_s = "—" if r["mos"] is None else f"{r['mos']:.1f}%"
        lines.append(
            f"{r['ticker']:<8} {_fmt(r['price'], True):>10} {_fmt(r['clv'], True):>8} "
            f"{_fmt(r['bear'], True):>10} {_fmt(r['base'], True):>10} {_fmt(r['bull'], True):>10} "
            f"{mos_s:>8} {(r['conf'] or '—'):<6} {(r['bs_date'] or '—'):<12} "
            f"{(r['bs_src'] or '—'):<10} {r['warn'] or '—'}"
        )
    lines.append("")
    lines.append(f"Mine watchlist: {MY_WATCHLIST}")
    lines.append("Engines: valuation_engine.ensure_valuations + clv_engine.ensure_clvs")
    lines.append("No DCF v1.3 / CLV haircut parameters were modified.")

    text = "\n".join(lines) + "\n"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(text)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
