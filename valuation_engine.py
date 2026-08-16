"""
Valuation Engine V1 — DCF / FCFF Estimated Intrinsic Value per share.

Independent of AI Score / technicals / news. Uses Yahoo annual financials only.

Baseline frozen at v1.3 (valuation_config.VALUATION_BASELINE_FROZEN):
  growth v1.1, share-count gate v1.2, tiered Kd v1.3, MOS stale-price guard.
Do not retune knobs to match one ticker's market price; log validation cases
via valuation_validation_cases.record_validation_case instead.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

import valuation_config as cfg


@dataclass
class ValuationResult:
    ticker: str
    est_value: float | None = None  # Base Case — never replace with range midpoint
    method: str = cfg.VALUATION_METHOD
    financial_period: str | None = None
    wacc: float | None = None
    terminal_growth: float | None = None
    growth_path: list[float] = field(default_factory=list)
    confidence: str | None = None  # HIGH | MEDIUM | LOW
    failure_reason: str | None = None
    currency: str | None = None
    fallbacks: list[str] = field(default_factory=list)
    as_of: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    # Sensitivity range (DCF parameter scenarios; not a price forecast)
    bear_value: float | None = None
    bull_value: float | None = None
    valuation_spread: float | None = None  # (bull - bear) / base
    high_sensitivity: bool = False

    @property
    def ok(self) -> bool:
        return self.est_value is not None and self.est_value > 0 and not self.failure_reason

    def tooltip(self) -> str:
        if not self.ok:
            reason = self.failure_reason or "unavailable"
            return f"Valuation unavailable: {reason}"
        lines = [
            f"Bear: ${self.bear_value:,.2f}" if self.bear_value is not None else "Bear: —",
            f"Base: ${self.est_value:,.2f}",
            f"Bull: ${self.bull_value:,.2f}" if self.bull_value is not None else "Bull: —",
        ]
        if self.bear_value is not None and self.bull_value is not None:
            lo, hi = min(self.bear_value, self.bull_value), max(self.bear_value, self.bull_value)
            lines.append(f"Estimated Range: ${lo:,.2f}–${hi:,.2f}")
        lines.extend(
            [
                f"Method: {self.method} (valuation range ≠ price forecast)",
                f"Financial period: {self.financial_period or '—'}",
                f"Base WACC: {self.wacc * 100:.1f}%" if self.wacc is not None else "Base WACC: —",
                (
                    f"Base terminal g: {self.terminal_growth * 100:.1f}%"
                    if self.terminal_growth is not None
                    else "Base terminal g: —"
                ),
                "Base 5Y growth: "
                + (
                    ", ".join(f"{g * 100:.0f}%" for g in self.growth_path)
                    if self.growth_path
                    else "—"
                ),
                f"Confidence: {self.confidence or '—'}",
            ]
        )
        if self.valuation_spread is not None:
            lines.append(f"Valuation spread: {self.valuation_spread * 100:.0f}%")
        if self.high_sensitivity:
            lines.append("High valuation sensitivity — Base Case is less precise")
        lines.append(f"Last valued: {(self.as_of or '')[:10] or '—'}")
        if self.fallbacks:
            lines.append("Fallbacks: " + ", ".join(self.fallbacks))
        return "\n".join(lines)


def _finite(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or math.isinf(v):
        return None
    return v


def _row(df: pd.DataFrame | None, *names: str) -> list[float | None] | None:
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            out: list[float | None] = []
            for v in df.loc[n].tolist():
                out.append(_finite(v))
            return out
    return None


def _cagr(series: list[float], years: int | None = None) -> float | None:
    clean = [v for v in series if v is not None and v > 0]
    if len(clean) < 2:
        return None
    if years is None:
        years = len(clean) - 1
    if years <= 0:
        return None
    start, end = clean[-1], clean[0]  # Yahoo: col0 = latest
    if start <= 0 or end <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def is_eligible_for_dcf(
    *,
    sector: str | None,
    industry: str | None,
    quote_type: str | None,
) -> tuple[bool, str | None]:
    qt = (quote_type or "").upper()
    if qt and qt not in ("EQUITY", "ETF"):  # ETF still skip below via sector
        if qt in ("MUTUALFUND", "INDEX", "CURRENCY", "FUTURE", "CRYPTOCURRENCY"):
            return False, f"unsupported quote type {qt}"
    blob = f"{sector or ''} {industry or ''}".lower()
    for kw in cfg.FINANCIAL_SECTOR_KEYWORDS:
        if kw in blob:
            return False, "financial company (DCF-FCFF V1 skipped)"
    return True, None


def normalize_financials(ticker_obj: yf.Ticker) -> dict[str, Any]:
    """Pull annual statements + key info into a normalized dict."""
    fallbacks: list[str] = []
    info: dict[str, Any] = {}
    try:
        info = ticker_obj.info or {}
    except Exception:
        info = {}
        fallbacks.append("info_unavailable")

    try:
        inc = ticker_obj.income_stmt
    except Exception:
        inc = None
    try:
        cf = ticker_obj.cashflow
    except Exception:
        cf = None
    try:
        bs = ticker_obj.balance_sheet
    except Exception:
        bs = None

    ebit = _row(inc, "EBIT", "Operating Income")
    if not ebit:
        ebit = _row(inc, "Normalized EBITDA")
        if ebit:
            fallbacks.append("ebit_from_normalized_ebitda")

    tax = _row(inc, "Tax Rate For Calcs")
    revenue = _row(inc, "Total Revenue", "Operating Revenue")
    da = _row(
        cf,
        "Depreciation And Amortization",
        "Depreciation",
    ) or _row(inc, "Reconciled Depreciation")
    capex = _row(cf, "Capital Expenditure")
    dwc = _row(cf, "Change In Working Capital")
    fcf_yahoo = _row(cf, "Free Cash Flow")

    cash = _row(
        bs,
        "Cash Cash Equivalents And Short Term Investments",
        "Cash And Cash Equivalents",
        "Cash And Short Term Investments",
    )
    debt = _row(bs, "Total Debt")
    shares_bs = _row(bs, "Ordinary Shares Number", "Share Issued")

    # Period labels (column headers)
    periods: list[str] = []
    for df in (inc, cf, bs):
        if df is not None and not getattr(df, "empty", True):
            periods = [str(c)[:10] for c in df.columns]
            break

    shares = _finite(info.get("sharesOutstanding") or info.get("impliedSharesOutstanding"))
    if shares is None and shares_bs:
        shares = shares_bs[0]
        fallbacks.append("shares_from_balance_sheet")

    cash0 = _finite(info.get("totalCash"))
    if cash0 is None and cash:
        cash0 = cash[0]
        fallbacks.append("cash_from_balance_sheet")
    debt0 = _finite(info.get("totalDebt"))
    if debt0 is None and debt:
        debt0 = debt[0]
        fallbacks.append("debt_from_balance_sheet")

    beta = _finite(info.get("beta"))
    mcap = _finite(info.get("marketCap"))
    currency = info.get("currency") or "USD"

    return {
        "info": info,
        "ebit": ebit or [],
        "tax": tax or [],
        "revenue": revenue or [],
        "da": da or [],
        "capex": capex or [],
        "dwc": dwc or [],
        "fcf_yahoo": fcf_yahoo or [],
        "cash": cash0,
        "debt": debt0 if debt0 is not None else 0.0,
        "shares": shares,
        "beta": beta,
        "market_cap": mcap,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "quote_type": info.get("quoteType"),
        "currency": currency,
        "periods": periods,
        "fallbacks": fallbacks,
    }


def _dual_class_or_share_structure_hints(info: dict[str, Any] | None) -> list[str]:
    """Heuristic labels only — never used to rewrite share count."""
    hints: list[str] = []
    if not info:
        return hints
    blob = " ".join(
        str(info.get(k) or "")
        for k in ("shortName", "longName", "longBusinessSummary", "quoteType", "category")
    ).lower()
    for h in cfg.SHARE_COUNT_DUAL_CLASS_NAME_HINTS:
        if h in blob:
            hints.append(f"name_hint:{h}")
            break
    qt = str(info.get("quoteType") or "").upper()
    if qt == "ADR":
        hints.append("quote_type_ADR")
    # Yahoo sometimes exposes both share counts when classes differ
    so = _finite(info.get("sharesOutstanding"))
    implied = _finite(info.get("impliedSharesOutstanding"))
    if so and implied and so > 0 and abs(implied - so) / so > 0.10:
        hints.append("sharesOutstanding_vs_implied_diverge")
    return hints


def check_share_count_integrity(
    *,
    price: float | None,
    shares: float | None,
    market_cap: float | None,
    info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Require MarketCap ≈ CurrentPrice × DilutedShares before per-share Est.Value.

    On material mismatch we do NOT substitute MarketCap/Price for shares
    (mcap may aggregate multiple classes). Return ok=False → caller sets
    failure_reason=share_count_mismatch.
    """
    out: dict[str, Any] = {
        "ok": True,
        "rel_diff": None,
        "implied_mcap": None,
        "market_cap": market_cap,
        "price": price,
        "shares": shares,
        "hints": _dual_class_or_share_structure_hints(info),
        "reason": None,
    }
    px = _finite(price)
    sh = _finite(shares)
    mc = _finite(market_cap)
    if sh is None or sh <= 0:
        out["ok"] = False
        out["reason"] = "missing diluted shares"
        return out
    if px is None or px <= 0 or mc is None or mc <= 0:
        # Cannot verify — do not invent shares; allow with unverified flag
        out["ok"] = True
        out["reason"] = "share_count_unverified"
        return out

    implied = px * sh
    out["implied_mcap"] = implied
    rel = abs(implied - mc) / mc
    out["rel_diff"] = rel
    if rel > cfg.SHARE_COUNT_MISMATCH_MAX:
        out["ok"] = False
        hint_s = ",".join(out["hints"]) if out["hints"] else "unresolved"
        out["reason"] = (
            f"share_count_mismatch (diff={rel*100:.1f}%>{cfg.SHARE_COUNT_MISMATCH_MAX*100:.0f}%; "
            f"hints={hint_s})"
        )
        return out
    return out


