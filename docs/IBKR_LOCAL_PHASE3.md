# LeiBot — IBKR Local Sync Phase 3 (compare only)

Yahoo Finance automatic updates are **unchanged**. No production sync. No orders.

## What Phase 3 does

For `AAPL`, `MSFT`, `SPY`:

1. Pull snapshot + daily history from **Paper TWS** (same connection settings as Phase 2)
2. Pull **live Yahoo** daily closes via LeiBot’s existing `load_yahoo_daily_closes` (read-only)
3. Read **local** `data/leibot.db` `dashboard_cache` (read-only)
4. Print comparison tables and lag vs IBKR

## Run (Windows)

Paper TWS logged in + API on (`127.0.0.1:7497`, Read-Only, client id `71`):

```bat
cd C:\Users\Admin\Documents\Online-Stock-Tracker
python scripts\ibkr_yahoo_compare.py
```

## Tables

1. **IBKR vs live Yahoo** — primary freshness check of the Yahoo API path  
2. **IBKR vs LeiBot cache** — shows whether the *stored* LeiBot prices are stale  

`Lag Days` = business-day count (IBKR latest daily bar date − Yahoo/cache date).  
Positive ⇒ Yahoo/LeiBot is behind IBKR.

## Out of scope

- Sync to production  
- Changing Yahoo schedule / formulas  
- Orders  
- Phase 4+

Stop after Phase 3 until you approve the next phase.
