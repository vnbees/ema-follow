#!/usr/bin/env bash
# !!! NGUY HIỂM — CHỈ dùng cho FRESH INSTALL VPS mới. KHÔNG dùng để cập nhật code PROD. !!!
# Script này:
#   - chạy reset_donchian_fresh_start.py --yes (local + VPS) → đóng TẤT CẢ lot đang mở trong DB (RESET_FRESH)
#   - upload cả data/ (bot.db local) + .env local → GHI ĐÈ DB và secret của PROD
#   - chạy vps-remote-setup.sh (apt upgrade, ufw, systemd)
# Cập nhật code PROD → dùng scripts/deploy_code_only.sh (xem docs/DEPLOY.md).
#
# Deploy bot lên VPS DigitalOcean (SG) từ máy local.
#
# Usage:
#   export I_UNDERSTAND_THIS_WIPES_PROD=yes   # bắt buộc, xem cảnh báo trên
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

if [[ "${I_UNDERSTAND_THIS_WIPES_PROD:-}" != "yes" ]]; then
  echo "REFUSED: script này reset lot + ghi đè bot.db/.env PROD." >&2
  echo "Cập nhật code → ./scripts/deploy_code_only.sh (docs/DEPLOY.md)." >&2
  echo "Chỉ fresh install VPS mới, có user đồng ý rõ ràng: I_UNDERSTAND_THIS_WIPES_PROD=yes $0" >&2
  exit 1
fi

echo "=== Reset Donchian state (fresh start) ==="
.venv/bin/python scripts/reset_donchian_fresh_start.py --yes || true

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

echo "=== Reset VPS Donchian state ==="
ssh "$REMOTE" "cd $APP && sudo -u bot .venv/bin/python scripts/reset_donchian_fresh_start.py --yes"

echo "=== Start bot ==="
ssh "$REMOTE" "systemctl restart bot-donchian && sleep 3 && systemctl status bot-donchian --no-pager"

echo ""
echo "Done. Dashboard: http://${VPS_HOST}:8080"
echo "Logs: ssh $REMOTE 'journalctl -u bot-donchian -f'"
