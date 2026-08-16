"""
Debug / sanity dump for Valuation Engine V1 (does not change Base Case math).

Usage:
  python valuation_debug.py UTI VCYT UA SEZL DVA
"""

from __future__ import annotations

import math
import sys
import time
from typing import Any

import yfinance as yf

import valuation_config as cfg
from db import get_dashboard_by_tickers, get_intrinsic_values, init_db
from market_data import compute_row_mos, mos_pct
from valuation_engine import (
    _cagr,
    _finite,
    _row,
    _terminal_g_for,
    calculate_cost_of_equity,
    calculate_dcf_value,
    calculate_est_value_per_share,
    calculate_fcff,
    calculate_wacc,
    estimate_growth_path,
    is_eligible_for_dcf,
    normalize_financials,
)


def _fmt(x: Any, pct: bool = False, money: bool = False) -> str:
    v = _finite(x)
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:.2f}%"
    if money:
        if abs(v) >= 1e9:
            return f"${v/1e9:.3f}B"
        if abs(v) >= 1e6:
            return f"${v/1e6:.3f}M"
        return f"${v:,.2f}"
    return f"{v:,.4f}" if abs(v) < 10 else f"{v:,.2f}"


def debug_ticker(ticker: str) -> dict[str, Any]:
    t = ticker.strip().upper()
    tk = yf.Ticker(t)
    fin = normalize_financials(tk)
    info = fin.get("info") or {}
    warnings: list[str] = []
    flags: list[str] = list(fin.get("fallbacks") or [])

    price_yahoo = _finite(info.get("currentPrice") or info.get("regularMarketPrice"))
    dash = get_dashboard_by_tickers([t]).get(t) or {}
    # MOS / display price = Watchlist source (dashboard_cache), not Yahoo valuation snapshot
    price_wl = _finite(dash.get("price"))
    price_source = "dashboard_cache"
    price_as_of = dash.get("updated_at")
    if price_wl is None and price_yahoo is not None:
        price_wl = price_yahoo
        price_source = "yahoo_info_fallback"
        price_as_of = None
        flags.append("price_mos_yahoo_fallback")
    price = price_wl  # used for MOS and Price×Shares display consistency with Watchlist
    if price_yahoo is not None and price_wl is not None and abs(price_yahoo - price_wl) / max(price_wl, 1e-9) > 0.02:
        warnings.append(
            f"Yahoo live ${price_yahoo:.2f} vs Watchlist ${price_wl:.2f} "
            f"(MOS uses Watchlist / {price_source})"
        )

    mcap = fin.get("market_cap")
    shares = fin.get("shares")
    name = info.get("shortName") or info.get("longName") or dash.get("name")
    sector = fin.get("sector")
    industry = fin.get("industry")

    # Price × shares vs mcap (use Yahoo price when available — same snapshot as mcap)
    px_for_mcap = price_yahoo if price_yahoo is not None else price
    if px_for_mcap and shares and shares > 0:
        implied = px_for_mcap * shares
        if mcap and mcap > 0:
            diff = abs(implied - mcap) / mcap
            if diff > 0.15:
                warnings.append(
                    f"Market Cap vs Price×Shares diverge {diff*100:.1f}% "
                    f"(implied={implied:,.0f}, mcap={mcap:,.0f})"
                )
        else:
            warnings.append("Market Cap missing; cannot cross-check Price×Shares")
            flags.append("mcap_missing")

    ok_elig, elig_reason = is_eligible_for_dcf(
        sector=sector, industry=industry, quote_type=fin.get("quote_type")
    )

    ebit_s = fin.get("ebit") or []
    tax_s = fin.get("tax") or []
    da_s = fin.get("da") or []
    cx_s = fin.get("capex") or []
    dwc_s = fin.get("dwc") or []
    rev_s = fin.get("revenue") or []

    ebit0 = ebit_s[0] if ebit_s else None
    tax0 = tax_s[0] if tax_s else None
    if tax0 is None:
        tax0 = cfg.DEFAULT_TAX_RATE
        flags.append("tax_default")
    tax0_c = min(cfg.TAX_RATE_MAX, max(cfg.TAX_RATE_MIN, tax0))
    if tax0_c != tax0:
        flags.append(f"tax_clamped:{tax0}->{tax0_c}")
    da0 = da_s[0] if da_s else None
    cx0 = cx_s[0] if cx_s else None
    dwc0 = dwc_s[0] if dwc_s else None
    nopat = (ebit0 * (1 - tax0_c)) if ebit0 is not None else None
    fcff0 = calculate_fcff(ebit0, tax0_c, da0, cx0, dwc0)

    fcff_hist: list[float | None] = []
    for i in range(min(len(ebit_s), 5)):
        f = calculate_fcff(
            ebit_s[i],
            tax_s[i] if i < len(tax_s) else tax0_c,
            da_s[i] if i < len(da_s) else None,
            cx_s[i] if i < len(cx_s) else None,
            dwc_s[i] if i < len(dwc_s) else None,
        )
        fcff_hist.append(f)

    rev_cagr = _cagr([v for v in rev_s if v][: cfg.GROWTH_LOOKBACK_YEARS + 1]) if rev_s else None
    # fix _cagr expects list with latest first - use raw rev_s
    rev_cagr = _cagr(rev_s[: cfg.GROWTH_LOOKBACK_YEARS + 1]) if rev_s else None
    fcff_cagr = _cagr([v for v in fcff_hist if v is not None and v > 0][: cfg.GROWTH_LOOKBACK_YEARS + 1])
    # better: use fcff_hist with Nones stripped carefully
    fcff_pos = [v for v in fcff_hist if v is not None and v > 0]
    fcff_cagr = _cagr(fcff_hist[: cfg.GROWTH_LOOKBACK_YEARS + 1]) if sum(1 for v in fcff_hist if v and v > 0) >= 2 else None

    beta_raw = fin.get("beta")
    beta_used = beta_raw if beta_raw is not None else cfg.DEFAULT_BETA
    if beta_raw is None:
        flags.append("beta_default")
    beta_clamped = min(cfg.BETA_MAX, max(cfg.BETA_MIN, beta_used))
    if beta_clamped != beta_used:
        flags.append(f"beta_clamped:{beta_used}->{beta_clamped}")

    fb: list[str] = list(flags)
    ke = calculate_cost_of_equity(beta_raw, fb)
    debt = fin.get("debt")
    cash = fin.get("cash")
    kd_meta: dict = {}
    wacc = calculate_wacc(
        beta=beta_raw,
        market_cap=mcap,
        debt=debt,
        tax_rate=tax0_c,
        fallbacks=fb,
        fcff0=float(fcff0) if fcff0 else None,
        kd_meta_out=kd_meta,
    )
    g_term = _terminal_g_for(mcap)

    e = mcap if mcap and mcap > 0 else None
    d = max(0.0, debt or 0.0)
    if e is None:
        we, wd = 1.0, 0.0
    else:
        tot = e + d
        we = e / tot if tot > 0 else 1.0
        wd = d / tot if tot > 0 else 0.0

    growth_path, g_recent, g_notes = estimate_growth_path(
        rev_s, ebit_s, fcff_hist, terminal_g=g_term
    )

    ev = proj = tv = None
    pv_explicit = pv_tv = equity = est = None
    if fcff0 and fcff0 > 0 and wacc > g_term:
        ev, proj, tv = calculate_dcf_value(float(fcff0), growth_path, wacc, g_term)
        if ev is not None and proj:
            pv_explicit = sum(
                f / ((1 + wacc) ** t) for t, f in enumerate(proj, start=1)
            )
            pv_tv = (tv / ((1 + wacc) ** cfg.EXPLICIT_YEARS)) if tv is not None else None
            equity = calculate_est_value_per_share(ev, cash, debt, shares)
            # calculate_est_value_per_share returns per share; also compute equity total
            if shares and shares > 0 and cash is not None:
                equity_total = ev + (cash or 0) - (debt or 0)
                est = equity_total / shares
            else:
                equity_total = None
                est = None
        else:
            equity_total = None
    else:
        equity_total = None

    mos = mos_pct(est, price) if est and price else None
    # Prefer same dynamic MOS rules as Watchlist (row price + stale threshold)
    mos_info = compute_row_mos(
        est,
        {
            "price": price,
            "updated_at": price_as_of,
            "price_source": price_source,
        },
    )
    if mos_info.get("stale"):
        mos = None
        warnings.append(
            f"MOS withheld: {mos_info.get('stale_reason')} "
            f"(limit={mos_info.get('stale_hours_limit')}h; Est.Value unchanged)"
        )
    else:
        mos = mos_info.get("mos_pct")

    # Scenarios (same engine as Watchlist)
    from valuation_engine import calculate_valuation_scenarios

    scenarios = None
    if fcff0 and fcff0 > 0 and growth_path and wacc > g_term:
        scenarios = calculate_valuation_scenarios(
            fcff0=float(fcff0),
            base_path=growth_path,
            base_wacc=wacc,
            base_g=g_term,
            cash=cash,
            debt=debt,
            shares=shares,
        )

    # Sanity checks
    if est is not None and est <= 0:
        warnings.append("INVALID: Est.Value <= 0")
    if wacc <= g_term:
        warnings.append("INVALID: WACC <= terminal g")
    if shares is not None and shares <= 0:
        warnings.append("INVALID: shares <= 0")
    if est and price and price > 0:
        ratio = est / price
        if ratio < 0.1 or ratio > 10:
            warnings.append(f"WARNING: Est/Price={ratio:.2f} extreme")
    if ev and tv and pv_tv is not None and ev > 0:
        tv_share = pv_tv / ev
        if tv_share > 0.85:
            warnings.append(f"WARNING: Terminal PV is {tv_share*100:.0f}% of EV")
    if ev and debt and debt > 0.7 * ev:
        warnings.append("WARNING: Debt large vs Enterprise Value")
    if cash is not None and ebit0 is not None and abs(cash) > 0 and abs(ebit0) > 0:
        # unit mismatch heuristic: cash orders of magnitude off vs ebit
        if abs(math.log10(abs(cash) + 1) - math.log10(abs(ebit0) + 1)) > 4:
            warnings.append("WARNING: possible unit mismatch Cash vs EBIT scale")
    pos = [v for v in fcff_hist[:4] if v is not None]
    if len(pos) >= 2:
        signs = sum(1 for v in pos if v > 0)
        if signs < len(pos) / 2:
            warnings.append("WARNING: FCFF unstable / mostly negative")

    # Cached vs live
    cached = get_intrinsic_values([t]).get(t) or {}

    table = {
        "ticker": t,
        "company_name": name,
        "sector": sector,
        "industry": industry,
        "quote_type": fin.get("quote_type"),
        "eligible": ok_elig,
        "elig_reason": elig_reason,
        "1_current_price": price,
        "1b_yahoo_live_price": price_yahoo,
        "1c_price_source": price_source,
        "1d_price_as_of": price_as_of,
        "2_market_cap": mcap,
        "3_diluted_shares": shares,
        "price_x_shares": (px_for_mcap * shares) if px_for_mcap and shares else None,
        "4_revenue_latest": rev_s[0] if rev_s else None,
        "5_ebit_latest": ebit0,
        "6_effective_tax_rate": tax0_c,
        "7_ebit_x_1_t": nopat,
        "8_da": da0,
        "9_capex_raw_yahoo": cx0,
        "9b_capex_spend_abs": abs(cx0) if cx0 is not None else None,
        "10_change_in_wc_yahoo": dwc0,
        "11_base_fcff": fcff0,
        "fcff_hist": fcff_hist,
        "12_rev_cagr": rev_cagr,
        "13_fcff_cagr": fcff_cagr,
        "14_growth_path": growth_path,
        "g_recent": g_recent,
        "g_notes": g_notes,
        "15_beta_raw": beta_raw,
        "15b_beta_clamped": beta_clamped,
        "16_rf": cfg.RISK_FREE_RATE,
        "17_erp": cfg.EQUITY_RISK_PREMIUM,
        "18_ke": ke,
        "19_kd_pretax": kd_meta.get("kd_pre", cfg.DEFAULT_PRE_TAX_COST_OF_DEBT),
        "19b_kd_after_tax": kd_meta.get("kd_after_tax"),
        "19c_kd_meta": kd_meta,
        "20_debt": debt,
        "21_cash": cash,
        "22_equity_weight": we,
        "23_debt_weight": wd,
        "24_wacc": wacc,
        "25_terminal_g": g_term,
        "26_fcff_y1_y5": proj,
        "27_terminal_value": tv,
        "28_pv_explicit_fcff": pv_explicit,
        "29_pv_terminal": pv_tv,
        "30_enterprise_value": ev,
        "31_equity_value": equity_total,
        "32_shares": shares,
        "33_est_value_share": round(est, 2) if est else None,
        "34_current_price": price,
        "35_mos_pct": mos,
        "mos_stale": mos_info.get("stale"),
        "mos_stale_reason": mos_info.get("stale_reason"),
        "mos_age_hours": mos_info.get("age_hours"),
        "scenarios": scenarios,
        "36_confidence_inputs": {
            "history_ebit_years": sum(1 for v in ebit_s if v is not None),
            "positive_fcff_years": sum(1 for v in fcff_hist[:4] if v and v > 0),
        },
        "37_flags": fb,
        "warnings": warnings,
        "cached_est": cached.get("est_value"),
        "cached_fail": cached.get("failure_reason"),
        "dashboard_name": dash.get("name"),
        "dashboard_price": dash.get("price"),
    }
    return table


