# AI STOCK SELECTION & BUY ARCHITECTURE

> Source of truth for LeiBot long-term universe + AI BUY.  
> Last updated: 2026-08-27 (Research Strategy Pools + five-strategy lab shells)

## Design principles

1. **Long-term numeric characteristics determine WHAT we watch.**
2. **Short-term market conditions determine WHEN we buy.**
3. The **Core Universe Filter** must be: deterministic · numeric · reproducible · explainable · configurable.
4. **Do not force exactly 100 stocks.** Qualification quality > list size.
5. **Do not let an LLM subjectively choose the Core Universe.** AI may nominate for review; numeric rules qualify.
6. Every rejected stock has an explicit numeric **failure reason**.
7. Do **not** mix Rising / Knife / Sector Rotation NOW / SMA25 entry into Core qualification — those belong to **AI BUY**.
8. The Core Universe may slowly evolve (occasional ADD / REMOVE), not daily churn.
9. **PRICE = opportunity. BLOCK = permission** (AI BUY).
10. **No auto real orders in V1.** Owner decides membership and READY trades.

## Architecture

```
RAW MARKET DATA (Yahoo daily closes → dashboard_cache)
        ↓
## MARKET DATA VALIDATION  (DATA QUALITY — not trading quality)
   history · price · SMA25_D · Dist · 63D · Avg Daily Move
        ↓
DERIVED METRICS (only after history validates)
   SMA25_D · Dist_SMA25 · 63D Position · Avg Daily Move 63D · …
        ↓
## DERIVED DATA VALIDATION
        ↓
## CORE UNIVERSE FILTER  (PASS / FAIL only — no Top-N sort)
        ↓
QUALIFIED STOCKS (+ Filter Funnel + failure reasons)
        ↓
Owner: ADD / KEEP / REMOVE  →  My Watchlist (optional)
        ↓
## OBSERVATION POOLS (parallel)
   My Watchlist  ∪  Nasdaq-100  ∪  AI Approved
        ↓
## SMA ALERT GATE  (Dist SMA25 bands — same on Watchlist + AI BUY)
   > −5% none · 🟡 WATCH −5~−10% · 🟢 LOW −10~−15% · 🟠 DEEP −15~−20% · 🔵 EXTREME ≤−20%
        ↓
## AI BUY  (short-term timing on Alert-marked names only)
   DATA_BLOCK hard gate before Price/Buy Score
        ↓
NEXT CANDIDATES / CANDIDATES (full day’s Alert-marked list, including HOLD) → READY → validate_buy_data() → Owner / Paper Trade
WAIT / APPROACHING / STABILIZING / READY / BLOCKED / HOLD

**Paper auto-buy (simulation):** On **Refresh AI BUY** (and day-roll auto refresh), if cash + trading-limit room remain, open next unused READY / STABILIZING names (prefer never-traded). Setting: `paper_auto_buy_on_refresh`.
```

### Dist SMA25 Alert bands

| Dist SMA25 | Label | Color | Meaning |
|------------|-------|-------|---------|
| > −5% | — | none | Normal — not highlighted |
| −5% ~ −10% | WATCH | 🟡 | Start watching |
| −10% ~ −15% | LOW | 🟢 | Clearly low |
| −15% ~ −20% | DEEP | 🟠 | Deep pullback |
| ≤ −20% | EXTREME | 🔵 | Extreme discount |

`Dist_SMA25% = (Price − SMA25_D) / SMA25_D × 100`. Manual Active Alert remains optional Owner note; **colored state always follows Dist**.

### Market Data Validation

> No trading decision should use a derived market metric before its source data and calculation have passed validation.

> **DATA ERROR is a hard BUY block, not a scoring penalty.**

> **SMA25 used by AI BUY means 25 trading-day SMA from DAILY closes (`SMA25_D`).** Not hourly/intraday bars.

Module: `market_data_validator.py`

| Status | Meaning |
|--------|---------|
| PASS | Usable for AI BUY / READY |
| WARNING | Research OK; READY requires PASS |
| ERROR / INSUFFICIENT_DATA | `DATA_BLOCK` → BUY Score N/A → BLOCKED |
| STALE_DATA | Timestamp too old for READY |

Canonical Dist:

`Dist_SMA25_pct = (Price − SMA25_D) / SMA25_D × 100`

Separate from trading gates: KNIFE / NEWS / HIGH are **trading permission**, not data integrity.

Owner tools on AI BUY: **CHECK DATA** (per ticker) · **Validate Market Data** (observation-pool batch).

### Stage 1 — Core Universe Filter (WHO)

