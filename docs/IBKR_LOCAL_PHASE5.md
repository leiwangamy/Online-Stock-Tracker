# LeiBot — IBKR Local Sync Phase 5 (secure HTTPS sync, controlled test)

Yahoo Finance remains **primary**. IBKR is **fallback only**.

## Endpoint

`POST /api/market/ibkr-sync`

- Auth: `Authorization: Bearer <LEIBOT_MARKET_SYNC_API_KEY>`
- Merge: **per-ticker upsert only** (never `replace_all`)
- Freshness: older incoming rows are **SKIP**ped
- Transaction: failure → **ROLLBACK**

## Environment

| Location | File | Variable |
|----------|------|----------|
| Local | `.env` (gitignored) | `LEIBOT_MARKET_SYNC_API_KEY` |
| Production | `/etc/leibot/prod.env` | `LEIBOT_MARKET_SYNC_API_KEY` |

Never commit the key. Never put it in frontend JS. Never log the full key.

## Payload (small test)

```json
{
  "dry_run": false,
  "rows": [
    {
      "ticker": "ZZIBKR1",
      "price": 111.11,
      "data_date": "2026-09-01",
      "data_source": "IBKR-Fallback",
      "status": "FALLBACK",
      "sma": 108.89,
      "dist_pct": 2.04,
      "rebound_pct": 5.5,
      "sma_period": 25
    }
  ]
}
```

## Local self-test

```bat
cd C:\Users\Admin\Documents\Online-Stock-Tracker
venv\Scripts\python.exe scripts\ibkr_phase5_sync_test.py
```

Creates a backup under `data/backups/` and does **not** delete it.

## Architecture reminder

```
Yahoo primary refresh (unchanged)
    → validate per ticker
    → keep fresh Yahoo
    → IBKR only for failed/missing/stale (later phases)
    → POST /api/market/ibkr-sync
    → per-ticker merge + metadata
```

## Out of scope

- Full-universe automatic fallback
- Yahoo scheduler changes
- Orders / trading
- Phase 6+
