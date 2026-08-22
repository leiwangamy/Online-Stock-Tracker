"""
Five independent AI Discovery channels (experiment V1).

Each channel returns up to TOP_N (5) cheaply-ranked recent events.
Cross-source dedupe + Event Score happen AFTER all channels finish.

Channels:
  1. usaspending   — official USAspending awards API
  2. dod           — DoD / defense contract announcements
  3. sec           — SEC EDGAR recent 8-K (classify; not auto-positive)
  4. fda           — openFDA approvals / clearances
  5. gov_tx        — government financial disclosures (stock purchases)
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("leibot.discovery.channels")

TOP_N_PER_CHANNEL = 5
CHANNEL_FETCH_LIMIT = 40  # cheap raw window before Top-N

CH_USASPENDING = "usaspending"
CH_DOD = "dod"
CH_SEC = "sec"
CH_FDA = "fda"
CH_GOV_TX = "gov_transactions"
CH_BROAD = "broad"

CHANNEL_ORDER = (CH_USASPENDING, CH_DOD, CH_SEC, CH_FDA, CH_GOV_TX)

# Stable source tags for Discovery analysis / UI.
SRC_BROAD = "BROAD"
SRC_USASPENDING = "USASPENDING"
SRC_DOD = "DOD"
SRC_SEC = "SEC"
SRC_FDA = "FDA"
SRC_GOV_DISCLOSURE = "GOV_DISCLOSURE"

CHANNEL_TO_SOURCE_TAG = {
    CH_BROAD: SRC_BROAD,
    CH_USASPENDING: SRC_USASPENDING,
    CH_DOD: SRC_DOD,
    CH_SEC: SRC_SEC,
    CH_FDA: SRC_FDA,
    CH_GOV_TX: SRC_GOV_DISCLOSURE,
}

CHANNEL_LABELS = {
    CH_BROAD: "Broad Discovery",
    CH_USASPENDING: "USAspending",
    CH_DOD: "DoD",
    CH_SEC: "SEC",
    CH_FDA: "FDA",
    CH_GOV_TX: "Gov Disclosure",
}

CHANNEL_SITES = {
    CH_BROAD: "news.google.com",
    CH_USASPENDING: "usaspending.gov",
    CH_DOD: "defense.gov / usaspending.gov",
    CH_SEC: "sec.gov",
    CH_FDA: "open.fda.gov",
    CH_GOV_TX: "house/senate disclosures",
}

_UA = "LeiBotDiscovery/1.0 (research; local; contact: local)"

# National-lab / non-equity noise for USAspending ranking.
_LAB_NOISE = re.compile(
    r"\b(national laboratory|national labs?|battelle|sandia|los alamos|"
    r"oak ridge|lawrence livermore|argonne|brookhaven|pacific northwest|"
    r"idaho national|fermi|SLAC|JET PROPULSION|NASA GODDARD|"
    r"TRIAD NATIONAL|UT-BATTELLE|MANAGEMENT AND OPERATION|"
    r"SAVANNAH RIVER|NUCLEAR SOLUTIONS|BECHTEL NATIONAL|"
    r"MISSION SUPPORT|FLUOR FEDERAL|HONEYWELL FEDERAL|"
    r"UNIVERSITY|TRUSTEES OF|COLLEGE|SCHOOL OF MEDICINE)\b",
    re.I,
)

_SEC_POS_HINTS = [
    "raises guidance", "raised guidance", "guidance raise", "increases guidance",
    "enters into", "material definitive agreement", "supply agreement",
    "purchase agreement", "awarded", "contract", "collaboration",
    "fda approval", "receives approval", "record revenue", "beats",
    "backlog", "new customer", "strategic partnership", "joint venture",
]
_SEC_NEG_HINTS = [
    "bankruptcy", "going concern", "restatement", "material weakness",
    "investigation", "subpoena", "impairment", "workforce reduction",
    "delay in", "unable to", "defaults", "delisting", "resigns",
]


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: int = 40,
) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _http_text(url: str, *, timeout: int = 40) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _UA, "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _raw_event(
    *,
    channel: str,
    summary: str,
    company_hint: str | None = None,
    ticker_hint: str | None = None,
    category_hint: str | None = None,
    amount: float | None = None,
    source_url: str | None = None,
    event_date: str | None = None,
    cheap_score: float = 0.0,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "channel": channel,
        "summary": (summary or "").strip()[:500],
        "company_hint": company_hint,
        "ticker_hint": (ticker_hint or "").strip().upper() or None,
        "category_hint": category_hint,
        "amount": amount,
        "source_url": source_url,
        "event_date": event_date,
        "cheap_score": float(cheap_score),
        "meta": meta or {},
    }


def _top_n(events: list[dict[str, Any]], n: int = TOP_N_PER_CHANNEL) -> list[dict[str, Any]]:
    ranked = sorted(events, key=lambda e: float(e.get("cheap_score") or 0), reverse=True)
    # Deduplicate within channel by normalized summary prefix.
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for e in ranked:
        key = re.sub(r"\s+", " ", (e.get("summary") or "").lower())[:120]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(e)
        if len(out) >= n:
            break
    return out


# ---------------------------------------------------------------------------
# 1) USAspending
# ---------------------------------------------------------------------------

def fetch_usaspending_raw(*, days: int = 21, limit: int = CHANNEL_FETCH_LIMIT) -> list[dict[str, Any]]:
    end = date.today()
    start = end - timedelta(days=days)
    body = {
        "filters": {
            "award_type_codes": ["A", "B", "C", "D"],
            "time_period": [
                {"start_date": start.isoformat(), "end_date": end.isoformat()}
            ],
            "award_amounts": [{"lower_bound": 10_000_000}],
        },
        "fields": [
            "Award ID",
            "Recipient Name",
            "Award Amount",
            "Description",
            "Start Date",
            "Awarding Agency",
            "Contract Award Type",
        ],
        "limit": min(100, max(limit, 25)),
        "page": 1,
        "sort": "Award Amount",
        "order": "desc",
    }
    try:
        data = _http_json(
            "https://api.usaspending.gov/api/v2/search/spending_by_award/",
            method="POST",
            body=body,
            timeout=50,
        )
    except Exception as exc:
        log.warning("USAspending fetch failed: %s", exc)
        return []
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("Recipient Name") or "").strip()
        desc = str(r.get("Description") or "").strip()
        amt = r.get("Award Amount")
        try:
            amount = float(amt) if amt is not None else None
        except (TypeError, ValueError):
            amount = None
        blob = f"{name} {desc}"
        if _LAB_NOISE.search(blob):
            continue
        if not name:
            continue
        summary = (
            f"{name} awarded US government contract"
            + (f" worth ${amount:,.0f}" if amount else "")
            + (f": {desc[:180]}" if desc else "")
        )
        # Cheap rank: log-ish amount + prefer shorter commercial names.
        cheap = 0.0
        if amount:
            cheap += min(95.0, 40.0 + (amount ** 0.15))
        if "inc" in name.lower() or "corp" in name.lower() or "ltd" in name.lower():
            cheap += 8.0
        out.append(
            _raw_event(
                channel=CH_USASPENDING,
                summary=summary,
                company_hint=name,
                category_hint="gov_contract",
                amount=amount,
                event_date=str(r.get("Start Date") or "")[:10] or None,
                source_url="https://www.usaspending.gov/",
                cheap_score=cheap,
                meta={
                    "award_id": r.get("Award ID"),
                    "agency": r.get("Awarding Agency"),
                },
            )
        )
    return out


def channel_usaspending() -> list[dict[str, Any]]:
    return _top_n(fetch_usaspending_raw())


# ---------------------------------------------------------------------------
# 2) DoD — USAspending DoD-funded + defense.gov Contracts RSS/HTML fallback
# ---------------------------------------------------------------------------

def fetch_dod_usaspending(*, days: int = 21, limit: int = CHANNEL_FETCH_LIMIT) -> list[dict[str, Any]]:
    end = date.today()
    start = end - timedelta(days=days)
    body = {
        "filters": {
            "award_type_codes": ["A", "B", "C", "D"],
            "time_period": [
                {"start_date": start.isoformat(), "end_date": end.isoformat()}
            ],
            "award_amounts": [{"lower_bound": 5_000_000}],
            "agencies": [
                {
                    "type": "awarding",
                    "tier": "toptier",
                    "name": "Department of Defense",
                }
            ],
        },
        "fields": [
            "Award ID",
            "Recipient Name",
            "Award Amount",
            "Description",
            "Start Date",
            "Awarding Agency",
        ],
        "limit": min(100, max(limit, 25)),
        "page": 1,
        "sort": "Award Amount",
        "order": "desc",
    }
    try:
        data = _http_json(
            "https://api.usaspending.gov/api/v2/search/spending_by_award/",
            method="POST",
            body=body,
            timeout=50,
        )
    except Exception as exc:
        log.warning("DoD USAspending fetch failed: %s", exc)
        return []
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("Recipient Name") or "").strip()
        desc = str(r.get("Description") or "").strip()
        if not name or _LAB_NOISE.search(f"{name} {desc}"):
            continue
        try:
            amount = float(r.get("Award Amount")) if r.get("Award Amount") is not None else None
        except (TypeError, ValueError):
            amount = None
        summary = (
            f"DoD contract: {name}"
            + (f" ${amount:,.0f}" if amount else "")
            + (f" — {desc[:160]}" if desc else "")
        )
        cheap = 50.0 + (min(45.0, (amount or 0) ** 0.12) if amount else 0)
        out.append(
            _raw_event(
                channel=CH_DOD,
                summary=summary,
                company_hint=name,
                category_hint="gov_contract",
                amount=amount,
                event_date=str(r.get("Start Date") or "")[:10] or None,
                source_url="https://www.defense.gov/News/Contracts/",
                cheap_score=cheap,
                meta={"award_id": r.get("Award ID"), "via": "usaspending_dod"},
            )
        )
    return out


def fetch_dod_defense_gov_titles() -> list[dict[str, Any]]:
    """Supplement with defense.gov Contracts listing titles when available."""
    try:
        html = _http_text("https://www.defense.gov/News/Contracts/", timeout=30)
    except Exception as exc:
        log.warning("defense.gov contracts page failed: %s", exc)
        return []
    # Article cards / listing links
    links = re.findall(
        r'href="(https://www\.defense\.gov/News/Contracts/Contract/Article/\d+/[^"]+)"',
        html,
        flags=re.I,
    )
    titles = re.findall(
        r'Contracts For ([A-Za-z]+ \d{1,2}, \d{4})',
        html,
    )
    out: list[dict[str, Any]] = []
    # Prefer extracting listing blurbs with dollar amounts if present
    blurbs = re.findall(
        r"([A-Z][A-Za-z0-9 &.,'\-]{2,60}),?\s+[A-Z][a-z]+,?\s+was awarded[^\.]{0,200}",
        html,
    )
    for i, blurb in enumerate(blurbs[:CHANNEL_FETCH_LIMIT]):
        amt_m = re.search(r"\$[\d,]+(?:\.\d+)?\s*(?:million|billion)?", blurb, re.I)
        amount = None
        if amt_m:
            raw = amt_m.group(0).lower().replace(",", "").replace("$", "")
            try:
                if "billion" in raw:
                    amount = float(re.findall(r"[\d.]+", raw)[0]) * 1e9
                elif "million" in raw:
                    amount = float(re.findall(r"[\d.]+", raw)[0]) * 1e6
                else:
                    amount = float(re.findall(r"[\d.]+", raw)[0])
            except Exception:
                amount = None
        company = blurb.split(",")[0].strip()
        cheap = 55.0 + (min(40.0, (amount or 0) ** 0.12) if amount else 5.0)
        out.append(
            _raw_event(
                channel=CH_DOD,
                summary=f"DoD announcement: {blurb[:280]}",
                company_hint=company,
                category_hint="gov_contract",
                amount=amount,
                source_url=links[i] if i < len(links) else "https://www.defense.gov/News/Contracts/",
                cheap_score=cheap,
                meta={"via": "defense_gov", "titles_hint": titles[:3]},
            )
        )
    return out


def channel_dod() -> list[dict[str, Any]]:
    raw = fetch_dod_usaspending()
    raw.extend(fetch_dod_defense_gov_titles())
    return _top_n(raw)


# ---------------------------------------------------------------------------
# 3) SEC EDGAR — recent 8-K, classify (not auto-positive)
# ---------------------------------------------------------------------------

def _sec_cheap_polarity(text: str) -> tuple[float, str | None]:
    low = (text or "").lower()
    if any(n in low for n in _SEC_NEG_HINTS):
        return -1.0, None
    hits = [h for h in _SEC_POS_HINTS if h in low]
    if not hits:
        return 0.0, None
    cat = None
    if any("guidance" in h for h in hits):
        cat = "guidance_raise"
    elif any("fda" in h or "approval" in h for h in hits):
        cat = "regulatory_approval"
    elif any("contract" in h or "agreement" in h or "awarded" in h for h in hits):
        cat = "commercial_contract"
    elif any("customer" in h for h in hits):
        cat = "new_customer"
    elif any("partnership" in h or "joint venture" in h or "collaboration" in h for h in hits):
        cat = "strategic_partnership"
    elif any("beat" in h or "record" in h for h in hits):
        cat = "earnings_acceleration"
    score = 40.0 + 8.0 * min(5, len(hits))
    return score, cat


def fetch_sec_8k_atom(*, limit: int = CHANNEL_FETCH_LIMIT) -> list[dict[str, Any]]:
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
        "&type=8-K&company=&dateb=&owner=include&count=100&output=atom"
    )
    try:
        raw = _http_text(url, timeout=35)
    except Exception as exc:
        log.warning("SEC atom failed: %s", exc)
        return []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall("a:entry", ns)[:limit]
    out: list[dict[str, Any]] = []
    for ent in entries:
        title = (ent.findtext("a:title", default="", namespaces=ns) or "").strip()
        link_el = ent.find("a:link", ns)
        href = ""
        if link_el is not None:
            href = link_el.attrib.get("href") or ""
        summary = (ent.findtext("a:summary", default="", namespaces=ns) or "").strip()
        # Title format: 8-K - Company Name (CIK) (Filer)
        co = None
        m = re.match(r"8-K\s*-\s*(.+?)\s*\(\d+\)", title, flags=re.I)
        if m:
            co = m.group(1).strip()
        blob = f"{title} {summary}"
        # Optional: peek filing index page for item text (cheap, capped).
        extra = ""
        if href and len(out) < 15:
            try:
                page = _http_text(href, timeout=12)
                # Strip tags lightly
                text = re.sub(r"<[^>]+>", " ", page)
                text = re.sub(r"\s+", " ", text)
                # Keep a window around Item 2.02 / 7.01 / 8.01
                for item in ("Item 2.02", "Item 7.01", "Item 8.01", "Item 1.01"):
                    idx = text.find(item)
                    if idx >= 0:
                        extra += " " + text[idx : idx + 500]
                time.sleep(0.12)
            except Exception:
                pass
        pol, cat = _sec_cheap_polarity(blob + " " + extra)
        if pol <= 0:
            continue  # not a positive material candidate for this experiment
        summary_line = (
            f"{co or 'Company'} 8-K: "
            + (extra.strip()[:220] if extra.strip() else (summary[:220] or "material event"))
        )
        # Prefer filings with clearer positive hints
        out.append(
            _raw_event(
                channel=CH_SEC,
                summary=summary_line,
                company_hint=co,
                category_hint=cat,
                source_url=href or None,
                cheap_score=pol,
                meta={"filing_title": title, "verified_source": "sec.gov"},
            )
        )
    return out


def channel_sec() -> list[dict[str, Any]]:
    return _top_n(fetch_sec_8k_atom())


# ---------------------------------------------------------------------------
# 4) FDA / openFDA
# ---------------------------------------------------------------------------

def fetch_fda_510k(*, days: int = 30, limit: int = CHANNEL_FETCH_LIMIT) -> list[dict[str, Any]]:
    end = date.today()
    start = end - timedelta(days=days)
    # openFDA wants unescaped [ ] and +TO+ in the query string.
    search = f"decision_date:[{start.strftime('%Y%m%d')}+TO+{end.strftime('%Y%m%d')}]"
    url = (
        "https://api.fda.gov/device/510k.json?search="
        + search
        + f"&limit={min(99, limit)}"
    )
    try:
        data = _http_json(url, timeout=35)
    except Exception as exc:
        log.warning("openFDA 510k failed: %s", exc)
        return []
    if not isinstance(data, dict):
        return []
    rows = data.get("results") or []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        decision = str(r.get("decision_description") or "")
        if re.search(r"deny|not substantially|withdraw", decision, re.I):
            continue
        applicant = str(r.get("applicant") or "").strip()
        device = str(r.get("device_name") or "").strip()
        of = r.get("openfda") if isinstance(r.get("openfda"), dict) else {}
        if not device and isinstance(of.get("device_name"), list) and of["device_name"]:
            device = str(of["device_name"][0])
        summary = (
            f"FDA 510(k): {applicant or 'Applicant'} — {device or 'device'}"
            + (f" ({decision})" if decision else "")
        )
        cheap = 70.0
        if re.search(r"SESE|substantially equivalent", decision, re.I):
            cheap += 10.0
        out.append(
            _raw_event(
                channel=CH_FDA,
                summary=summary,
                company_hint=applicant or None,
                category_hint="regulatory_approval",
                event_date=str(r.get("decision_date") or "")[:10] or None,
                source_url="https://open.fda.gov/apis/device/510k/",
                cheap_score=cheap,
                meta={
                    "k_number": r.get("k_number"),
                    "verified_source": "openfda",
                },
            )
        )
    return out


def fetch_fda_drugs_recent(*, limit: int = 30) -> list[dict[str, Any]]:
    """Recent Drugs@FDA applications with approval-ish submissions (cheap filter)."""
    end = date.today()
    start = end - timedelta(days=45)
    search = (
        f"submissions.submission_status:AP+AND+"
        f"submissions.submission_status_date:[{start.strftime('%Y%m%d')}+TO+{end.strftime('%Y%m%d')}]"
    )
    url = (
        "https://api.fda.gov/drug/drugsfda.json?search="
        + search
        + f"&limit={min(99, limit)}"
    )
    try:
        data = _http_json(url, timeout=35)
    except Exception as exc:
        log.warning("openFDA drugsfda failed: %s", exc)
        return []
    rows = data.get("results") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        sponsor = str(r.get("sponsor_name") or "").strip()
        app = str(r.get("application_number") or "")
        brands = []
        of = r.get("openfda") if isinstance(r.get("openfda"), dict) else {}
        bn = of.get("brand_name")
        if isinstance(bn, list):
            brands = [str(x) for x in bn[:2]]
        summary = (
            f"FDA drug approval activity: {sponsor or 'Sponsor'}"
            + (f" — {', '.join(brands)}" if brands else "")
            + (f" ({app})" if app else "")
        )
        out.append(
            _raw_event(
                channel=CH_FDA,
                summary=summary,
                company_hint=sponsor or None,
                category_hint="regulatory_approval",
                source_url="https://open.fda.gov/apis/drug/drugsfda/",
                cheap_score=78.0,
                meta={"application_number": app, "verified_source": "openfda"},
            )
        )
    return out


def channel_fda() -> list[dict[str, Any]]:
    raw = fetch_fda_510k()
    raw.extend(fetch_fda_drugs_recent())
    return _top_n(raw)


# ---------------------------------------------------------------------------
# 5) Government financial disclosures (stock purchases)
# ---------------------------------------------------------------------------

def fetch_gov_tx_google_news(*, limit: int = CHANNEL_FETCH_LIMIT) -> list[dict[str, Any]]:
    """
    Official PTR pages are awkward to scrape; use focused news radar for
    disclosed congressional / official purchases (discovery signal only).
    """
    query = (
        '("periodic transaction report" OR "STOCK Act" OR "congressman bought" '
        'OR "senator bought" OR "lawmaker bought" OR "disclosed purchase") '
        "(shares OR stock) when:7d"
    )
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        raw = _http_text(url, timeout=25)
    except Exception as exc:
        log.warning("gov_tx news RSS failed: %s", exc)
        return []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if " - " in title:
            title = title.rsplit(" - ", 1)[0].strip()
        low = title.lower()
        if not any(x in low for x in ("buy", "bought", "purchase", "purchased", "acquires")):
            # still allow disclosed PTR headlines
            if "transaction" not in low and "stock act" not in low:
                continue
        # Prefer purchase language
        cheap = 60.0
        if any(x in low for x in ("bought", "purchase", "purchased", "buys")):
            cheap += 15.0
        if any(x in low for x in ("sold", "sale", "sells")):
            continue  # purchases only for this positive discovery channel
        out.append(
            _raw_event(
                channel=CH_GOV_TX,
                summary=title,
                category_hint="politician_purchase",
                source_url=link or None,
                cheap_score=cheap,
                meta={"via": "disclosure_news_radar"},
            )
        )
    return out


def channel_gov_transactions() -> list[dict[str, Any]]:
    return _top_n(fetch_gov_tx_google_news())


# ---------------------------------------------------------------------------
# Orchestrator — independent Top-5 per channel
# ---------------------------------------------------------------------------

def collect_channel_top5() -> dict[str, Any]:
    """
    Run five channels independently. Each contributes up to 5 events.
    Returns {channel: [events], stats: {...}}.
    """
    fetchers = {
        CH_USASPENDING: channel_usaspending,
        CH_DOD: channel_dod,
        CH_SEC: channel_sec,
        CH_FDA: channel_fda,
        CH_GOV_TX: channel_gov_transactions,
    }
    by_channel: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    for ch in CHANNEL_ORDER:
        try:
            rows = fetchers[ch]()
            by_channel[ch] = rows[:TOP_N_PER_CHANNEL]
            log.info("channel %s → %s events", ch, len(by_channel[ch]))
        except Exception as exc:
            log.exception("channel %s failed", ch)
            by_channel[ch] = []
            errors[ch] = str(exc)
        time.sleep(0.2)

    counts = {ch: len(by_channel.get(ch) or []) for ch in CHANNEL_ORDER}
    raw_total = sum(counts.values())
    return {
        "by_channel": by_channel,
        "channel_counts": counts,
        "raw_total": raw_total,
        "errors": errors,
        "top_n": TOP_N_PER_CHANNEL,
    }
