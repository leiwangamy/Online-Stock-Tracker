#!/bin/bash
# LeiBot production switch — pre-build, cutover, smoke, rollback if needed.
set -euo pipefail

APP_DIR=/var/www/leibot
COMPOSE="sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml"
BACKUP_IMAGE=stock_web_prod_backup:latest
LOG=/tmp/leibot_deploy_$(date -u +%Y%m%d_%H%M%S).log
exec > >(tee -a "$LOG") 2>&1

echo "=== DEPLOY START $(date -u -Iseconds) ==="
cd "$APP_DIR"

rollback() {
  echo "!!! ROLLBACK to $BACKUP_IMAGE !!!"
  sudo docker stop stock_web_prod 2>/dev/null || true
  sudo docker rm stock_web_prod 2>/dev/null || true
  sudo docker run -d \
    --name stock_web_prod \
    --restart always \
    --network shared_net \
    -p 127.0.0.1:8001:8001 \
    "$BACKUP_IMAGE"
  sleep 3
  code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 15 https://stock.lwsoc.com/ || echo 000)
  echo "Rollback HTTPS status=$code"
  exit 1
}

echo "=== 1) PRE-BUILD (old container still running) ==="
sudo docker ps --format "{{.Names}} {{.Image}} {{.Status}}"
$COMPOSE build
echo "Build OK"
sudo docker images | head -20
df -h /
free -h

echo "=== 2) CUTOVER ==="
# Ensure data dir exists and is writable
mkdir -p "$APP_DIR/data"
chmod 755 "$APP_DIR/data"
test -f /etc/leibot/prod.env
# Stop/remove old container then start new via compose
sudo docker stop stock_web_prod
sudo docker rm stock_web_prod
$COMPOSE up -d --no-build
sleep 5
sudo docker ps --format "{{.Names}} {{.Image}} {{.Status}} {{.Ports}}"

echo "=== 3) SMOKE TESTS ==="
FAIL=0

# HTTPS 200
HTTPS_CODE=$(curl -sS -o /tmp/leibot_home.html -w "%{http_code}" --max-time 20 https://stock.lwsoc.com/ || echo 000)
echo "HTTPS home=$HTTPS_CODE"
if [ "$HTTPS_CODE" != "200" ]; then echo "FAIL https"; FAIL=1; fi

# Home looks like LeiBot (not legacy Stock Price Tracker alone)
if grep -qi "LeiBot\|AI Trading\|Market Dashboard\|Watchlist" /tmp/leibot_home.html; then
  echo "Home content: LeiBot markers OK"
else
  # still accept if not legacy title
  if grep -q "Stock Price Tracker" /tmp/leibot_home.html && ! grep -qi "AI Trading\|LeiBot\|Watchlist" /tmp/leibot_home.html; then
    echo "FAIL home still looks like legacy tracker"
    FAIL=1
  else
    echo "Home content: non-legacy (check manually if needed)"
    head -c 400 /tmp/leibot_home.html | tr '\n' ' '; echo
  fi
fi

# Owner login
OWNER_PW=$(grep '^LEIBOT_OWNER_PASSWORD=' /etc/leibot/prod.env | cut -d= -f2-)
LOGIN_CODE=$(curl -sS -c /tmp/leibot_cj -b /tmp/leibot_cj -o /tmp/leibot_login.html -w "%{http_code}" --max-time 20 \
  -X POST https://stock.lwsoc.com/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "password=${OWNER_PW}" \
  --data-urlencode "next=/ai-trading" || echo 000)
echo "Login POST status=$LOGIN_CODE (expect 302/200)"
# Follow to AI Trading with session
AI_CODE=$(curl -sS -c /tmp/leibot_cj -b /tmp/leibot_cj -o /tmp/leibot_ai.html -w "%{http_code}" --max-time 30 \
  https://stock.lwsoc.com/ai-trading || echo 000)
echo "AI Trading=$AI_CODE"
if [ "$AI_CODE" != "200" ]; then echo "FAIL ai-trading"; FAIL=1; fi
if ! grep -qi "AI Trading\|Open Positions\|AI Discovery\|Paper" /tmp/leibot_ai.html; then
  echo "WARN ai-trading body missing expected markers"
  # treat as fail if redirected to login
  if grep -qi "password\|Sign in\|login" /tmp/leibot_ai.html && ! grep -qi "AI Discovery\|Open Positions" /tmp/leibot_ai.html; then
    echo "FAIL owner login/session"
    FAIL=1
  fi
fi

# data mount + db
sleep 2
sudo docker exec stock_web_prod ls -la /app/data || { echo "FAIL /app/data"; FAIL=1; }
# touch DB by hitting a page that init_db
curl -sS -o /dev/null --max-time 20 -b /tmp/leibot_cj -c /tmp/leibot_cj https://stock.lwsoc.com/ || true
sleep 2
if sudo docker exec stock_web_prod sh -c 'ls /app/data/leibot.db 2>/dev/null || ls /app/data/*.db 2>/dev/null'; then
  echo "DB inside container OK"
else
  echo "WARN no db yet in container — forcing init"
  sudo docker exec stock_web_prod python -c "from db import init_db; init_db(); print('init_db ok')" || true
fi
ls -la "$APP_DIR/data" || true
HOST_DB=$(ls "$APP_DIR/data"/leibot.db 2>/dev/null || ls "$APP_DIR/data"/*.db 2>/dev/null || true)
if [ -z "$HOST_DB" ]; then
  echo "FAIL host data/ has no leibot.db"
  FAIL=1
else
  echo "Host DB: $HOST_DB"
  DB_SIZE_BEFORE=$(stat -c%s "$HOST_DB")
  echo "DB size before restart=$DB_SIZE_BEFORE"
fi

# Mount check
MOUNT_OK=$(sudo docker inspect stock_web_prod --format '{{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}')
echo "Mounts: $MOUNT_OK"
echo "$MOUNT_OK" | grep -q '/app/data' || { echo "FAIL data mount missing"; FAIL=1; }

# Restart persistence
echo "=== 4) RESTART PERSISTENCE TEST ==="
sudo docker restart stock_web_prod
sleep 6
HTTPS2=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 20 https://stock.lwsoc.com/ || echo 000)
echo "After restart HTTPS=$HTTPS2"
if [ "$HTTPS2" != "200" ]; then echo "FAIL after restart"; FAIL=1; fi
HOST_DB2=$(ls "$APP_DIR/data"/leibot.db 2>/dev/null || true)
if [ -n "${HOST_DB:-}" ] && [ -f "${HOST_DB}" ]; then
  DB_SIZE_AFTER=$(stat -c%s "$HOST_DB")
  echo "DB size after restart=$DB_SIZE_AFTER"
  if [ "$DB_SIZE_AFTER" -lt 1 ]; then echo "FAIL db empty after restart"; FAIL=1; fi
else
  echo "FAIL db missing after restart"
  FAIL=1
fi
sudo docker exec stock_web_prod ls -la /app/data/leibot.db || { echo "FAIL db gone in container"; FAIL=1; }

echo "=== 5) RESULT ==="
if [ "$FAIL" -ne 0 ]; then
  echo "SMOKE FAILED — rolling back"
  rollback
fi

echo "DEPLOY SUCCESS"
sudo docker ps
df -h /
free -h
echo "Log: $LOG"
echo "Retrieve owner password: sudo cat /etc/leibot/prod.env"
