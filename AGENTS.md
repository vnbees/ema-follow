# Hướng dẫn cho AI agent

Bot futures Binance chạy **tiền thật** trên VPS PROD `root@152.42.255.130` (`/home/bot/app`, service `bot-donchian`).
Logic: [`docs/BOT_LOGIC.md`](docs/BOT_LOGIC.md).

## Deploy / đụng VPS — BẮT BUỘC đọc [`docs/DEPLOY.md`](docs/DEPLOY.md) trước

- Cập nhật code PROD: **chỉ** `scripts/deploy_code_only.sh` (`check` trước, rồi `CONFIRM=yes ... deploy`).
- **KHÔNG** chạy `scripts/deploy_to_vps.sh` hoặc `scripts/reset_donchian_fresh_start.py` — chúng reset lot đang mở và ghi đè `bot.db` + `.env` PROD.
- **KHÔNG** upload `data/` / `*.db` / `.env` lên VPS; không copy DB local đè PROD.
- Luôn backup `src` + `data/bot.db` trên VPS (`backups/pre-<TS>-*`) trước khi ghi file.
- Trước khi ghi đè file trên VPS: kéo bản VPS về, diff, xác nhận dòng chỉ-có-trên-VPS là dòng cố ý thay.
- Restart `bot-donchian` **một lần**; đọc log boot (candles ≥ 18, listenKey reused, không rate limit).
- Bot local phải tắt khi VPS chạy (cùng API key → rate limit 418).
- Chỉ deploy khi user yêu cầu trong lượt chat hiện tại.

## Ghi chú môi trường

- VPS không có `sqlite3`: dùng `sudo -u bot env PYTHONPATH=/home/bot/app .venv/bin/python`.
- zsh: danh sách file phải là array, không phải chuỗi.
- Test: `.venv/bin/python -m pytest tests -q` (vài test `test_donchian_symbol_filter` và `TestCandleRestCooldown` fail sẵn, không liên quan).
