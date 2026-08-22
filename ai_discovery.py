"""
News-Driven AI Discovery (LeiBot Paper Trading experiment).

NEWS / major positive event discovers the stock → analyze with existing engines
→ optional auto Paper Order with source_at_entry = AI_DISCOVERY.

Does not modify Research Center thresholds. Does not auto-add My Watchlist.
Knife Risk AUTO BLOCK and other hard safety gates are never bypassed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from db import get_conn, get_dashboard_by_tickers, get_setting, init_db, set_setting

log = logging.getLogger("leibot.discovery")

PT = ZoneInfo("America/Los_Angeles")

SRC_AI_DISCOVERY = "ai_discovery"

# Status workflow
ST_DISCOVERED = "DISCOVERED"
ST_ANALYZING = "ANALYZING"
ST_WATCH = "WATCH"
ST_TRADE_CANDIDATE = "TRADE_CANDIDATE"
ST_AUTO_BLOCK = "AUTO_BLOCK"
ST_ORDER_CREATED = "ORDER_CREATED"

# Dual-layer Discovery identity tags (stable for analytics / UI).
SRC_BROAD = "BROAD"
SRC_USASPENDING = "USASPENDING"
SRC_DOD = "DOD"
SRC_SEC = "SEC"
SRC_FDA = "FDA"
SRC_GOV_DISCLOSURE = "GOV_DISCLOSURE"

# Underlying event must be recent to compete as "Today's Discovery".
MAX_EVENT_AGE_DAYS = 90

SENT_POSITIVE = "POSITIVE"
SENT_NEGATIVE = "NEGATIVE"
SENT_NEUTRAL = "NEUTRAL"

# Pool admission / default display: unique events with Event Score >= 70.
# No Top-N cap on Broad Discovery — Official layer uses 5×5.
DEFAULT_MIN_EVENT_SCORE_DISPLAY = 70.0
MIN_EVENT_SCORE_FOR_POOL = 70.0
# Optional lower storage only for manual / legacy cache experiments.
MIN_EVENT_SCORE_FOR_STORAGE = 55.0

# Auto-trade still separate from discovery visibility / admission.
MIN_EVENT_SCORE_FOR_TRADE = 70.0
MIN_AI_SCORE_FOR_TRADE = 45.0
# Politician / official purchase: discovery clue only — never auto-order.
POLITICIAN_AUTO_TRADE = False

# Event categories (high-impact positive).
CAT_GOV_CONTRACT = "gov_contract"
CAT_COMMERCIAL_CONTRACT = "commercial_contract"
CAT_NEW_CUSTOMER = "new_customer"
CAT_ORDER_BACKLOG = "order_backlog"
CAT_GUIDANCE_RAISE = "guidance_raise"
CAT_EARNINGS_BEAT = "earnings_acceleration"
CAT_FDA_APPROVAL = "regulatory_approval"
CAT_PARTNERSHIP = "strategic_partnership"
CAT_INVESTMENT = "major_investment"
CAT_CAPACITY = "capacity_expansion"
CAT_NEW_MARKET = "new_market"
CAT_POLITICIAN_BUY = "politician_purchase"
CAT_OTHER_MAJOR = "other_major_positive"

# (category, base_score, keywords) — first match wins.
# Base scores unchanged; keywords expanded for thematic discovery radar only.
# NOTE: "8-K" / "SEC filing" alone are NOT positive-event keywords.
_EVENT_PATTERNS: list[tuple[str, float, list[str]]] = [
    (CAT_GOV_CONTRACT, 88.0, [
        "government contract", "awarded a contract", "dod contract", "defense contract",
        "nasa contract", "federal contract", "wins contract", "contract award",
        "awarded contract", "pentagon", "department of defense",
    ]),
    (CAT_FDA_APPROVAL, 86.0, [
        "fda approval", "fda approves", "receives approval", "regulatory approval",
        "cleared by fda", "ema approval", "drug approval",
    ]),
    (CAT_GUIDANCE_RAISE, 84.0, [
        "raises guidance", "raised guidance", "guidance raise", "lifts outlook",
        "raises outlook", "raises full-year", "boosts forecast", "raises forecast",
        "raises revenue guidance", "raises earnings guidance",
        "raises free cash flow guidance", "beat and raise",
    ]),
    (CAT_COMMERCIAL_CONTRACT, 80.0, [
        "multi-year contract", "major contract", "signed a contract", "wins deal",
        "lands contract", "supply agreement", "purchase agreement", "master agreement",
        "long-term supply", "large purchase order", "major customer agreement",
        "recurring revenue agreement", "recurring revenue",
    ]),
    (CAT_EARNINGS_BEAT, 78.0, [
        "beats estimates", "beats expectations", "tops estimates", "record revenue",
        "record earnings", "blowout quarter", "earnings beat", "revenue beat",
    ]),
    (CAT_ORDER_BACKLOG, 76.0, [
        "backlog", "order book", "large order", "significant order", "orders surge",
        "record backlog", "record orders",
    ]),
    (CAT_NEW_CUSTOMER, 74.0, [
        "new customer", "wins customer", "lands customer", "selected by",
        "chosen as supplier", "preferred supplier", "confirmed customer",
        "major customer",
    ]),
    (CAT_PARTNERSHIP, 72.0, [
        "strategic partnership", "strategic alliance", "joint venture",
        "collaboration agreement", "exclusive partnership", "major partnership",
    ]),
    (CAT_INVESTMENT, 70.0, [
        "invests in", "strategic investment", "takes stake", "equity investment",
        "funding round", "capital injection",
    ]),
    (CAT_CAPACITY, 68.0, [
        "capacity expansion", "new plant", "new factory", "expands production",
        "opens facility", "manufacturing expansion",
    ]),
    (CAT_NEW_MARKET, 66.0, [
        "enters market", "new market", "launches in", "expands into",
    ]),
    (CAT_POLITICIAN_BUY, 52.0, [
        "congressman buys", "senator buys", "politician bought", "disclosed purchase",
        "stock act", "congressional trade", "lawmaker bought", "official bought shares",
    ]),
]

_LOW_VALUE = [
    "price target", "initiates coverage", "maintains rating", "reiterates",
    "interview", "podcast", "what to know", "stock to watch", "appears on",
    "mentions", "rumor", "unconfirmed", "according to sources",
    "is down", "shares plummet", "stock fair value", "still rich or",
    "attractive after", "highlights:", "earnings call transcript",
    "earnings call summary", "earnings call highlights",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trading_day() -> str:
    return datetime.now(PT).date().isoformat()


def extract_event_period(summary: str, *, fallback_date: str | None = None) -> str:
    """
    Reporting / announcement period for underlying-event identity.
    Prefer fiscal quarter (Q2 2026), else YYYY-MM from headline date, else discovery day.
    """
    text = summary or ""
    low = text.lower()
    m = re.search(r"\b(q[1-4])\s*(?:fy)?\s*((?:20)?\d{2})\b", low)
    if m:
        q = m.group(1).upper()
        yr = m.group(2)
        if len(yr) == 2:
            yr = "20" + yr
        return f"{q} {yr}"
    # "Q2 Earnings" without year → use discovery-year (same reporting season).
    m = re.search(r"\b(q[1-4])\b", low)
    if m and fallback_date and len(fallback_date) >= 4:
        return f"{m.group(1).upper()} {fallback_date[:4]}"
    m = re.search(r"\b(fy|full[- ]?year)\s*((?:20)?\d{2})\b", low)
    if m:
        yr = m.group(2)
        if len(yr) == 2:
            yr = "20" + yr
        return f"FY{yr}"
    m = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # MM/DD in cached news lines — attach year from fallback discovery date.
    m = re.search(r"\b(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])\b", text)
    if m and fallback_date and len(fallback_date) >= 4:
        return f"{fallback_date[:4]}-{int(m.group(1)):02d}"
    if fallback_date:
        return fallback_date[:10]
    return _trading_day()


_LEAD_TICKER_RE = re.compile(r"^([A-Z]{1,5})\b")
_PEER_HEADLINE_RE = re.compile(
    r"^([A-Z]{1,5})\s+(?:Q[1-4]|FY\d{2,4}|Earnings|Revenue|Guidance|"
    r"Raises|Beats|Wins|Awarded|Secures|Gets|Reports)\b",
    re.IGNORECASE,
)


def headline_belongs_to_ticker(summary: str, ticker: str) -> bool:
    """
    True if the headline is about this ticker (not a peer pasted into its news feed).
    Example: under SAH, skip 'PAG Q2 Earnings…' / 'AN Q2 Earnings…'.
    Company-name headlines without a leading peer ticker are kept.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return False
    text = (summary or "").strip()
    if not text:
        return False
    # Explicit mention of our ticker wins.
    if re.search(rf"\b{re.escape(t)}\b", text, flags=re.IGNORECASE):
        return True
    # Peer dealer / sector roundup: another ticker leads with earnings language.
    peer = _PEER_HEADLINE_RE.match(text)
    if peer and peer.group(1).upper() != t:
        return False
    return True


def periods_compatible(a: str | None, b: str | None) -> bool:
    """Same reporting period, or both blank."""
    aa = (a or "").strip().upper()
    bb = (b or "").strip().upper()
    if not aa or not bb:
        return True
    return aa == bb

def underlying_event_key(
    ticker: str | None, category: str, period: str
) -> str:
    """Stable identity: one corporate event → one discovery (not one headline)."""
    t = (ticker or "").strip().upper()
    cat = (category or "").strip().lower()
    per = (period or "").strip().upper()
    raw = f"{t}|{cat}|{per}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def event_fingerprint(ticker: str | None, summary: str, category: str) -> str:
    """Backward-compatible name → underlying event key (ticker+category+period)."""
    period = extract_event_period(summary, fallback_date=_trading_day())
    return underlying_event_key(ticker, category, period)


def _title_tokens(text: str) -> set[str]:
    stop = {
        "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "after",
        "as", "at", "by", "with", "from", "is", "its", "stock", "shares",
    }
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in stop}


def headlines_similar(a: str, b: str, *, min_overlap: float = 0.35) -> bool:
    """Light semantic overlap for same-period same-category articles."""
    ta, tb = _title_tokens(a), _title_tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    return inter / max(1, min(len(ta), len(tb))) >= min_overlap


_NEG_SENTIMENT = [
    "lawsuit", "recall", "probe", "investigation", "downgrade", "misses",
    "cuts guidance", "bankruptcy", "going concern", "restatement",
    "material weakness", "impairment", "layoffs", "workforce reduction",
    "delisting", "defaults", "fraud", "subpoena", "warning letter",
]
_POS_SENTIMENT = [
    "raises guidance", "raised guidance", "approval", "awarded", "wins contract",
    "beats", "record", "partnership", "expansion", "clearance", "purchase order",
]


def classify_sentiment(text: str) -> str:
    """POSITIVE / NEGATIVE / NEUTRAL — independent of Impact Score magnitude."""
    low = (text or "").lower()
    neg = any(x in low for x in _NEG_SENTIMENT)
    pos = any(x in low for x in _POS_SENTIMENT) or bool(classify_event(text or ""))
    if neg and not pos:
        return SENT_NEGATIVE
    if pos and not neg:
        return SENT_POSITIVE
    if neg and pos:
        # Mixed language: prefer negative if hard risk words dominate.
        if any(x in low for x in ("bankruptcy", "fraud", "delisting", "defaults")):
            return SENT_NEGATIVE
        return SENT_NEUTRAL
    return SENT_NEUTRAL


def parse_underlying_event_date(
    *,
    explicit: str | None = None,
    summary: str = "",
    period: str | None = None,
) -> str | None:
    """Best-effort YYYY-MM-DD for the underlying corporate event (not harvest day)."""
    if explicit:
        d = str(explicit).strip()[:10]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            return d
        # YYYYMMDD
        if re.match(r"^\d{8}$", d):
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    text = summary or ""
    m = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    per = (period or "").strip().upper()
    # Q2 2024 → mid-quarter estimate
    m = re.match(r"Q([1-4])\s+(20\d{2})$", per)
    if m:
        q, yr = int(m.group(1)), m.group(2)
        month = {1: "02", 2: "05", 3: "08", 4: "11"}[q]
        return f"{yr}-{month}-15"
    m = re.match(r"FY(20\d{2})$", per)
    if m:
        return f"{m.group(1)}-06-30"
    m = re.match(r"(20\d{2})-(0[1-9]|1[0-2])$", per)
    if m:
        return f"{m.group(1)}-{m.group(2)}-15"
    return None


def is_recent_underlying_event(
    event_date: str | None,
    *,
    period: str | None = None,
    max_age_days: int = MAX_EVENT_AGE_DAYS,
) -> bool:
    """
    True if the underlying event (not retrieval day) is recent enough for
    Today's Discovery. Old FY2021/2008/etc. stay in History only.
    """
    today = datetime.now(PT).date()
    if event_date:
        try:
            d = datetime.strptime(str(event_date)[:10], "%Y-%m-%d").date()
            age = (today - d).days
            if age < -7:  # future-dated noise
                return False
            return age <= int(max_age_days)
        except ValueError:
            pass
    per = (period or "").strip().upper()
    m = re.search(r"(20\d{2})", per)
    if m:
        yr = int(m.group(1))
        # Period year more than 1 calendar year behind → not today's discovery.
        if yr < today.year - 1:
            return False
        if yr == today.year - 1 and today.month >= 4:
            # Prior-year period after Q1 of current year is stale for "today".
            return False
    # Unknown date: allow Broad news (usually fresh) but Official should prefer explicit dates.
    return event_date is None


