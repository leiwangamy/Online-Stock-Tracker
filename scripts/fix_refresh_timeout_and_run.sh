#!/bin/bash
# Fix 502 on long price refresh: gunicorn + nginx timeouts, then run one refresh.
set -euo pipefail

echo "=== patch nginx timeouts ==="
STOCK=/etc/nginx/sites-available/stock
if ! sudo grep -q "proxy_read_timeout" "$STOCK"; then
  sudo python3 <<'PY'
from pathlib import Path
p = Path("/etc/nginx/sites-available/stock")
t = p.read_text()
needle = "        proxy_set_header X-Forwarded-Proto $scheme;"
insert = needle + """
        proxy_connect_timeout 60s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
"""
if "proxy_read_timeout" not in t:
    if needle not in t:
        raise SystemExit("nginx stock pattern not found")
    t = t.replace(needle, insert, 1)
    p.write_text(t)
    print("nginx patched")
else:
    print("nginx already has timeouts")
PY
else
  echo "nginx timeouts already present"
fi
sudo nginx -t
sudo systemctl reload nginx

echo "=== sync app files & rebuild container ==="
# files already scp'd by caller into /var/www/leibot
cd /var/www/leibot
sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml build
sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate --no-deps
sleep 4
sudo docker ps --format "{{.Names}} {{.Status}} {{.Image}}"

echo "=== health ==="
curl -sS -o /dev/null -w "https=%{http_code}\n" --max-time 15 https://stock.lwsoc.com/

echo "=== kick off price refresh inside container (may take several minutes) ==="
sudo docker exec stock_web_prod python - <<'PY'
from update_jobs import job_refresh_prices
print("refresh start", flush=True)
r = job_refresh_prices(max_workers=2)
print(r, flush=True)
print("refresh done", flush=True)
PY

echo "=== done ==="
curl -sS -o /dev/null -w "https=%{http_code}\n" --max-time 15 https://stock.lwsoc.com/
