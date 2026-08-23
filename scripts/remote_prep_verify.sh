#!/bin/bash
set -euo pipefail

echo "=== docker inspect ==="
sudo docker inspect stock_web_prod > /tmp/sw.json
python3 <<'PY'
import json
d = json.load(open("/tmp/sw.json"))[0]
print("Image", d["Config"]["Image"])
print("Restart", d["HostConfig"]["RestartPolicy"])
print("Binds", d["HostConfig"].get("Binds"))
print("Mounts", d.get("Mounts"))
print("Nets", list(d["NetworkSettings"]["Networks"]))
print("Status", d["State"]["Status"])
print("Ports", d["NetworkSettings"].get("Ports"))
PY

echo "=== leibot checkout prep ==="
if [ ! -d /var/www/leibot/.git ]; then
  sudo mkdir -p /var/www/leibot
  sudo chown ubuntu:ubuntu /var/www/leibot
  git clone --depth 1 https://github.com/leiwangamy/Online-Stock-Tracker.git /var/www/leibot
fi
cd /var/www/leibot
git fetch --depth 1 origin main
git reset --hard origin/main
git log -1 --oneline
git rev-parse HEAD
mkdir -p data
chmod 755 data

if [ ! -f docker-compose.prod.yml ]; then
  cat > docker-compose.prod.yml <<'YML'
version: "3.9"

services:
  stock_web_prod:
    container_name: stock_web_prod
    build: .
    restart: always
    env_file:
      - /etc/leibot/prod.env
    environment:
      - TZ=America/Los_Angeles
    ports:
      - "127.0.0.1:8001:8001"
    volumes:
      - ./data:/app/data
    networks:
      - shared_net

networks:
  shared_net:
    external: true
YML
  echo "created docker-compose.prod.yml"
fi

echo "=== files ==="
ls -la docker-compose.yml docker-compose.prod.yml Dockerfile requirements.txt data
echo "=== requirements ==="
cat requirements.txt
echo "=== env keys ==="
cut -d= -f1 /etc/leibot/prod.env | grep -v '^#' | grep -v '^$' || true
echo "=== running container unchanged ==="
sudo docker ps
echo "=== rollback images ==="
sudo docker images
echo "=== disk ==="
df -h /
free -h
swapon --show