def _host_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        host = (urlparse(str(url)).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


def enrich_discovery_source_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Human-readable source tags + website for Discovery table."""
    r = dict(row)
    tags_raw = r.get("source_tags") or ""
    sites_raw = r.get("source_sites") or ""
    tags = [t.strip() for t in str(tags_raw).split("|") if t.strip()] if tags_raw else []
    sites = [s.strip() for s in str(sites_raw).split("|") if s.strip()] if sites_raw else []
    meta: dict[str, Any] = {}
    try:
        raw_meta = r.get("event_meta_json") or r.get("meta_json")
        if raw_meta:
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else dict(raw_meta)
    except Exception:
        meta = {}
    if not tags and isinstance(meta.get("source_tags"), list):
        tags = [str(x) for x in meta["source_tags"] if x]
    if not sites and isinstance(meta.get("source_urls"), list):
        for u in meta["source_urls"]:
            h = _host_from_url(u)
            if h and h not in sites:
                sites.append(h)
    url = r.get("event_source_url") or r.get("source_url")
    if url:
        h = _host_from_url(url)
        if h and h not in sites:
            sites.insert(0, h)
    if not tags:
        ps = str(r.get("primary_source") or r.get("source_name") or "").strip()
        if ps:
            # Split combined labels like USASPENDING+DOD or "DoD defense.gov"
            tags = [t for t in re.split(r"[+|/,]", ps) if t.strip()] or [ps]
    tag_txt = "+".join(tags) if tags else "—"
    site_txt = ", ".join(sites[:3]) if sites else "—"
    r["source_tags_list"] = tags
    r["source_sites_list"] = sites
    r["source_display"] = tag_txt if site_txt == "—" else f"{tag_txt} · {site_txt}"
    r["source_link"] = url

    blob = " ".join(
        [
            " ".join(tags),
            str(r.get("primary_source") or ""),
            str(r.get("source_name") or ""),
            str(r.get("source_display") or ""),
        ]
    ).upper()
    official_keys = (
        SRC_USASPENDING,
        SRC_DOD,
        SRC_SEC,
        SRC_FDA,
        SRC_GOV_DISCLOSURE,
        "GOV_TRANSACTIONS",
        "GOVTX",
        "DEFENSE.GOV",
        "USASPENDING",
    )
    has_official = any(k in blob for k in official_keys)
    has_broad = SRC_BROAD in {t.upper() for t in tags} or (
        "BROAD" in blob and not has_official
    )
    # Legacy / untagged: treat thematic news as Broad; channel-like as Official.
    if not has_official and not has_broad:
        if any(k in blob for k in ("GOOGLE", "NEWS", "RSS", "THEMATIC")):
            has_broad = True
        else:
            # Default unknown high-impact contracts from official harvest → Official.
            has_official = True
    r["is_broad_layer"] = bool(has_broad or (not has_official))
    r["is_official_layer"] = bool(has_official)
    # Prefer dual membership when both tags present.
    if has_broad and has_official:
        r["is_broad_layer"] = True
        r["is_official_layer"] = True

    sent = str(r.get("sentiment") or "").upper().strip()
    if sent not in (SENT_POSITIVE, SENT_NEGATIVE, SENT_NEUTRAL):
        sent = SENT_NEUTRAL
    # Display-only: if stored NEUTRAL/empty, cheap re-read from headline so 好/中/差 is visible.
    if sent == SENT_NEUTRAL:
        guess = classify_sentiment(str(r.get("event_summary") or ""))
        if guess in (SENT_POSITIVE, SENT_NEGATIVE):
            sent = guess
    if sent == SENT_POSITIVE:
        r["news_quality"] = "good"
    elif sent == SENT_NEGATIVE:
        r["news_quality"] = "bad"
    else:
        r["news_quality"] = "medium"
    r["sentiment"] = sent
    return r


def partition_discovery_by_layer(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Split enriched Discovery rows into Broad vs Official 5×5 lists."""
    broad: list[dict[str, Any]] = []
    official: list[dict[str, Any]] = []
    for r in rows:
        er = enrich_discovery_source_fields(r) if "is_official_layer" not in r else r
        if er.get("is_broad_layer"):
            broad.append(er)
        if er.get("is_official_layer"):
            official.append(er)
        # Safety: never drop a row from both tabs.
        if not er.get("is_broad_layer") and not er.get("is_official_layer"):
            official.append(er)
    return {"broad": broad, "official": official}

def classify_event(headline: str) -> tuple[str, float, str] | None:
    """
    Return (category, base_event_score, reliability) or None if not high-impact.
    Ignores routine / low-information headlines.
    """
    title = (headline or "").strip()
    if len(title) < 12:
        return None
    low = title.lower()
    if any(x in low for x in _LOW_VALUE):
        return None
    # Prefer positive / award language; skip obvious negatives.
    neg = ["lawsuit", "recall", "probe", "investigation", "downgrade", "misses", "cuts guidance"]
    if any(n in low for n in neg):
        return None

    for cat, base, keys in _EVENT_PATTERNS:
        if any(k in low for k in keys):
            reliability = "headline"
            if any(x in low for x in ("sec ", "8-k", "filing", "fda", "awarded", "press release")):
                reliability = "primary_like"
            return cat, float(base), reliability
    return None


def refine_event_score(
    *,
    base: float,
    category: str,
    reliability: str,
    revenue: float | None = None,
    contract_value: float | None = None,
) -> float:
    score = float(base)
    if reliability == "primary_like":
        score += 6.0
    elif reliability == "unverified":
        score -= 12.0
    if category == CAT_POLITICIAN_BUY:
        score = min(score, 58.0)  # discovery clue, capped
    # Relative size: Contract / Revenue when both known.
    if (
        contract_value is not None
        and revenue is not None
        and revenue > 0
        and contract_value > 0
    ):
        ratio = contract_value / revenue
        if ratio >= 0.30:
            score += 12.0
        elif ratio >= 0.10:
            score += 8.0
        elif ratio >= 0.03:
            score += 4.0
        elif ratio < 0.005:
            score -= 8.0
    return max(0.0, min(100.0, round(score, 1)))


def _extract_money_usd(text: str) -> float | None:
    """Best-effort $Xm / $Xb parse from headline."""
    low = (text or "").lower()
    m = re.search(r"\$\s*([\d,.]+)\s*(billion|bn|b|million|mn|m)\b", low)
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = m.group(2)
    if unit in ("billion", "bn", "b"):
        return n * 1e9
    return n * 1e6


def _load_news_disk() -> dict[str, Any]:
    try:
        from market_data import _load_news_disk as _load

        return _load() or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# External thematic discovery radar (Google News RSS) — not list-bound.
# ---------------------------------------------------------------------------

_DISCOVERY_RSS_THEMES: list[tuple[str, str]] = [
    (
        "contracts",
        (
            '"government contract" OR "defense contract" OR "multi-year contract" '
            'OR "major contract" OR "supply agreement" OR "purchase order" '
            'OR "customer agreement" OR "contract award"'
        ),
    ),
    (
        "guidance",
        (
            '"raises guidance" OR "raises full-year" OR "raises outlook" '
            'OR "raises revenue guidance" OR "raises earnings guidance" '
            'OR "beat and raise" OR "raises free cash flow guidance"'
        ),
    ),
    (
        "backlog",
        '"record backlog" OR "record orders" OR "order backlog"',
    ),
    (
        "regulatory",
        '"FDA approval" OR "FDA approves" OR "regulatory approval"',
    ),
    (
        "strategic",
        (
            '"strategic partnership" OR "strategic investment" OR "confirmed customer" '
            'OR "recurring revenue" OR "major partnership"'
        ),
    ),
]

_PRIMARY_SOURCE_MARKERS = (
    "sec.gov",
    "edgar",
    "fda.gov",
    "businesswire.com",
    "globenewswire.com",
    "prnewswire.com",
    "accesswire.com",
    "investor.",
    "investors.",
    "ir.",
    "defense.gov",
    "usaspending.gov",
    "sam.gov",
)

_NAME_STOP = {
    "inc", "inc.", "corp", "corp.", "corporation", "ltd", "ltd.", "limited",
    "plc", "co", "co.", "company", "holdings", "holding", "group", "the",
    "class", "ordinary", "shares", "stock",
}

_symbol_index_cache: list[tuple[str, str]] | None = None
_symbol_index_built_at: float = 0.0

_TICKER_IN_PARENS = re.compile(r"\(([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\)")
_TICKER_LEAD = re.compile(
    r"^([A-Z]{1,5}(?:\.[A-Z]{1,2})?)\b(?=\s+(?:Q[1-4]|FY|Earnings|Raises|Beats|Wins|Awarded|Gets|Reports|Stock))"
)


def _normalize_company_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9\s]", " ", (name or "").lower())
    parts = [p for p in s.split() if p and p not in _NAME_STOP]
    return " ".join(parts)


def _build_symbol_name_index() -> list[tuple[str, str]]:
    """Longest-name-first (normalized_name, TICKER) from universe + dashboard."""
    global _symbol_index_cache, _symbol_index_built_at
    now = time.time()
    if _symbol_index_cache is not None and (now - _symbol_index_built_at) < 3600:
        return _symbol_index_cache
    pairs: dict[str, str] = {}
    try:
        from db import list_universe

        for r in list_universe() or []:
            t = str(r.get("ticker") or "").strip().upper()
            n = _normalize_company_name(str(r.get("name") or ""))
            if t and n and len(n) >= 3:
                pairs[n] = t
    except Exception:
        log.exception("universe symbol index")
    try:
        init_db()
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT ticker, name FROM dashboard_cache WHERE name IS NOT NULL"
            ).fetchall()
        for r in rows:
            t = str(r["ticker"] or "").strip().upper()
            n = _normalize_company_name(str(r["name"] or ""))
            if t and n and len(n) >= 3 and n not in pairs:
                pairs[n] = t
    except Exception:
        pass
    out = sorted(pairs.items(), key=lambda x: len(x[0]), reverse=True)
    _symbol_index_cache = out
    _symbol_index_built_at = now
    return out


def source_reliability_hint(
    *, title: str = "", link: str = "", publisher: str = ""
) -> str:
    """
    Discovery vs verification: news discovers; primary-like markers raise confidence.
    Presence of '8-K' alone does not classify positivity — only source quality.
    """
    blob = f"{title} {link} {publisher}".lower()
    if any(m in blob for m in _PRIMARY_SOURCE_MARKERS):
        return "primary_like"
    return "headline"


def _strip_google_title(title: str) -> str:
    t = (title or "").strip()
    # Google RSS: "Headline - Publisher"
    if " - " in t:
        left, right = t.rsplit(" - ", 1)
        if len(right) <= 40:
            return left.strip()
    return t


def fetch_google_news_theme(
    query: str, *, when: str = "2d", limit: int = 40
) -> list[dict[str, Any]]:
    """Fetch Google News RSS items for a thematic query."""
    import urllib.parse
    import urllib.request
    import xml.etree.ElementTree as ET

    q = f"{query} when:{when}"
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(q)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; LeiBotDiscovery/1.0; +local)"
            )
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
    except Exception as exc:
        log.warning("Google News RSS failed: %s", exc)
        return []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = ""
        src = item.find("{http://www.google.com/schemas/sitemap-news/0.9}source")
        if src is None:
            src = item.find("source")
        if src is not None:
            pub = (src.text or "").strip()
        if not title:
            continue
        out.append(
            {
                "title": _strip_google_title(title),
                "raw_title": title,
                "link": link,
                "publisher": pub,
            }
        )
    return out


def scan_thematic_news_headlines() -> list[dict[str, Any]]:
    """External thematic scan across focused material-event themes."""
    seen: set[str] = set()
    articles: list[dict[str, Any]] = []
    for theme, query in _DISCOVERY_RSS_THEMES:
        for it in fetch_google_news_theme(query):
            title = it.get("title") or ""
            key = title.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            articles.append({**it, "theme": theme})
        time.sleep(0.15)  # light politeness
    return articles