- Scans the **raw eligible market universe** directly.
- Does **NOT** require prior membership in STRONG / RISING / Oversold / Discovery / Sector Rotation.
- Those modules may inform later research or AI BUY — they do **not** define the long-term pool.
- **No subjective weighted “CORE SCORE” for inclusion** in V1 — transparent PASS/FAIL gates.
- Optional Path A (established industry position) / Path B (emerging growth + RS).

**V1 gates (configurable thresholds):**

| Gate | Default (test) |
|------|----------------|
| Market Cap | ≥ $10B |
| Avg Dollar Volume | ≥ $50M/day (V1 proxy: price × avg_vol_20d) |
| Avg Daily Move %(63D) | ≥ 1.0% |
| Revenue Growth YoY | > 0% (missing ≠ 0) |
| 126D Return | > 0% |
| 252D Return | > 0% |
| RS 252D vs SPY | ≥ −5% |
| Industry | Path A: industry mcap percentile ≥ 70 **or** Path B: strong rev growth + RS |

**Filter Funnel** is shown to Owner (counts after each stage). Final qualified count is whatever passes — not forced to 100.

### Stage 2 — AI BUY (WHEN)

**Observation pool:** My Watchlist ∪ Nasdaq-100 ∪ AI Approved.  
**Buy candidates:** only names currently **marked** by Dist SMA25 Alert (🟡 WATCH / 🟢 LOW / 🟠 DEEP / 🔵 EXTREME).  
**Timing:** Dist SMA25 Price Score, Recovery, HIGH/KNIFE/NEWS blocks → statuses READY → …  
**Paper fill:** Refresh AI BUY auto-opens unused READY/STABILIZING while fund/limit remain (`paper_auto_buy_on_refresh`).  
Short-term only. Never used to qualify Core Universe. No auto **real** brokerage orders in V1.

## Watchlist navigation

| Tab | Role |
|-----|------|
| **My Watchlist** | Owner manual observation / holdings research |
| **AI Approved** | Owner long-term pool; **same SMA Alert system as My Watchlist** |
| **Core Universe** | Numeric PASS list; **focus = not in My Watchlist ∪ Nasdaq-100** |
| **Nasdaq-100** | Independent index observation pool |
| **AI Discovery** | Nomination / event inbox (not auto-buy) |

### Core Universe table columns

| Column | Meaning | Visibility |
|--------|---------|------------|
| Ticker / **Price** / Industry / Market Cap / Ind % / $Vol / ADM% / Rev% / 126D / 252D | Long-term numeric metrics (+ latest cached price) | All users |
| **RS252** | Stock 252D return − SPY 252D return (%) | All users |
| **Path** | `ESTABLISHED` (industry mcap percentile) and/or `EMERGING` (strong growth + RS) | All users |
| **Status** | ✓ PASS / ✗ FAIL | **Admin only** |
| **Fail** | Failure reason codes (`FAIL_MARKET_CAP`, …) | **Admin only** |
| **APPROVAL** | Owner button → add to **AI Approved** (or KEEP/REMOVE on No Longer) | **Admin only** |

Add names to **My Watchlist** manually on that tab, or use **APPROVAL** on Core Universe (Admin) to send a name into **AI Approved**.

Public visitors see metrics for research transparency; they do not see Status / Fail / APPROVAL and cannot modify My Watchlist.


## Deprecated assumptions (do not restore)

- LLM / “AI SELECT” subjectively ranking or approving the long-term universe
- Building Core Universe only from STRONG ∪ RISING ∪ Discovery
- Soft weighted CORE SCORE as the *inclusion* rule (informational ranks OK later)
- Auto Top-100 by score sort
- Mixing Knife / Rising / SMA25 pullback into Core qualification
- Auto-adding/removing Core Watch without Owner confirmation

## WATCHLIST ↔ TRADING MODE (conceptual map)

**Watchlist** stays a flat product surface (My / AI Approved / Nasdaq-100 / screens / Temp).
AI Trading strategies *consume* lists via Strategy Pools; they do not rename the nav.

| Trading mode | Typical Watchlist sources | Status |
|---|---|---|
| **Alert Buy** | My Watchlist · AI Approved · Nasdaq-100 | Live |
| **Deep Recovery** | Oversold Pullback · Low 63D | Live screen |
| **Stable Growth** | Strong ∪ Rising ∪ ETF | TBD |
| **Safe Margin** | Target Ratio < 80% | Live screen; rank TBD |
| **Short Sell** | Bearish mirror of Alert Buy | TBD |

## RESEARCH / STRATEGY POOL ARCHITECTURE