def calculate_fcff(
    ebit: float | None,
    tax_rate: float | None,
    da: float | None,
    capex: float | None,
    change_in_wc: float | None,
) -> float | None:
    """
    FCFF = EBIT×(1−t) + D&A − CapEx − ΔNWC
    Yahoo CapEx is typically negative (outflow); Change In WC is cash-flow signed.
    """
    e = _finite(ebit)
    if e is None:
        return None
    t = _finite(tax_rate)
    if t is None:
        t = cfg.DEFAULT_TAX_RATE
    t = min(cfg.TAX_RATE_MAX, max(cfg.TAX_RATE_MIN, t))
    d = _finite(da) or 0.0
    cx = _finite(capex)
    # CapEx spend as positive dollars
    capex_spend = abs(cx) if cx is not None else None
    if capex_spend is None:
        return None
    # Yahoo ΔWC cash item: negative ⇒ WC increased ⇒ ΔNWC > 0
    wc_cf = _finite(change_in_wc)
    delta_nwc = (-wc_cf) if wc_cf is not None else 0.0
    return e * (1.0 - t) + d - capex_spend - delta_nwc


def _series_cagr(series: list[float | None], lookback: int | None = None) -> float | None:
    vals = [v for v in series if v is not None and v > 0]
    if lookback is not None:
        vals = vals[: lookback + 1]
    return _cagr(vals) if len(vals) >= 2 else None


