# D20 — vote xu hướng theo MTM lệnh đang mở

- Sinh luc: 2026-08-22 11:15:05 +07
- Script: `scripts/backtest_donchian_20coin_open_mtm_trend.py` (cache-only)
- D20_like_bot 15m 365d 1000$ · sau TP cùng nến, nhìn **PnL nổi tất cả lệnh còn mở** (20 coin).
- Không dùng lệnh đã đóng. Neutral = cho cả long lẫn short.
- `ls_bal` = min(L,S)/max(L,S). `_any` = vote dù sách 1 phía (dễ khóa).

| Config | %/ngày | MaxDD | PF | WR | n | t/d | L/S | bal | skip | Note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |
| `D20_base` | **+33.450%** | **49.9%** | 1.48 | 73.4% | 16905 | 46.3 | 8446/8459 | 1.00 | 0 | không lọc (baseline) |
| `mtm_sum_1.5x` | **+31.360%** | **47.9%** | 1.49 | 73.3% | 16345 | 44.8 | 8068/8277 | 0.97 | 2576 | Tổng MTM; 2 phía; lead ≥1.5× |
| `mtm_green` | **+29.902%** | **47.9%** | 1.49 | 73.2% | 16321 | 44.7 | 8066/8255 | 0.98 | 2648 | Chỉ chặn khi đúng 1 phía tổng MTM > 0 (phía kia ≤0) |
| `mtm_win_both` | **+21.759%** | **49.7%** | 1.64 | 73.1% | 14705 | 40.3 | 7214/7491 | 0.96 | 10440 | Số lệnh đang lãi; cần 2 phía đang mở |
| `mtm_pos_both` | **+20.261%** | **50.0%** | 1.61 | 72.9% | 14524 | 39.8 | 7069/7455 | 0.95 | 11300 | Chỉ cộng MTM dương; cần 2 phía đang mở |
| `mtm_sum_both` | **+18.415%** | **57.9%** | 1.41 | 72.8% | 14466 | 39.6 | 7082/7384 | 0.96 | 11028 | Tổng MTM; cần ≥1 long VÀ ≥1 short đang mở |
| `mtm_sum_both2` | **+18.168%** | **59.8%** | 1.42 | 72.6% | 14980 | 41.0 | 7369/7611 | 0.97 | 8213 | Tổng MTM; ≥2 mỗi phía đang mở |
| `mtm_avg_both` | **+17.834%** | **58.7%** | 1.47 | 72.7% | 14413 | 39.5 | 7097/7316 | 0.97 | 11247 | MTM trung bình / lệnh mỗi phía (cần 2 phía) |
| `mtm_win_any` | **+14.173%** | **50.0%** | 1.55 | 72.5% | 14240 | 39.0 | 7039/7201 | 0.98 | 12517 | Số lệnh mở đang lãi (MTM>0) L vs S |
| `mtm_sum_n4` | **+11.394%** | **54.7%** | 1.38 | 72.7% | 14194 | 38.9 | 6927/7267 | 0.95 | 12934 | Tổng MTM; cần ≥4 lệnh mở |
| `mtm_cnt_both` | **+10.490%** | **49.3%** | 1.56 | 73.1% | 13266 | 36.3 | 6280/6986 | 0.90 | 19203 | Số lệnh mở L vs S (không nhìn $); cần 2 phía |
| `mtm_sum_any` | **+10.019%** | **54.9%** | 1.35 | 72.9% | 13965 | 38.3 | 6721/7244 | 0.93 | 14261 | Tổng MTM L vs S — vote dù sách 1 phía |

## Đọc

- Baseline: %/ngày **+33.450%**, MaxDD **49.9%**, L/S 8446/8459
- Best vừa hạ DD vừa không khóa sách (`ls_bal`≥0.25): `mtm_sum_1.5x` → +31.360%/ngày, MaxDD 47.9%

Paper only — không ảnh hưởng bot live.
