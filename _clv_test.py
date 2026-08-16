"""CLV test breakdown — prefers latest quarterly BS; independent of DCF."""

from __future__ import annotations

from pathlib import Path

import valuation_config as cfg
from clv_engine import calculate_clv, select_latest_balance_sheet
import yfinance as yf

OUT = Path(__file__).resolve().parent / "data" / "logs" / "clv_v1_1_test.txt"


def m(v) -> str:
    if v is None:
        return "—"
    a = abs(float(v))
    if a >= 1e9:
        return f"${v/1e9:.3f}B"
    if a >= 1e6:
        return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"


def main() -> None:
    lines: list[str] = []
    lines.append("CLV V1.1 TEST — latest Balance Sheet (Quarterly preferred)")
    lines.append(f"CLV_VERSION={cfg.CLV_VERSION}  stale_days={cfg.CLV_BALANCE_SHEET_STALE_DAYS}")
    lines.append(
        f"Recoveries: Cash={cfg.CLV_CASH_RECOVERY} STI={cfg.CLV_MARKETABLE_SECURITIES_RECOVERY} "
        f"AR={cfg.CLV_RECEIVABLE_RECOVERY} Inv={cfg.CLV_INVENTORY_RECOVERY} "
        f"NMktInv={cfg.CLV_NONMARKETABLE_INVESTMENT_RECOVERY} PPE={cfg.CLV_PPE_RECOVERY} GW/Int=0"
    )
    lines.append("")
    for t in ["TSLA", "AAPL", "IBM", "DVA", "GOOG"]:
        tk = yf.Ticker(t)
        sel = select_latest_balance_sheet(tk)
        r = calculate_clv(t, ticker_obj=tk)
        L = r.lines or {}
        lines.append("=" * 72)
        lines.append(
            f"{t}  ok={r.ok}  fail={r.failure_reason}  conf={r.confidence}"
        )
        lines.append(
            f"Balance Sheet Date: {r.report_date}  "
            f"Source: {r.bs_period_type}  "
            f"Age: {r.bs_age_days} days"
        )
        lines.append(
            f"  annual_latest={sel.get('annual_date')}  "
            f"quarterly_latest={sel.get('quarterly_date')}  "
            f"selected={sel.get('period_type')}"
        )
        lines.append(
            f"total_assets={m(r.total_assets)}  adjusted_assets={m(r.adjusted_assets)}  "
            f"total_liabilities={m(r.total_liabilities)}"
        )
        lines.append(
            f"liquidation_equity={m(r.liquidation_equity)}  shares={r.shares}  "
            f"CLV/share={m(r.clv_per_share)}"
        )
        for lab, k, rk, rec in [
            ("Cash", "cash", "cash_adj", cfg.CLV_CASH_RECOVERY),
            (
                "Securities",
                "marketable_securities",
                "marketable_securities_adj",
                cfg.CLV_MARKETABLE_SECURITIES_RECOVERY,
            ),
            ("Receivables", "receivables", "receivables_adj", cfg.CLV_RECEIVABLE_RECOVERY),
            ("Inventory", "inventory", "inventory_adj", cfg.CLV_INVENTORY_RECOVERY),
            (
                "Investments",
                "nonmarketable_investments",
                "nonmarketable_investments_adj",
                cfg.CLV_NONMARKETABLE_INVESTMENT_RECOVERY,
            ),
            ("PP&E", "ppe", "ppe_adj", cfg.CLV_PPE_RECOVERY),
        ]:
            lines.append(f"  {lab}: {m(L.get(k))} ×{rec*100:.0f}% = {m(L.get(rk))}")
        lines.append(
            f"  GW/Intangibles: {m(L.get('goodwill_intangibles'))} ×0% = "
            f"{m(L.get('goodwill_intangibles_adj'))}"
        )
        lines.append(f"missing_fields={r.missing_fields}")
        lines.append(f"warnings={r.warnings}")
        lines.append("")
        print(
            f"{t}: BS={r.report_date} {r.bs_period_type} age={r.bs_age_days}d "
            f"CLV={m(r.clv_per_share)} fail={r.failure_reason}"
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
