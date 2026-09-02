# LeiBot — IBKR Local Sync Phase 4 (indicator compatibility, LOCAL ONLY)

Yahoo Finance remains the **primary** market-data source.  
IBKR is evaluated only as a **per-ticker fallback** into the **same** LeiBot indicator engine.

## Architecture (design)

```
Yahoo Primary
    ↓ validate each ticker
Fresh / valid → keep Yahoo
    ↓
Failed / missing / stale → IBKR-Fallback (that ticker only)
    ↓
Same LeiBot indicator engine
    ↓
Merge + per-ticker source label (Yahoo | IBKR-Fallback)
```

Do **not** request IBKR when Yahoo is already fresh and valid.

## What Phase 4 tests

For `AAPL`, `MSFT`, `SPY`:

1. Load IBKR daily bars (`whatToShow=TRADES`, Paper TWS, read-only)
2. Load live Yahoo closes via `load_yahoo_daily_closes` (read-only)
3. Run **both** series through `market_data.compute_indicators_from_closes`
4. Compare SMA25/50/63/90, Dist%, Rebound%, 63D position%
5. Propose fallback eligibility rules (not enforced yet)
6. Preview per-ticker source tracking (not written to DB)

## Run (Windows)

Paper TWS logged in + API on (`127.0.0.1:7497`, Read-Only):

```bat
cd C:\Users\Admin\Documents\Online-Stock-Tracker
python scripts\ibkr_phase4_indicators.py
```

## Shared engine

`compute_indicators_from_closes(closes)` reuses existing helpers:

- `_sma`, `_rebound_pct`, `_range_63d`
- `dist_sma_pct` → `(price / SMA - 1) * 100`

## Out of scope

- Production sync / DB migration
- Yahoo scheduler changes
- Enabling fallback in production
- Orders
- Phase 5+

Stop after Phase 4 until explicitly approved.