def _yahoo_symbol_search(query: str) -> tuple[str | None, str | None, float]:
    """Market-search fallback → (ticker, name, confidence)."""
    import urllib.parse
    import urllib.request

    q = (query or "").strip()
    if len(q) < 2:
        return None, None, 0.0
    url = (
        "https://query1.finance.yahoo.com/v1/finance/search?q="
        + urllib.parse.quote(q)
        + "&quotesCount=6&newsCount=0&listsCount=0"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None, None, 0.0
    quotes = data.get("quotes") if isinstance(data, dict) else None
    if not isinstance(quotes, list):
        return None, None, 0.0
    for qt in quotes:
        if not isinstance(qt, dict):
            continue
        sym = str(qt.get("symbol") or "").strip().upper()
        if not sym or sym.startswith("^"):
            continue
        # Prefer equity-like quotes.
        qtype = str(qt.get("quoteType") or qt.get("typeDisp") or "").lower()
        if qtype and qtype not in ("equity", "stock", ""):
            if "etf" in qtype or "index" in qtype:
                continue
        name = str(qt.get("shortname") or qt.get("longname") or "")
        return sym, name or None, 0.72
    return None, None, 0.0


_AMBIGUOUS_NAME_TOKENS = {
    "bill", "best", "real", "new", "one", "care", "gain", "mark", "national",
    "american", "united", "first", "general", "pacific", "digital", "global",
    "tech", "energy", "capital", "financial", "holdings", "group", "company",
    "nasdaq", "nyse", "market", "markets", "bio", "therapeutics", "pharma",
}


def _name_matches_headline(nname: str, headline_norm: str) -> bool:
    """Require whole-token / phrase match — never substring ('bill' in 'billions')."""
    if not nname or not headline_norm:
        return False
    parts = nname.split()
    if not parts:
        return False
    if len(parts) == 1:
        tok = parts[0]
        if len(tok) < 5 and tok in _AMBIGUOUS_NAME_TOKENS:
            return False
        if tok in _AMBIGUOUS_NAME_TOKENS and len(tok) < 8:
            return False
        tokens = set(headline_norm.split())
        return tok in tokens and len(tok) >= 4
    needle = f" {nname} "
    hay = f" {headline_norm} "
    return needle in hay


def resolve_ticker_from_headline(
    headline: str,
) -> dict[str, Any]:
    """
    Broad-market resolution (not limited to Watchlist / news_cache).
    Order: explicit ticker → company-name dictionary → Yahoo search.
    """
    text = (headline or "").strip()
    if not text:
        return {
            "ticker": None,
            "company_name": None,
            "confidence": 0.0,
            "method": "empty",
            "ok": False,
        }

    m = _TICKER_IN_PARENS.search(text.upper())
    if m:
        return {
            "ticker": m.group(1),
            "company_name": None,
            "confidence": 0.95,
            "method": "parens_ticker",
            "ok": True,
        }
    m = _TICKER_LEAD.match(text.upper())
    if m:
        return {
            "ticker": m.group(1),
            "company_name": None,
            "confidence": 0.9,
            "method": "lead_ticker",
            "ok": True,
        }

    low = _normalize_company_name(text)
    for nname, ticker in _build_symbol_name_index():
        if _name_matches_headline(nname, low):
            return {
                "ticker": ticker,
                "company_name": nname,
                "confidence": 0.84,
                "method": "universe_name",
                "ok": True,
            }

    # Search using leading proper-name chunk (before common verbs).
    chunk = re.split(
        r"\b(raises|raised|beats|wins|awarded|signs|lands|reports|announces|gets|nets|slips)\b",
        text,
        maxsplit=1,
        flags=re.I,
    )[0].strip(" -–—,:")
    if len(chunk) >= 3:
        sym, name, conf = _yahoo_symbol_search(chunk)
        if sym and conf >= 0.7:
            # Guard: returned company name must relate to the query chunk.
            n_chunk = _normalize_company_name(chunk)
            n_hit = _normalize_company_name(name or "")
            overlap = False
            if n_hit and n_chunk:
                ct = set(n_chunk.split())
                ht = set(n_hit.split())
                overlap = bool(ct & ht - _AMBIGUOUS_NAME_TOKENS) or (
                    n_hit in n_chunk or n_chunk in n_hit
                )
            if overlap or (len(chunk) <= 6 and chunk.upper() == sym):
                return {
                    "ticker": sym,
                    "company_name": name,
                    "confidence": conf,
                    "method": "yahoo_search",
                    "ok": True,
                }

    return {
        "ticker": None,
        "company_name": None,
        "confidence": 0.0,
        "method": "unresolved",
        "ok": False,
    }


def log_unresolved_discovery(
    *,
    headline: str,
    source_name: str | None,
    source_url: str | None,
    category: str | None,
    event_score: float | None,
    notes: str | None = None,
) -> None:
    init_db()
    fp = hashlib.sha256((headline or "").strip().lower().encode("utf-8")).hexdigest()[:40]
    now = _utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO ai_discovery_unresolved (
              headline_fingerprint, headline, source_name, source_url,
              event_category, event_score, resolve_notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(headline_fingerprint) DO UPDATE SET
              event_score = excluded.event_score,
              resolve_notes = excluded.resolve_notes,
              created_at = excluded.created_at
            """,
            (
                fp,
                (headline or "")[:500],
                source_name,
                source_url,
                category,
                event_score,
                notes,
                now,
            ),
        )


def list_unresolved_discoveries(*, limit: int = 50) -> list[dict[str, Any]]:
    """Open unresolved only — never tradeable / Priority until confidently resolved."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM ai_discovery_unresolved
            WHERE COALESCE(status, 'open') = 'open'
            ORDER BY created_at DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


def count_unresolved_discoveries() -> int:
    init_db()
    with get_conn() as conn:
        n = conn.execute(
            """
            SELECT COUNT(*) AS n FROM ai_discovery_unresolved
            WHERE COALESCE(status, 'open') = 'open'
            """
        ).fetchone()["n"]
    return int(n or 0)


def count_resolved_unresolved_today() -> int:
    """How many previously-unresolved headlines were resolved today (UTC date)."""
    init_db()
    day = _trading_day()
    with get_conn() as conn:
        n = conn.execute(
            """
            SELECT COUNT(*) AS n FROM ai_discovery_unresolved
            WHERE status = 'resolved'
              AND resolved_at IS NOT NULL
              AND substr(resolved_at, 1, 10) = ?
            """,
            (day,),
        ).fetchone()["n"]
    return int(n or 0)


def retry_unresolved_ticker_resolution(
    *,
    limit: int = 40,
    min_confidence: float = 0.75,
) -> dict[str, Any]:
    """
    Periodically re-attempt ticker resolution for open unresolved rows.
    Only promotes when resolve confidence >= min_confidence (no guessing).
    Preserves original discovery timestamp (created_at) and underlying event_date.
    Never marks unresolved as Priority / trade-eligible by itself.
    """
    init_db()
    rows = list_unresolved_discoveries(limit=limit)
    now = _utc_now_iso()
    resolved_n = 0
    still_open = 0
    errors = 0
    for u in rows:
        uid = int(u["id"])
        headline = str(u.get("headline") or "")
        try:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE ai_discovery_unresolved SET last_retry_at = ? WHERE id = ?",
                    (now, uid),
                )
            resolved = resolve_ticker_from_headline(headline)
            conf = float(resolved.get("confidence") or 0)
            ok = bool(resolved.get("ok")) and conf >= float(min_confidence)
            ticker = (resolved.get("ticker") or "").strip().upper() if ok else ""
            if not ok or not ticker:
                still_open += 1
                continue

            classified = classify_event(headline)
            if classified:
                cat, base, reliability = classified
            else:
                cat = str(u.get("event_category") or CAT_OTHER_MAJOR)
                base = float(u.get("event_score") or 65.0)
                reliability = "retry_resolve"
            score = refine_event_score(
                base=float(base),
                category=str(cat),
                reliability=str(reliability),
            )
            impact = max(
                float(score),
                float(u.get("event_score") or 0),
            )
            sent = classify_sentiment(headline)
            period = extract_event_period(headline, fallback_date=_trading_day())
            edate = parse_underlying_event_date(summary=headline, period=period)
            recent = is_recent_underlying_event(edate, period=period)
            # Preserve original discovery day from unresolved created_at.
            created = str(u.get("created_at") or now)
            disc_day = created[:10] if len(created) >= 10 else _trading_day()
            tags_raw = str(u.get("source_name") or "")
            tags = [t for t in re.split(r"[+|/,]", tags_raw) if t.strip()]
            if not tags:
                tags = ["BROAD"]
            fp = underlying_event_key(ticker, str(cat), str(period))
            ev = upsert_event(
                fingerprint=fp,
                ticker=ticker,
                company_name=resolved.get("company_name"),
                category=str(cat),
                summary=headline,
                source_name="+".join(tags),
                source_url=u.get("source_url"),
                event_score=impact,
                reliability=str(reliability),
                event_period=str(period),
                sentiment=sent,
                impact_score=impact,
                event_date=edate,
                source_tags=tags,
                source_sites=[],
                is_recent=recent,
                discovered_at=disc_day,
                meta={
                    "resolve_method": resolved.get("method"),
                    "resolve_confidence": conf,
                    "layer": "unresolved_retry",
                    "original_unresolved_id": uid,
                    "original_discovered_at": created,
                },
            )
            cand = ensure_candidate_from_event(int(ev["id"]))
            if cand and sent == SENT_NEGATIVE:
                _set_candidate_fields(
                    int(cand["id"]),
                    block_reason="negative_event_risk_monitor",
                    trade_eligible=0,
                    updated_at=now,
                )
            elif cand and not recent:
                _set_candidate_fields(
                    int(cand["id"]),
                    block_reason="stale_event_date_history_only",
                    trade_eligible=0,
                    updated_at=now,
                )
            with get_conn() as conn:
                conn.execute(
                    """
                    UPDATE ai_discovery_unresolved SET
                      status = 'resolved',
                      resolved_ticker = ?,
                      resolved_at = ?,
                      resolved_event_id = ?,
                      resolve_notes = ?,
                      last_retry_at = ?
                    WHERE id = ?
                    """,
                    (
                        ticker,
                        now,
                        int(ev["id"]),
                        f"resolved via {resolved.get('method')} conf={conf:.2f}",
                        now,
                        uid,
                    ),
                )
            resolved_n += 1
        except Exception:
            log.exception("retry unresolved id=%s", uid)
            errors += 1
            still_open += 1
    set_setting("ai_discovery_unresolved_retry_at", now)
    return {
        "checked": len(rows),
        "resolved": resolved_n,
        "still_open": still_open,
        "errors": errors,
        "open_remaining": count_unresolved_discoveries(),
        "resolved_today": count_resolved_unresolved_today(),
    }


def maybe_retry_unresolved(*, force: bool = False, min_interval_sec: int = 900) -> dict[str, Any]:
    """Throttle background retry (default every 15 minutes)."""
    if not force:
        last = get_setting("ai_discovery_unresolved_retry_at", "") or ""
        if last:
            try:
                from datetime import datetime, timezone

                ts = str(last).replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - dt).total_seconds()
                if age < float(min_interval_sec):
                    return {
                        "skipped": True,
                        "age_sec": int(age),
                        "open_remaining": count_unresolved_discoveries(),
                        "resolved_today": count_resolved_unresolved_today(),
                    }
            except Exception:
                pass
    return retry_unresolved_ticker_resolution()


def discovery_threshold_counts() -> dict[str, Any]:
    """Natural score distribution for experiment (no Top-N)."""
    init_db()
    with get_conn() as conn:
        rows = [
            float(r["event_score"])
            for r in conn.execute(
                "SELECT event_score FROM ai_discovery_candidates WHERE event_score IS NOT NULL"
            ).fetchall()
        ]
    out: dict[str, Any] = {"total_stored": len(rows)}
    for thr in (70, 75, 80, 85):
        ev = sum(1 for s in rows if s >= thr)
        # unique stocks at threshold need tickers
        out[f"events_ge_{thr}"] = ev
    with get_conn() as conn:
        for thr in (70, 75, 80, 85):
            n = conn.execute(
                """
                SELECT COUNT(DISTINCT upper(ticker)) AS n
                FROM ai_discovery_candidates
                WHERE event_score IS NOT NULL AND event_score >= ?
                """,
                (float(thr),),
            ).fetchone()["n"]
            out[f"stocks_ge_{thr}"] = int(n or 0)
    return out


