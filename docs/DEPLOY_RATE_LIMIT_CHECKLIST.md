# Checklist deploy — tránh Binance 418/429 (rate limit)

Pre-flight script: `scripts/preflight_prod_deploy.py` — **phải exit 0** trước deploy.  
Deploy VPS: `scripts/deploy_to_vps.sh` · Logic bot: [BOT_LOGIC.md](./BOT_LOGIC.md)

Dùng **trước / trong / sau** mỗi lần deploy PROD (VPS).  
Mục tiêu: boot **một lần**, dùng **WS cache trên disk**, REST optional tối thiểu.

---

## Tại sao bị limit? (tóm tắt)

| Nguyên nhân | Hậu quả |
|-------------|---------|
| Boot nhiều lần trong vài phút (restart liên tiếp) | Mỗi boot = listenKey + kline warmup + account |
| `binance_ws_candles.json` trống / thiếu (`candles_symbols=0`) | REST seed **20 coin**, gap **60s** → ~20 phút REST liên tục |
| `binance_ws_account.json` thiếu positions | Reconcile sai + thêm REST positionRisk |
| File `binance_rate_limit_until_ms` cũ trên server | Boot skip REST nhưng vẫn cố warmup → burst khi clear |
| Local + PROD cùng API key | 2 bot × REST/WS → weight gấp đôi |
| IP VPS mới chưa whitelist Binance API | 401 income/transfer; thêm REST retry |

**Quy tắc vàng:** sync cache **đủ** trước, deploy **một lần**, verify log **không** thấy REST seed hàng loạt.

---

## A. Trước deploy (local)

- [ ] Bot local chạy ổn ≥ vài chu kỳ 15m (cache WS đã đầy)
- [ ] **Tests pass:** `pytest -q`
- [ ] **Không** deploy nếu local log có `Binance rate limited` / cooldown còn > 0

### Sync + pre-flight (bắt buộc)

```bash
cd /path/to/bot-ema-follow-trend

# Bot local vẫn ĐANG CHẠY khi sync (OK)
.venv/bin/python scripts/sync_prod_deploy_data.py
.venv/bin/python scripts/preflight_prod_deploy.py --strict-candles
pytest -q
```

| Check | Pass khi |
|-------|----------|
| Files tồn tại | `bot.db`, `binance_exchange_info.json`, `binance_listen_key`, `binance_ws_account.json`, `binance_ws_candles.json` |
| DB = sàn | `open` count khớp |
| Candles | **Strict: 20/20 symbol ≥44 nến** |
| Account snapshot | Positions count = sàn, có `balance` |
| Không upload cooldown | Không copy `binance_rate_limit_until_ms` lên VPS |

- [ ] Pre-flight **exit 0**
- [ ] Có `data/binance_listen_key` (reuse → không REST create listenKey lúc boot)
- [ ] **Không** deploy `data/binance_rate_limit_until_ms`

### Refresh account snapshot (nếu pre-flight báo thiếu positions)

```bash
.venv/bin/python -c "
from src.exchange.binance import fetch_all_open_positions_rest
from src.exchange.binance_ws.cache import CACHE
from src.exchange.binance_ws.persist import save_account_snapshot
from src.exchange.types import Position
positions = [p for p in fetch_all_open_positions_rest() if abs(p.size) > 1e-12]
by_symbol = {}
for pos in positions:
    b = by_symbol.setdefault(pos.symbol.upper(), {
        'long': Position(symbol=pos.symbol, side=None, size=0.0, avg_price=0.0),
        'short': Position(symbol=pos.symbol, side=None, size=0.0, avg_price=0.0),
    })
    if pos.side in b:
        b[pos.side] = pos
CACHE.set_positions(positions, by_symbol)
save_account_snapshot()
print(f'saved {len(positions)} positions')
"
```

---

## B. Deploy VPS — một lần

```bash
export VPS_HOST=your.droplet.ip
./scripts/deploy_to_vps.sh
```

