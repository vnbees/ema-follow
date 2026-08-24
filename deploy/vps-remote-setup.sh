#!/bin/bash
# Chạy trên VPS (Ubuntu 24.04) sau khi rsync code + data.
set -euo pipefail

APP=/home/bot/app

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip git rsync ufw

if ! id bot &>/dev/null; then
  adduser bot --disabled-password --gecos ""
fi

mkdir -p "$APP"
chown -R bot:bot "$APP"

sudo -u bot bash -c "
  cd '$APP'
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
"

if [[ ! -f "$APP/.env" ]]; then
  cp "$APP/.env.example" "$APP/.env"
  chown bot:bot "$APP/.env"
  echo 'WARN: Tạo .env từ .env.example — cần điền API key trên VPS hoặc rsync .env từ local'
fi

cp "$APP/deploy/bot-donchian.service" /etc/systemd/system/bot-donchian.service
systemctl daemon-reload
systemctl enable bot-donchian

ufw allow OpenSSH
ufw allow 8080/tcp comment 'bot dashboard' || true
echo y | ufw enable || true

echo "Remote setup done. Start: systemctl start bot-donchian"