def _margin_series(
    numer: list[float | None],
    denom: list[float | None],
) -> list[float]:
    out: list[float] = []
    n = min(len(numer), len(denom))
    for i in range(n):
        a, b = numer[i], denom[i]
        if a is not None and b is not None and b > 0 and a > 0:
            out.append(a / b)
    return out


def _coeff_var(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    if mean <= 0:
        return None
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    return (var ** 0.5) / mean


def _yoy_growth_std(series: list[float | None]) -> float | None:
    """Std-dev of consecutive YoY growth for positive values (latest-first)."""
    vals = [v for v in series if v is not None and v > 0]
    if len(vals) < 3:
        return None
    yoys: list[float] = []
    for i in range(len(vals) - 1):
        older, newer = vals[i + 1], vals[i]
        if older > 0:
            yoys.append(newer / older - 1.0)
    if len(yoys) < 2:
        return None
    mean = sum(yoys) / len(yoys)
    var = sum((y - mean) ** 2 for y in yoys) / len(yoys)
    return var ** 0.5


def _signal_stable(
    *,
    margins: list[float],
    yoy_std: float | None,
    notes: list[str],
    label: str,
) -> bool:
    stable = True
    cv = _coeff_var(margins)
    if cv is not None:
        notes.append(f"{label}_margin_cv={cv:.3f}")
        if cv > cfg.GROWTH_MARGIN_CV_STABLE:
            stable = False
            notes.append(f"{label}_margin_unstable")
    if yoy_std is not None:
        notes.append(f"{label}_yoy_std={yoy_std:.3f}")
        if yoy_std > cfg.GROWTH_YOY_STD_UNSTABLE:
            stable = False
            notes.append(f"{label}_path_unstable")
    # Low-base: oldest margin << later median → CAGR inflated
    if len(margins) >= 3:
        # margins are latest-first (aligned with series)
        older = margins[-1]
        later = sorted(margins[:-1])[len(margins[:-1]) // 2]
        if later > 0 and older / later < cfg.GROWTH_LOW_BASE_MARGIN_RATIO:
            stable = False
            notes.append(f"{label}_low_base_margin")
    # Smooth margin expansion can keep CV low but still inflate EBIT/FCFF CAGR
    if len(margins) >= 2 and margins[-1] > 0:
        expand = margins[0] / margins[-1] - 1.0
        notes.append(f"{label}_margin_expand={expand:.3f}")
        if expand > cfg.GROWTH_MARGIN_EXPAND_RATIO:
            stable = False
            notes.append(f"{label}_margin_expanding")
    return stable


def _cap_vs_revenue(signal: float, rev: float, stable: bool) -> float:
    """Keep only a capped premium of signal over revenue CAGR."""
    premium = signal - rev
    if premium <= 0:
        return signal
    cap = cfg.GROWTH_PREMIUM_CAP_STABLE if stable else cfg.GROWTH_PREMIUM_CAP_UNSTABLE
    return rev + min(premium, cap)


def estimate_growth_path(
    revenue: list[float | None],
    ebit: list[float | None],
    fcff_hist: list[float | None],
    *,
    terminal_g: float,
) -> tuple[list[float], float, list[str]]:
    """
    Revenue-anchored normalized growth → 5Y fade path.

    Rules (uniform for all tickers; not price-tuned):
    - Revenue CAGR is the primary sustainable-growth anchor when available.
    - EBIT/FCFF CAGRs only contribute after capping any premium vs revenue
      when margins are expanding, YoY path is erratic, or low-base effects
      inflate multi-year CAGR.
    - DCF arithmetic (FCFF→WACC→TV) is unchanged; only g_recent changes.
    """
    notes: list[str] = []
    look = cfg.GROWTH_LOOKBACK_YEARS
    rev_cagr = _series_cagr(revenue, look)
    ebit_cagr = _series_cagr(ebit, look)
    fcff_cagr = _series_cagr(fcff_hist, look)
    if rev_cagr is not None:
        notes.append(f"rev_cagr={rev_cagr:.3f}")
    if ebit_cagr is not None:
        notes.append(f"ebit_cagr={ebit_cagr:.3f}")
    if fcff_cagr is not None:
        notes.append(f"fcff_cagr={fcff_cagr:.3f}")

    if rev_cagr is None:
        # No revenue anchor: conservative median of available signals
        cands = [g for g in (ebit_cagr, fcff_cagr) if g is not None]
        if not cands:
            g_recent = terminal_g
            notes.append("growth_fallback_terminal")
        else:
            cands.sort()
            g_recent = cands[len(cands) // 2]
            notes.append("growth_no_rev_anchor_median")
    else:
        weights: list[tuple[float, float, str]] = [
            (rev_cagr, cfg.GROWTH_REV_WEIGHT, "rev"),
        ]

        ebit_margins = _margin_series(ebit, revenue)
        ebit_stable = _signal_stable(
            margins=ebit_margins,
            yoy_std=_yoy_growth_std(ebit),
            notes=notes,
            label="ebit",
        )
        if ebit_cagr is not None:
            ebit_adj = _cap_vs_revenue(ebit_cagr, rev_cagr, ebit_stable)
            w = (
                cfg.GROWTH_EBIT_WEIGHT_STABLE
                if ebit_stable
                else cfg.GROWTH_EBIT_WEIGHT_UNSTABLE
            )
            weights.append((ebit_adj, w, "ebit_adj"))
            notes.append(f"ebit_adj={ebit_adj:.3f}")
            if ebit_adj < ebit_cagr - 1e-9:
                notes.append("ebit_premium_capped_vs_rev")

        fcff_margins = _margin_series(fcff_hist, revenue)
        fcff_stable = _signal_stable(
            margins=fcff_margins,
            yoy_std=_yoy_growth_std(fcff_hist),
            notes=notes,
            label="fcff",
        )
        if fcff_cagr is not None:
            fcff_adj = _cap_vs_revenue(fcff_cagr, rev_cagr, fcff_stable)
            w = (
                cfg.GROWTH_FCFF_WEIGHT_STABLE
                if fcff_stable
                else cfg.GROWTH_FCFF_WEIGHT_UNSTABLE
            )
            weights.append((fcff_adj, w, "fcff_adj"))
            notes.append(f"fcff_adj={fcff_adj:.3f}")
            if fcff_adj < fcff_cagr - 1e-9:
                notes.append("fcff_premium_capped_vs_rev")

        # Normalize weights; revenue already has its configured share
        wsum = sum(w for _, w, _ in weights) or 1.0
        g_recent = sum(g * w for g, w, _ in weights) / wsum
        notes.append(
            "growth_weights="
            + ",".join(f"{lab}:{w / wsum:.2f}" for _, w, lab in weights)
        )
        notes.append("growth_method=rev_anchored_v1.1")

    g_recent = max(cfg.GROWTH_FLOOR_Y1, min(cfg.GROWTH_CAP_Y1, g_recent))
    notes.append(f"g_recent={g_recent:.4f}")
    path: list[float] = []
    for fade in cfg.GROWTH_FADE:
        g_t = terminal_g + fade * (g_recent - terminal_g)
        path.append(round(g_t, 4))
    return path, g_recent, notes


def calculate_cost_of_equity(beta: float | None, fallbacks: list[str]) -> float:
    b = beta if beta is not None else cfg.DEFAULT_BETA
    if beta is None:
        fallbacks.append("beta_default")
    b = min(cfg.BETA_MAX, max(cfg.BETA_MIN, b))
    return cfg.RISK_FREE_RATE + b * cfg.EQUITY_RISK_PREMIUM


def _mutually_exclusive_tier_premium(
    value: float,
    bounds: tuple[float, ...] | list[float],
    premiums: tuple[float, ...] | list[float],
) -> tuple[float, int]:
    """
    Exclusive tiers: v <= b0 → p0; b0 < v <= b1 → p1; ...; v > b_last → p_last.
    len(premiums) must be len(bounds) + 1. Returns (premium, tier_index).
    """
    if len(premiums) != len(bounds) + 1:
        raise ValueError("premiums length must be len(bounds)+1")
    if value <= bounds[0]:
        return float(premiums[0]), 0
    for i in range(1, len(bounds)):
        if value <= bounds[i]:
            return float(premiums[i]), i
    return float(premiums[-1]), len(bounds)


def calculate_tiered_fallback_kd(
    *,
    debt: float | None,
    market_cap: float | None,
    fcff0: float | None,
) -> dict[str, Any]:
    """
    Scheme A: Kd_pre = min(KD_MAX, Rf + base + D/E tier + Debt/FCFF tier).
    D/E and Debt/FCFF tiers are mutually exclusive within each dimension.
    """
    d = max(0.0, float(debt or 0.0))
    e = float(market_cap) if market_cap and market_cap > 0 else None
    de = (d / e) if e and e > 0 else 0.0
    ff = _finite(fcff0)
    debt_fcff: float | None
    if ff is not None and ff > 0:
        debt_fcff = d / ff
        dfcff_for_tier = debt_fcff
        dfcff_note = None
    elif d > 0:
        # No usable FCFF → assign top Debt/FCFF tier (conservative)
        debt_fcff = None
        dfcff_for_tier = cfg.KD_DEBT_FCFF_TIER_BOUNDS[-1] + 1.0
        dfcff_note = "debt_fcff_unusable_top_tier"
    else:
        debt_fcff = 0.0
        dfcff_for_tier = 0.0
        dfcff_note = None

    de_prem, de_tier = _mutually_exclusive_tier_premium(
        de, cfg.KD_DE_TIER_BOUNDS, cfg.KD_DE_TIER_PREMIUMS
    )
    dfcff_prem, dfcff_tier = _mutually_exclusive_tier_premium(
        dfcff_for_tier, cfg.KD_DEBT_FCFF_TIER_BOUNDS, cfg.KD_DEBT_FCFF_TIER_PREMIUMS
    )
    spread = cfg.KD_BASE_SPREAD + de_prem + dfcff_prem
    kd_pre = min(cfg.KD_MAX, cfg.RISK_FREE_RATE + spread)
    high_lev = bool(
        (de > cfg.HIGH_LEVERAGE_DE_MIN)
        or (debt_fcff is not None and debt_fcff > cfg.HIGH_LEVERAGE_DEBT_FCFF_MIN)
        or (debt_fcff is None and d > 0 and dfcff_note == "debt_fcff_unusable_top_tier")
    )
    return {
        "kd_pre": kd_pre,
        "spread": spread,
        "base_spread": cfg.KD_BASE_SPREAD,
        "de": de,
        "debt_fcff": debt_fcff,
        "de_premium": de_prem,
        "de_tier": de_tier,
        "debt_fcff_premium": dfcff_prem,
        "debt_fcff_tier": dfcff_tier,
        "high_leverage": high_lev,
        "dfcff_note": dfcff_note,
        "source": "tiered_fallback",
    }


def resolve_pre_tax_cost_of_debt(
    *,
    company_kd_pre: float | None,
    debt: float | None,
    market_cap: float | None,
    fcff0: float | None,
    fallbacks: list[str],
) -> dict[str, Any]:
    """
    Prefer reliable company debt yield / credit spread when available;
    otherwise Scheme A tiered leverage-aware fallback.
    """
    ck = _finite(company_kd_pre)
    if ck is not None and cfg.KD_COMPANY_YIELD_MIN <= ck <= cfg.KD_COMPANY_YIELD_MAX:
        d = max(0.0, float(debt or 0.0))
        e = float(market_cap) if market_cap and market_cap > 0 else None
        de = (d / e) if e and e > 0 else 0.0
        ff = _finite(fcff0)
        debt_fcff = (d / ff) if ff and ff > 0 else None
        high_lev = bool(
            (de > cfg.HIGH_LEVERAGE_DE_MIN)
            or (debt_fcff is not None and debt_fcff > cfg.HIGH_LEVERAGE_DEBT_FCFF_MIN)
        )
        return {
            "kd_pre": min(cfg.KD_MAX, ck),
            "spread": None,
            "de": de,
            "debt_fcff": debt_fcff,
            "high_leverage": high_lev,
            "source": "company_yield",
            "kd_default": False,
        }

    tiered = calculate_tiered_fallback_kd(debt=debt, market_cap=market_cap, fcff0=fcff0)
    fallbacks.append("kd_default")
    if tiered.get("dfcff_note"):
        fallbacks.append(tiered["dfcff_note"])
    if tiered.get("high_leverage"):
        fallbacks.append("high_leverage_warning")
    return {
        **tiered,
        "kd_default": True,
    }


def calculate_wacc(
    *,
    beta: float | None,
    market_cap: float | None,
    debt: float | None,
    tax_rate: float | None,
    fallbacks: list[str],
    fcff0: float | None = None,
    company_kd_pre: float | None = None,
    kd_meta_out: dict[str, Any] | None = None,
) -> float:
    ke = calculate_cost_of_equity(beta, fallbacks)
    t = tax_rate if tax_rate is not None else cfg.DEFAULT_TAX_RATE
    if tax_rate is None:
        fallbacks.append("tax_default")
    t = min(cfg.TAX_RATE_MAX, max(cfg.TAX_RATE_MIN, t))

    kd_info = resolve_pre_tax_cost_of_debt(
        company_kd_pre=company_kd_pre,
        debt=debt,
        market_cap=market_cap,
        fcff0=fcff0,
        fallbacks=fallbacks,
    )
    kd = float(kd_info["kd_pre"])
    kd_at = kd * (1.0 - t)
    if kd_meta_out is not None:
        kd_meta_out.clear()
        kd_meta_out.update(kd_info)
        kd_meta_out["kd_after_tax"] = kd_at
        kd_meta_out["tax_rate"] = t

    e = market_cap if market_cap and market_cap > 0 else None
    d = max(0.0, debt or 0.0)
    if e is None:
        fallbacks.append("wacc_all_equity")
        we, wd = 1.0, 0.0
    else:
        total = e + d
        we = e / total if total > 0 else 1.0
        wd = d / total if total > 0 else 0.0

    wacc = we * ke + wd * kd_at
    return min(cfg.WACC_MAX, max(cfg.WACC_MIN, wacc))


def _clamp_scenario_growth(path: list[float], delta: float) -> list[float]:
    out: list[float] = []
    for g in path:
        x = g + delta
        x = min(cfg.SCENARIO_GROWTH_CAP, max(cfg.SCENARIO_GROWTH_FLOOR, x))
        out.append(round(x, 4))
    return out


def _scenario_wacc_g(base_wacc: float, base_g: float, wacc_delta: float, g_delta: float) -> tuple[float, float]:
    wacc = min(cfg.WACC_MAX, max(cfg.WACC_MIN, base_wacc + wacc_delta))
    g = min(cfg.TERMINAL_GROWTH_MAX, max(cfg.TERMINAL_GROWTH_MIN, base_g + g_delta))
    # Enforce WACC > g with a small cushion
    if wacc <= g:
        wacc = min(cfg.WACC_MAX, g + 0.01)
    if wacc <= g:
        g = max(cfg.TERMINAL_GROWTH_MIN, wacc - 0.01)
    return wacc, g


def calculate_valuation_scenarios(
    *,
    fcff0: float,
    base_path: list[float],
    base_wacc: float,
    base_g: float,
    cash: float | None,
    debt: float | None,
    shares: float | None,
) -> dict[str, Any]:
    """
    Bear / Base / Bull Est.Value from DCF parameter sensitivity.
    Base path/wacc/g are the Base Case; scenarios do not alter Base math.
    """
    base_ev, _, _ = calculate_dcf_value(fcff0, base_path, base_wacc, base_g)
    base = calculate_est_value_per_share(base_ev, cash, debt, shares) if base_ev else None

    bear_path = _clamp_scenario_growth(base_path, cfg.BEAR_GROWTH_DELTA)
    bear_wacc, bear_g = _scenario_wacc_g(
        base_wacc, base_g, cfg.BEAR_WACC_DELTA, cfg.BEAR_TERMINAL_G_DELTA
    )
    bear_ev, _, _ = calculate_dcf_value(fcff0, bear_path, bear_wacc, bear_g)
    bear = calculate_est_value_per_share(bear_ev, cash, debt, shares) if bear_ev else None

    bull_path = _clamp_scenario_growth(base_path, cfg.BULL_GROWTH_DELTA)
    bull_wacc, bull_g = _scenario_wacc_g(
        base_wacc, base_g, cfg.BULL_WACC_DELTA, cfg.BULL_TERMINAL_G_DELTA
    )
    bull_ev, _, _ = calculate_dcf_value(fcff0, bull_path, bull_wacc, bull_g)
    bull = calculate_est_value_per_share(bull_ev, cash, debt, shares) if bull_ev else None

    spread = None
    high_sens = False
    if base and base > 0 and bear is not None and bull is not None:
        spread = abs(bull - bear) / base
        high_sens = spread >= cfg.VALUATION_SPREAD_WIDE

    return {
        "base": round(base, 2) if base else None,
        "bear": round(bear, 2) if bear else None,
        "bull": round(bull, 2) if bull else None,
        "spread": round(spread, 4) if spread is not None else None,
        "high_sensitivity": high_sens,
        "bear_path": bear_path,
        "bull_path": bull_path,
        "bear_wacc": bear_wacc,
        "bull_wacc": bull_wacc,
        "bear_g": bear_g,
        "bull_g": bull_g,
    }


def _apply_spread_to_confidence(confidence: str, spread: float | None) -> str:
    if spread is None:
        return confidence
    order = ["HIGH", "MEDIUM", "LOW"]
    idx = order.index(confidence) if confidence in order else 1
    if spread >= cfg.VALUATION_SPREAD_VERY_WIDE:
        idx = min(2, idx + 2)
    elif spread >= cfg.VALUATION_SPREAD_WIDE:
        idx = min(2, idx + 1)
    return order[idx]


def _cap_confidence_at(confidence: str, max_level: str) -> str:
    order = ["HIGH", "MEDIUM", "LOW"]
    cur = order.index(confidence) if confidence in order else 1
    lim = order.index(max_level) if max_level in order else 1
    return order[max(cur, lim)]


def calculate_terminal_value(fcff5: float, wacc: float, g: float) -> float | None:
    if wacc <= g or fcff5 is None or fcff5 <= 0:
        return None
    fcff6 = fcff5 * (1.0 + g)
    return fcff6 / (wacc - g)


def calculate_dcf_value(
    fcff0: float,
    growth_path: list[float],
    wacc: float,
    terminal_g: float,
) -> tuple[float | None, list[float], float | None]:
    """Return (EV, projected FCFF list, TV)."""
    if fcff0 <= 0 or wacc <= terminal_g:
        return None, [], None
    proj: list[float] = []
    prev = fcff0
    for g in growth_path:
        prev = prev * (1.0 + g)
        proj.append(prev)
    if len(proj) != cfg.EXPLICIT_YEARS:
        return None, [], None
    pv = 0.0
    for t, f in enumerate(proj, start=1):
        pv += f / ((1.0 + wacc) ** t)
    tv = calculate_terminal_value(proj[-1], wacc, terminal_g)
    if tv is None:
        return None, proj, None
    pv += tv / ((1.0 + wacc) ** cfg.EXPLICIT_YEARS)
    return pv, proj, tv


def calculate_est_value_per_share(
    enterprise_value: float,
    cash: float | None,
    debt: float | None,
    shares: float | None,
) -> float | None:
    if shares is None or shares <= 0:
        return None
    equity = enterprise_value + (cash or 0.0) - (debt or 0.0)
    if equity <= 0:
        return None
    return equity / shares


def calculate_mos(est_value: float | None, price: float | None) -> float | None:
    if est_value is None or price is None or est_value <= 0:
        return None
    return round((est_value - price) / est_value * 100, 2)


def calculate_confidence(
    *,
    history_years: int,
    fcff_hist: list[float | None],
    fallbacks: list[str],
    growth_recent: float,
) -> str:
    score = 0
    if history_years >= 4:
        score += 2
    elif history_years >= 3:
        score += 1
    pos = sum(1 for v in fcff_hist[:4] if v is not None and v > 0)
    if pos >= 3:
        score += 2
    elif pos >= 2:
        score += 1
    if len(fallbacks) <= 1:
        score += 2
    elif len(fallbacks) <= 3:
        score += 1
    if abs(growth_recent) <= 0.15:
        score += 1
    if score >= 6:
        return "HIGH"
    if score >= 3:
        return "MEDIUM"
    return "LOW"


def _terminal_g_for(market_cap: float | None) -> float:
    g = cfg.TERMINAL_GROWTH_DEFAULT
    if market_cap and market_cap >= cfg.MATURE_MARKET_CAP_USD:
        g = cfg.TERMINAL_GROWTH_MATURE
    return min(cfg.TERMINAL_GROWTH_MAX, max(cfg.TERMINAL_GROWTH_MIN, g))


def calculate_dcf_valuation(ticker: str, *, ticker_obj: yf.Ticker | None = None) -> ValuationResult:
    """Full pipeline for one ticker. Never raises to callers — returns failure_reason."""
    t = (ticker or "").strip().upper()
    now = datetime.now(timezone.utc).isoformat()
    result = ValuationResult(
        ticker=t,
        as_of=now,
        method=f"{cfg.VALUATION_METHOD}/{cfg.VALUATION_VERSION}",
    )
    try:
        tk = ticker_obj or yf.Ticker(t)
        fin = normalize_financials(tk)
        result.fallbacks = list(fin.get("fallbacks") or [])
        result.currency = fin.get("currency")

        ok, reason = is_eligible_for_dcf(
            sector=fin.get("sector"),
            industry=fin.get("industry"),
            quote_type=fin.get("quote_type"),
        )
        if not ok:
            result.failure_reason = reason
            return result

        ebit_s: list[float | None] = fin["ebit"]
        if sum(1 for v in ebit_s if v is not None) < cfg.MIN_HISTORY_YEARS:
            result.failure_reason = "insufficient financial history"
            return result

        n = min(len(ebit_s), len(fin["da"] or ebit_s), len(fin["capex"] or ebit_s))
        if n < cfg.MIN_HISTORY_YEARS:
            # Align by taking available lengths carefully
            n = min(len(ebit_s), max(len(fin.get("capex") or []), cfg.MIN_HISTORY_YEARS))

        tax_s = fin["tax"] or []
        da_s = fin["da"] or []
        cx_s = fin["capex"] or []
        dwc_s = fin["dwc"] or []

        fcff_hist: list[float | None] = []
        for i in range(min(len(ebit_s), 5)):
            tax_i = tax_s[i] if i < len(tax_s) else None
            da_i = da_s[i] if i < len(da_s) else None
            cx_i = cx_s[i] if i < len(cx_s) else None
            dwc_i = dwc_s[i] if i < len(dwc_s) else None
            # If CapEx missing, try Yahoo FCF bridge: FCFF ≈ FCF (approx) as last resort for history check only
            f = calculate_fcff(ebit_s[i], tax_i, da_i, cx_i, dwc_i)
            if f is None and i < len(fin.get("fcf_yahoo") or []):
                f = _finite(fin["fcf_yahoo"][i])
                if f is not None and i == 0:
                    result.fallbacks.append("fcff_approx_yahoo_fcf")
            fcff_hist.append(f)

        pos_years = sum(1 for v in fcff_hist[:4] if v is not None and v > 0)
        if pos_years < cfg.MIN_POSITIVE_FCFF_YEARS:
            result.failure_reason = "negative/unstable FCFF"
            return result
        fcff0 = fcff_hist[0]
        if cfg.REQUIRE_LATEST_FCFF_POSITIVE and (fcff0 is None or fcff0 <= 0):
            result.failure_reason = "negative/unstable FCFF"
            return result

        shares = fin.get("shares")
        cash = fin.get("cash")
        debt = fin.get("debt")
        if shares is None or shares <= 0:
            result.failure_reason = "missing diluted shares"
            return result
        if cash is None:
            result.failure_reason = "missing cash"
            return result

        price = _finite(
            (fin.get("info") or {}).get("currentPrice")
            or (fin.get("info") or {}).get("regularMarketPrice")
        )
        share_gate = check_share_count_integrity(
            price=price,
            shares=shares,
            market_cap=fin.get("market_cap"),
            info=fin.get("info"),
        )
        if share_gate.get("reason") == "share_count_unverified":
            result.fallbacks.append("share_count_unverified")
        if not share_gate.get("ok"):
            # Never rewrite shares via MarketCap/Price
            result.failure_reason = "share_count_mismatch"
            result.meta = {
                "share_count_check": share_gate,
                "version": cfg.VALUATION_VERSION,
            }
            return result

        tax0 = tax_s[0] if tax_s else None
        kd_meta: dict[str, Any] = {}
        wacc = calculate_wacc(
            beta=fin.get("beta"),
            market_cap=fin.get("market_cap"),
            debt=debt,
            tax_rate=tax0,
            fallbacks=result.fallbacks,
            fcff0=float(fcff0) if fcff0 else None,
            company_kd_pre=None,  # reserved: plug reliable YTM/credit spread when available
            kd_meta_out=kd_meta,
        )
        g_term = _terminal_g_for(fin.get("market_cap"))
        if wacc <= g_term:
            result.failure_reason = "WACC <= terminal growth"
            return result

        growth_path, g_recent, g_notes = estimate_growth_path(
            fin.get("revenue") or [],
            ebit_s,
            fcff_hist,
            terminal_g=g_term,
        )
        if "growth_fallback_terminal" in g_notes:
            result.fallbacks.append("growth_fallback_terminal")

        ev, proj, tv = calculate_dcf_value(float(fcff0), growth_path, wacc, g_term)
        if ev is None or tv is None:
            result.failure_reason = "DCF calculation failed"
            return result

        est = calculate_est_value_per_share(ev, cash, debt, shares)
        if est is None or est <= 0:
            result.failure_reason = "non-positive equity value"
            return result

        # Sanity vs last price if available (Est/Price band only; not MOS)
        if price and price > 0:
            ratio = est / price
            if ratio > cfg.EST_TO_PRICE_MAX_RATIO or ratio < cfg.EST_TO_PRICE_MIN_RATIO:
                result.failure_reason = "unreasonable Est.Value vs price"
                return result

        periods = fin.get("periods") or []
        result.financial_period = periods[0] if periods else "TTM/Annual"
        result.est_value = round(float(est), 2)
        result.wacc = round(wacc, 4)
        result.terminal_growth = round(g_term, 4)
        result.growth_path = growth_path
        conf = calculate_confidence(
            history_years=sum(1 for v in ebit_s if v is not None),
            fcff_hist=fcff_hist,
            fallbacks=result.fallbacks,
            growth_recent=g_recent,
        )
        scenarios = calculate_valuation_scenarios(
            fcff0=float(fcff0),
            base_path=growth_path,
            base_wacc=wacc,
            base_g=g_term,
            cash=cash,
            debt=debt,
            shares=shares,
        )
        result.bear_value = scenarios.get("bear")
        result.bull_value = scenarios.get("bull")
        result.valuation_spread = scenarios.get("spread")
        result.high_sensitivity = bool(scenarios.get("high_sensitivity"))
        conf = _apply_spread_to_confidence(conf, result.valuation_spread)
        # kd_default + high leverage → Confidence at most MEDIUM (do not suppress Base)
        high_lev_warn = bool(
            kd_meta.get("kd_default")
            and (
                (kd_meta.get("de") or 0) > cfg.HIGH_LEVERAGE_DE_MIN
                or (
                    kd_meta.get("debt_fcff") is not None
                    and kd_meta["debt_fcff"] > cfg.HIGH_LEVERAGE_DEBT_FCFF_MIN
                )
            )
        )
        if high_lev_warn:
            if "high_leverage_warning" not in result.fallbacks:
                result.fallbacks.append("high_leverage_warning")
            conf = _cap_confidence_at(conf, "MEDIUM")
        result.confidence = conf
        result.meta = {
            "version": cfg.VALUATION_VERSION,
            "fcff0": fcff0,
            "fcff_hist": fcff_hist[:4],
            "projected_fcff": proj,
            "terminal_value": tv,
            "enterprise_value": ev,
            "equity_value": ev + (cash or 0) - (debt or 0),
            "shares": shares,
            "cash": cash,
            "debt": debt,
            "growth_notes": g_notes,
            "g_recent": g_recent,
            "sector": fin.get("sector"),
            "industry": fin.get("industry"),
            "scenarios": scenarios,
            "bear_value": result.bear_value,
            "bull_value": result.bull_value,
            "valuation_spread": result.valuation_spread,
            "high_sensitivity": result.high_sensitivity,
            "share_count_check": share_gate,
            "kd": kd_meta,
            "high_leverage_warning": high_lev_warn,
        }
        return result
    except Exception as exc:
        result.failure_reason = f"exception: {type(exc).__name__}"
        result.meta = {"error": str(exc)[:300]}
        return result


def ensure_valuations(
    tickers: list[str],
    *,
    force: bool = False,
    max_age_days: int | None = None,
    pause_s: float | None = None,
    max_new: int | None = None,
) -> dict[str, ValuationResult]:
    """
    Compute & cache Est.Value for tickers missing/stale cache.
    Safe for Watchlist: per-ticker failures become —.
    max_new: optional cap on live Yahoo valuations per call (rest stay uncached/—).
    """
    from db import get_intrinsic_values, upsert_intrinsic_value

    age_days = cfg.VALUATION_CACHE_DAYS if max_age_days is None else max_age_days
    pause = cfg.YAHOO_PAUSE_S if pause_s is None else pause_s
    clean: list[str] = []
    seen: set[str] = set()
    for t in tickers:
        u = (t or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            clean.append(u)

    cached = get_intrinsic_values(clean)
    out: dict[str, ValuationResult] = {}
    now = datetime.now(timezone.utc)
    computed = 0

    def _fresh(row: dict[str, Any]) -> bool:
        if force:
            return False
        # Invalidate when growth/model methodology version changes (or missing)
        meta = _safe_json(row.get("meta_json"))
        cached_ver = str(meta.get("version") or "")
        if cached_ver != cfg.VALUATION_VERSION:
            return False
        ts = row.get("updated_at") or ""
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (now - dt).days < age_days and (
                row.get("est_value") is not None or bool(row.get("failure_reason"))
            )
        except Exception:
            return False

    def _from_row(t: str, row: dict[str, Any]) -> ValuationResult:
        gp = []
        try:
            gp = json.loads(row.get("growth_path") or "[]")
        except Exception:
            gp = []
        meta = _safe_json(row.get("meta_json"))
        bear = meta.get("bear_value")
        bull = meta.get("bull_value")
        spread = meta.get("valuation_spread")
        return ValuationResult(
            ticker=t,
            est_value=row.get("est_value"),
            method=row.get("model") or cfg.VALUATION_METHOD,
            financial_period=row.get("financial_period") or row.get("as_of"),
            wacc=row.get("wacc"),
            terminal_growth=row.get("terminal_growth"),
            growth_path=gp if isinstance(gp, list) else [],
            confidence=row.get("confidence"),
            failure_reason=row.get("failure_reason"),
            currency=row.get("currency"),
            as_of=row.get("updated_at"),
            meta=meta,
            bear_value=_finite(bear),
            bull_value=_finite(bull),
            valuation_spread=_finite(spread),
            high_sensitivity=bool(meta.get("high_sensitivity")),
        )

    for t in clean:
        row = cached.get(t)
        if row and _fresh(row):
            out[t] = _from_row(t, row)
            continue

        if max_new is not None and computed >= max_new:
            # Leave uncached; UI shows — until a later pass fills cache.
            out[t] = ValuationResult(
                ticker=t,
                failure_reason="pending valuation (open Watchlist again to continue)",
            )
            continue

        if pause:
            time.sleep(pause)
        res = calculate_dcf_valuation(t)
        upsert_intrinsic_value(
            t,
            est_value=res.est_value if res.ok else None,
            currency=res.currency,
            model=res.method,
            as_of=res.financial_period,
            notes=res.failure_reason,
            wacc=res.wacc,
            terminal_growth=res.terminal_growth,
            confidence=res.confidence,
            failure_reason=None if res.ok else res.failure_reason,
            growth_path=json.dumps(res.growth_path),
            financial_period=res.financial_period,
            meta_json=json.dumps(res.meta, default=str)[:8000],
        )
        out[t] = res
        computed += 1
    return out


def _safe_json(text: Any) -> dict[str, Any]:
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
