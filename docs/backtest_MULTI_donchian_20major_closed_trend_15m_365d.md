# D20 — gate entry theo xu hướng lệnh đã đóng

- Sinh luc: 2026-08-22 10:54:25 +07
- Script: `scripts/backtest_donchian_20coin_closed_trend.py` (cache-only, **không** đụng bot)
- Pool/rule: D20_like_bot (1%, max20, body 0.3–1.2, pot_rr≥0.5), 15m ~365d, 1000$
- Ý: sau khi có tín hiệu Donchian, hỏi **sách lệnh đã đóng** đang trả hướng nào rồi mới vào.
- Warmup: chưa đủ lịch sử đóng → **vẫn cho vào** (trừ khi ghi chú).
- Fail gate → bỏ nến này, **giữ waiting** (thử nến sau).

## Kết quả

| Config | %/ngày | Net | MaxDD | PF | WR | n | lệnh/ngày | skip | L/S | Note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `D20_base` | **+33.450%** | +122091 | **49.9%** | 1.48 | 73.4% | 16905 | 46.3 | 0 | 8446/8459 | no closed-trend gate |
| `last1_win` | **+28.303%** | +103307 | **32.0%** | 1.45 | 74.8% | 13346 | 36.6 | 19865 | 3/13343 | chỉ vào cùng side lệnh *thắng* gần nhất |
| `last1_any` | **+28.303%** | +103307 | **32.0%** | 1.45 | 74.8% | 13346 | 36.6 | 19865 | 3/13343 | chỉ vào cùng side lệnh đóng gần nhất (kể cả lỗ) |
| `win3_maj` | **+28.150%** | +102747 | **32.0%** | 1.45 | 74.8% | 13345 | 36.6 | 19850 | 4/13341 | majority 3 lệnh thắng gần nhất (tie=cho) |
| `win5_maj` | **+28.150%** | +102747 | **32.0%** | 1.45 | 74.8% | 13345 | 36.6 | 19850 | 4/13341 | majority 5 lệnh thắng gần nhất (tie=cho) |
| `win8_maj` | **+28.150%** | +102747 | **32.0%** | 1.45 | 74.8% | 13345 | 36.6 | 19850 | 4/13341 | majority 8 lệnh thắng gần nhất (tie=cho) |
| `any5_maj` | **+28.150%** | +102747 | **32.0%** | 1.45 | 74.8% | 13345 | 36.6 | 19850 | 4/13341 | majority 5 lệnh đóng gần nhất |
| `win3_all` | **+28.078%** | +102483 | **32.0%** | 1.45 | 74.8% | 13343 | 36.6 | 19855 | 4/13339 | 3 lệnh thắng gần nhất phải cùng side |
| `sym_last_same` | **+22.049%** | +80477 | **25.2%** | 1.49 | 74.5% | 13182 | 36.1 | 19871 | 3104/10078 | chỉ vào symbol cùng side lần đóng trước của symbol đó |
| `win4h` | **+10.310%** | +37633 | **60.6%** | 1.45 | 72.3% | 14700 | 40.3 | 8971 | 7413/7287 | majority thắng đóng trong ~4h (16 nến) |
| `win8h` | **+5.707%** | +20832 | **63.5%** | 1.30 | 71.8% | 13509 | 37.0 | 16327 | 6850/6659 | majority thắng đóng trong ~8h (32 nến) |
| `pnl5` | **+5.529%** | +20181 | **64.7%** | 1.31 | 71.8% | 12302 | 33.7 | 21916 | 5676/6626 | side có tổng PnL cao hơn trong 5 lệnh đóng gần nhất |
| `pnl10` | **+3.919%** | +14305 | **57.3%** | 1.27 | 71.4% | 12092 | 33.1 | 23182 | 5715/6377 | side có tổng PnL cao hơn trong 10 lệnh đóng gần nhất |
| `sym_last_win` | **+0.022%** | +82 | **5.5%** | 2.13 | 82.8% | 116 | 0.3 | 87265 | 42/74 | chỉ vào symbol nếu lần đóng *trước đó* của symbol là lãi |

## Đọc

- Baseline D20: %/ngày **+33.450%**, MaxDD **49.9%**, n=16905
- Best %/ngày: `D20_base` (không gate)
- **Không dùng vote toàn sách (`last1_win` / `winN_maj`)** — L/S ≈ **3/13343**. Một lệnh thắng short → chỉ còn được short → khóa hướng cả năm. MaxDD 32% là artifact, không phải “đọc trend”.
- **Cửa sổ thời gian (`win4h`/`win8h`) / PnL gần đây:** không khóa L/S nhưng **MaxDD tệ hơn** baseline (57–65%).
- **`sym_last_win`:** sau 1 lệnh lỗ trên coin là **kẹt coin đó mãi** → gần chết (116 lệnh/năm).
- **Hướng dùng được:** `sym_last_same` — chỉ vào **cùng side lần đóng trước của đúng coin đó**.
  - %/ngày **+22.0%** (vs +33.5%)
  - MaxDD **25.2%** (vs 49.9%)
  - PF **1.49** (vs 1.48), WR 74.5%
  - Ý: coin đang “trả” long thì đừng lật short ngay (và ngược lại).

Paper only — không ảnh hưởng bot live.
