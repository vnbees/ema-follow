# Coins Signal

Website public hiển thị tín hiệu Donchian (open/closed). Chỉ hiện **% equity**, không lộ khối lượng / margin USDT / equity tài khoản.

## Kiến trúc

| Layer | Stack |
|-------|--------|
| Backend | FastAPI + SQLAlchemy async + Postgres |
| Frontend | Next.js 14 + Tailwind |
| Deploy | Railway (2 services + Postgres) |
| Bot sync | VPS bot `POST` upsert/close với `X-API-Key` |

```
Bot VPS  --POST /api/v1/signals/*-->  Railway backend  -->  Postgres
Browser  <--GET /api/v1/signals-----  Railway frontend
Browser  <--mark price--------------  Binance fapi (public)
```

## API

| Method | Path | Auth |
|--------|------|------|
| GET | `/api/v1/health` | — |
| GET | `/api/v1/signals?status=open\|closed\|all` | — |
| POST | `/api/v1/signals/upsert` | `X-API-Key` |
| POST | `/api/v1/signals/{external_id}/close` | `X-API-Key` |

**Upsert body (bot → API):**

```json
{
  "external_id": "42",
  "symbol": "LINKUSDT",
  "side": "long",
  "trend": "up",
  "entry": 12.34,
  "tp": 12.80,
  "equity_pct": 1.5,
  "opened_at": "2026-08-26T01:00:00+00:00"
}
```

**Close body:**

```json
{
  "close_px": 12.75,
  "pnl_pct": 8.2,
  "close_reason": "tp",
  "closed_at": "2026-08-26T02:00:00+00:00"
}
```

`equity_pct` = `DONCHIAN_MARGIN_PCT × size_mult × 100` (vd 0.01 × 1.5 → **1.5%**).  
`pnl_pct` = ROI trên margin (`pnl_usdt / margin_usdt × 100`).

## Local

### Backend

```bash
cd coins-signal/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Cần Postgres chạy; sửa DATABASE_URL
./start.sh
```

### Frontend

```bash
cd coins-signal/frontend
cp .env.example .env.local
npm install
npm run dev
```

Mở http://localhost:3000.

## Railway

1. Tạo project Railway, root directory = `coins-signal` (hoặc deploy từ folder này).
2. Thêm plugin **Postgres**.
3. Deploy 2 services theo [`railway.toml`](railway.toml): `backend`, `frontend`.
4. Shared / service variables:
   - `SIGNAL_API_KEY` — secret mạnh (bot dùng cùng giá trị)
   - `DATABASE_URL` — từ Postgres (`${{Postgres.DATABASE_URL}}`)
   - `CORS_ORIGINS` — domain frontend
   - `NEXT_PUBLIC_API_URL` — public URL backend
5. Generate domain cho backend + frontend.

**Build note:** `NEXT_PUBLIC_API_URL` phải có lúc **build** frontend Docker (ARG trong Dockerfile). Trên Railway, set biến đó trước khi build hoặc dùng Config as Code như `railway.toml`.

## Bot VPS (repo gốc)

Thêm vào `.env` bot:

```bash
SIGNAL_API_URL=https://<backend-domain>
SIGNAL_API_KEY=<cùng key Railway>
```

Bot gọi fail-soft sau `open_lot` / `_finalize_close` qua `src/signal_publish.py`. Để trống `SIGNAL_API_URL` = tắt publish.
