# Checklist deploy PROD (Railway + Binance)

Dùng checklist này **mỗi lần** chuyển bot từ local → PROD hoặc redeploy production.  
Mục tiêu: **không bị Binance 418/429 (ban IP)**, không chạy 2 bot cùng lúc, region **Singapore**.

---

## Trước khi deploy

- [ ] **Tắt bot local** trước khi bật PROD (chỉ 1 instance trade/live)
  ```bash
  pkill -f 'python -m src.main'
  lsof -ti:8080 | xargs kill -9 2>/dev/null
  ```
- [ ] **Commit + push** code lên `main` (Railway deploy từ repo hoặc `railway up`)
- [ ] **Kiểm tra DB local** — mỗi position mở phải có `sl_order_id` + `tp_order_id` trong `data/bot.db`
- [ ] **Sync file cần upload lên volume** `/data`:
  - `bot.db`
  - `binance_exchange_info.json`
  - `binance_listen_key`
  - `binance_ws_account.json`
  - `binance_ws_candles.json`
- [ ] **Không upload** `binance_rate_limit_until_ms` từ local lên PROD (trừ khi cố ý giữ cooldown)
- [ ] **Tests pass** local: `pytest -q`

---

## Railway — region & service (Singapore)

- [ ] Region: **Southeast Asia only**
  ```bash
  railway service scale us-west=0 southeast-asia=1
  ```
- [ ] **Không** set `startCommand=sleep infinity` (chỉ dùng tạm khi migrate volume, nhớ revert)
- [ ] Start command PROD: `/bin/sh start.sh` hoặc để Railpack đọc `Procfile` (`web: sh start.sh`)
- [ ] Volume gắn service: `bot-ema-follow-trend-volume-o5NS` (hoặc volume **cùng region SG**), mount `/data`
- [ ] **Không** scale cả 2 region về 0 rồi redeploy nếu muốn giữ config SG — dùng scale 0 có thể khiến UI hiện region lạ; offline PROD nên dùng `southeast-asia=0` + `railway down` rồi khi bật lại pin `southeast-asia=1`

---

## Railway — biến môi trường bắt buộc

| Biến | Giá trị PROD |
|------|----------------|
| `TRADING_ENABLED` | `true` |
| `EXCHANGE` | `binance` |
| `BINANCE_WS_ENABLED` | `true` |
| `DATABASE_PATH` | `/data/bot.db` |
| `BINANCE_WS_REST_TICKER_SEED` | `false` (mặc định — **không** seed ticker/24hr bằng REST) |
| `BINANCE_CLEAR_RATE_LIMIT` | `false` (chỉ bật **một lần** khi đổi egress IP và chắc chắn Binance đã hết ban) |
| `REST_BOOT_QUIET_SEC` | `600` (mặc định — **không cần set** trên Railway trừ khi muốn đổi) |

**Không bật** `BINANCE_CLEAR_RATE_LIMIT=true` nếu IP mới vẫn có thể bị ban — clear local không xóa ban phía Binance.

---

## Upload database lên volume

```bash
VOL=0e82cf3f-54bd-4538-9206-23b5383aa1a4   # volume SG, kiểm tra bằng: railway volume list

for f in bot.db binance_exchange_info.json binance_listen_key binance_ws_account.json binance_ws_candles.json; do
  railway volume files -v "$VOL" upload "data/$f" "/$f" --overwrite
done

railway volume files -v "$VOL" list /
```

- [ ] Volume hiển thị > 0 MB (không phải volume trống / sai region)

---

## Deploy

```bash
railway service scale us-west=0 southeast-asia=1
railway up -d -y
```

- [ ] **Không** redeploy liên tiếp nhiều lần trong vài phút (mỗi boot = thêm REST risk)
- [ ] **Không** chạy local và PROD song song

---

## Sau deploy — verify (log & HTTP)

### Log phải có

- [ ] `bot-ema-follow-trend: starting (PORT=... DATABASE_PATH=/data/bot.db)`
- [ ] `Binance REST boot quiet — optional REST paused 600s (deploy safety; WS/disk only)`
- [ ] `Binance WS starting without REST seed` (hoặc REST-pause nếu cooldown còn hợp lệ)
- [ ] `Binance kline WS connected`
- [ ] `Binance EMA-RSI bot started` + `Trading: LIVE`
- [ ] `First cycle — skip REST orphan/protective reconcile (WS-only boot)` (cycle đầu)
- [ ] `Volume rank cache hit` hoặc `Volume rank loaded` từ **WS** (không thấy `Volume rank REST fallback` ngay lúc boot)

