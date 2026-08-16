"""
Conservative Liquidation Value (CLV) — independent of DCF Valuation Engine v1.3.

Balance-sheet asset floor with uniform, configurable recovery haircuts.
Not a target price and not Klarman's firm-specific appraisal — a mechanical
conservative liquidation equity estimate for ordinary shareholders.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yfinance as yf

import valuation_config as cfg
from valuation_engine import _finite, _row, check_share_count_integrity


@dataclass
class CLVResult:
    ticker: str
    clv_per_share: float | None = None
    adjusted_assets: float | None = None
    total_liabilities: float | None = None
    liquidation_equity: float | None = None
    shares: float | None = None
    confidence: str | None = None
    failure_reason: str | None = None
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    report_date: str | None = None
    bs_period_type: str | None = None  # "Quarterly" | "Annual"
    bs_age_days: int | None = None
    data_source: str = "yahoo_balance_sheet"
    total_assets: float | None = None
    currency: str | None = None
    lines: dict[str, Any] = field(default_factory=dict)
    as_of: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.clv_per_share is not None
            and self.clv_per_share >= 0
            and not self.failure_reason
        )

    def tooltip(self, price: float | None = None) -> str:
        if self.failure_reason:
            miss = ", ".join(self.missing_fields) if self.missing_fields else ""
            extra = f"\nMissing: {miss}" if miss else ""
            age_fail = (
                f"{self.bs_age_days} days"
                if self.bs_age_days is not None
                else "—"
            )
            hdr = (
                f"Balance Sheet Date: {self.report_date or '—'}\n"
                f"Source: {self.bs_period_type or '—'}\n"
                f"Age: {age_fail}\n"
                if (self.report_date or self.bs_period_type or self.bs_age_days is not None)
                else ""
            )
            return (
                f"Conservative Liquidation Value (CLV) unavailable\n"
                f"{hdr}"
                f"Reason: {self.failure_reason}{extra}"
            )
        L = self.lines or {}

        def _m(x: Any) -> str:
            v = _finite(x)
            if v is None:
                return "—"
            if abs(v) >= 1e9:
                return f"${v/1e9:.2f}B"
            if abs(v) >= 1e6:
                return f"${v/1e6:.1f}M"
            return f"${v:,.0f}"

        def _line(label: str, key: str, rec_key: str, recovery: float) -> str:
            gross = L.get(key)
            adj = L.get(rec_key)
            if gross is None and adj is None:
                return f"{label}: — (missing)"
            return (
                f"{label}: {_m(gross)} ×{recovery*100:.0f}% = {_m(adj if adj is not None else 0)}"
            )

        age_s = f"{self.bs_age_days} days" if self.bs_age_days is not None else "—"
        lines = [
            "Conservative Liquidation Value (CLV)",
            "Asset floor — not a target price / not a buy-sell signal",
            f"Balance Sheet Date: {self.report_date or '—'}",
            f"Source: {self.bs_period_type or '—'} (Yahoo)",
            f"Age: {age_s}",
            _line("Cash", "cash", "cash_adj", cfg.CLV_CASH_RECOVERY),
            _line(
                "Securities",
                "marketable_securities",
                "marketable_securities_adj",
                cfg.CLV_MARKETABLE_SECURITIES_RECOVERY,
            ),
            _line(
                "Receivables",
                "receivables",
                "receivables_adj",
                cfg.CLV_RECEIVABLE_RECOVERY,
            ),
            _line("Inventory", "inventory", "inventory_adj", cfg.CLV_INVENTORY_RECOVERY),
            _line(
                "Investments",
                "nonmarketable_investments",
                "nonmarketable_investments_adj",
                cfg.CLV_NONMARKETABLE_INVESTMENT_RECOVERY,
            ),
            _line("PP&E", "ppe", "ppe_adj", cfg.CLV_PPE_RECOVERY),
            (
                f"Goodwill/Intangibles: {_m(L.get('goodwill_intangibles'))} "
                f"×0% = {_m(L.get('goodwill_intangibles_adj') or 0)}"
            ),
            f"Other CA / DTA / Other LT (×0%): recorded, not recovered",
            f"Adjusted Assets: {_m(self.adjusted_assets)}",
            f"− Total Liabilities: {_m(self.total_liabilities)}",
            f"= Conservative Liquidation Equity: {_m(self.liquidation_equity)}",
            f"/ Shares: {self.shares:,.0f}" if self.shares else "/ Shares: —",
            f"= CLV/share: {_m(self.clv_per_share)}",
            f"Confidence: {self.confidence or '—'}",
        ]
        if price and self.clv_per_share is not None and price > 0:
            lines.append(f"CLV / Current Price: {self.clv_per_share / price * 100:.1f}%")
        if self.warnings:
            lines.append("Warnings: " + "; ".join(self.warnings))
        if self.missing_fields:
            lines.append("Missing fields: " + ", ".join(self.missing_fields))
        return "\n".join(lines)


def _bs_report_ts(bs) -> datetime | None:
    try:
        if bs is None or getattr(bs, "empty", True) or not len(bs.columns):
            return None
        ts = bs.columns[0]
        dt = pd_timestamp_to_utc(ts)
        return dt
    except Exception:
        return None


def pd_timestamp_to_utc(ts: Any) -> datetime | None:
    try:
        import pandas as pd

        t = pd.Timestamp(ts)
        if t.tzinfo is None:
            return t.to_pydatetime().replace(tzinfo=timezone.utc)
        return t.tz_convert("UTC").to_pydatetime()
    except Exception:
        try:
            s = str(ts)[:10]
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None


def select_latest_balance_sheet(ticker_obj: yf.Ticker) -> dict[str, Any]:
    """
    Prefer the newest available quarterly balance sheet; fall back to annual
    only if quarterly is missing or older than annual (should be rare).
    """
    annual = None
    quarterly = None
    try:
        annual = ticker_obj.balance_sheet
    except Exception:
        annual = None
    try:
        quarterly = ticker_obj.quarterly_balance_sheet
    except Exception:
        quarterly = None

    a_ts = _bs_report_ts(annual)
    q_ts = _bs_report_ts(quarterly)

    chosen = None
    period = None
    chosen_ts = None
    if q_ts is not None and a_ts is not None:
        if q_ts >= a_ts:
            chosen, period, chosen_ts = quarterly, "Quarterly", q_ts
        else:
            chosen, period, chosen_ts = annual, "Annual", a_ts
    elif q_ts is not None:
        chosen, period, chosen_ts = quarterly, "Quarterly", q_ts
    elif a_ts is not None:
        chosen, period, chosen_ts = annual, "Annual", a_ts
    else:
        return {
            "bs": None,
            "period_type": None,
            "report_date": None,
            "age_days": None,
            "annual_date": None,
            "quarterly_date": None,
        }

    now = datetime.now(timezone.utc)
    age_days = int((now - chosen_ts).total_seconds() // 86400) if chosen_ts else None
    return {
        "bs": chosen,
        "period_type": period,
        "report_date": chosen_ts.strftime("%Y-%m-%d") if chosen_ts else None,
        "age_days": age_days,
        "annual_date": a_ts.strftime("%Y-%m-%d") if a_ts else None,
        "quarterly_date": q_ts.strftime("%Y-%m-%d") if q_ts else None,
    }


def _latest(series: list[float | None] | None) -> float | None:
    if not series:
        return None
    return _finite(series[0])


def _bs_scalar(bs, *names: str) -> float | None:
    row = _row(bs, *names)
    return _latest(row)


def extract_balance_sheet_items(bs) -> dict[str, Any]:
    """Map Yahoo balance sheet → CLV input buckets (latest column)."""
    missing: list[str] = []
    notes: list[str] = []

    cash = _bs_scalar(bs, "Cash And Cash Equivalents")
    sti = _bs_scalar(bs, "Other Short Term Investments")
    combined = _bs_scalar(
        bs,
        "Cash Cash Equivalents And Short Term Investments",
        "Cash And Short Term Investments",
    )
    if cash is None and sti is None and combined is not None:
        cash = combined
        notes.append("cash_bucket_from_combined_sti")
    elif cash is not None and sti is None and combined is not None:
        residual = combined - cash
        if residual > 0:
            sti = residual
            notes.append("sti_inferred_from_combined_minus_cash")
    elif cash is None and sti is not None and combined is not None:
        residual = combined - sti
        if residual > 0:
            cash = residual
            notes.append("cash_inferred_from_combined_minus_sti")

    receivables = _bs_scalar(bs, "Receivables", "Accounts Receivable")
    inventory = _bs_scalar(bs, "Inventory")
    other_ca = _bs_scalar(bs, "Other Current Assets")

    afs = _bs_scalar(bs, "Available For Sale Securities")
    inv_adv = _bs_scalar(bs, "Investments And Advances")
    lt_equity = _bs_scalar(bs, "Long Term Equity Investment")
    other_inv = _bs_scalar(bs, "Other Investments")
    held_m = _bs_scalar(bs, "Held To Maturity Securities")

    # Marketable LT AFS counted at securities recovery (not double-count in nonmarketable)
    marketable_extra = afs
    nonmarketable = None
    if inv_adv is not None:
        nonmarketable = inv_adv
        if afs is not None:
            nonmarketable = max(0.0, inv_adv - afs)
            notes.append("nonmarketable_investments_adv_minus_afs")
    else:
        parts = [v for v in (lt_equity, other_inv, held_m) if v is not None]
        if parts:
            nonmarketable = sum(parts)

    # If AFS already inside STI path, avoid double count: only add afs if not in sti
    marketable = sti
    if marketable_extra is not None:
        if marketable is None:
            marketable = marketable_extra
            notes.append("marketable_includes_afs")
        # if both exist, AFS is typically LT — keep both (STI + AFS)

    ppe = _bs_scalar(bs, "Net PPE")
    goodwill = _bs_scalar(bs, "Goodwill")
    intang = _bs_scalar(bs, "Other Intangible Assets")
    gw_combo = _bs_scalar(bs, "Goodwill And Other Intangible Assets")
    if goodwill is None and intang is None and gw_combo is not None:
        goodwill = gw_combo
        notes.append("goodwill_from_combined_intangibles_line")
    elif goodwill is not None and intang is None and gw_combo is not None:
        intang = max(0.0, gw_combo - goodwill)

    dta = _bs_scalar(bs, "Non Current Deferred Taxes Assets", "Non Current Deferred Assets")
    other_lt = _bs_scalar(bs, "Other Non Current Assets")

    total_assets = _bs_scalar(bs, "Total Assets")
    total_liab = _bs_scalar(bs, "Total Liabilities Net Minority Interest")
    if total_liab is None:
        cur_l = _bs_scalar(bs, "Current Liabilities")
        ncur_l = _bs_scalar(bs, "Total Non Current Liabilities Net Minority Interest")
        if cur_l is not None and ncur_l is not None:
            total_liab = cur_l + ncur_l
            notes.append("liabilities_sum_current_plus_noncurrent")
        elif cur_l is not None:
            total_liab = cur_l
            missing.append("noncurrent_liabilities")
            notes.append("liabilities_current_only")

    report_date = None
    try:
        if bs is not None and not getattr(bs, "empty", True) and len(bs.columns):
            report_date = str(bs.columns[0])[:10]
    except Exception:
        report_date = None

    for label, val in (
        ("cash", cash),
        ("total_liabilities", total_liab),
        ("total_assets", total_assets),
    ):
        if val is None:
            missing.append(label)

    soft = []
    for label, val in (
        ("receivables", receivables),
        ("inventory", inventory),
        ("ppe", ppe),
        ("marketable_securities", marketable),
        ("nonmarketable_investments", nonmarketable),
    ):
        if val is None:
            soft.append(label)

    return {
        "cash": cash,
        "marketable_securities": marketable,
        "receivables": receivables,
        "inventory": inventory,
        "other_current_assets": other_ca,
        "nonmarketable_investments": nonmarketable,
        "ppe": ppe,
        "goodwill": goodwill,
        "intangibles": intang,
        "deferred_tax_assets": dta,
        "other_lt_assets": other_lt,
        "total_assets": total_assets,
        "total_liabilities": total_liab,
        "report_date": report_date,
        "missing_core": [m for m in missing],
        "missing_soft": soft,
        "notes": notes,
    }


def _recover(gross: float | None, rate: float) -> float | None:
    if gross is None:
        return None
    return float(gross) * float(rate)


def calculate_clv(ticker: str, *, ticker_obj: yf.Ticker | None = None) -> CLVResult:
    t = (ticker or "").strip().upper()
    now = datetime.now(timezone.utc).isoformat()
    result = CLVResult(ticker=t, as_of=now, data_source="yahoo_balance_sheet")
    try:
        tk = ticker_obj or yf.Ticker(t)
        try:
            info = tk.info or {}
        except Exception:
            info = {}
        result.currency = info.get("currency") or "USD"

        sel = select_latest_balance_sheet(tk)
        bs = sel.get("bs")
        result.bs_period_type = sel.get("period_type")
        result.report_date = sel.get("report_date")
        result.bs_age_days = sel.get("age_days")
        if result.bs_age_days is not None and result.bs_age_days > cfg.CLV_BALANCE_SHEET_STALE_DAYS:
            result.warnings.append(
                f"stale_balance_sheet_warning "
                f"(age={result.bs_age_days}d>{cfg.CLV_BALANCE_SHEET_STALE_DAYS}d)"
            )

        if bs is None or getattr(bs, "empty", True):
            result.failure_reason = "missing balance sheet"
            return result

        items = extract_balance_sheet_items(bs)
        # Prefer selector date (already from latest column of chosen frame)
        if not result.report_date:
            result.report_date = items.get("report_date")
        result.total_assets = items.get("total_assets")
        result.total_liabilities = items.get("total_liabilities")
        result.missing_fields = list(items.get("missing_core") or []) + list(
            items.get("missing_soft") or []
        )

        # Shares + integrity (same gate as DCF; never rewrite via Mcap/Price)
        shares = _finite(info.get("sharesOutstanding") or info.get("impliedSharesOutstanding"))
        if shares is None:
            sh_bs = _row(bs, "Ordinary Shares Number", "Share Issued")
            shares = _latest(sh_bs)
        price = _finite(info.get("currentPrice") or info.get("regularMarketPrice"))
        mcap = _finite(info.get("marketCap"))
        gate = check_share_count_integrity(
            price=price, shares=shares, market_cap=mcap, info=info
        )
        if shares is None or shares <= 0:
            result.failure_reason = "missing diluted shares"
            result.shares = shares
            return result
        if not gate.get("ok"):
            result.failure_reason = "share_count_mismatch"
            result.shares = shares
            result.warnings.append(str(gate.get("reason") or "share_count_mismatch"))
            result.lines = {"share_count_check": gate}
            return result
        if gate.get("reason") == "share_count_unverified":
            result.warnings.append("share_count_unverified")
        result.shares = shares

        # Core fields required
        if items.get("cash") is None:
            result.failure_reason = "missing cash"
            return result
        if items.get("total_liabilities") is None:
            result.failure_reason = "missing total liabilities"
            return result

        cash = items["cash"]
        sti = items.get("marketable_securities")
        recv = items.get("receivables")
        inv = items.get("inventory")
        other_ca = items.get("other_current_assets")
        nminv = items.get("nonmarketable_investments")
        ppe = items.get("ppe")
        gw = items.get("goodwill")
        intang = items.get("intangibles")
        dta = items.get("deferred_tax_assets")
        other_lt = items.get("other_lt_assets")

        cash_adj = _recover(cash, cfg.CLV_CASH_RECOVERY) or 0.0
        sti_adj = _recover(sti, cfg.CLV_MARKETABLE_SECURITIES_RECOVERY)
        recv_adj = _recover(recv, cfg.CLV_RECEIVABLE_RECOVERY)
        inv_adj = _recover(inv, cfg.CLV_INVENTORY_RECOVERY)
        other_ca_adj = _recover(other_ca, cfg.CLV_OTHER_CURRENT_ASSETS_RECOVERY)
        nminv_adj = _recover(nminv, cfg.CLV_NONMARKETABLE_INVESTMENT_RECOVERY)
        ppe_adj = _recover(ppe, cfg.CLV_PPE_RECOVERY)
        gw_adj = _recover(gw, cfg.CLV_GOODWILL_RECOVERY)
        intang_adj = _recover(intang, cfg.CLV_INTANGIBLES_RECOVERY)
        dta_adj = _recover(dta, cfg.CLV_DEFERRED_TAX_ASSETS_RECOVERY)
        other_lt_adj = _recover(other_lt, cfg.CLV_ROU_OTHER_LT_ASSETS_RECOVERY)

        # Important soft fields missing → confidence down, still compute if core ok
        soft_missing = items.get("missing_soft") or []
        for s in soft_missing:
            result.warnings.append(f"missing_{s}")

        parts = [
            cash_adj,
            sti_adj or 0.0,
            recv_adj or 0.0,
            inv_adj or 0.0,
            other_ca_adj or 0.0,
            nminv_adj or 0.0,
            ppe_adj or 0.0,
            gw_adj or 0.0,
            intang_adj or 0.0,
            dta_adj or 0.0,
            other_lt_adj or 0.0,
        ]
        adjusted = float(sum(parts))
        liab = float(items["total_liabilities"])
        equity = adjusted - liab
        clv = max(0.0, equity) / float(shares)

        gw_int_gross = None
        if gw is not None or intang is not None:
            gw_int_gross = (gw or 0.0) + (intang or 0.0)

        result.adjusted_assets = adjusted
        result.liquidation_equity = equity
        result.clv_per_share = round(clv, 2)
        result.lines = {
            "cash": cash,
            "cash_adj": cash_adj,
            "marketable_securities": sti,
            "marketable_securities_adj": sti_adj if sti is not None else 0.0,
            "receivables": recv,
            "receivables_adj": recv_adj if recv is not None else 0.0,
            "inventory": inv,
            "inventory_adj": inv_adj if inv is not None else 0.0,
            "other_current_assets": other_ca,
            "other_current_assets_adj": other_ca_adj if other_ca is not None else 0.0,
            "nonmarketable_investments": nminv,
            "nonmarketable_investments_adj": nminv_adj if nminv is not None else 0.0,
            "ppe": ppe,
            "ppe_adj": ppe_adj if ppe is not None else 0.0,
            "goodwill_intangibles": gw_int_gross,
            "goodwill_intangibles_adj": (gw_adj or 0.0) + (intang_adj or 0.0),
            "deferred_tax_assets": dta,
            "other_lt_assets": other_lt,
            "notes": list(items.get("notes") or [])
            + [
                f"bs_selected={result.bs_period_type}",
                f"bs_annual_latest={sel.get('annual_date')}",
                f"bs_quarterly_latest={sel.get('quarterly_date')}",
            ],
            "share_count_check": gate,
            "bs_period_type": result.bs_period_type,
            "bs_age_days": result.bs_age_days,
            "recoveries": {
                "cash": cfg.CLV_CASH_RECOVERY,
                "securities": cfg.CLV_MARKETABLE_SECURITIES_RECOVERY,
                "receivables": cfg.CLV_RECEIVABLE_RECOVERY,
                "inventory": cfg.CLV_INVENTORY_RECOVERY,
                "investments": cfg.CLV_NONMARKETABLE_INVESTMENT_RECOVERY,
                "ppe": cfg.CLV_PPE_RECOVERY,
                "goodwill_intangibles": 0.0,
            },
        }

        # Confidence
        score = 3
        if soft_missing:
            score -= min(2, len(soft_missing))
        if "share_count_unverified" in result.warnings:
            score -= 1
        if equity < 0:
            result.warnings.append("liquidation_equity_negative_floored_at_zero")
        if score >= 3:
            result.confidence = "HIGH"
        elif score >= 1:
            result.confidence = "MEDIUM"
        else:
            result.confidence = "LOW"
        return result
    except Exception as exc:
        result.failure_reason = f"exception: {type(exc).__name__}"
        result.warnings.append(str(exc)[:200])
        return result


def ensure_clvs(
    tickers: list[str],
    *,
    force: bool = False,
    max_new: int | None = 8,
    pause_s: float | None = None,
) -> dict[str, CLVResult]:
    """
    Compute CLV for tickers. Optional lightweight JSON cache in meta via intrinsic table
    is intentionally NOT used — CLV stays independent; process-local results only for now.
    """
    pause = cfg.CLV_YAHOO_PAUSE_S if pause_s is None else pause_s
    out: dict[str, CLVResult] = {}
    clean: list[str] = []
    seen: set[str] = set()
    for t in tickers:
        u = (t or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            clean.append(u)

    # Optional disk cache file (does not touch DCF intrinsic_value table)
    from pathlib import Path

    cache_path = Path(__file__).resolve().parent / "data" / "logs" / "clv_cache.json"
    cache: dict[str, Any] = {}
    if cache_path.exists() and not force:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    now = datetime.now(timezone.utc)
    computed = 0
    for t in clean:
        row = cache.get(t)
        fresh = False
        if row and not force:
            try:
                dt = datetime.fromisoformat(str(row.get("as_of", "")).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ver = str(row.get("version") or "")
                fresh = (now - dt).days < cfg.CLV_CACHE_DAYS and ver == cfg.CLV_VERSION
            except Exception:
                fresh = False
        if fresh:
            out[t] = CLVResult(
                ticker=t,
                clv_per_share=row.get("clv_per_share"),
                adjusted_assets=row.get("adjusted_assets"),
                total_liabilities=row.get("total_liabilities"),
                liquidation_equity=row.get("liquidation_equity"),
                shares=row.get("shares"),
                confidence=row.get("confidence"),
                failure_reason=row.get("failure_reason"),
                missing_fields=list(row.get("missing_fields") or []),
                warnings=list(row.get("warnings") or []),
                report_date=row.get("report_date"),
                bs_period_type=row.get("bs_period_type"),
                bs_age_days=row.get("bs_age_days"),
                data_source=row.get("data_source") or "yahoo_balance_sheet",
                total_assets=row.get("total_assets"),
                currency=row.get("currency"),
                lines=dict(row.get("lines") or {}),
                as_of=row.get("as_of"),
            )
            continue
        if max_new is not None and computed >= max_new:
            out[t] = CLVResult(
                ticker=t,
                failure_reason="pending CLV (open Watchlist again to continue)",
            )
            continue
        if pause and computed:
            time.sleep(pause)
        res = calculate_clv(t)
        out[t] = res
        cache[t] = {
            "version": cfg.CLV_VERSION,
            "as_of": res.as_of,
            "clv_per_share": res.clv_per_share,
            "adjusted_assets": res.adjusted_assets,
            "total_liabilities": res.total_liabilities,
            "liquidation_equity": res.liquidation_equity,
            "shares": res.shares,
            "confidence": res.confidence,
            "failure_reason": res.failure_reason,
            "missing_fields": res.missing_fields,
            "warnings": res.warnings,
            "report_date": res.report_date,
            "bs_period_type": res.bs_period_type,
            "bs_age_days": res.bs_age_days,
            "data_source": res.data_source,
            "total_assets": res.total_assets,
            "currency": res.currency,
            "lines": res.lines,
        }
        computed += 1

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception:
        pass
    return out
