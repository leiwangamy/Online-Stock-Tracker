"""
Validation-case log for Valuation Engine baseline (frozen at v1.3).

When a ticker looks wrong vs market / fundamentals, append a case here or via
`record_validation_case(...)`. Do NOT retune valuation_config.py for one name.

Only revisit model knobs when several comparable companies share the same
failure mode (systemic), and document that decision in a new case batch.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CASES_PATH = Path(__file__).resolve().parent / "data" / "logs" / "valuation_validation_cases.jsonl"


def record_validation_case(
    ticker: str,
    *,
    issue: str,
    evidence: str,
    suspected_class: str | None = None,
    action: str = "observe_only",
    extras: dict[str, Any] | None = None,
) -> Path:
    """
    Append one validation case (JSONL). action default = observe_only
    (no config change). Use action='systemic_review' when proposing a rule change.
    """
    CASES_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticker": (ticker or "").strip().upper(),
        "issue": issue,
        "evidence": evidence,
        "suspected_class": suspected_class,
        "action": action,
        "baseline": "v1.3-frozen",
        "extras": extras or {},
    }
    with CASES_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return CASES_PATH


# Seed known cases from recent review (baseline freeze).
if __name__ == "__main__":
    seeds = [
        {
            "ticker": "GOOG",
            "issue": "share_count_mismatch",
            "evidence": "Price×Shares vs MarketCap diverge ~55%; dual-class / implied shares",
            "suspected_class": "dual_class_or_multi_share",
        },
        {
            "ticker": "UA",
            "issue": "negative_unstable_FCFF_and_share_class",
            "evidence": "Class C; Price×Shares vs mcap diverge; FCFF negative → —",
            "suspected_class": "dual_class_or_multi_share",
        },
        {
            "ticker": "DVA",
            "issue": "high_leverage_wacc_sensitivity",
            "evidence": "Debt/FCFF~9.7x D/E~1.16; tiered Kd v1.3 lowers Est vs flat Kd; wide Bear/Bull spread",
            "suspected_class": "high_leverage_healthcare_services",
        },
        {
            "ticker": "TSLA",
            "issue": "est_far_below_price_growth_collapse",
            "evidence": "Low/volatile FCFF path; Est << price; may be model-limit for hyper-growth narrative names",
            "suspected_class": "high_multiple_auto_ev",
        },
        {
            "ticker": "AAPL",
            "issue": "mature_low_growth_vs_market_multiple",
            "evidence": "Revenue-anchored low g → Est << price; not a unit bug; MOS large negative when price fresh",
            "suspected_class": "mega_cap_premium_multiple",
        },
    ]
    for s in seeds:
        record_validation_case(**s, action="observe_only")
    print(f"Wrote {len(seeds)} seed cases → {CASES_PATH}")
