#!/usr/bin/env bash
# Deploy CODE ONLY lên PROD VPS. Không bao giờ đụng data/ (bot.db, cache WS) hay .env.
# Quy trình đầy đủ + lý do: docs/DEPLOY.md
#
# Usage:
#   ./scripts/deploy_code_only.sh check                      # chỉ so sánh, không đổi gì
#   CONFIRM=yes ./scripts/deploy_code_only.sh deploy         # upload mọi file src/ khác VPS
#   CONFIRM=yes ./scripts/deploy_code_only.sh deploy src/web/app.py docs/BOT_LOGIC.md
#
# Env: VPS_HOST (mặc định 152.42.255.130), VPS_USER (mặc định root)
#      BEFORE_START: lệnh shell chạy trên VPS (cwd app) giữa stop và start, ví dụ sửa setting DB
#      — vẫn chỉ một lần boot, DB đã được backup ở bước 5.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VPS_HOST="${VPS_HOST:-152.42.255.130}"
VPS_USER="${VPS_USER:-root}"
REMOTE="${VPS_USER}@${VPS_HOST}"
APP="/home/bot/app"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=20 "$REMOTE")

MODE="${1:-check}"
shift || true
if [[ "$MODE" != "check" && "$MODE" != "deploy" ]]; then
  echo "Usage: $0 check | deploy [paths...]" >&2
  exit 2
fi

