# Deploy PROD — quy trình chuẩn (đọc hết trước khi đụng VPS)

> **Dành cho người và AI.** PROD đang chạy tiền thật, có lot đang mở trong `data/bot.db`.
> Mất / ghi đè DB = bot quên lot đang giữ, TP limit mồ côi, dashboard sai mốc. Không có cách khôi phục nếu không có backup.

| | |
|---|---|
| VPS | `root@152.42.255.130` (DigitalOcean SG) |
| App dir | `/home/bot/app` (owner `bot:bot`) |
| Python | `/home/bot/app/.venv/bin/python` — chạy dưới user `bot`, cần `PYTHONPATH=/home/bot/app` |
| Service | `systemd` unit `bot-donchian` (file mẫu `deploy/bot-donchian.service`) |
| DB | `/home/bot/app/data/bot.db` (SQLite) — **nguồn sự thật PROD**, không phải bản local |
| Secret | `/home/bot/app/.env` — **khác** `.env` local, không ghi đè |
| Dashboard | port 8080 trên VPS |

---

## 1. Chọn đúng cách deploy

| Tình huống | Dùng | KHÔNG dùng |
|---|---|---|
| Sửa code / template / docs, bot PROD đang chạy | `scripts/deploy_code_only.sh` (mục 2) | `deploy_to_vps.sh` |
| Cài VPS **mới tinh**, chưa có lot, user đồng ý rõ ràng | `I_UNDERSTAND_THIS_WIPES_PROD=yes scripts/deploy_to_vps.sh` | — |
| Thêm package Python | mục 2 + `sudo -u bot .venv/bin/pip install -r requirements.txt` trên VPS trước restart | — |

`scripts/deploy_to_vps.sh` **phá dữ liệu**: chạy `reset_donchian_fresh_start.py --yes` (đóng mọi lot thành `RESET_FRESH`),
upload `data/` + `.env` local đè lên PROD, chạy `apt upgrade`/`ufw`. Script đã bị khóa bằng biến `I_UNDERSTAND_THIS_WIPES_PROD`.
**AI không được tự đặt biến này.**

---

## 2. Deploy code-only (cách chuẩn)

```bash
./scripts/deploy_code_only.sh check                 # bước A: chỉ đọc, không đổi gì
CONFIRM=yes ./scripts/deploy_code_only.sh deploy    # bước B: upload mọi file src/ khác VPS
# hoặc chỉ định file (kể cả ngoài src/):
CONFIRM=yes ./scripts/deploy_code_only.sh deploy src/web/app.py docs/BOT_LOGIC.md .env.example
```

Script làm đúng các bước dưới đây. Nếu phải làm tay, **làm đủ, đúng thứ tự**.

### A. Kiểm tra trước (không thay đổi gì)

1. **Bot local phải tắt**: `pgrep -fl "python -m src.main"` rỗng. Local + VPS cùng API key → gấp đôi REST weight → 418.
2. **So checksum** `src/**/*.py|html` local vs VPS:
   ```bash
   ssh root@152.42.255.130 "cd /home/bot/app && find src -type f \( -name '*.py' -o -name '*.html' \) ! -path '*__pycache__*' | sort | xargs sha1sum"
   ```
   so với `shasum -a 1` local → danh sách file khác.
3. **Kéo bản VPS về so diff** (quan trọng nhất). Có thể có hotfix chỉ nằm trên VPS:
   ```bash
   mkdir -p /tmp/vpssrc
   ssh root@152.42.255.130 "cd /home/bot/app && tar czf - FILE1 FILE2" | tar xzf - -C /tmp/vpssrc
   diff /tmp/vpssrc/FILE1 FILE1 | grep '^<'     # dòng CHỈ có trên VPS
   ```
   Mọi dòng `<` phải là dòng bạn **cố ý thay**. Nếu là code lạ → merge về local trước, không ghi đè.
4. **Sức khỏe VPS** (VPS không có `sqlite3` — dùng Python của venv):
   ```bash
   ssh root@152.42.255.130 'cd /home/bot/app && sudo -u bot env PYTHONPATH=/home/bot/app .venv/bin/python -c "
   from src.exchange import binance as b; print(\"rate_limited\", b.is_rate_limited(), round(b.rate_limit_remaining_sec()))
   import sqlite3; c=sqlite3.connect(\"data/bot.db\")
   print(c.execute(\"select symbol, side, count(*) from donchian_lots where status=\x27open\x27 group by 1,2\").fetchall())"'
   ```
   `rate_limited True` → **dừng**, đợi hết cooldown. Ghi lại số lot mở để so sau restart.

### B. Backup (luôn luôn, trước khi upload)

```bash
ssh root@152.42.255.130 'cd /home/bot/app && TS=$(date +%Y%m%d-%H%M%S) && mkdir -p backups \
  && tar czf backups/pre-$TS-src.tgz src docs .env.example \
  && cp data/bot.db backups/pre-$TS-bot.db && chown -R bot:bot backups && ls -lh backups/pre-$TS-*'
```

### C. Upload CHỈ file code đã đổi