### Log **không** nên thấy ngay sau boot

- [ ] `Binance rate limited — pausing REST calls for XXXXs`
- [ ] Hàng loạt `Restore SL/TP failed` ngay cycle 1
- [ ] `Volume rank REST fallback — WS miniTicker empty` (WS chưa kịp seed → REST burst)

### HTTP

- [ ] https://bot-ema-follow-trend-production.up.railway.app/login → **200**
- [ ] Dashboard login OK, positions khớp local

```bash
railway logs --lines 80
railway status   # region: Southeast Asia
```

---

## REST optional vs critical — có ảnh hưởng logic không?

Bot phân loại mọi REST call Binance thành 2 nhóm (`src/exchange/binance.py`):

| Loại | Ví dụ | Khi nào chạy |
|------|--------|--------------|
| **Critical** | POST market order, POST algo SL/TP, cancel order | Mở/đóng lệnh thật — **luôn được phép**, kể cả boot quiet |
| **Optional** | GET balance, GET positionRisk (full book), GET ticker/24hr, GET order detail (fill poll), restore SL/TP verify | Chỉ để sync/monitor — **có thể dùng WS/disk thay** |

### Boot quiet 600s (`REST_BOOT_QUIET_SEC`) — ảnh hưởng gì?

**10 phút đầu sau mỗi lần start**, bot **chặn toàn bộ optional REST**; dùng WebSocket + file trên disk thay.

| Chức năng | Boot quiet (0–10 phút) | Sau boot quiet |
|-----------|------------------------|----------------|
| Scan tín hiệu 5m | ✅ WS kline | ✅ WS kline |
| Volume rank top 50 | ✅ WS miniTicker | ✅ WS (cache 300s) |
| Watcher đóng lệnh (SL/TP hit) | ✅ User Data Stream | ✅ UDS + REST verify nếu cần |
| Mở lệnh mới (signal + entry) | ✅ REST critical (market + SL/TP) | ✅ |
| Dashboard equity | WS/disk cache (có thể hơi cũ) | REST balance định kỳ |
| Restore SL/TP nếu DB thiếu id | ⏸ Defer (cycle 2+) | ✅ |
| Orphan reconcile | ⏸ Defer (cycle 2+) | ✅ |

**Logic trading core không đổi** — EMA200 cross, RSI swing, RR 1:2, max 20 positions, watcher vẫn như cũ. Chỉ **hoãn** các bước “kiểm tra/sync phụ” qua REST trong 10 phút đầu để tránh ban IP lúc deploy.

### Lưu ý khi deploy PROD

- [ ] DB phải có **đủ `sl_order_id` + `tp_order_id`** trước deploy — boot quiet sẽ không verify lại ngay bằng REST
- [ ] Upload **WS persist files** (`binance_ws_account.json`, `binance_ws_candles.json`, …) để boot không cần REST seed
- [ ] Sau deploy, **đợi ≥ 10 phút** rồi check log: balance reconcile, protective restore (nếu cần) chạy bình thường
- [ ] **Không** bật `BINANCE_CLEAR_RATE_LIMIT` khi deploy — trừ khi đổi IP và chắc chắn Binance đã hết ban
- [ ] **Không** redeploy liên tiếp — mỗi restart reset boot quiet 600s và tăng rủi ro nếu optional REST bị gọi sớm
- [ ] Env tùy chọn: `REST_BOOT_QUIET_SEC=600` (mặc định); có thể tăng `900` nếu IP Railway hay bị 418
- [ ] Weight budget (mặc định, không cần set trừ khi đổi): `REST_WEIGHT_SAFE_MAX=800`, `REST_WEIGHT_HARD_MAX=1800` trên trần futures `2400`/phút. Bot đọc `X-MBX-USED-WEIGHT-1M` từ mỗi REST — **không** gọi thêm API chỉ để check weight.

### Weight 1 phút — có đảm bảo 100% không?