def harvest_external_thematic_news(
    *, min_score: float | None = None
) -> dict[str, Any]:
    """
    Broader-market discovery radar:
      thematic news → classify → score → resolve ticker → dedupe → pool if >=70.
    Unresolved tickers go to inspection log (not tradable pool).
    No Top-N quantity cap.
    """
    init_db()
    thr = float(min_score if min_score is not None else MIN_EVENT_SCORE_FOR_POOL)
    articles = scan_thematic_news_headlines()
    created = 0
    merged = 0
    skipped = 0
    unresolved = 0
    seen_fp: set[str] = set()

    for art in articles:
        title = str(art.get("title") or "").strip()
        if not title:
            skipped += 1
            continue
        classified = classify_event(title)
        if not classified:
            skipped += 1
            continue
        cat, base, _rel0 = classified
        reliability = source_reliability_hint(
            title=title,
            link=str(art.get("link") or ""),
            publisher=str(art.get("publisher") or ""),
        )
        contract_val = _extract_money_usd(title)
        score = refine_event_score(
            base=base,
            category=cat,
            reliability=reliability,
            contract_value=contract_val,
        )
        if score < thr:
            skipped += 1
            continue

        resolved = resolve_ticker_from_headline(title)
        if not resolved.get("ok") or float(resolved.get("confidence") or 0) < 0.7:
            log_unresolved_discovery(
                headline=title,
                source_name=art.get("publisher") or "google_news",
                source_url=art.get("link"),
                category=cat,
                event_score=score,
                notes=f"method={resolved.get('method')}; conf={resolved.get('confidence')}",
            )
            unresolved += 1
            continue

        ticker = str(resolved["ticker"]).upper()
        period = extract_event_period(title, fallback_date=_trading_day())
        fp = underlying_event_key(ticker, cat, period)
        src = art.get("publisher") or "google_news_rss"
        if fp in seen_fp:
            upsert_event(
                fingerprint=fp,
                ticker=ticker,
                company_name=resolved.get("company_name"),
                category=cat,
                summary=title,
                source_name=src,
                source_url=art.get("link"),
                event_score=score,
                reliability=reliability,
                event_period=period,
                meta={
                    "theme": art.get("theme"),
                    "contract_value_usd": contract_val,
                    "resolve_method": resolved.get("method"),
                },
            )
            merged += 1
            continue
        seen_fp.add(fp)
        ev = upsert_event(
            fingerprint=fp,
            ticker=ticker,
            company_name=resolved.get("company_name"),
            category=cat,
            summary=title,
            source_name=src,
            source_url=art.get("link"),
            event_score=score,
            reliability=reliability,
            event_period=period,
            meta={
                "theme": art.get("theme"),
                "contract_value_usd": contract_val,
                "resolve_method": resolved.get("method"),
                "resolve_confidence": resolved.get("confidence"),
            },
        )
        if ev.get("inserted"):
            created += 1
        else:
            merged += 1
        ensure_candidate_from_event(int(ev["id"]))

    merge_stats = merge_duplicate_underlying_events()
    set_setting("ai_discovery_last_harvest_at", _utc_now_iso())
    set_setting("ai_discovery_last_harvest_mode", "external_thematic")
    return {
        "mode": "external_thematic",
        "scanned_articles": len(articles),
        "created_events": created,
        "merged_into_existing": merged,
        "skipped": skipped,
        "unresolved": unresolved,
        "merged_duplicates": merge_stats.get("merged", 0),
        "threshold_counts": discovery_threshold_counts(),
    }


def _headlines_from_news_payload(news: dict[str, Any]) -> list[str]:
    """Extract headline strings from cached news payload (detail lines or lists)."""
    out: list[str] = []
    detail = news.get("detail")
    if isinstance(detail, str) and detail.strip():
        for line in detail.splitlines():
            line = line.strip()
            if not line or "无重要新闻" in line:
                continue
            m = re.match(r"^\[([A-EO])[+\-−]?\]\s+\S+\s+(.+)$", line)
            if m:
                out.append(m.group(2).strip())
            else:
                out.append(re.sub(r"^\[[^\]]+\]\s*", "", line).strip())
    for key in ("headlines", "items", "titles", "top", "major", "parsed"):
        arr = news.get(key)
        if not isinstance(arr, list):
            continue
        for h in arr:
            if isinstance(h, str) and h.strip():
                out.append(h.strip())
            elif isinstance(h, dict):
                t = h.get("title") or h.get("headline")
                if t:
                    out.append(str(t).strip())
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def harvest_events_from_news_cache(*, min_score: float | None = None) -> dict[str, Any]:
    """
    Legacy supplemental scan of shared news_cache (list-bound).
    Prefer harvest_external_thematic_news for broader-market discovery.
    """
    init_db()
    thr = float(min_score if min_score is not None else MIN_EVENT_SCORE_FOR_POOL)
    disk = _load_news_disk()
    created = 0
    skipped = 0
    seen_fp: set[str] = set()

    for ticker, entry in disk.items():
        if not isinstance(entry, dict):
            continue
        t = str(ticker or "").strip().upper()
        if not t or t.startswith("_"):
            continue
        news = entry.get("news") if isinstance(entry.get("news"), dict) else entry
        if not isinstance(news, dict):
            continue
        tone = str(news.get("tone") or "").lower()
        status = str(news.get("status") or "").upper()
        if tone == "neg" or status == "NEGATIVE":
            continue

        for title in _headlines_from_news_payload(news):
            if not headline_belongs_to_ticker(title, t):
                skipped += 1
                continue
            classified = classify_event(title)
            if not classified:
                skipped += 1
                continue
            cat, base, reliability = classified
            contract_val = _extract_money_usd(title)
            score = refine_event_score(
                base=base,
                category=cat,
                reliability=reliability,
                contract_value=contract_val,
            )
            if score < thr:
                skipped += 1
                continue
            period = extract_event_period(title, fallback_date=_trading_day())
            fp = underlying_event_key(t, cat, period)
            if fp in seen_fp:
                upsert_event(
                    fingerprint=fp,
                    ticker=t,
                    company_name=None,
                    category=cat,
                    summary=title,
                    source_name="news_cache",
                    source_url=None,
                    event_score=score,
                    reliability=reliability,
                    event_period=period,
                    meta={"contract_value_usd": contract_val},
                )
                continue
            seen_fp.add(fp)
            ev = upsert_event(
                fingerprint=fp,
                ticker=t,
                company_name=None,
                category=cat,
                summary=title,
                source_name="news_cache",
                source_url=None,
                event_score=score,
                reliability=reliability,
                event_period=period,
                meta={"contract_value_usd": contract_val},
            )
            if ev.get("inserted"):
                created += 1
            ensure_candidate_from_event(ev["id"])
    merged = merge_duplicate_underlying_events()
    set_setting("ai_discovery_last_harvest_at", _utc_now_iso())
    return {
        "mode": "news_cache",
        "created_events": created,
        "skipped": skipped,
        "scanned_tickers": len(disk),
        "merged_duplicates": merged.get("merged", 0),
    }


def upsert_event(
    *,
    fingerprint: str,
    ticker: str | None,
    company_name: str | None,
    category: str,
    summary: str,
    source_name: str | None,
    source_url: str | None,
    event_score: float,
    reliability: str,
    event_period: str | None = None,
    meta: dict[str, Any] | None = None,
    sentiment: str | None = None,
    impact_score: float | None = None,
    event_date: str | None = None,
    source_tags: list[str] | None = None,
    source_sites: list[str] | None = None,
    is_recent: bool | None = None,
    discovered_at: str | None = None,
) -> dict[str, Any]:
    """
    Insert or merge by underlying event fingerprint (ticker+category+period).
    Multiple articles/sources → one event; Event Scores are NOT summed.
    """
    init_db()
    now = _utc_now_iso()
    day = discovered_at or _trading_day()
    if discovered_at and len(str(discovered_at)) >= 10:
        day = str(discovered_at)[:10]
    period = event_period or extract_event_period(summary, fallback_date=day)
    src = source_name or "news_cache"
    sent = sentiment or classify_sentiment(summary)
    impact = float(impact_score if impact_score is not None else event_score)
    edate = event_date or parse_underlying_event_date(summary=summary, period=period)
    tags = list(source_tags or [])
    sites = list(source_sites or [])
    if source_url:
        h = _host_from_url(source_url)
        if h and h not in sites:
            sites.append(h)
    recent_flag = (
        bool(is_recent)
        if is_recent is not None
        else is_recent_underlying_event(edate, period=period)
    )
    tags_s = "|".join(dict.fromkeys([t for t in tags if t]))
    sites_s = "|".join(dict.fromkeys([s for s in sites if s]))
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ai_discovery_events WHERE event_fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if row:
            ev = dict(row)
            try:
                meta_old = json.loads(ev.get("meta_json") or "{}")
            except Exception:
                meta_old = {}
            supporting = []
            try:
                supporting = json.loads(ev.get("supporting_sources_json") or "[]")
            except Exception:
                supporting = []
            if not isinstance(supporting, list):
                supporting = []
            # Append supporting article if distinct from primary summary.
            primary_sum = str(ev.get("event_summary") or "")
            if summary and summary.strip() and summary.strip() != primary_sum.strip():
                entry = {
                    "summary": summary[:400],
                    "source": src,
                    "url": source_url,
                    "ts": now,
                    "event_score": float(event_score),
                    "reliability": reliability,
                }
                # Avoid duplicate supporting lines.
                if not any(
                    str(s.get("summary") or "").strip() == summary.strip()
                    for s in supporting
                    if isinstance(s, dict)
                ):
                    supporting.append(entry)
            # Keep strongest Event Score (do not average / sum).
            new_score = float(ev.get("event_score") or 0)
            keep_primary = True
            if float(event_score) > new_score + 1e-9:
                new_score = float(event_score)
                keep_primary = False
            elif float(event_score) >= new_score - 1e-9 and reliability == "primary_like":
                # Prefer more reliable wording as primary when scores similar.
                if str(ev.get("reliability") or "") != "primary_like":
                    keep_primary = False

            primary_source = ev.get("primary_source") or ev.get("source_name") or src
            event_summary = primary_sum
            rel = ev.get("reliability") or reliability
            if not keep_primary:
                # Demote old primary into supporting.
                if primary_sum.strip():
                    supporting.insert(
                        0,
                        {
                            "summary": primary_sum[:400],
                            "source": primary_source,
                            "url": ev.get("source_url"),
                            "ts": now,
                            "event_score": float(ev.get("event_score") or 0),
                            "reliability": ev.get("reliability"),
                        },
                    )
                event_summary = summary[:500]
                primary_source = src
                rel = reliability

            earliest = ev.get("earliest_discovered_at") or ev.get("discovered_at") or day
            latest = day
            meta_old.update(meta or {})
            old_tags = [t for t in str(ev.get("source_tags") or "").split("|") if t.strip()]
            old_sites = [t for t in str(ev.get("source_sites") or "").split("|") if t.strip()]
            tags_s = "|".join(dict.fromkeys(old_tags + tags))
            sites_s = "|".join(dict.fromkeys(old_sites + sites))
            try:
                impact = max(impact, float(ev["impact_score"])) if ev.get("impact_score") is not None else impact
            except (TypeError, ValueError):
                pass
            edate_keep = ev.get("event_date") or edate
            recent_flag = is_recent_underlying_event(edate_keep, period=period)
            old_sent = ev.get("sentiment") or SENT_NEUTRAL
            if sent == SENT_NEGATIVE:
                sent_keep = SENT_NEGATIVE
            elif old_sent == SENT_NEGATIVE:
                sent_keep = SENT_NEGATIVE
            elif sent == SENT_POSITIVE or old_sent == SENT_POSITIVE:
                sent_keep = SENT_POSITIVE
            else:
                sent_keep = SENT_NEUTRAL
            meta_old["source_tags"] = [t for t in tags_s.split("|") if t]
            conn.execute(
                """
                UPDATE ai_discovery_events SET
                  event_summary = ?, source_name = ?, source_url = ?,
                  event_score = ?, reliability = ?, event_period = ?,
                  primary_source = ?, supporting_sources_json = ?,
                  supporting_count = ?, earliest_discovered_at = ?,
                  latest_confirmed_at = ?, meta_json = ?,
                  sentiment = ?, impact_score = ?, event_date = ?,
                  source_tags = ?, source_sites = ?, is_recent = ?
                WHERE id = ?
                """,
                (
                    event_summary,
                    primary_source,
                    source_url if not keep_primary else ev.get("source_url"),
                    new_score,
                    rel,
                    period,
                    primary_source,
                    json.dumps(supporting[-20:]),  # cap
                    max(0, len(supporting)),
                    earliest,
                    latest,
                    json.dumps(meta_old),
                    sent_keep,
                    impact,
                    edate_keep,
                    tags_s,
                    sites_s,
                    1 if recent_flag else 0,
                    int(ev["id"]),
                ),
            )
            # Sync candidate denormalized fields (same event_id).
            conn.execute(
                """
                UPDATE ai_discovery_candidates SET
                  event_summary = ?, event_score = ?, source_name = ?,
                  event_period = ?, primary_source = ?, supporting_count = ?,
                  sentiment = ?, impact_score = ?, event_date = ?,
                  source_tags = ?, source_sites = ?, is_recent = ?,
                  updated_at = ?
                WHERE event_id = ?
                """,
                (
                    event_summary,
                    new_score,
                    primary_source,
                    period,
                    primary_source,
                    max(0, len(supporting)),
                    sent_keep,
                    impact,
                    edate_keep,
                    tags_s,
                    sites_s,
                    1 if recent_flag else 0,
                    now,
                    int(ev["id"]),
                ),
            )
            row = conn.execute(
                "SELECT * FROM ai_discovery_events WHERE id = ?", (int(ev["id"]),)
            ).fetchone()
            return {**dict(row), "inserted": False}

        cur = conn.execute(
            """
            INSERT INTO ai_discovery_events (
              event_fingerprint, ticker, company_name, event_category, event_summary,
              source_name, source_url, event_score, reliability, discovered_at,
              meta_json, created_at, event_period, primary_source,
              supporting_sources_json, supporting_count,
              earliest_discovered_at, latest_confirmed_at,
              sentiment, impact_score, event_date, source_tags, source_sites, is_recent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fingerprint,
                (ticker or "").upper() or None,
                company_name,
                category,
                summary[:500],
                src,
                source_url,
                float(event_score),
                reliability,
                day,
                json.dumps(meta or {}),
                now,
                period,
                src,
                "[]",
                day,
                day,
                sent,
                impact,
                edate,
                tags_s,
                sites_s,
                1 if recent_flag else 0,
            ),
        )
        eid = int(cur.lastrowid)
        row = conn.execute(
            "SELECT * FROM ai_discovery_events WHERE id = ?", (eid,)
        ).fetchone()
    return {**dict(row), "inserted": True}


def ensure_candidate_from_event(event_id: int) -> dict[str, Any] | None:
    init_db()
    with get_conn() as conn:
        ev = conn.execute(
            "SELECT * FROM ai_discovery_events WHERE id = ?", (int(event_id),)
        ).fetchone()
        if not ev:
            return None
        ev = dict(ev)
        ticker = (ev.get("ticker") or "").upper()
        if not ticker:
            return None
        existing = conn.execute(
            "SELECT * FROM ai_discovery_candidates WHERE ticker = ? AND event_id = ?",
            (ticker, int(event_id)),
        ).fetchone()
        if existing:
            return dict(existing)
        now = _utc_now_iso()
        cur = conn.execute(
            """
            INSERT INTO ai_discovery_candidates (
              ticker, company_name, status, discovery_date, event_id, event_category,
              event_summary, event_score, source_name, trade_eligible,
              event_period, primary_source, supporting_count,
              sentiment, impact_score, event_date, source_tags, source_sites, is_recent,
              updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker,
                ev.get("company_name"),
                ST_DISCOVERED,
                ev.get("discovered_at") or _trading_day(),
                int(event_id),
                ev.get("event_category"),
                ev.get("event_summary"),
                ev.get("event_score"),
                ev.get("primary_source") or ev.get("source_name") or "AI DISCOVERY",
                ev.get("event_period"),
                ev.get("primary_source") or ev.get("source_name"),
                int(ev.get("supporting_count") or 0),
                ev.get("sentiment"),
                ev.get("impact_score") if ev.get("impact_score") is not None else ev.get("event_score"),
                ev.get("event_date"),
                ev.get("source_tags"),
                ev.get("source_sites"),
                int(ev.get("is_recent") if ev.get("is_recent") is not None else 1),
                now,
                now,
            ),
        )
        cid = int(cur.lastrowid)
        row = conn.execute(
            "SELECT * FROM ai_discovery_candidates WHERE id = ?", (cid,)
        ).fetchone()
    return dict(row) if row else None