```bash
FILES=(src/a.py src/b.py)          # zsh: phải dùng array, chuỗi có dấu cách không tự tách từ
COPYFILE_DISABLE=1 tar czf - "${FILES[@]}" | ssh root@152.42.255.130 \
  "cd /home/bot/app && tar xzf - --no-same-owner && chown bot:bot ${FILES[*]}"
```

- `COPYFILE_DISABLE=1` để macOS không nhét file `._*`. Cảnh báo `LIBARCHIVE.xattr` khi giải nén là vô hại.
- Upload xong, so lại `sha1sum` các file trên VPS với local.

### D. Compile + import trên VPS (trước restart)

```bash
ssh root@152.42.255.130 "cd /home/bot/app && sudo -u bot .venv/bin/python -m py_compile ${FILES[*]} \
  && sudo -u bot env PYTHONPATH=/home/bot/app .venv/bin/python -c 'import src.donchian.cycle, src.web.app; print(\"IMPORT_OK\")'"
```

Lỗi → restore src từ backup (mục 4), **không restart**. Service đang chạy vẫn dùng code cũ trong RAM.

### E. Restart MỘT lần

```bash
ssh root@152.42.255.130 "systemctl restart bot-donchian && sleep 45 && journalctl -u bot-donchian --since '-50 sec' --no-pager"
```

Log boot **phải có**:
- `disk restore` / `candles_symbols=` **≥ 18** (dùng cache WS trên disk, không REST seed)
- `listenKey` reused / `account=yes listenKey=yes`
- `kline cache ready` cho các coin
- dòng mode (ví dụ `dual-limit mode ... coins=x/10`)

**Không được có**: `rate limited`, `418`, `429`, REST seed nhiều coin, hàng loạt `flat on exchange — closing in DB`.

### F. Verify sau

- `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/` trên VPS → `200` (dashboard có auth thì 401/302 cũng là sống).
- Số lot `status='open'` bằng lúc trước deploy (mục A.4).
- Theo dõi **cycle 15m kế tiếp** (`journalctl -u bot-donchian -f`): không lỗi, TP limit sync bình thường.

---

## 3. Danh sách CẤM (cả người lẫn AI)

1. **Không** chạy `scripts/deploy_to_vps.sh` hay `scripts/reset_donchian_fresh_start.py` khi PROD có lot mở.
2. **Không** upload `data/` (bot.db, `binance_ws_*.json`, `binance_rate_limit_until_ms`) từ local lên VPS. Không copy DB local đè DB PROD.
3. **Không** ghi đè `/home/bot/app/.env` bằng `.env` local. Đổi env PROD → sửa tay trên VPS từng dòng, backup trước (`cp .env .env.bak-$TS`).
4. **Không** `rsync --delete` / `scp -r .` / tar cả repo.
5. **Không** chạy bot local song song khi VPS đang chạy (cùng API key).
6. **Không** restart nhiều lần trong ~30 phút. Mỗi boot tốn listenKey + account + có thể kline warmup.
7. **Không** đặt `BINANCE_CLEAR_RATE_LIMIT=true` tùy tiện — xoá cooldown khi Binance còn ban IP → 418 dài hơn.
8. **Không** sửa/xóa bảng trong `bot.db` PROD bằng tay khi bot đang chạy. Cần sửa → `systemctl stop`, backup, sửa, start.
9. **Không** tự đặt `I_UNDERSTAND_THIS_WIPES_PROD=yes` hay `CONFIRM=yes` nếu user chưa đồng ý deploy trong lượt chat đó.

---

## 4. Rollback

```bash
ssh root@152.42.255.130
cd /home/bot/app && ls -lt backups | head          # chọn pre-<TS>-*
tar xzf backups/pre-<TS>-src.tgz && chown -R bot:bot src docs .env.example
systemctl restart bot-donchian
```

Chỉ restore DB khi DB thực sự hỏng (lot sẽ quay về trạng thái lúc backup — lệnh khớp sau đó sẽ lệch, phải reconcile):

```bash
systemctl stop bot-donchian
cp data/bot.db backups/broken-$(date +%Y%m%d-%H%M%S).db
cp backups/pre-<TS>-bot.db data/bot.db && chown bot:bot data/bot.db
systemctl start bot-donchian
```

---

## 5. Ghi chú

- Migration schema (bảng/cột mới) chạy tự động khi boot qua `ensure_schema` — không cần chạy SQL tay.
- Setting dashboard mốc (`dual_book_at`, `dual_book_anchor`) nằm trong DB PROD; chỉ bot set một lần. Ghi đè DB = mất mốc.
- Cần sửa DB cùng lúc deploy: `BEFORE_START='<lệnh>' CONFIRM=yes ./scripts/deploy_code_only.sh deploy` — lệnh chạy giữa stop và start (sau backup), vẫn một lần boot. Ví dụ 2026-10-10: `dual_book_anchor` 994.17 → 971.73 (mốc cũ lấy từ cache WS cũ lúc boot).
- Rate limit chi tiết: [DEPLOY_RATE_LIMIT_CHECKLIST.md](./DEPLOY_RATE_LIMIT_CHECKLIST.md). Logic bot: [BOT_LOGIC.md](./BOT_LOGIC.md).
- Các lần deploy đã làm: backup `backups/pre-dual-20261010-095229-*` (dual-limit, 2026-10-10).
