#!/usr/bin/env bash
# Deploy bot lên VPS DigitalOcean (SG) từ máy local.
#
# Usage:
#   export VPS_HOST=123.45.67.89          # IP Droplet
#   export VPS_USER=root                  # default root
#   ./scripts/deploy_to_vps.sh
#
# Yêu cầu: SSH key đã add vào Droplet, pre-flight pass, .env local có API keys.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VPS_HOST="${VPS_HOST:?Set VPS_HOST= droplet IP}"
VPS_USER="${VPS_USER:-root}"
REMOTE="${VPS_USER}@${VPS_HOST}"
APP="/home/bot/app"

echo "=== Pre-flight ==="
.venv/bin/python scripts/sync_prod_deploy_data.py
.venv/bin/python scripts/preflight_prod_deploy.py --strict-candles

echo "=== Stop local bot ==="
pkill -f 'python -m src.main' 2>/dev/null || true
sleep 2

echo "=== Upload code + data (tar) ==="
ssh -o StrictHostKeyChecking=accept-new "$REMOTE" "mkdir -p $APP"
tar czf - \
  --exclude='./.venv' \
  --exclude='./.git' \
  --exclude='./logs' \
  --exclude='./__pycache__' \
  --exclude='./.cursor' \
  --exclude='./data/binance_rate_limit_until_ms' \
  -C "$ROOT" . | ssh "$REMOTE" "tar xzf - -C $APP"

echo "=== Remote setup + systemd ==="
ssh "$REMOTE" "bash $APP/deploy/vps-remote-setup.sh"

echo "=== Start bot ==="
ssh "$REMOTE" "systemctl restart bot-donchian && sleep 3 && systemctl status bot-donchian --no-pager"

echo ""
echo "Done. Dashboard: http://${VPS_HOST}:8080"
echo "Logs: ssh $REMOTE 'journalctl -u bot-donchian -f'"
