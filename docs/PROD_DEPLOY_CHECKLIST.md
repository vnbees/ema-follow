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
| `REST_BOOT_QUIET_SEC` | `0` (mặc định — **không** chặn REST 10 phút) |
| `REST_BOOT_GAP_SEC` | `60` (mặc định — optional REST lúc boot cách nhau 1 phút) |

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
- [ ] `Boot REST warmup started (quiet=0s gap=60s)`
- [ ] `Binance WS starting without REST seed` (hoặc REST-pause nếu cooldown còn hợp lệ)
- [ ] `Binance kline WS connected`
- [ ] `Binance RSI-rev bot started` + `Trading: LIVE`
- [ ] `First cycle — skip REST position reconcile (WS-only boot)` (cycle đầu)
- [ ] Coin thiếu nến: `REST kline warmup seeded` **cách nhau ~60s**, không burst 4 symbol cùng lúc
- [ ] Coin đã đủ cache: `kline cache ready — skip REST warmup`

### Log **không** nên thấy ngay sau boot

- [ ] `Binance rate limited — pausing REST calls for XXXXs`
- [ ] `Volume rank REST fallback` (bot RSI-rev không scan volume rank)

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
| **Critical** | POST market order, cancel / reduce-only close | Mở/đóng lệnh thật — **luôn được phép**, không chờ boot gap |
| **Optional** | GET kline warmup, listenKey create, GET account, GET ticker/24hr, GET order detail (fill poll) | Chỉ khi cache thiếu — **cách 60s + weight gate** |

### Boot REST (`REST_BOOT_GAP_SEC=60`, quiet mặc định 0)

**Không** đợi 10 phút. Lúc start, REST optional **chỉ khi cần** (không có listenKey trên disk, nến WS < ~20, không có account snapshot). Mỗi lần gọi xong chờ **60 giây** rồi mới gọi optional tiếp theo. Weight `X-MBX-USED-WEIGHT-1M` ≥ 800 thì skip optional đến hết cửa sổ ~60s.

| Chức năng | Ngay sau start |
|-----------|----------------|
| Scan tín hiệu 5m | WS kline; REST warmup 1 coin / 60s nếu cache ngắn |
| Watcher đóng lot (TP / BE / timeout) | Mark WS + REST critical khi đóng |
| Mở lệnh mới | REST critical (market); config hedge optional không qua boot gap |
| Dashboard equity | WS/disk; không REST chỉ để log |
| Position size reconcile | Cycle 1 skip; cycle 2+ WS trước |

**Logic trading core không đổi.** Quiet 10 phút vẫn bật được bằng `REST_BOOT_QUIET_SEC=600` nếu IP đang 418.

### Lưu ý khi deploy PROD

- [ ] Upload **WS persist files** (`binance_ws_account.json`, `binance_ws_candles.json`, `binance_listen_key`, …) để bớt REST seed
- [ ] **Không** bật `BINANCE_CLEAR_RATE_LIMIT` khi deploy — trừ khi đổi IP và chắc chắn Binance đã hết ban
- [ ] **Không** redeploy liên tiếp trong vài phút (mỗi restart có thể REST listenKey + kline warmup)
- [ ] Chỉ set `REST_BOOT_QUIET_SEC=600` khi IP Railway đang hot 418
- [ ] Weight budget (mặc định): `REST_WEIGHT_SAFE_MAX=800`, `REST_WEIGHT_HARD_MAX=1800`. Bot đọc `X-MBX-USED-WEIGHT-1M` từ mỗi REST — **không** gọi thêm API chỉ để check weight.

### Weight 1 phút — có đảm bảo 100% không?

**Không 100%** trên Railway shared IP (user khác cùng IP cũng ăn weight). Cách bot giữ an toàn:

1. Đọc header `X-MBX-USED-WEIGHT-1M` sau **mỗi** REST đã gọi (không probe).
2. `used >= 800` → chặn **optional** REST đến hết cửa sổ ~60s.
3. `used >= 1800` → chặn **cả order** (tránh 429→418).
4. Serial REST (1 request tại một thời điểm) nên header luôn cập nhật trước call tiếp.

Không ping Binance để “hỏi còn ban không”.

### Local vs PROD

Gap 60s + weight áp dụng **cả local lẫn PROD**. Quiet 10 phút chỉ khi set `REST_BOOT_QUIET_SEC`.

---

## Cơ chế chống ban IP (đã có trong code)

| Cơ chế | File |
|--------|------|
| **Boot gap 60s** — optional REST lúc start (listenKey / kline / cold account) cách nhau 1 phút | `src/exchange/binance.py` (`REST_BOOT_GAP_SEC`) |
| **Boot quiet (tắt mặc định)** — chỉ bật `REST_BOOT_QUIET_SEC=600` khi IP đang 418 | `src/exchange/binance.py` |
| **Weight budget** — skip REST khi `X-MBX-USED-WEIGHT-1M` ≥ 800 / 1800 | `src/exchange/binance.py` (`REST_WEIGHT_SAFE_MAX` / `HARD_MAX`) |
| Scan WS-only (`ws_only=True`) | `src/rsi_rev/candles.py` |
| REST kline warmup nếu cache < ~20 nến, 1 symbol / gap | `src/rsi_rev/candles.py` |
| Watched symbols = `RSI_REV_SYMBOLS` (LINK, SUI, WLD, HYPE) | `src/rsi_rev/cycle.py` |
| Cycle 1 skip REST position reconcile | `src/rsi_rev/cycle.py` |
| Equity: WS cache rồi REST fallback | `src/rsi_rev/trading.py` |
| Cooldown persist `/data/binance_rate_limit_until_ms` | `src/exchange/binance.py` |
| Không REST ticker/24hr khi WS bật (default) | `src/exchange/binance.py` |

---

## Nếu bị cooldown / 418

1. **Không** xóa file cooldown và **không** bật `BINANCE_CLEAR_RATE_LIMIT` cho đến khi Binance hết ban thật (~ vài phút đến vài giờ).
2. Bot vẫn chạy được qua **WS** (watcher mark, scan kline, đóng lệnh reduce-only).
3. REST (balance fresh nếu cache stale) tự retry sau khi cooldown hết.
4. RSI-rev **không** đặt algo SL/TP trên sàn — thoát lot do watcher mark.
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