def get_min_event_score_display() -> float:
    """UI / pool visibility threshold (unique events). Does not delete history."""
    init_db()
    raw = get_setting("ai_discovery_min_event_score", DEFAULT_MIN_EVENT_SCORE_DISPLAY)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = DEFAULT_MIN_EVENT_SCORE_DISPLAY
    return max(0.0, min(100.0, v))


def set_min_event_score_display(score: float) -> float:
    v = max(0.0, min(100.0, float(score)))
    set_setting("ai_discovery_min_event_score", v)
    return v


def discovery_pool_counts(
    *, min_event_score: float | None = None
) -> dict[str, Any]:
    """
    Qualifying Events = unique underlying events at/above threshold
    for Today's Long Discovery pool (recent + not NEGATIVE).
    """
    thr = float(
        min_event_score
        if min_event_score is not None
        else get_min_event_score_display()
    )
    init_db()
    with get_conn() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT ticker, event_score FROM ai_discovery_candidates
                WHERE event_score IS NOT NULL AND event_score >= ?
                  AND (is_recent IS NULL OR is_recent = 1)
                  AND (sentiment IS NULL OR sentiment != ?)
                """,
                (thr, SENT_NEGATIVE),
            ).fetchall()
        ]
        stored_total = conn.execute(
            "SELECT COUNT(*) AS n FROM ai_discovery_candidates"
        ).fetchone()["n"]
    tickers = {
        str(r.get("ticker") or "").upper()
        for r in rows
        if str(r.get("ticker") or "").strip()
    }
    return {
        "min_event_score": thr,
        "qualifying_events": len(rows),
        "unique_stocks": len(tickers),
        "stored_total": int(stored_total or 0),
    }


def list_discovery_candidates(
    *,
    status: str | None = None,
    limit: int = 200,
    min_event_score: float | None = None,
    apply_display_threshold: bool = True,
    recent_only: bool = True,
    exclude_negative: bool = True,
) -> list[dict[str, Any]]:
    """
    Pool list sorted by Event Score DESC.
    By default: display threshold + recent underlying events only (Today's Discovery).
    History rows remain stored; pass recent_only=False for full history jobs.
    Set exclude_negative=False to show 差/NEGATIVE rows in the Discovery UI
    (still not trade-eligible until analyze gates say otherwise).
    """
    init_db()
    sql = """
        SELECT c.*,
               e.source_url AS event_source_url,
               e.meta_json AS event_meta_json
        FROM ai_discovery_candidates c
        LEFT JOIN ai_discovery_events e ON e.id = c.event_id
        WHERE 1=1
    """
    args: list[Any] = []
    if status:
        sql += " AND c.status = ?"
        args.append(status)
    if apply_display_threshold:
        thr = float(
            min_event_score
            if min_event_score is not None
            else get_min_event_score_display()
        )
        sql += " AND c.event_score IS NOT NULL AND c.event_score >= ?"
        args.append(thr)
    elif min_event_score is not None:
        sql += " AND c.event_score IS NOT NULL AND c.event_score >= ?"
        args.append(float(min_event_score))
    if recent_only:
        # Prefer is_recent=1; also allow NULL legacy rows.
        sql += " AND (c.is_recent IS NULL OR c.is_recent = 1)"
    if exclude_negative:
        # Long Discovery pool: prefer POSITIVE; still show NEUTRAL if high score.
        sql += " AND (c.sentiment IS NULL OR c.sentiment != ?)"
        args.append(SENT_NEGATIVE)
    sql += " ORDER BY c.event_score DESC, c.ai_score DESC, c.id DESC LIMIT ?"
    args.append(int(limit))
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [enrich_discovery_source_fields(dict(r)) for r in rows]


def discovery_performance() -> dict[str, Any]:
    """
    Split metrics:
      discovery_alpha — forward returns for ALL unique events (incl. not traded)
      trading — closed paper trades with source ai_discovery
    """
    init_db()
    try:
        update_discovery_forward_returns(limit=300)
    except Exception:
        log.exception("update_discovery_forward_returns failed")

    with get_conn() as conn:
        closed = conn.execute(
            "SELECT * FROM paper_trades WHERE status = 'closed'"
        ).fetchall()
        open_n = conn.execute(
            "SELECT COUNT(*) AS n FROM paper_trades WHERE status = 'open' "
            "AND lower(COALESCE(source_at_entry,'')) LIKE '%ai_discovery%'"
        ).fetchone()["n"]
        rows = [
            dict(r)
            for r in conn.execute("SELECT * FROM ai_discovery_candidates").fetchall()
        ]
        all_rows = rows
        trade_cand_n = sum(
            1
            for r in rows
            if r.get("status") == ST_TRADE_CANDIDATE
            or int(r.get("became_trade_candidate") or 0) == 1
            or r.get("status") == ST_ORDER_CREATED
        )
        traded_n = sum(
            1
            for r in rows
            if r.get("status") == ST_ORDER_CREATED or r.get("paper_trade_id")
        )

    thr = get_min_event_score_display()
    pool = discovery_pool_counts(min_event_score=thr)
    buckets = discovery_threshold_counts()
    # Discovery Alpha averages: all stored events (incl. not traded), not Top-N.
    rows = all_rows

    disc_closed = []
    other_closed = []
    for r in closed:
        src = str(r["source_at_entry"] or "").lower()
        if "ai_discovery" in src:
            disc_closed.append(dict(r))
        else:
            other_closed.append(dict(r))

    # All AI_DISCOVERY paper trades (open + closed) for News Trading KPI.
    with get_conn() as conn:
        disc_all = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM paper_trades
                WHERE lower(COALESCE(source_at_entry,'')) LIKE '%ai_discovery%'
                """
            ).fetchall()
        ]
    disc_open = [t for t in disc_all if str(t.get("status") or "") == "open"]
    realized_pnl = sum(float(t.get("realized_pnl") or 0) for t in disc_closed)
    unrealized_pnl = sum(float(t.get("unrealized_pnl") or 0) for t in disc_open)
    total_pnl = round(realized_pnl + unrealized_pnl, 2)
    cost_basis = 0.0
    for t in disc_all:
        try:
            cost_basis += float(t.get("entry_price") or 0) * float(t.get("shares") or 0)
        except (TypeError, ValueError):
            pass
    return_pct = (
        round(100.0 * total_pnl / cost_basis, 1) if cost_basis > 1e-9 else None
    )
    wins = sum(1 for t in disc_closed if float(t.get("realized_pnl") or 0) > 0)
    n_closed = len(disc_closed)
    news_trading = {
        "total_pnl": total_pnl,
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "return_pct": return_pct,
        "trades": len(disc_all),
        "closed_trades": n_closed,
        "win_rate": round(100.0 * wins / n_closed, 1) if n_closed else None,
    }

    def _trade_stats(trows: list[dict[str, Any]]) -> dict[str, Any]:
        if not trows:
            return {
                "closed_trades": 0,
                "wins": 0,
                "win_rate": None,
                "avg_return_pct": None,
                "total_pnl": 0.0,
                "stop_loss_rate": None,
                "take_profit_rate": None,
            }
        wins = sum(1 for t in trows if float(t.get("realized_pnl") or 0) > 0)
        rets = [float(t["return_pct"]) for t in trows if t.get("return_pct") is not None]
        pnls = [float(t.get("realized_pnl") or 0) for t in trows]
        stops = sum(
            1
            for t in trows
            if str(t.get("exit_reason") or "").upper() in ("STOP_LOSS", "STOP")
        )
        takes = sum(
            1
            for t in trows
            if str(t.get("exit_reason") or "").upper() in ("TAKE_PROFIT", "TAKE")
        )
        n = len(trows)
        return {
            "closed_trades": n,
            "wins": wins,
            "win_rate": round(100.0 * wins / n, 1) if n else None,
            "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
            "total_pnl": round(sum(pnls), 2),
            "stop_loss_rate": round(100.0 * stops / n, 1) if n else None,
            "take_profit_rate": round(100.0 * takes / n, 1) if n else None,
        }

    def _avg(field: str) -> float | None:
        vals = [
            float(r[field])
            for r in rows
            if r.get(field) is not None
        ]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 2)

    alpha = {
        "unique_events": pool["qualifying_events"],
        "qualifying_events": pool["qualifying_events"],
        "unique_stocks": pool["unique_stocks"],
        "min_event_score": thr,
        "threshold_counts": buckets,
        "trade_candidates": trade_cand_n,
        "traded": traded_n,
        "avg_ret_5d": _avg("ret_5d"),
        "avg_ret_20d": _avg("ret_20d"),
        "avg_ret_63d": _avg("ret_63d"),
        "avg_ret_5d_vs_spy": _avg("ret_5d_vs_spy"),
        "avg_ret_20d_vs_spy": _avg("ret_20d_vs_spy"),
        "avg_ret_63d_vs_spy": _avg("ret_63d_vs_spy"),
        "n_with_5d": sum(1 for r in rows if r.get("ret_5d") is not None),
        "n_with_20d": sum(1 for r in rows if r.get("ret_20d") is not None),
        "n_with_63d": sum(1 for r in rows if r.get("ret_63d") is not None),
    }

    return {
        "candidates_discovered": len(all_rows),
        "candidates_traded": traded_n,
        "open_discovery_positions": int(open_n or 0),
        "pool": pool,
        "discovery_alpha": alpha,
        "discovery": _trade_stats(disc_closed),
        "other_sources": _trade_stats(other_closed),
        "news_trading": news_trading,
    }


