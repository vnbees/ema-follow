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


## Deploy PROD

**Đọc [`docs/DEPLOY.md`](docs/DEPLOY.md) trước.** PROD đang có lot mở — cập nhật code chỉ dùng:

```bash
./scripts/deploy_code_only.sh check
CONFIRM=yes ./scripts/deploy_code_only.sh deploy
```

`scripts/deploy_to_vps.sh` chỉ dành cho VPS mới tinh: nó reset lot và ghi đè `bot.db` + `.env` PROD.