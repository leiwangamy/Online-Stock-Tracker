"""
One-shot CURRENT BASELINE SNAPSHOT for Valuation Engine v1.3.
Read-only — does not change valuation formulas/config.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yfinance as yf

import valuation_config as cfg
from db import get_dashboard_by_tickers, init_db
from market_data import compute_row_mos
from valuation_engine import (
    _apply_spread_to_confidence,
    _cap_confidence_at,
    _finite,
    _series_cagr,
    _terminal_g_for,
    calculate_confidence,
    calculate_cost_of_equity,
    calculate_dcf_value,
    calculate_est_value_per_share,
    calculate_fcff,
    calculate_valuation_scenarios,
    calculate_wacc,
    check_share_count_integrity,
    estimate_growth_path,
    is_eligible_for_dcf,
    normalize_financials,
)

OUT = Path(__file__).resolve().parent / "data" / "logs" / "valuation_v1_3_baseline_audit.txt"
TICKERS = ["INTU", "AAPL", "IBM", "DVA", "TSLA", "GOOG"]


def _money(v: Any) -> str:
    x = _finite(v)
    if x is None:
        return "—"
    ax = abs(x)
    if ax >= 1e12:
        return f"${x / 1e12:.3f}T"
    if ax >= 1e9:
        return f"${x / 1e9:.3f}B"
    if ax >= 1e6:
        return f"${x / 1e6:.3f}M"
    return f"${x:,.2f}"


def _pct(v: Any) -> str:
    x = _finite(v)
    if x is None:
        return "—"
    return f"{x * 100:.2f}%"


def _series_money(xs: list[Any]) -> str:
    return ", ".join(_money(v) for v in xs) if xs else "—"


def audit_one(ticker: str) -> dict[str, Any]:
    t = ticker.upper()
    fin = normalize_financials(yf.Ticker(t))
    info = fin.get("info") or {}
    dash = get_dashboard_by_tickers([t]).get(t) or {}

    name = info.get("shortName") or info.get("longName") or dash.get("name")
    sector = fin.get("sector")
    industry = fin.get("industry")
    ok_elig, elig_reason = is_eligible_for_dcf(
        sector=sector, industry=industry, quote_type=fin.get("quote_type")
    )

    price_yahoo = _finite(info.get("currentPrice") or info.get("regularMarketPrice"))
    price_wl = _finite(dash.get("price"))
    price_source = "dashboard_cache" if price_wl is not None else "yahoo_info_fallback"
    price_as_of = dash.get("updated_at")
    if price_wl is None:
        price_wl = price_yahoo
        price_as_of = None
        price_source = "yahoo_info_fallback"

    rev_s = fin.get("revenue") or []
    ebit_s = fin.get("ebit") or []
    tax_s = fin.get("tax") or []
    da_s = fin.get("da") or []
    cx_s = fin.get("capex") or []
    dwc_s = fin.get("dwc") or []

    tax0 = tax_s[0] if tax_s else None
    if tax0 is None:
        tax0 = cfg.DEFAULT_TAX_RATE
    tax0_c = min(cfg.TAX_RATE_MAX, max(cfg.TAX_RATE_MIN, tax0))

    fcff_hist: list[float | None] = []
    for i in range(min(len(ebit_s), 5)):
        fcff_hist.append(
            calculate_fcff(
                ebit_s[i],
                tax_s[i] if i < len(tax_s) else tax0_c,
                da_s[i] if i < len(da_s) else None,
                cx_s[i] if i < len(cx_s) else None,
                dwc_s[i] if i < len(dwc_s) else None,
            )
        )

    ebit0 = ebit_s[0] if ebit_s else None
    da0 = da_s[0] if da_s else None
    cx0 = cx_s[0] if cx_s else None
    dwc0 = dwc_s[0] if dwc_s else None
    nopat = (ebit0 * (1 - tax0_c)) if ebit0 is not None else None
    fcff0 = fcff_hist[0] if fcff_hist else None

    rev_cagr = _series_cagr(rev_s, cfg.GROWTH_LOOKBACK_YEARS)
    ebit_cagr = _series_cagr(ebit_s, cfg.GROWTH_LOOKBACK_YEARS)
    fcff_cagr = _series_cagr(fcff_hist, cfg.GROWTH_LOOKBACK_YEARS)

    shares = fin.get("shares")
    cash = fin.get("cash")
    debt = fin.get("debt")
    mcap = fin.get("market_cap")
    beta = fin.get("beta")

    share_gate = check_share_count_integrity(
        price=price_yahoo if price_yahoo is not None else price_wl,
        shares=shares,
        market_cap=mcap,
        info=info,
    )

    failure = None
    if not ok_elig:
        failure = elig_reason
    elif sum(1 for v in ebit_s if v is not None) < cfg.MIN_HISTORY_YEARS:
        failure = "insufficient financial history"
    elif sum(1 for v in fcff_hist[:4] if v and v > 0) < cfg.MIN_POSITIVE_FCFF_YEARS:
        failure = "negative/unstable FCFF"
    elif cfg.REQUIRE_LATEST_FCFF_POSITIVE and (fcff0 is None or fcff0 <= 0):
        failure = "negative/unstable FCFF"
    elif shares is None or shares <= 0:
        failure = "missing diluted shares"
    elif cash is None:
        failure = "missing cash"
    elif not share_gate.get("ok"):
        failure = "share_count_mismatch"

    fb: list[str] = list(fin.get("fallbacks") or [])
    kd_meta: dict[str, Any] = {}
    g_term = _terminal_g_for(mcap)
    growth_path: list[float] = []
    g_recent = None
    g_notes: list[str] = []
    ke = None
    wacc = None
    proj = None
    tv = None
    pv_explicit = None
    pv_tv = None
    ev = None
    equity_total = None
    est = None
    scenarios = None
    tv_ev_pct = None
    confidence = None

    if failure is None:
        ke = calculate_cost_of_equity(beta, fb)
        wacc = calculate_wacc(
            beta=beta,
            market_cap=mcap,
            debt=debt,
            tax_rate=tax0_c,
            fallbacks=fb,
            fcff0=float(fcff0) if fcff0 else None,
            kd_meta_out=kd_meta,
        )
        if wacc <= g_term:
            failure = "WACC <= terminal growth"
        else:
            growth_path, g_recent, g_notes = estimate_growth_path(
                rev_s, ebit_s, fcff_hist, terminal_g=g_term
            )
            ev, proj, tv = calculate_dcf_value(float(fcff0), growth_path, wacc, g_term)
            if ev is None or tv is None or not proj:
                failure = "DCF calculation failed"
            else:
                pv_explicit = sum(
                    f / ((1 + wacc) ** i) for i, f in enumerate(proj, start=1)
                )
                pv_tv = tv / ((1 + wacc) ** cfg.EXPLICIT_YEARS)
                tv_ev_pct = (pv_tv / ev) if ev else None
                equity_total = ev + (cash or 0) - (debt or 0)
                est = calculate_est_value_per_share(ev, cash, debt, shares)
                if est is None or est <= 0:
                    failure = "non-positive equity value"
                    est = None
                else:
                    if price_yahoo and price_yahoo > 0:
                        ratio = est / price_yahoo
                        if (
                            ratio > cfg.EST_TO_PRICE_MAX_RATIO
                            or ratio < cfg.EST_TO_PRICE_MIN_RATIO
                        ):
                            failure = "unreasonable Est.Value vs price"
                            est = None
                    if est is not None:
                        scenarios = calculate_valuation_scenarios(
                            fcff0=float(fcff0),
                            base_path=growth_path,
                            base_wacc=wacc,
                            base_g=g_term,
                            cash=cash,
                            debt=debt,
                            shares=shares,
                        )
                        conf = calculate_confidence(
                            history_years=sum(1 for v in ebit_s if v is not None),
                            fcff_hist=fcff_hist,
                            fallbacks=fb,
                            growth_recent=float(g_recent or 0),
                        )
                        conf = _apply_spread_to_confidence(conf, scenarios.get("spread"))
                        high_lev_warn = bool(
                            kd_meta.get("kd_default")
                            and (
                                (kd_meta.get("de") or 0) > cfg.HIGH_LEVERAGE_DE_MIN
                                or (
                                    kd_meta.get("debt_fcff") is not None
                                    and kd_meta["debt_fcff"]
                                    > cfg.HIGH_LEVERAGE_DEBT_FCFF_MIN
                                )
                            )
                        )
                        if high_lev_warn:
                            if "high_leverage_warning" not in fb:
                                fb.append("high_leverage_warning")
                            conf = _cap_confidence_at(conf, "MEDIUM")
                        confidence = conf

    mos_info = compute_row_mos(
        est if failure is None else None,
        {"price": price_wl, "updated_at": price_as_of, "price_source": price_source},
    )

    de = kd_meta.get("de")
    if de is None and mcap and debt is not None and mcap > 0:
        de = max(0.0, float(debt)) / mcap
    d_fcff = kd_meta.get("debt_fcff")
    if d_fcff is None and fcff0 and fcff0 > 0 and debt is not None:
        d_fcff = max(0.0, float(debt)) / fcff0

    return {
        "ticker": t,
        "company": name,
        "sector": sector,
        "industry": industry,
        "eligible": ok_elig,
        "failure_reason": failure,
        "price_wl": price_wl,
        "price_yahoo": price_yahoo,
        "price_source": mos_info.get("source") or price_source,
        "price_as_of": price_as_of,
        "mos": mos_info,
        "rev_s": rev_s,
        "ebit_s": ebit_s,
        "fcff_hist": fcff_hist,
        "revenue0": rev_s[0] if rev_s else None,
        "ebit0": ebit0,
        "tax0": tax0_c,
        "nopat": nopat,
        "da0": da0,
        "cx0": cx0,
        "cx_abs": abs(cx0) if cx0 is not None else None,
        "dwc0": dwc0,
        "fcff0": fcff0,
        "rev_cagr": rev_cagr,
        "ebit_cagr": ebit_cagr,
        "fcff_cagr": fcff_cagr,
        "g_recent": g_recent,
        "g_notes": g_notes,
        "growth_path": growth_path,
        "beta": beta,
        "rf": cfg.RISK_FREE_RATE,
        "erp": cfg.EQUITY_RISK_PREMIUM,
        "ke": ke,
        "debt": debt,
        "cash": cash,
        "de": de,
        "debt_fcff": d_fcff,
        "kd_meta": kd_meta,
        "wacc": wacc,
        "g_term": g_term,
        "proj": proj,
        "tv": tv,
        "pv_explicit": pv_explicit,
        "pv_tv": pv_tv,
        "tv_ev_pct": tv_ev_pct,
        "ev": ev,
        "equity_total": equity_total,
        "shares": shares,
        "share_gate": share_gate,
        "est": round(est, 2) if est else None,
        "scenarios": scenarios,
        "confidence": confidence,
        "flags": fb,
        "mcap": mcap,
    }


def write_report(rows: list[dict[str, Any]]) -> None:
    lines: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    lines += [
        "=" * 78,
        "VALUATION ENGINE — CURRENT BASELINE SNAPSHOT (AUDIT ONLY)",
        f"Generated (UTC): {now}",
        (
            f"Baseline: {getattr(cfg, 'VALUATION_BASELINE_LABEL', cfg.VALUATION_VERSION)} "
            f"| frozen={getattr(cfg, 'VALUATION_BASELINE_FROZEN', None)} "
            f"| version={cfg.VALUATION_VERSION}"
        ),
        "Components: Growth v1.1 | Share-count gate v1.2 | Tiered Kd v1.3 | MOS stale protection",
        "NO parameter changes; market price NOT used to adjust assumptions.",
        "=" * 78,
        "",
    ]

    for d in rows:
        km = d.get("kd_meta") or {}
        sg = d.get("share_gate") or {}
        sc = d.get("scenarios") or {}
        mos = d.get("mos") or {}
        rel = sg.get("rel_diff")
        rel_s = f"{rel * 100:.2f}%" if rel is not None else "—"
        path = d.get("growth_path") or []
        proj = d.get("proj") or []
        spread_s = (
            f"{sc.get('spread') * 100:.1f}%" if sc.get("spread") is not None else "—"
        )

        lines += [
            "-" * 78,
            f"{d['ticker']}  |  {d.get('company')}  |  {d.get('sector')} / {d.get('industry')}",
            f"Eligible: {d['eligible']}  |  failure_reason: {d.get('failure_reason') or '—'}",
            (
                f"Current Price (MOS/WL): {_money(d['price_wl'])}  "
                f"source={d['price_source']}  as_of={d.get('price_as_of') or '—'}"
            ),
            f"Yahoo live (ref only): {_money(d['price_yahoo'])}",
            f"Market Cap: {_money(d['mcap'])}",
            "",
            "INPUTS (latest year)",
            f"  Revenue: {_money(d['revenue0'])}",
            f"  EBIT: {_money(d['ebit0'])}",
            f"  Tax Rate: {_pct(d['tax0'])}",
            f"  EBIT×(1-T): {_money(d['nopat'])}",
            f"  D&A: {_money(d['da0'])}",
            f"  CapEx (Yahoo raw): {_money(d['cx0'])}  |abs|={_money(d['cx_abs'])}",
            f"  Change in WC (Yahoo CF): {_money(d['dwc0'])}",
            f"  Base FCFF: {_money(d['fcff0'])}",
            "",
            "HISTORY (latest → older)",
            f"  Revenue: {_series_money(d['rev_s'][:5])}",
            f"  EBIT:    {_series_money(d['ebit_s'][:5])}",
            f"  FCFF:    {_series_money(d['fcff_hist'][:5])}",
            f"  Revenue CAGR: {_pct(d['rev_cagr'])}",
            f"  EBIT CAGR: {_pct(d['ebit_cagr'])}",
            f"  FCFF CAGR: {_pct(d['fcff_cagr'])}",
            f"  g_recent (normalized): {_pct(d['g_recent'])}  notes={d.get('g_notes')}",
            (
                f"  5Y Growth Path: {', '.join(_pct(g) for g in path)}"
                if path
                else "  5Y Growth Path: —"
            ),
            "",
            "WACC / CAPITAL",
            f"  Beta: {d.get('beta')}",
            f"  Rf: {_pct(d['rf'])}  ERP: {_pct(d['erp'])}  Ke: {_pct(d['ke'])}",
            f"  Debt: {_money(d['debt'])}  Cash: {_money(d['cash'])}",
            f"  D/E: {d.get('de'):.4f}" if d.get("de") is not None else "  D/E: —",
            (
                f"  Debt/FCFF: {d.get('debt_fcff'):.2f}x"
                if d.get("debt_fcff") is not None
                else "  Debt/FCFF: —"
            ),
            (
                f"  Kd pre-tax: {_pct(km.get('kd_pre'))}  after-tax: {_pct(km.get('kd_after_tax'))}  "
                f"source={km.get('source') or '—'}  kd_default={km.get('kd_default')}"
            ),
        ]
        if km.get("spread") is not None:
            lines.append(
                f"  Kd spread detail: base={_pct(km.get('base_spread'))} "
                f"+ DE_tier{km.get('de_tier')}={_pct(km.get('de_premium'))} "
                f"+ DFCFF_tier{km.get('debt_fcff_tier')}={_pct(km.get('debt_fcff_premium'))} "
                f"=> spread={_pct(km.get('spread'))}"
            )
        lines += [
            f"  WACC: {_pct(d['wacc'])}",
            f"  Terminal g: {_pct(d['g_term'])}",
            "",
            "DCF PROJECTION",
            (
                f"  FCFF Y1–Y5: {', '.join(_money(x) for x in proj)}"
                if proj
                else "  FCFF Y1–Y5: —"
            ),
            f"  Terminal Value: {_money(d['tv'])}",
            f"  PV Explicit FCFF: {_money(d['pv_explicit'])}",
            f"  PV Terminal Value: {_money(d['pv_tv'])}",
            f"  Terminal Value / Enterprise Value: {_pct(d['tv_ev_pct'])}",
            f"  Enterprise Value: {_money(d['ev'])}",
            f"  Equity Value: {_money(d['equity_total'])}",
            f"  Diluted Shares: {d.get('shares')}",
            (
                f"  Share-count check: ok={sg.get('ok')} rel_diff={rel_s} "
                f"reason={sg.get('reason')} hints={sg.get('hints')}"
            ),
            "",
        ]
        if d.get("est") is not None and d.get("ev") is not None:
            lines += [
                "BASE VALUE BRIDGE (audit)",
                f"  PV explicit 5Y FCFF:     {_money(d['pv_explicit'])}",
                f"  PV Terminal Value:      {_money(d['pv_tv'])}",
                f"  = Enterprise Value:     {_money(d['ev'])}",
                f"  + Cash:                 {_money(d['cash'])}",
                f"  − Debt:                 {_money(d['debt'])}",
                f"  = Equity Value:         {_money(d['equity_total'])}",
                f"  / Diluted Shares:       {d.get('shares')}",
                f"  = Est.Value / share:    {_money(d['est'])}",
                f"  TV contribution % = PV_TV / EV = {_pct(d['tv_ev_pct'])}",
                "",
            ]
        lines += [
            f"Bear / Base / Bull: {sc.get('bear')} / {d.get('est')} / {sc.get('bull')}",
            f"Valuation Spread: {spread_s}  high_sens={sc.get('high_sensitivity')}",
            f"Confidence: {d.get('confidence') or '—'}",
            f"Warnings / flags: {', '.join(d.get('flags') or []) or '(none)'}",
        ]
        if mos.get("stale"):
            lines.append(
                f"MOS%: — (stale) age={mos.get('age_hours')}h "
                f"reason={mos.get('stale_reason')} limit={mos.get('stale_hours_limit')}h"
            )
        elif mos.get("mos_pct") is not None:
            lines.append(
                f"MOS%: {mos.get('mos_pct'):+.1f}%  "
                f"(price={_money(mos.get('price'))} age={mos.get('age_hours')}h)"
            )
        else:
            lines.append("MOS%: —")
        lines.append("")

    lines += [
        "=" * 78,
        "SUMMARY TABLE",
        (
            f"{'Ticker':6} {'Price':>10} {'Base':>10} {'Bear':>10} {'Bull':>10} "
            f"{'TV/EV%':>8} {'WACC':>7} {'g':>6} {'Conf':>7} Failure/Warning"
        ),
    ]
    for d in rows:
        sc = d.get("scenarios") or {}
        mos = d.get("mos") or {}
        warns: list[str] = []
        if d.get("failure_reason"):
            warns.append(str(d["failure_reason"]))
        if mos.get("stale"):
            warns.append(f"mos_stale:{mos.get('stale_reason')}")
        for f in d.get("flags") or []:
            if f in ("high_leverage_warning", "share_count_unverified"):
                warns.append(f)
        seen: set[str] = set()
        wuniq = []
        for w in warns:
            if w not in seen:
                seen.add(w)
                wuniq.append(w)
        bear_s = f"${sc['bear']:,.2f}" if sc.get("bear") is not None else "—"
        bull_s = f"${sc['bull']:,.2f}" if sc.get("bull") is not None else "—"
        base_s = _money(d["est"]) if d.get("est") is not None else "—"
        lines.append(
            f"{d['ticker']:6} {_money(d['price_wl']):>10} {base_s:>10} {bear_s:>10} {bull_s:>10} "
            f"{_pct(d['tv_ev_pct']):>8} {_pct(d['wacc']):>7} {_pct(d['g_term']):>6} "
            f"{(d.get('confidence') or '—'):>7} {' | '.join(wuniq) or '—'}"
        )
    lines += [
        "=" * 78,
        "END OF AUDIT — no model judgment / no parameter changes.",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    init_db()
    rows = [audit_one(t) for t in TICKERS]
    write_report(rows)
    print(f"Wrote {OUT}")