def analyze_discovery_candidate(candidate_id: int) -> dict[str, Any]:
    """
    After discovery: reuse Financial / News / AI Score / Knife / price location.
    Updates status → WATCH / TRADE_CANDIDATE / AUTO_BLOCK.
    """
    from knife_risk import attach_knife_risk, knife_auto_blocked
    from market_data import (
        compute_ai_score,
        compute_target_proxy_mos,
        fund_qualifies_for_news,
        get_fund_cached_only,
        get_news_cached_only,
        make_news_skipped,
    )
    from paper_trading import (
        _fund_label,
        _news_label,
        ai_auto_price_location_hits,
        passes_ai_auto_price_location,
        target_ratio_from_row,
    )

    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ai_discovery_candidates WHERE id = ?",
            (int(candidate_id),),
        ).fetchone()
        if not row:
            raise ValueError("discovery candidate not found")
        cand = dict(row)

    ticker = str(cand["ticker"]).upper()
    now = _utc_now_iso()
    _set_candidate_fields(cand["id"], status=ST_ANALYZING, updated_at=now)

    dash = get_dashboard_by_tickers([ticker]).get(ticker) or {}
    fund_map = get_fund_cached_only([ticker])
    fund = fund_map.get(ticker)
    if fund_qualifies_for_news(fund):
        news = (get_news_cached_only([ticker]) or {}).get(ticker)
    else:
        news = make_news_skipped()

    r: dict[str, Any] = dict(dash) if dash else {"ticker": ticker}
    r["ticker"] = ticker
    if r.get("price") is None and cand.get("price") is not None:
        r["price"] = cand["price"]
    r["fund"] = fund
    r["news"] = news
    r.update(compute_target_proxy_mos(r.get("price"), r.get("target_1y")))
    ai = compute_ai_score(r)
    r["ai"] = ai
    r["ai_score"] = float(ai.get("final") or 0)
    try:
        attach_knife_risk([r], ensure_bench=True)
    except Exception:
        r["knife"] = None

    knife = r.get("knife") if isinstance(r.get("knife"), dict) else {}
    knife_score = knife.get("score")
    price_hits = ai_auto_price_location_hits(r)
    price_ok = passes_ai_auto_price_location(r)
    blocked = knife_auto_blocked(knife_score)

    event_score = float(cand.get("event_score") or 0)
    category = str(cand.get("event_category") or "")
    ai_score = float(r.get("ai_score") or 0)
    sentiment = str(cand.get("sentiment") or SENT_POSITIVE).upper()
    is_recent = cand.get("is_recent")
    if is_recent is None:
        is_recent = 1

    block_reason = None
    trade_eligible = 0
    status = ST_WATCH

    if blocked:
        status = ST_AUTO_BLOCK
        block_reason = f"knife_auto_block:{knife_score}"
    elif sentiment == SENT_NEGATIVE:
        status = ST_WATCH
        block_reason = "negative_event_risk_monitor"
    elif int(is_recent) == 0:
        status = ST_WATCH
        block_reason = "stale_event_date_history_only"
    elif category == CAT_POLITICIAN_BUY and not POLITICIAN_AUTO_TRADE:
        status = ST_WATCH
        block_reason = "politician_discovery_clue_only"
    elif event_score < MIN_EVENT_SCORE_FOR_TRADE:
        status = ST_WATCH
        block_reason = f"event_score<{MIN_EVENT_SCORE_FOR_TRADE}"
    elif ai_score < MIN_AI_SCORE_FOR_TRADE:
        status = ST_WATCH
        block_reason = f"ai_score<{MIN_AI_SCORE_FOR_TRADE}"
    elif not price_ok:
        status = ST_WATCH
        block_reason = "price_location_not_met"
    else:
        status = ST_TRADE_CANDIDATE
        trade_eligible = 1
        block_reason = None

    analysis = {
        "ai": ai,
        "price_hits": price_hits,
        "knife": knife,
        "financial_ok": fund.get("ok") if fund else None,
        "financial_known": fund.get("total_known") if fund else None,
    }
    fields = {
        "status": status,
        "company_name": r.get("name") or cand.get("company_name"),
        "ai_score": ai_score,
        "financial_label": _fund_label(fund),
        "news_label": _news_label(news if isinstance(news, dict) else None),
        "knife_score": knife_score,
        "knife_level": knife.get("level"),
        "price": r.get("price"),
        "dist_pct": r.get("dist_pct"),
        "target_ratio": target_ratio_from_row(r),
        "range_63d_pos": r.get("range_63d_pos"),
        "trade_eligible": trade_eligible,
        "block_reason": block_reason,
        "analysis_json": json.dumps(analysis),
        "updated_at": now,
    }
    # Freeze Discovery snapshot once (for Discovery Alpha — even if never traded).
    if cand.get("discovery_price") is None and r.get("price") is not None:
        fields["discovery_price"] = r.get("price")
        fields["discovery_ai_score"] = ai_score
        fields["discovery_financial_label"] = fields["financial_label"]
        fields["discovery_knife_score"] = knife_score
        fields["discovery_status"] = status
        fields["discovery_block_reason"] = block_reason
    if status == ST_TRADE_CANDIDATE or trade_eligible:
        fields["became_trade_candidate"] = 1
    elif cand.get("became_trade_candidate"):
        fields["became_trade_candidate"] = int(cand["became_trade_candidate"])
    _set_candidate_fields(cand["id"], **fields)
    out = {**cand, **fields, "id": cand["id"], "ticker": ticker}
    return out


def _set_candidate_fields(candidate_id: int, **fields: Any) -> None:
    if not fields:
        return
    cols = []
    vals: list[Any] = []
    for k, v in fields.items():
        cols.append(f"{k} = ?")
        vals.append(v)
    vals.append(int(candidate_id))
    with get_conn() as conn:
        conn.execute(
            f"UPDATE ai_discovery_candidates SET {', '.join(cols)} WHERE id = ?",
            vals,
        )


def analyze_all_pending(*, limit: int = 40) -> dict[str, Any]:
    rows = list_discovery_candidates(limit=500, apply_display_threshold=False, recent_only=False)
    pending = [
        r
        for r in rows
        if r.get("status") in (ST_DISCOVERED, ST_ANALYZING, ST_WATCH, ST_TRADE_CANDIDATE)
        and r.get("status") != ST_ORDER_CREATED
    ][:limit]
    # Prefer never-analyzed first.
    pending.sort(
        key=lambda r: (
            0 if r.get("status") == ST_DISCOVERED else 1,
            -(float(r.get("event_score") or 0)),
        )
    )
    done = []
    errors = []
    for r in pending[:limit]:
        try:
            done.append(analyze_discovery_candidate(int(r["id"])))
        except Exception as exc:
            log.exception("analyze discovery %s", r.get("ticker"))
            errors.append({"id": r.get("id"), "ticker": r.get("ticker"), "error": str(exc)})
    return {"analyzed": len(done), "errors": errors, "rows": done}


def create_discovery_paper_orders(*, auto_only: bool = True) -> dict[str, Any]:
    """
    For TRADE_CANDIDATE rows: create Paper orders with source = ai_discovery.
    Respects open position, cash, trading limit, Knife (already gated), duplicate event.
    """
    from paper_trading import (
        ALLOC_LADDER,
        ensure_portfolio,
        list_open_trades,
        save_equity_snapshot,
        size_position,
        stop_take_prices,
        sum_open_invested,
        trading_day_pt,
        validate_long_levels,
        _cfg,
        _utc_now_iso as paper_now,
    )

    init_db()
    cands = [
        c
        for c in list_discovery_candidates(limit=200, apply_display_threshold=False, recent_only=True)
        if c.get("status") == ST_TRADE_CANDIDATE and int(c.get("trade_eligible") or 0) == 1
    ]
    if auto_only:
        cands = [c for c in cands if float(c.get("event_score") or 0) >= MIN_EVENT_SCORE_FOR_TRADE]

    port = ensure_portfolio()
    cash = float(port["cash"])
    trading_limit = float(port["trading_limit"])
    invested = sum_open_invested()
    open_tickers = {t["ticker"].upper() for t in list_open_trades()}
    cfg = _cfg()
    created = []
    skipped = []
    day = trading_day_pt()
    now = paper_now()

    # Already ordered for same underlying event (by event_id OR fingerprint period)?
    with get_conn() as conn:
        ordered_events = {
            int(r["event_id"])
            for r in conn.execute(
                "SELECT event_id FROM ai_discovery_candidates "
                "WHERE paper_trade_id IS NOT NULL AND event_id IS NOT NULL"
            ).fetchall()
            if r["event_id"] is not None
        }
        ordered_keys = {
            str(r["event_fingerprint"])
            for r in conn.execute(
                """
                SELECT e.event_fingerprint FROM ai_discovery_candidates c
                JOIN ai_discovery_events e ON e.id = c.event_id
                WHERE c.paper_trade_id IS NOT NULL
                """
            ).fetchall()
            if r["event_fingerprint"]
        }

    slot = 0
    for c in cands:
        ticker = str(c["ticker"]).upper()
        eid = c.get("event_id")
        if eid is not None and int(eid) in ordered_events:
            skipped.append({"ticker": ticker, "reason": "event_already_traded"})
            continue
        # Resolve fingerprint for this candidate's event.
        fp = None
        if eid is not None:
            with get_conn() as conn:
                erow = conn.execute(
                    "SELECT event_fingerprint FROM ai_discovery_events WHERE id = ?",
                    (int(eid),),
                ).fetchone()
            if erow:
                fp = erow["event_fingerprint"]
                if fp in ordered_keys:
                    skipped.append({"ticker": ticker, "reason": "underlying_event_already_traded"})
                    continue
        if ticker in open_tickers:
            skipped.append({"ticker": ticker, "reason": "already_open"})
            _set_candidate_fields(
                int(c["id"]),
                status=ST_WATCH,
                block_reason="already_open",
                trade_eligible=0,
                updated_at=now,
            )
            continue
        # Pending agent order requests (same ticker) — avoid double entry.
        try:
            with get_conn() as conn:
                pend = conn.execute(
                    """
                    SELECT 1 FROM trading_order_requests
                    WHERE upper(symbol) = ?
                      AND upper(COALESCE(status,'')) IN (
                        'PENDING','QUEUED','SUBMITTED','OPEN','NEW'
                      )
                    LIMIT 1
                    """,
                    (ticker,),
                ).fetchone()
            if pend:
                skipped.append({"ticker": ticker, "reason": "pending_order"})
                continue
        except Exception:
            pass
        try:
            price = float(c["price"])
        except (TypeError, ValueError):
            skipped.append({"ticker": ticker, "reason": "no_price"})
            continue
        if price <= 0:
            skipped.append({"ticker": ticker, "reason": "bad_price"})
            continue

        target = ALLOC_LADDER[slot] if slot < len(ALLOC_LADDER) else ALLOC_LADDER[-1]
        room = max(0.0, trading_limit - invested)
        target = min(target, room)
        if target <= 0:
            skipped.append({"ticker": ticker, "reason": "trading_limit"})
            break
        shares, cost, mode = size_position(price, target)
        if shares <= 0 or cost > cash + 1e-6:
            skipped.append({"ticker": ticker, "reason": "insufficient_cash"})
            continue
        stop, take = stop_take_prices(price, cfg["stop_loss_pct"], cfg["take_profit_pct"])
        err = validate_long_levels(price, stop, take)
        if err:
            skipped.append({"ticker": ticker, "reason": err})
            continue

        with get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO paper_trades (
                  ticker, name, status, entry_date, entry_price, shares, shares_mode,
                  cost, stop_price, take_profit_price, stop_pct, take_profit_pct,
                  ai_score_entry, mos_t_entry, financial_entry, news_entry,
                  range_63d_pos_entry, source_at_entry, is_priority, rank_at_entry,
                  current_price, market_value, unrealized_pnl, unrealized_pnl_pct,
                  ai_score_current, created_at, updated_at
                ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 0, ?, ?, ?, 0, 0, ?, ?, ?)
                """,
                (
                    ticker,
                    c.get("company_name") or "",
                    day,
                    price,
                    shares,
                    mode,
                    cost,
                    stop,
                    take,
                    cfg["stop_loss_pct"],
                    cfg["take_profit_pct"],
                    c.get("ai_score"),
                    c.get("financial_label"),
                    c.get("news_label"),
                    c.get("range_63d_pos"),
                    SRC_AI_DISCOVERY,
                    slot + 1,
                    price,
                    cost,
                    c.get("ai_score"),
                    now,
                    now,
                ),
            )
            trade_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE paper_portfolio SET cash = ?, updated_at = ? WHERE id = 1",
                (round(cash - cost, 4), now),
            )
            conn.execute(
                """
                UPDATE ai_discovery_candidates
                SET status = ?, paper_trade_id = ?, trade_eligible = 0,
                    block_reason = NULL, updated_at = ?
                WHERE id = ?
                """,
                (ST_ORDER_CREATED, trade_id, now, int(c["id"])),
            )
        cash -= cost
        invested += cost
        open_tickers.add(ticker)
        if eid is not None:
            ordered_events.add(int(eid))
        if fp:
            ordered_keys.add(fp)
        slot += 1
        created.append(
            {"ticker": ticker, "trade_id": trade_id, "shares": shares, "cost": cost}
        )

    try:
        save_equity_snapshot(as_of_date=day)
    except Exception:
        log.exception("equity snapshot after discovery orders")
    return {"created": created, "skipped": skipped, "count": len(created)}


def _closes_from_date(ticker: str, start_date: str) -> list[tuple[str, float]]:
    """Ascending (date, close) from daily_bars on/after start_date."""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT date, close FROM daily_bars
            WHERE ticker = ? AND date >= ? AND close IS NOT NULL
            ORDER BY date ASC
            """,
            (ticker.upper(), start_date[:10]),
        ).fetchall()
    out: list[tuple[str, float]] = []
    for r in rows:
        try:
            out.append((str(r["date"])[:10], float(r["close"])))
        except (TypeError, ValueError):
            continue
    return out