Script tự làm: sync → pre-flight → tắt local → tar upload code + `data/` → systemd setup → restart.

- [ ] **Whitelist IP VPS** trên Binance API key (futures + spot nếu dùng skim)
- [ ] `.env` trên VPS có API keys (script upload cả repo kèm `.env` local)
- [ ] **Không** restart bot liên tiếp trong 30 phút sau deploy
- [ ] **Không** chạy local và VPS song song

---

## C. Sau deploy — verify log (5 phút đầu)

```bash
ssh root@$VPS_HOST 'journalctl -u bot-donchian -n 100 --no-pager'
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://$VPS_HOST:8080/
```

### Log **phải có**

- [ ] `Binance WS disk restore: candles_symbols=18+` (mục tiêu ~20)
- [ ] `Binance WS disk restore: ... account=yes listenKey=yes`
- [ ] `Binance WS starting without REST seed`
- [ ] `Binance listenKey reused from disk`
- [ ] `Fixed scan pool (20, BT majors)`
- [ ] `scan=fixed×20` · `breadth=flip` · `WS kline=15m`
- [ ] Phần lớn coin: `kline cache ready — skip REST warmup`
- [ ] `First cycle — skip REST position reconcile`
- [ ] Dashboard positions **khớp** sàn

### Log **không được thấy** ngay sau boot

- [ ] `Binance rate limited — pausing REST calls for XXXXs` (XXXX > 60)
- [ ] REST seed **liên tục** 10+ symbol trong 5 phút
- [ ] `flat on exchange — closing in DB` hàng loạt khi sàn vẫn có lệnh
- [ ] `Volume rank REST fallback`

### Nếu log báo `candles_symbols=0` hoặc REST seed > 5 symbol

1. **Stop VPS bot** ngay: `ssh root@$VPS_HOST 'systemctl stop bot-donchian'`
2. Bật lại **local**, chạy thêm đến khi pre-flight pass
3. Deploy lại **một lần** (`./scripts/deploy_to_vps.sh`)

---

## D. Khi IP đang hot / vừa 418

- [ ] **Không** `BINANCE_CLEAR_RATE_LIMIT=true` trừ khi chắc chắn Binance hết ban
- [ ] **Không** xóa file cooldown nếu ban còn thật
- [ ] Bot vẫn trade được qua **WS** (mark, kline, UDS) — REST optional tự retry sau cooldown
- [ ] Chỉ deploy lại khi `optional_rest_blocked_sec()` = 0 trên local

---

## E. Checklist nhanh (copy-paste)

```
[ ] sync_prod_deploy_data.py exit 0
[ ] preflight_prod_deploy.py --strict-candles exit 0
[ ] pytest -q pass
[ ] Binance API whitelist IP VPS
[ ] VPS_HOST set → ./scripts/deploy_to_vps.sh (1 lần)
[ ] Log: candles_symbols≥18, listenKey reused, skip REST warmup hầu hết coin
[ ] Log: KHÔNG rate limit / KHÔNG REST seed 20 coin / KHÔNG flat-on-exchange
[ ] Dashboard positions khớp sàn
[ ] Local OFF cho đến khi verify xong
```

---

## Tham chiếu code

| Cơ chế | File |
|--------|------|
| Boot gap 60s giữa REST optional | `src/exchange/binance.py` (`REST_BOOT_GAP_SEC`) |
| Weight gate 800 / 1800 | `src/exchange/binance.py` |
| Donchian boot warmup 20 coin | `src/donchian/cycle.py` |
| Cycle 1 skip position reconcile | `src/donchian/cycle.py` |
| Không coi thiếu WS cache = flat | `src/exchange/binance_ws/manager.py` |
| REST confirm trước khi đóng lot DB | `src/donchian/trading.py` |
| Persist candles / account / listenKey | `src/exchange/binance_ws/persist.py` |