```
MARKET DATA  →  shared price / SMA / returns / validation (calculate once)
      ↓
RESEARCH     →  discover · classify · approve · Strategy Pools
      ↓
STRATEGY POOLS  (membership = filters / unions — no duplicate downloads)
      ├── ALERT BUY      MY ∪ NDX100 ∪ AI APPROVED
      ├── DEEP RECOVERY  Dist SMA25 ≤ −10% (dynamic)
      ├── STABLE GROWTH  STRONG ∪ RISING ∪ ETF
      ├── SAFE MARGIN    TARGET-T + Financial — shell / empty
      └── SHORT SELL     3 bearish mirrors — shell / empty
      ↓
AI TRADING   →  Primary Rank → BLOCK → Trade Queue → Positions
```

> Market Data owns reusable market/fundamental data.

> Research owns discovery, classification and Strategy Pools.

> AI Trading consumes Strategy Pools and owns ranking, eligibility, positions and exits.

> Strategy Pools should reference shared ticker data rather than duplicate it.

> One ticker may belong to multiple Strategy Pools.

> Expensive calculations must be shared wherever practical.

> System principle: **Calculate Once — Classify Many — Trade Many.**

> Primary ranking is assigned before BLOCK evaluation.

> BLOCK affects eligibility only and must never reorder the original rank.

UI: Research → **Strategy Pools**; AI Trading Overview **Source Pool** column links back.

## FIVE-STRATEGY AI TRADING ARCHITECTURE

```
AI TRADING
├── Overview          (lab dashboard)
├── Alert Buy         (existing — live)
├── Deep Recovery     (shell / empty for now)
├── Stable Growth     (shell / empty)
├── Safe Margin       (shell / empty)
├── Short Sell        (shell / empty)
├── All Positions
├── History
└── AI Discovery
```

> Each strategy has one transparent primary ranking. Ranking describes opportunity order. BLOCK determines trade eligibility. **BLOCK must never alter the original ranking.**

> Assign and persist rank before applying BLOCK filters.

> The trading engine scans candidates in original rank order, skips blocked/ineligible candidates, and purchases the next eligible candidate according to available **strategy** capital.

> AI Score is supplementary unless explicitly designated as a strategy's primary ranking metric.

> Independent paper capital per strategy (`paper_strategy_accounts`). Same ticker may appear in multiple strategies (`ticker + strategy_id`).

V1 status: **layout + accounts first**. Alert Buy remains the live experiment. Deep Recovery / Stable Growth / Safe Margin / Short Sell pages are empty shells until ranking formulas are approved.

## Modules

| Module | Role |
|--------|------|
| `core_universe.py` | PASS/FAIL filter, funnel, failure reasons, diffs |
| `ai_select.py` | Membership helpers for `ai_approved` (ADD/REMOVE); legacy inbox deprecated |
| `ai_buy.py` | Short-term BUY scores / blocks / status |
| `ai_discovery.py` | Nomination / research — not qualification |
| `etf_universe.py` | Curated LeiBot ETF Universe metadata (categories / market / currency) |

## ETF DATA ARCHITECTURE

> ETFs and stocks share the same price-data infrastructure but remain distinct asset types (`asset_type = STOCK | ETF`).

> ETF price/technical metrics may reuse stock calculations (Yahoo daily closes → SMA25 / Dist / SMA63 / 63D / returns / dollar volume / validation).

> Company fundamental metrics must not be applied to ETFs (no Revenue / EPS / FCF / MOS / earnings-as-company). Prefer N/A / skip.

> ETF membership lives in `etf_universe` (separate from Wikipedia equity `universe`) so weekly stock refresh cannot wipe ETFs.

> ETF data should be downloaded once into `dashboard_cache` and shared by all future strategies.

> Future strategies must not independently duplicate ETF market-data downloads.

> Adding a strategy should primarily add strategy logic, not another market-data pipeline.

> **V1:** ETF Market Data UI only (`/dashboard/etf`). Do **not** auto-send ETF Universe into AI BUY / Alert Buy.

```
MARKET DATA
│
├── STOCK  (universe flags: SP500 / NDX100 / SP400 / SP600 / TSX)
│
└── ETF    (etf_universe → shared fetch_metrics_for_ticker)
      ├── Broad Market · Sector · Industry · Style
      ├── Bond · Commodity · Real Estate
      ├── International / Country · Canada
```

## Tuning workflow

1. Run Core Universe Filter on real Market Data.  
2. Read funnel + metric distributions.  
3. Tune thresholds so natural qualified set is ~50–100 **without** lowering quality just to hit 100.  
4. Owner ADD newly qualified; KEEP/REMOVE no-longer-qualified.