def _closes_for_discovery_forward(
    ticker: str, discovery_date: str
) -> list[tuple[str, float]]:
    """
    Day-0 = first bar on/after discovery_date; if market data lags the
    discovery calendar day, fall back to last bar on/before discovery.
    """
    d0 = (discovery_date or "")[:10]
    if not d0:
        return []
    series = _closes_from_date(ticker, d0)
    if series:
        return series
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT date FROM daily_bars
            WHERE ticker = ? AND date <= ? AND close IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (ticker.upper(), d0),
        ).fetchone()
    if not row:
        return []
    return _closes_from_date(ticker, str(row["date"])[:10])


def _ensure_spy_close_series() -> list[tuple[str, float]]:
    """Reuse daily_bars; lightly backfill SPY via existing upsert if empty."""
    spy = _closes_from_date("SPY", "2020-01-01")
    if len(spy) >= 80:
        return spy
    try:
        import yfinance as yf
        from strong_stocks import upsert_daily_bars

        hist = yf.Ticker("SPY").history(period="1y", auto_adjust=True)
        if hist is not None and not hist.empty and "Close" in hist.columns:
            upsert_daily_bars("SPY", hist["Close"].dropna())
    except Exception:
        log.exception("SPY backfill for discovery returns")
    return _closes_from_date("SPY", "2020-01-01")


def _return_after_n_sessions(
    series: list[tuple[str, float]], *, n: int
) -> float | None:
    """
    Return % from first close to the close n trading sessions later.
    Needs at least n+1 points. Never invent 0 when incomplete.
    """
    if n < 1 or len(series) < n + 1:
        return None
    p0 = series[0][1]
    p1 = series[n][1]
    if p0 <= 0 or p1 <= 0:
        return None
    return round((p1 / p0 - 1.0) * 100.0, 2)


def update_discovery_forward_returns(*, limit: int = 300) -> dict[str, Any]:
    """Fill 1/5/20/63D absolute and vs-SPY returns when enough bars exist."""
    init_db()
    rows = list_discovery_candidates(limit=limit, apply_display_threshold=False, recent_only=False)
    spy_all = _ensure_spy_close_series()
    spy_by_date = {d: c for d, c in spy_all}
    updated = 0
    now = _utc_now_iso()
    for cand in rows:
        ticker = str(cand.get("ticker") or "").upper()
        start = str(cand.get("discovery_date") or "")[:10]
        if not ticker or not start:
            continue
        series = _closes_for_discovery_forward(ticker, start)
        if not series:
            continue
        # Align SPY on the same session dates as the stock series.
        common = [d for d, _ in series if d in spy_by_date]
        if common:
            series_aligned = [(d, c) for d, c in series if d in spy_by_date]
            spy_series = [(d, spy_by_date[d]) for d in common]
        else:
            series_aligned = []
            spy_series = []

        fields: dict[str, Any] = {"returns_updated_at": now}
        for n, key in ((1, "ret_1d"), (5, "ret_5d"), (20, "ret_20d"), (63, "ret_63d")):
            ret = _return_after_n_sessions(series, n=n)
            fields[key] = ret
            ret_a = (
                _return_after_n_sessions(series_aligned, n=n) if series_aligned else None
            )
            spy_ret = (
                _return_after_n_sessions(spy_series, n=n) if spy_series else None
            )
            vs_key = f"{key}_vs_spy"
            if ret_a is None or spy_ret is None:
                fields[vs_key] = None
            else:
                fields[vs_key] = round(ret_a - spy_ret, 2)
        _set_candidate_fields(int(cand["id"]), **fields)
        updated += 1
    return {"updated": updated}


def _delete_discovery_event(event_id: int) -> None:
    """Remove a false / orphan discovery event and its candidates (no paper trade)."""
    with get_conn() as conn:
        cands = conn.execute(
            "SELECT id, paper_trade_id FROM ai_discovery_candidates WHERE event_id = ?",
            (int(event_id),),
        ).fetchall()
        for c in cands:
            if c["paper_trade_id"]:
                # Keep history tied to a real trade — just detach event.
                conn.execute(
                    "UPDATE ai_discovery_candidates SET event_id = NULL WHERE id = ?",
                    (int(c["id"]),),
                )
            else:
                conn.execute(
                    "DELETE FROM ai_discovery_candidates WHERE id = ?",
                    (int(c["id"]),),
                )
        conn.execute("DELETE FROM ai_discovery_events WHERE id = ?", (int(event_id),))


def merge_duplicate_underlying_events() -> dict[str, Any]:
    """
    Each harvest: drop peer-misattributed rows; merge legacy headline-level rows
    that share ticker + category + period into one underlying event.
    Keeps the highest event_score row as survivor; Event Scores are never summed.
    """
    init_db()
    with get_conn() as conn:
        events = [dict(r) for r in conn.execute("SELECT * FROM ai_discovery_events").fetchall()]

    dropped = 0
    kept: list[dict[str, Any]] = []
    for ev in events:
        ticker = (ev.get("ticker") or "").upper()
        summary = str(ev.get("event_summary") or "")
        if ticker and summary and not headline_belongs_to_ticker(summary, ticker):
            _delete_discovery_event(int(ev["id"]))
            dropped += 1
            continue
        kept.append(ev)

    groups: dict[str, list[dict[str, Any]]] = {}
    for ev in kept:
        ticker = (ev.get("ticker") or "").upper()
        cat = ev.get("event_category") or ""
        period = ev.get("event_period") or extract_event_period(
            str(ev.get("event_summary") or ""),
            fallback_date=str(ev.get("discovered_at") or _trading_day()),
        )
        key = underlying_event_key(ticker, cat, period)
        groups.setdefault(key, []).append(ev)

    # Soft merge: same ticker+category, compatible period, similar headlines,
    # but different legacy fingerprints (pre-period keys).
    soft_merged = 0
    keys = list(groups.keys())
    for i, k1 in enumerate(keys):
        if k1 not in groups:
            continue
        for k2 in keys[i + 1 :]:
            if k2 not in groups:
                continue
            a, b = groups[k1][0], groups[k2][0]
            if (a.get("ticker") or "").upper() != (b.get("ticker") or "").upper():
                continue
            if (a.get("event_category") or "") != (b.get("event_category") or ""):
                continue
            pa = a.get("event_period") or extract_event_period(
                str(a.get("event_summary") or ""),
                fallback_date=str(a.get("discovered_at") or _trading_day()),
            )
            pb = b.get("event_period") or extract_event_period(
                str(b.get("event_summary") or ""),
                fallback_date=str(b.get("discovered_at") or _trading_day()),
            )
            if not periods_compatible(pa, pb):
                continue
            if not headlines_similar(
                str(a.get("event_summary") or ""), str(b.get("event_summary") or "")
            ):
                continue
            # Fold k2 into k1
            groups[k1].extend(groups.pop(k2))
            soft_merged += 1

    merged = 0
    for key, members in list(groups.items()):
        # Recompute canonical key from survivor after soft merge.
        if len(members) < 2:
            ev = members[0]
            period = ev.get("event_period") or extract_event_period(
                str(ev.get("event_summary") or ""),
                fallback_date=str(ev.get("discovered_at") or _trading_day()),
            )
            canon = underlying_event_key(
                (ev.get("ticker") or "").upper(),
                ev.get("event_category") or "",
                period,
            )
            if ev.get("event_fingerprint") != canon or not ev.get("event_period"):
                with get_conn() as conn:
                    conn.execute(
                        """
                        UPDATE ai_discovery_events
                        SET event_fingerprint = ?, event_period = ?,
                            primary_source = COALESCE(primary_source, source_name),
                            earliest_discovered_at = COALESCE(earliest_discovered_at, discovered_at),
                            latest_confirmed_at = COALESCE(latest_confirmed_at, discovered_at)
                        WHERE id = ?
                        """,
                        (canon, period, int(ev["id"])),
                    )
                    conn.execute(
                        """
                        UPDATE ai_discovery_candidates
                        SET event_period = COALESCE(event_period, ?)
                        WHERE event_id = ?
                        """,
                        (period, int(ev["id"])),
                    )
            continue

        members.sort(
            key=lambda e: (
                float(e.get("event_score") or 0),
                1 if headline_belongs_to_ticker(
                    str(e.get("event_summary") or ""), str(e.get("ticker") or "")
                )
                else 0,
                1 if e.get("reliability") == "primary_like" else 0,
                int(e.get("id") or 0),
            ),
            reverse=True,
        )
        survivor = members[0]
        sid = int(survivor["id"])
        period = survivor.get("event_period") or extract_event_period(
            str(survivor.get("event_summary") or ""),
            fallback_date=str(survivor.get("discovered_at") or _trading_day()),
        )
        canon = underlying_event_key(
            (survivor.get("ticker") or "").upper(),
            survivor.get("event_category") or "",
            period,
        )
        supporting: list[dict[str, Any]] = []
        try:
            supporting = json.loads(survivor.get("supporting_sources_json") or "[]")
        except Exception:
            supporting = []
        if not isinstance(supporting, list):
            supporting = []

        earliest = (
            survivor.get("earliest_discovered_at")
            or survivor.get("discovered_at")
            or _trading_day()
        )

        for other in members[1:]:
            supporting.append(
                {
                    "summary": (other.get("event_summary") or "")[:400],
                    "source": other.get("source_name"),
                    "ts": other.get("created_at"),
                    "event_score": other.get("event_score"),
                    "merged_from_event_id": other.get("id"),
                }
            )
            od = other.get("discovered_at") or other.get("earliest_discovered_at")
            if od and str(od) < str(earliest):
                earliest = od
            oid = int(other["id"])
            with get_conn() as conn:
                cands = conn.execute(
                    "SELECT * FROM ai_discovery_candidates WHERE event_id = ?",
                    (oid,),
                ).fetchall()
                for c in cands:
                    c = dict(c)
                    exist = conn.execute(
                        "SELECT id, paper_trade_id, status FROM ai_discovery_candidates "
                        "WHERE ticker = ? AND event_id = ?",
                        (c["ticker"], sid),
                    ).fetchone()
                    if exist:
                        keep_id = int(exist["id"])
                        drop_id = int(c["id"])
                        if c.get("paper_trade_id") and not exist["paper_trade_id"]:
                            keep_id, drop_id = int(c["id"]), int(exist["id"])
                            conn.execute(
                                "UPDATE ai_discovery_candidates SET event_id = ? WHERE id = ?",
                                (sid, keep_id),
                            )
                        conn.execute(
                            "DELETE FROM ai_discovery_candidates WHERE id = ?",
                            (drop_id,),
                        )
                    else:
                        conn.execute(
                            "UPDATE ai_discovery_candidates SET event_id = ? WHERE id = ?",
                            (sid, int(c["id"])),
                        )
                conn.execute("DELETE FROM ai_discovery_events WHERE id = ?", (oid,))
            merged += 1

        with get_conn() as conn:
            conn.execute(
                """
                UPDATE ai_discovery_events SET
                  event_fingerprint = ?, event_period = ?,
                  primary_source = COALESCE(primary_source, source_name),
                  supporting_sources_json = ?, supporting_count = ?,
                  earliest_discovered_at = ?,
                  latest_confirmed_at = ?
                WHERE id = ?
                """,
                (
                    canon,
                    period,
                    json.dumps(supporting[-20:]),
                    len(supporting),
                    earliest,
                    _trading_day(),
                    sid,
                ),
            )
            conn.execute(
                """
                UPDATE ai_discovery_candidates SET
                  event_period = ?, primary_source = ?, supporting_count = ?,
                  event_summary = ?, event_score = ?
                WHERE event_id = ?
                """,
                (
                    period,
                    survivor.get("primary_source") or survivor.get("source_name"),
                    len(supporting),
                    survivor.get("event_summary"),
                    survivor.get("event_score"),
                    sid,
                ),
            )
    return {
        "merged": merged,
        "soft_merged": soft_merged,
        "dropped_peer": dropped,
        "groups": len(groups),
    }

