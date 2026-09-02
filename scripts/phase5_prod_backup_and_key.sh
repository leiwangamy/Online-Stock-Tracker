#!/bin/bash
# Phase 5 production helper — run ON the EC2 host after code deploy.
# Usage:
#   bash scripts/phase5_prod_backup_and_key.sh
set -euo pipefail

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST="/home/ubuntu/backups/leibot_pre_phase5_${STAMP}.db"
mkdir -p /home/ubuntu/backups
sudo cp -a /var/www/leibot/data/leibot.db "$DEST"
sudo chown ubuntu:ubuntu "$DEST"
echo "BACKUP_PATH=$DEST"
ls -la "$DEST"
python3 - <<PY
import sqlite3
c=sqlite3.connect("$DEST")
print("ROWS", c.execute("select count(*) from dashboard_cache").fetchone()[0])
print("AAPL", c.execute("select price, updated_at from dashboard_cache where ticker='AAPL'").fetchone())
PY

# Append sync API key if missing (does not print the secret).
if sudo grep -q '^LEIBOT_MARKET_SYNC_API_KEY=' /etc/leibot/prod.env; then
  echo "LEIBOT_MARKET_SYNC_API_KEY already present in /etc/leibot/prod.env"
else
  KEY=$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)
  echo "LEIBOT_MARKET_SYNC_API_KEY=$KEY" | sudo tee -a /etc/leibot/prod.env >/dev/null
  sudo chmod 600 /etc/leibot/prod.env
  echo "LEIBOT_MARKET_SYNC_API_KEY added (len=${#KEY}, prefix=${KEY:0:4}...)"
fi
