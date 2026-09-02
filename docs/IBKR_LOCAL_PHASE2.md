# LeiBot — IBKR Local Sync Phase 2 (Paper TWS smoke test)

Yahoo Finance automatic updates are unchanged. This document is **local only**.

## What Phase 2 does

- Connects to **Paper TWS** (already logged in by you)
- Resolves contracts for `AAPL`, `MSFT`, `SPY`
- Prints latest price / previous close / timestamp / daily bar count
- **Does not** write to production
- **Does not** change Yahoo
- **Does not** place orders
- **Does not** store IBKR username / password / 2FA

## TWS settings you must enable (Paper)

1. Open **IBKR Paper Trading** TWS (not the live account).
2. Log in yourself (2FA as usual).
3. **Edit → Global Configuration → API → Settings** (wording may vary slightly by TWS version):
   - **Enable ActiveX and Socket Clients** — checked
   - **Socket port** — **7497** (Paper TWS default)
   - **Read-Only API** — **checked** (recommended for Phase 2)
   - **Download open orders on connection** — optional; leave default
   - **Allow connections from localhost** — ensure local clients are allowed
4. Click **Apply** / **OK**.
5. If TWS shows an inbound API connection dialog on first run, **accept** it for `127.0.0.1`.

### Ports (reference)

| App | Typical port |
|-----|----------------|
| **Paper TWS** | **7497** ← use this |
| Live TWS | 7496 ← do not use in Phase 2 |
| Paper Gateway | 4002 |
| Live Gateway | 4001 |

### Connection defaults used by LeiBot Phase 2

| Setting | Value |
|---------|--------|
| Host | `127.0.0.1` |
| Port | `7497` (Paper TWS) |
| Client ID | `71` |
| Read-only API mode | **Yes** (`readonly=True`) |
| Credentials in LeiBot | **None** |

Optional overrides (env vars — host/port/id only, never passwords):

```bat
set IBKR_HOST=127.0.0.1
set IBKR_PAPER_PORT=7497
set IBKR_CLIENT_ID=71
```

## Install (Windows, once)

From the repo root, in your Python environment:

```bat
pip install -r requirements-ibkr.txt
```

Requires a local Python with `asyncio` (3.10+ is fine; 3.14 works with the smoke-test loop bootstrap).

Production Docker / `requirements.txt` is intentionally **not** changed.

## Manual smoke test command

1. Paper TWS logged in + API enabled (above).
2. In PowerShell or cmd:

```bat
cd C:\Users\Admin\Documents\Online-Stock-Tracker
python scripts\ibkr_paper_smoke_test.py
```

Optional JSON dump:

```bat
python scripts\ibkr_paper_smoke_test.py --json
```

Custom tickers (still small):

```bat
python scripts\ibkr_paper_smoke_test.py --tickers AAPL,MSFT,SPY
```

## Expected output (success — Paper TWS open + API on)

```
=== LeiBot IBKR Phase 2 - Paper TWS smoke test ===
Host: 127.0.0.1  Port: 7497  ClientId: 71  ReadOnly: True
Connected: True  OK: True
Managed accounts (hint only): ['DUxxxxxxxx']

--- AAPL ---
  IBKR connection:        CONNECTED
  Contract resolved:      OK
  Latest price:           226.5
  Previous close:         225.1
  Price timestamp:        2026-09-01T...
  Historical daily avail: True
  Daily bars returned:    251
  Error/message:          None

--- MSFT ---
  IBKR connection:        CONNECTED
  Contract resolved:      OK
  Latest price:           ...
  Previous close:         ...
  Price timestamp:        ...
  Historical daily avail: True
  Daily bars returned:    ...
  Error/message:          None

--- SPY ---
  IBKR connection:        CONNECTED
  Contract resolved:      OK
  Latest price:           ...
  Previous close:         ...
  Price timestamp:        ...
  Historical daily avail: True
  Daily bars returned:    ...
  Error/message:          None

Phase 2 complete - no production sync, no Yahoo changes, no orders.
```

Numbers vary by market session. Delayed data on Paper is OK for Phase 2.

## Expected output (TWS closed / API off)

```
Connected: False  OK: False
ERROR: Could not connect to IBKR at 127.0.0.1:7497 ...
  IBKR connection:        DISCONNECTED
  Contract resolved:      SKIPPED
  ...
```

Still **no** credentials prompt from LeiBot.

## Exit codes

- `0` — connected and at least one ticker returned a usable price
- `1` — connection failed or no usable prices

## Out of scope (later phases)

- Recalculating LeiBot SMA / Dist locally
- Syncing to production
- Full universe refresh
- Live account

Stop after Phase 2 until you approve Phase 3.