**Không 100%** trên Railway shared IP (user khác cùng IP cũng ăn weight). Cách bot giữ an toàn:

1. Đọc header `X-MBX-USED-WEIGHT-1M` sau **mỗi** REST đã gọi (không probe).
2. `used >= 800` → chặn **optional** REST đến hết cửa sổ ~60s.
3. `used >= 1800` → chặn **cả order** (tránh 429→418).
4. Serial REST (1 request tại một thời điểm) nên header luôn cập nhật trước call tiếp.

Không ping Binance để “hỏi còn ban không”.

### Local vs PROD

Boot quiet áp dụng **cả local lẫn PROD** mỗi khi restart — hành vi giống nhau, local cũng an toàn khi khởi động lại.

---

## Cơ chế chống ban IP (đã có trong code)

| Cơ chế | File |
|--------|------|
| **Boot quiet 600s** — chặn mọi optional REST sau deploy | `src/exchange/binance.py` (`REST_BOOT_QUIET_SEC`) |
| **Weight budget** — skip REST khi `X-MBX-USED-WEIGHT-1M` ≥ 800 / 1800 | `src/exchange/binance.py` (`REST_WEIGHT_SAFE_MAX` / `HARD_MAX`) |
| Scan WS-only (`ws_only=True`) | `src/ema_rsi/candles.py` |
| Entry confirm REST tối đa ~3/cycle | `src/ema_rsi/candles.py` |
| Chờ miniTicker seed trước volume rank | `src/ema_rsi/cycle.py` |
| Cache volume rank 300s (`max_age_sec`) | `src/market_universe.py`, `cycle.py` |
| Cycle 1 skip orphan/protective REST | `src/ema_rsi/cycle.py` |
| Skip restore SL/TP nếu DB đã có cả 2 order id | `src/ema_rsi/trading.py` |
| Cooldown persist `/data/binance_rate_limit_until_ms` | `src/exchange/binance.py` |
| Không REST ticker/24hr khi WS bật (default) | `src/exchange/binance.py` |

---

## Nếu bị cooldown / 418

1. **Không** xóa file cooldown và **không** bật `BINANCE_CLEAR_RATE_LIMIT` cho đến khi Binance hết ban thật (~ vài phút đến vài giờ).
2. Bot vẫn chạy được qua **WS** (watcher, scan, đóng lệnh qua UDS).
3. REST (restore SL/TP, balance fresh, entry confirm) tự retry sau khi cooldown hết.
4. Kiểm tra SL/TP trên Binance app — nếu đã có trên exchange thì an toàn dù restore REST fail.
5. Chỉ khi **đổi region / egress IP mới** và chắc chắn Binance không còn ban IP cũ:
   ```bash
   railway variables set BINANCE_CLEAR_RATE_LIMIT=true
   # deploy một lần, verify log "cooldown cleared", rồi:
   railway variables set BINANCE_CLEAR_RATE_LIMIT=false
   ```
6. **Không** burst REST ngay sau clear (code hiện tại đã defer cycle 1).

Xóa file cooldown trên volume (chỉ khi Binance đã hết ban):

```bash
railway volume files delete --volume bot-ema-follow-trend-volume-o5NS /binance_rate_limit_until_ms
```

---

## Offline PROD / bật lại local

### Offline PROD (giữ region SG)

```bash
railway service scale us-west=0 southeast-asia=0
railway down -y
# verify: curl → 404, railway status → region Southeast Asia
```

### Bật local

```bash
cd /path/to/bot-ema-follow-trend
.venv/bin/python -m src.main
# dashboard: http://localhost:8080
```

---

## Bật lại PROD (lần sau)

- [ ] Tắt bot local trước
- [ ] Làm lại mục **Upload database** nếu DB local mới hơn PROD
- [ ] `railway service scale us-west=0 southeast-asia=1`
- [ ] `railway up -d -y`
- [ ] Verify theo mục **Sau deploy**

---

## Tham chiếu nhanh

| | |
|---|---|
| PROD URL | https://bot-ema-follow-trend-production.up.railway.app |
| Region | Southeast Asia (`southeast-asia=1`) |
| Volume mount | `/data` |
| Project Railway | `bot-ema-follow-trend` |
| Bot logic | [BOT_LOGIC.md](./BOT_LOGIC.md) |
