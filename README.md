# Bitget EMA Follow Trend Bot

Bot trading futures Bitget theo trend EMA 34/89/144/200 trên nến 5m.

## Phase 2

- **Long:** uptrend + nến đỏ → limit buy @ giá đóng (huỷ limit cũ trước)
- **Short:** downtrend + nến xanh → limit sell @ giá đóng
- **Sideway:** không làm gì
- Skip entry nếu giá đóng đã lời so với giá TB
- Đóng position ngược chiều khi có tín hiệu flip
- Margin **cross**, leverage **5x**
- SQLite lưu lệnh, giá TB, trade cycles + P&L
- Dashboard web: `http://localhost:8080`
- **Multi-coin watchlist:** thêm/xóa coin trên dashboard, xem trạng thái từng coin

## Cài đặt

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Điền `.env` với API key Bitget (permission **Read + Trade** futures).

## Chạy

```bash
source .venv/bin/activate
python -m src.main
```

Log ghi ra console và `logs/bot.log`. Database tại `data/bot.db`.

## Config

| Biến | Mặc định | Mô tả |
|------|----------|-------|
| `ORDER_SIZE_USDT` | 5 | Notional USDT mỗi lệnh limit |
| `LEVERAGE` | 5 | Đòn bẩy |
| `MARGIN_MODE` | crossed | Ký quỹ chéo |
| `WEB_PORT` | 8080 | Port dashboard |

Symbol có thể thêm/xóa trên dashboard (Watchlist) hoặc qua API `POST/DELETE /api/symbols`.