for f in "$@"; do
  case "$f" in
    data/*|./data/*|.env|./.env|.env.bak*|*.db)
      echo "FAIL: không được upload '$f' (dữ liệu/secret PROD). Xem docs/DEPLOY.md" >&2
      exit 1 ;;
  esac
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

list_src() {
  find src -type f \( -name '*.py' -o -name '*.html' \) ! -name '._*' ! -path '*__pycache__*' | sort
}

echo "=== 1. Local bot phải tắt (không chạy song song với PROD) ==="
if pgrep -f "python -m src.main" >/dev/null 2>&1; then
  echo "FAIL: bot local đang chạy. Tắt trước khi deploy." >&2
  exit 1
fi
echo "OK"

echo "=== 2. So checksum src/ local vs VPS ==="
list_src | xargs shasum -a 1 | awk '{print $1"  "$2}' > "$TMP/local.sum"
"${SSH[@]}" "cd $APP && find src -type f \( -name '*.py' -o -name '*.html' \) ! -name '._*' ! -path '*__pycache__*' | sort | xargs sha1sum" \
  | awk '{print $1"  "$2}' > "$TMP/remote.sum"

: > "$TMP/changed"
while read -r sum path; do
  remote_sum="$(awk -v p="$path" '$2==p {print $1}' "$TMP/remote.sum")"
  if [[ "$remote_sum" != "$sum" ]]; then
    echo "$path" >> "$TMP/changed"
  fi
done < "$TMP/local.sum"
comm -13 <(awk '{print $2}' "$TMP/local.sum" | sort) <(awk '{print $2}' "$TMP/remote.sum" | sort) \
  | sed 's/^/WARN: chỉ có trên VPS (không xóa): /'

if [[ $# -gt 0 ]]; then
  printf '%s\n' "$@" > "$TMP/files"
else
  cp "$TMP/changed" "$TMP/files"
fi

if [[ ! -s "$TMP/files" ]]; then
  echo "Không có file nào khác VPS. Không cần deploy."
  exit 0
fi

while read -r f; do
  case "$f" in
    data/*|./data/*|.env|./.env|.env.bak*|*.db)
      echo "FAIL: không được upload '$f' (dữ liệu/secret PROD). Xem docs/DEPLOY.md" >&2
      exit 1 ;;
  esac
  [[ -f "$f" ]] || { echo "FAIL: không thấy file local '$f'" >&2; exit 1; }
done < "$TMP/files"

echo "File sẽ upload:"
sed 's/^/  /' "$TMP/files"

echo "=== 3. Dòng CHỈ CÓ trên VPS (sẽ bị mất nếu ghi đè) ==="
FILES=()
while read -r f; do FILES+=("$f"); done < "$TMP/files"
mkdir -p "$TMP/vps"
"${SSH[@]}" "cd $APP && tar czf - --ignore-failed-read ${FILES[*]} 2>/dev/null" | tar xzf - -C "$TMP/vps" 2>/dev/null || true
for f in "${FILES[@]}"; do
  if [[ -f "$TMP/vps/$f" ]]; then
    lines="$(diff "$TMP/vps/$f" "$f" | grep '^<' || true)"
    if [[ -n "$lines" ]]; then
      echo "--- $f"
      echo "$lines" | head -40
    fi
  else
    echo "--- $f (file mới, chưa có trên VPS)"
  fi
done
echo "Đọc kỹ các dòng trên: chúng phải là dòng bạn CỐ Ý thay thế. Nếu có code chỉ tồn tại trên VPS, kéo về local trước."

if [[ "$MODE" == "check" ]]; then
  echo "check xong — chưa thay đổi gì."
  exit 0
fi

if [[ "${CONFIRM:-}" != "yes" ]]; then
  echo "FAIL: cần CONFIRM=yes sau khi đã xem mục 3." >&2
  exit 1
fi

echo "=== 4. VPS không bị REST cooldown ==="
"${SSH[@]}" "cd $APP && sudo -u bot env PYTHONPATH=$APP .venv/bin/python -c '
from src.exchange import binance as b
import sys
r = b.rate_limit_remaining_sec()
print(\"rate_limit_remaining_sec=\", round(r))
sys.exit(1 if r > 0 else 0)
'" || { echo "FAIL: VPS đang REST cooldown — đợi hết rồi deploy." >&2; exit 1; }

TS="$(date +%Y%m%d-%H%M%S)"
echo "=== 5. Backup trên VPS: backups/pre-$TS-* ==="
"${SSH[@]}" "set -e; cd $APP; mkdir -p backups; tar czf backups/pre-$TS-src.tgz src docs .env.example; cp data/bot.db backups/pre-$TS-bot.db; chown -R bot:bot backups; ls -lh backups/pre-$TS-*"

echo "=== 6. Upload ==="
COPYFILE_DISABLE=1 tar czf - "${FILES[@]}" | "${SSH[@]}" "set -e; cd $APP; tar xzf - --no-same-owner 2>/dev/null; for f in ${FILES[*]}; do mkdir -p \"\$(dirname \"\$f\")\"; chown bot:bot \"\$(dirname \"\$f\")\" \"\$f\"; done"

echo "=== 7. Compile + import trên VPS (fail → tự restore, KHÔNG restart) ==="
PY_FILES=()
for f in "${FILES[@]}"; do [[ "$f" == *.py ]] && PY_FILES+=("$f"); done
if ! "${SSH[@]}" "cd $APP && { [ ${#PY_FILES[@]} -eq 0 ] || sudo -u bot .venv/bin/python -m py_compile ${PY_FILES[*]:-}; } && sudo -u bot env PYTHONPATH=$APP .venv/bin/python -c 'import src.donchian.cycle, src.web.app; print(\"IMPORT_OK\")'"; then
  echo "FAIL: compile/import lỗi — restore src từ backup." >&2
  "${SSH[@]}" "cd $APP && tar xzf backups/pre-$TS-src.tgz && chown -R bot:bot src docs .env.example"
  exit 1
fi

echo "=== 8. Restart MỘT LẦN + log boot ==="
if [[ -n "${BEFORE_START:-}" ]]; then
  echo "BEFORE_START (chạy khi bot đã stop, DB đã backup): $BEFORE_START"
  "${SSH[@]}" "systemctl stop bot-donchian && cd $APP && { $BEFORE_START; } ; rc=\$?; systemctl start bot-donchian; exit \$rc" \
    || echo "WARN: BEFORE_START lỗi — bot vẫn được start lại; xem log trên."
  "${SSH[@]}" "sleep 45 && systemctl is-active bot-donchian && journalctl -u bot-donchian --since '-50 sec' --no-pager | cut -c60-240 | grep -vE 'kline cache ready' | tail -40"
else
"${SSH[@]}" "systemctl restart bot-donchian && sleep 45 && systemctl is-active bot-donchian && journalctl -u bot-donchian --since '-50 sec' --no-pager | cut -c60-240 | grep -vE 'kline cache ready' | tail -40"
fi

echo ""
echo "Kiểm tra log trên: phải có 'candles_symbols=18+', 'listenKey reused', 'First cycle — skip REST position reconcile'."
echo "KHÔNG được có: 'rate limited', REST seed nhiều coin, 'flat on exchange — closing in DB' hàng loạt."
echo "Rollback: xem docs/DEPLOY.md mục Rollback (backup = backups/pre-$TS-*)."