def print_report(d: dict[str, Any]) -> None:
    t = d["ticker"]
    print("=" * 72)
    print(f"{t}  |  {d.get('company_name')}  |  {d.get('sector')} / {d.get('industry')}")
    print(f"Eligible: {d['eligible']}  reason={d.get('elig_reason')}")
    print(f"Dashboard cache: name={d.get('dashboard_name')!r} price={d.get('dashboard_price')}")
    print(f"Cached Est.Value={d.get('cached_est')} fail={d.get('cached_fail')}")
    print("-" * 72)
    rows = [
        ("1 Current Price (MOS/WL)", _fmt(d["1_current_price"], money=True)),
        ("1b Yahoo live (ref only)", _fmt(d.get("1b_yahoo_live_price"), money=True)),
        ("1c Price source", str(d.get("1c_price_source"))),
        ("1d Price as_of", str(d.get("1d_price_as_of") or "—")),
        ("2 Market Cap", _fmt(d["2_market_cap"], money=True)),
        ("3 Diluted Shares", _fmt(d["3_diluted_shares"])),
        ("   Price × Shares", _fmt(d["price_x_shares"], money=True)),
        ("4 Revenue (latest)", _fmt(d["4_revenue_latest"], money=True)),
        ("5 EBIT", _fmt(d["5_ebit_latest"], money=True)),
        ("6 Effective Tax Rate", _fmt(d["6_effective_tax_rate"], pct=True)),
        ("7 EBIT*(1-T)", _fmt(d["7_ebit_x_1_t"], money=True)),
        ("8 D&A", _fmt(d["8_da"], money=True)),
        ("9 CapEx (Yahoo raw)", _fmt(d["9_capex_raw_yahoo"], money=True)),
        ("9b CapEx spend |abs|", _fmt(d["9b_capex_spend_abs"], money=True)),
        ("10 Chg WC (Yahoo CF)", _fmt(d["10_change_in_wc_yahoo"], money=True)),
        ("11 Base FCFF", _fmt(d["11_base_fcff"], money=True)),
        ("   FCFF hist", str(d["fcff_hist"])),
        ("12 Revenue CAGR", _fmt(d["12_rev_cagr"], pct=True)),
        ("13 FCFF CAGR", _fmt(d["13_fcff_cagr"], pct=True)),
        ("14 5Y Growth Path", ", ".join(_fmt(g, pct=True) for g in (d["14_growth_path"] or []))),
        ("   g_recent / notes", f"{_fmt(d.get('g_recent'), pct=True)} | {d.get('g_notes')}"),
        ("15 Beta raw -> clamp", f"{d['15_beta_raw']} -> {d['15b_beta_clamped']}"),
        ("16 Risk Free Rate", _fmt(d["16_rf"], pct=True)),
        ("17 Equity Risk Premium", _fmt(d["17_erp"], pct=True)),
        ("18 Cost of Equity Ke", _fmt(d["18_ke"], pct=True)),
        ("19 Kd pre-tax / after-tax", f"{_fmt(d['19_kd_pretax'], pct=True)} / {_fmt(d.get('19b_kd_after_tax'), pct=True)}"),
        ("   Kd meta", str({k: d.get('19c_kd_meta', {}).get(k) for k in ('source','de','debt_fcff','de_tier','debt_fcff_tier','spread','high_leverage','kd_default')})),
        ("20 Debt", _fmt(d["20_debt"], money=True)),
        ("21 Cash", _fmt(d["21_cash"], money=True)),
        ("22 Equity Weight", _fmt(d["22_equity_weight"], pct=True)),
        ("23 Debt Weight", _fmt(d["23_debt_weight"], pct=True)),
        ("24 WACC", _fmt(d["24_wacc"], pct=True)),
        ("25 Terminal g", _fmt(d["25_terminal_g"], pct=True)),
        ("26 FCFF Y1-Y5", ", ".join(_fmt(x, money=True) for x in (d["26_fcff_y1_y5"] or [])) or "—"),
        ("27 Terminal Value", _fmt(d["27_terminal_value"], money=True)),
        ("28 PV Explicit FCFF", _fmt(d["28_pv_explicit_fcff"], money=True)),
        ("29 PV Terminal", _fmt(d["29_pv_terminal"], money=True)),
        ("30 Enterprise Value", _fmt(d["30_enterprise_value"], money=True)),
        ("31 Equity Value", _fmt(d["31_equity_value"], money=True)),
        ("32 Shares", _fmt(d["32_shares"])),
        ("33 Est.Value / share", _fmt(d["33_est_value_share"], money=True)),
        ("34 Current Price (MOS)", _fmt(d["34_current_price"], money=True)),
        ("35 MOS%", f"{d['35_mos_pct']:+.1f}%" if d["35_mos_pct"] is not None else ("— (stale)" if d.get("mos_stale") else "—")),
        ("   MOS age / reason", f"{d.get('mos_age_hours')}h | {d.get('mos_stale_reason') or 'ok'}"),
        ("36 Confidence inputs", str(d["36_confidence_inputs"])),
        ("37 Flags", ", ".join(d["37_flags"]) or "(none)"),
    ]
    for k, v in rows:
        print(f"{k:28s} {v}")
    sc = d.get("scenarios") or {}
    if sc:
        print("-" * 72)
        print("BEAR / BASE / BULL")
        print(
            f"  Bear: ${sc.get('bear')}  WACC={_fmt(sc.get('bear_wacc'), pct=True)}  "
            f"g={_fmt(sc.get('bear_g'), pct=True)}  "
            f"path={', '.join(_fmt(x, pct=True) for x in (sc.get('bear_path') or []))}"
        )
        print(
            f"  Base: ${sc.get('base')}  WACC={_fmt(d.get('24_wacc'), pct=True)}  "
            f"g={_fmt(d.get('25_terminal_g'), pct=True)}  "
            f"path={', '.join(_fmt(x, pct=True) for x in (d.get('14_growth_path') or []))}"
        )
        print(
            f"  Bull: ${sc.get('bull')}  WACC={_fmt(sc.get('bull_wacc'), pct=True)}  "
            f"g={_fmt(sc.get('bull_g'), pct=True)}  "
            f"path={', '.join(_fmt(x, pct=True) for x in (sc.get('bull_path') or []))}"
        )
        print(f"  Spread={(sc.get('spread') or 0)*100:.1f}%  high_sens={sc.get('high_sensitivity')}")
    if d["warnings"]:
        print("SANITY:")
        for w in d["warnings"]:
            print(f"  ! {w}")
    else:
        print("SANITY: (no auto warnings)")
    print()


def main(argv: list[str]) -> int:
    init_db()
    tickers = argv or ["UTI", "VCYT", "UA", "SEZL", "DVA"]
    for i, t in enumerate(tickers):
        if i:
            time.sleep(1.0)
        try:
            print_report(debug_ticker(t))
        except Exception as exc:
            print(f"{t}: DEBUG FAILED: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
