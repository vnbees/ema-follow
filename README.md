# RSI reversion live bot (LINK)

Bot futures USDT-M: **RSI-anchor mean reversion** trên nến **5m** (`LINKUSDT`, `SUIUSDT`, `WLDUSDT`, `HYPEUSDT`). Chi tiết logic: [`docs/BOT_LOGIC.md`](docs/BOT_LOGIC.md).

- RSI14 vừa vào vùng 70 / 30 / 48–52 → lưu anchor = close
- Giá rời 0.5% → market LONG hoặc SHORT (song song, stack lot)
- TP về anchor ± 0.25%; BE sau 7 ngày; timeout 30 ngày
- Size: 0.5% equity × 10x; thiếu số dư thì skip, không giảm size
- Binance: ưu tiên WebSocket (kline, mark, user-data)

## Cài đặt

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Điền API key Binance Futures (`EXCHANGE=binance`) hoặc Bitget.

## Chạy

```bash
source .venv/bin/activate
python -m src.main
```

Log: `logs/rsi_rev.log`. Database: `data/bot.db`. Dashboard: `http://localhost:8080`.


--- PROMPT deploy

Deploy PROD bot Donchian lên VPS. BẮT BUỘC:
1. Đọc docs/DEPLOY_RATE_LIMIT_CHECKLIST.md — không bỏ bước
2. Bot local đang chạy → scripts/sync_prod_deploy_data.py exit 0
3. scripts/preflight_prod_deploy.py --strict-candles exit 0 — fail thì báo user, KHÔNG deploy
4. Whitelist IP VPS trên Binance API key
5. VPS_HOST=IP ./scripts/deploy_to_vps.sh — MỘT LẦN
6. Verify log: candles_symbols≥18, skip REST warmup; REST seed >5 coin hoặc 418 → stop VPS, bật local
7. Local OFF cho đến khi PROD verify xong

Báo cáo từng bước pass/fail trước khi deploy.

\\ --- PROMPT deploy