def harvest_combined_discovery(
    *, min_score: float | None = None
) -> dict[str, Any]:
    """
    Broad Discovery (kept) + Official 5×5 Radar → shared dedupe → Discovery table.

    Source tags: BROAD / USASPENDING / DOD / SEC / FDA / GOV_DISCLOSURE
    Sentiment: POSITIVE / NEGATIVE / NEUTRAL
    Impact/Event Score = materiality (not positivity alone)
    Old underlying event dates → History only (is_recent=0), not Today's Discovery.
    """
    from ai_discovery_channels import (
        CHANNEL_LABELS,
        CHANNEL_ORDER,
        CHANNEL_SITES,
        CHANNEL_TO_SOURCE_TAG,
        collect_channel_top5,
    )

    init_db()
    thr = float(min_score if min_score is not None else MIN_EVENT_SCORE_FOR_POOL)

    # --- Layer A: existing Broad Discovery (thematic news) — unchanged spirit ---
    broad_articles = scan_thematic_news_headlines()
    broad_raw: list[dict[str, Any]] = []
    for art in broad_articles:
        title = str(art.get("title") or "").strip()
        if not title:
            continue
        classified = classify_event(title)
        if not classified:
            continue
        cat, base, _rel0 = classified
        reliability = source_reliability_hint(
            title=title,
            link=str(art.get("link") or ""),
            publisher=str(art.get("publisher") or ""),
        )
        contract_val = _extract_money_usd(title)
        score = refine_event_score(
            base=base,
            category=cat,
            reliability=reliability,
            contract_value=contract_val,
        )
        if score < thr:
            continue
        broad_raw.append(
            {
                "channel": "broad",
                "summary": title,
                "company_hint": None,
                "ticker_hint": None,
                "category_hint": cat,
                "amount": contract_val,
                "source_url": art.get("link"),
                "event_date": None,
                "cheap_score": score,
                "base_score": score,
                "reliability": reliability,
                "publisher": art.get("publisher") or "google_news",
                "meta": {"theme": art.get("theme"), "layer": "broad"},
            }
        )

    # --- Layer B: Official 5×5 ---
    collected = collect_channel_top5()
    by_ch: dict[str, list] = collected.get("by_channel") or {}
    channel_counts = dict(collected.get("channel_counts") or {})
    channel_counts["broad"] = len(broad_raw)

    raw_events: list[dict[str, Any]] = list(broad_raw)
    for ch in CHANNEL_ORDER:
        for ev in by_ch.get(ch) or []:
            raw_events.append(ev)

    raw_total = len(raw_events)

    # Shared cross-source groups (Broad + Official).
    groups: dict[str, dict[str, Any]] = {}
    for ev in raw_events:
        summary = str(ev.get("summary") or "")
        sent = classify_sentiment(summary)
        cat = ev.get("category_hint") or ""
        if not cat:
            classified = classify_event(summary)
            cat = classified[0] if classified else "other_major_positive"
        period = extract_event_period(
            summary, fallback_date=str(ev.get("event_date") or _trading_day())
        )
        edate = parse_underlying_event_date(
            explicit=ev.get("event_date"), summary=summary, period=period
        )
        company = (ev.get("company_hint") or "").strip()
        ticker_h = (ev.get("ticker_hint") or "").strip().upper()
        if ticker_h:
            ident = ticker_h
        elif company:
            ident = re.sub(r"[^a-z0-9]+", "", company.lower())[:40]
        else:
            # Broad: resolve later; provisional key from tokens.
            ident = hashlib.sha256(summary.lower().encode()).hexdigest()[:16]
        key = f"{ident}|{cat}|{period}".lower()
        ch = ev.get("channel") or "broad"
        tag = CHANNEL_TO_SOURCE_TAG.get(ch, str(ch).upper())
        site = CHANNEL_SITES.get(ch) or _host_from_url(ev.get("source_url"))
        if key not in groups:
            groups[key] = {
                "summary": summary,
                "company_hint": company or None,
                "ticker_hint": ticker_h or None,
                "category": cat,
                "period": period,
                "event_date": edate,
                "amount": ev.get("amount"),
                "cheap_score": float(ev.get("cheap_score") or ev.get("base_score") or 0),
                "reliability": ev.get("reliability") or "headline",
                "channels": [ch],
                "source_tags": [tag],
                "source_sites": [site] if site else [],
                "source_urls": [ev.get("source_url")] if ev.get("source_url") else [],
                "sentiment": sent,
                "meta": dict(ev.get("meta") or {}),
                "publisher": ev.get("publisher"),
            }
        else:
            g = groups[key]
            if ch not in g["channels"]:
                g["channels"].append(ch)
            if tag not in g["source_tags"]:
                g["source_tags"].append(tag)
            if site and site not in g["source_sites"]:
                g["source_sites"].append(site)
            if ev.get("source_url") and ev["source_url"] not in g["source_urls"]:
                g["source_urls"].append(ev["source_url"])
            if float(ev.get("cheap_score") or 0) > float(g.get("cheap_score") or 0):
                g["cheap_score"] = float(ev.get("cheap_score") or 0)
                if len(summary) > 20:
                    g["summary"] = summary
            if ev.get("amount") and (
                g.get("amount") is None
                or float(ev["amount"]) > float(g.get("amount") or 0)
            ):
                g["amount"] = ev.get("amount")
            if not g.get("ticker_hint") and ticker_h:
                g["ticker_hint"] = ticker_h
            if not g.get("company_hint") and company:
                g["company_hint"] = company
            if edate and not g.get("event_date"):
                g["event_date"] = edate
            # Sentiment merge
            if sent == SENT_NEGATIVE:
                g["sentiment"] = SENT_NEGATIVE
            elif g.get("sentiment") != SENT_NEGATIVE and sent == SENT_POSITIVE:
                g["sentiment"] = SENT_POSITIVE
            if ev.get("reliability") == "primary_like":
                g["reliability"] = "primary_like"

    unique_events = list(groups.values())
    created = 0
    merged = 0
    unresolved = 0
    skipped = 0
    admitted_today = 0
    history_only = 0
    negative_stored = 0

    for g in unique_events:
        summary = str(g.get("summary") or "")
        sent = g.get("sentiment") or classify_sentiment(summary)
        classified = classify_event(summary)
        if classified:
            cat, base, _rel = classified
        else:
            cat = g.get("category") or CAT_OTHER_MAJOR
            base = float(g.get("cheap_score") or 72.0)
        tags = list(g.get("source_tags") or [])
        reliability = g.get("reliability") or (
            "primary_like"
            if any(t in (SRC_SEC, SRC_FDA, SRC_USASPENDING, SRC_DOD) for t in tags)
            else "headline"
        )
        score = refine_event_score(
            base=float(base),
            category=str(cat),
            reliability=str(reliability),
            contract_value=float(g["amount"]) if g.get("amount") else None,
        )
        if len(tags) >= 2:
            score = min(100.0, score + 2.0)
        # Impact = materiality. For negatives, keep magnitude from cheap/official rank.
        impact = max(score, float(g.get("cheap_score") or 0))
        if sent == SENT_NEGATIVE:
            impact = max(impact, float(g.get("cheap_score") or 60.0))

        period = g.get("period") or extract_event_period(summary, fallback_date=_trading_day())
        edate = g.get("event_date") or parse_underlying_event_date(
            summary=summary, period=period
        )
        recent = is_recent_underlying_event(edate, period=period)

        # Always keep History; only recent POSITIVE high-impact compete in Long pool path.
        store_anyway = True
        long_pool = (
            sent == SENT_POSITIVE
            and impact >= thr
            and recent
        )
        if sent == SENT_NEGATIVE and impact >= thr:
            negative_stored += 1
        if not recent:
            history_only += 1

        if not store_anyway:
            skipped += 1
            continue
        # Skip tiny noise for history too
        if impact < MIN_EVENT_SCORE_FOR_STORAGE and sent != SENT_NEGATIVE:
            skipped += 1
            continue

        ticker = g.get("ticker_hint")
        company_name = g.get("company_hint")
        resolve_method = "channel_hint"
        conf = 0.9 if ticker else 0.0
        if not ticker:
            resolved = resolve_ticker_from_headline(
                f"{company_name + ' — ' if company_name else ''}{summary}"
            )
            if resolved.get("ok") and float(resolved.get("confidence") or 0) >= 0.7:
                ticker = resolved.get("ticker")
                company_name = resolved.get("company_name") or company_name
                resolve_method = resolved.get("method")
                conf = float(resolved.get("confidence") or 0)
            else:
                log_unresolved_discovery(
                    headline=summary,
                    source_name="+".join(tags) or "discovery",
                    source_url=(g.get("source_urls") or [None])[0],
                    category=str(cat),
                    event_score=impact,
                    notes=f"tags={tags}; resolve={resolved.get('method')}; recent={recent}",
                )
                unresolved += 1
                continue

        # Re-key after ticker resolution for Broad provisional groups.
        fp = underlying_event_key(str(ticker), str(cat), str(period))
        src_label = "+".join(tags) if tags else (g.get("publisher") or "BROAD")
        sites = [s for s in (g.get("source_sites") or []) if s]

        # Negative → store for risk/history; do not mark trade-eligible later via analyze prefer.
        block_note = None
        if sent == SENT_NEGATIVE:
            block_note = "negative_event_risk_monitor"
        elif not recent:
            block_note = "stale_event_date_history_only"
        elif not long_pool:
            block_note = "below_long_pool_gate"

        ev = upsert_event(
            fingerprint=fp,
            ticker=str(ticker).upper(),
            company_name=company_name,
            category=str(cat),
            summary=summary,
            source_name=src_label,
            source_url=(g.get("source_urls") or [None])[0],
            event_score=impact if sent != SENT_NEGATIVE else impact,
            reliability=str(reliability),
            event_period=str(period),
            sentiment=sent,
            impact_score=impact,
            event_date=edate,
            source_tags=tags,
            source_sites=sites,
            is_recent=recent,
            meta={
                "channels": g.get("channels"),
                "source_tags": tags,
                "source_urls": g.get("source_urls") or [],
                "contract_value_usd": g.get("amount"),
                "resolve_method": resolve_method,
                "resolve_confidence": conf,
                "layer": "broad+official",
                "long_pool": long_pool,
                "block_note": block_note,
            },
        )
        if ev.get("inserted"):
            created += 1
        else:
            merged += 1
        cand = ensure_candidate_from_event(int(ev["id"]))
        if cand and block_note:
            _set_candidate_fields(
                int(cand["id"]),
                block_reason=block_note,
                trade_eligible=0,
                updated_at=_utc_now_iso(),
            )
        if long_pool:
            admitted_today += 1

    merge_stats = merge_duplicate_underlying_events()
    set_setting("ai_discovery_last_harvest_at", _utc_now_iso())
    set_setting("ai_discovery_last_harvest_mode", "broad_plus_official_5x5")
    stats = {
        "channel_counts": channel_counts,
        "raw_total": raw_total,
        "broad_raw": len(broad_raw),
        "official_raw": int(collected.get("raw_total") or 0),
        "unique_before_score": len(unique_events),
        "admitted_today": admitted_today,
        "history_only_stale": history_only,
        "negative_stored": negative_stored,
        "unresolved": unresolved,
        "skipped": skipped,
        "created_events": created,
        "merged_into_existing": merged,
    }
    set_setting("ai_discovery_last_channel_stats", stats)
    pool = discovery_pool_counts()
    return {
        "mode": "broad_plus_official_5x5",
        **stats,
        "channel_labels": {**{c: CHANNEL_LABELS[c] for c in CHANNEL_ORDER}, "broad": "Broad Discovery"},
        "merged_duplicates": merge_stats.get("merged", 0),
        "qualifying_events": pool.get("qualifying_events"),
        "unique_stocks": pool.get("unique_stocks"),
        "threshold_counts": discovery_threshold_counts(),
        "errors": collected.get("errors") or {},
    }


def harvest_five_independent_channels(
    *, min_score: float | None = None
) -> dict[str, Any]:
    """Backward-compatible alias → combined Broad + Official 5×5 harvest."""
    return harvest_combined_discovery(min_score=min_score)


def run_discovery_cycle(*, create_orders: bool = True) -> dict[str, Any]:
    """Broad Discovery + Official 5×5 → analyze → optional orders → returns."""
    harvest = harvest_combined_discovery()
    retry = maybe_retry_unresolved(force=True)
    analyzed = analyze_all_pending(limit=50)
    orders = (
        create_discovery_paper_orders(auto_only=True)
        if create_orders
        else {"created": [], "skipped": [], "count": 0}
    )
    try:
        rets = update_discovery_forward_returns(limit=300)
    except Exception:
        log.exception("forward returns")
        rets = {"updated": 0}
    return {
        "harvest": harvest,
        "unresolved_retry": retry,
        "analyze": {"analyzed": analyzed.get("analyzed"), "errors": analyzed.get("errors")},
        "orders": orders,
        "returns": rets,
        "performance": discovery_performance(),
    }


def add_manual_discovery_event(
    *,
    ticker: str,
    summary: str,
    category: str | None = None,
    source_name: str = "manual_event",
) -> dict[str, Any]:
    """Admin: paste a major positive event (news discovers / confirms the ticker)."""
    t = (ticker or "").strip().upper()
    if not t:
        raise ValueError("ticker required")
    summary = (summary or "").strip()
    if len(summary) < 8:
        raise ValueError("event summary too short")
    classified = classify_event(summary)
    if classified:
        cat, base, reliability = classified
    else:
        cat = category or CAT_OTHER_MAJOR
        base = 65.0
        reliability = "manual"
    if category:
        cat = category
    score = refine_event_score(
        base=base,
        category=cat,
        reliability=reliability,
        contract_value=_extract_money_usd(summary),
    )
    period = extract_event_period(summary, fallback_date=_trading_day())
    fp = underlying_event_key(t, cat, period)
    ev = upsert_event(
        fingerprint=fp,
        ticker=t,
        company_name=None,
        category=cat,
        summary=summary,
        source_name=source_name,
        source_url=None,
        event_score=score,
        reliability=reliability,
        event_period=period,
    )
    cand = ensure_candidate_from_event(int(ev["id"]))
    if cand:
        analyze_discovery_candidate(int(cand["id"]))
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM ai_discovery_candidates WHERE event_id = ? ORDER BY id DESC LIMIT 1",
                (int(ev["id"]),),
            ).fetchone()
        cand = dict(row) if row else cand
    return {"event": ev, "candidate": cand}
