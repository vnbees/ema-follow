# D20 — vote xu hướng theo cửa sổ thời gian (cả sách, mọi coin)

- Sinh luc: 2026-08-22 11:09:09 +07
- Script: `scripts/backtest_donchian_20coin_timewindow_trend.py` (cache-only)
- D20_like_bot 15m 365d 1000$ · sau tín hiệu Donchian, nhìn **toàn bộ lệnh 20 coin** trong cửa sổ.
- Cửa sổ: **2h / 4h / 8h / 24h**. Hold thường ~1–3h → 8h ≈ vài vòng; 24h ≈ 1 ngày.
- **Neutral:** chưa đủ mẫu, hoặc (bản `both`) cửa sổ thiếu một phía → **cho cả long lẫn short** (tránh khóa 1 hướng).
- `ls_bal` = min(L,S)/max(L,S) — gần 1 = không lệch sách; gần 0 = khóa 1 phía.

| Config | %/ngày | MaxDD | PF | WR | n | t/d | L/S | bal | skip | Note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |
| `D20_base` | **+33.450%** | **49.9%** | 1.48 | 73.4% | 16905 | 46.3 | 8446/8459 | 1.00 | 0 | không lọc (baseline) |
| `setup_8h` | **+21.214%** | **55.4%** | 1.53 | 73.1% | 14993 | 41.1 | 7509/7484 | 1.00 | 8268 | Số tín hiệu L/S pass filter trong 8h |
| `setup_24h` | **+16.085%** | **63.4%** | 1.54 | 72.7% | 15319 | 42.0 | 7605/7714 | 0.99 | 6765 | Số tín hiệu L/S 24h |
| `pnl_2h` | **+15.706%** | **45.1%** | 1.43 | 72.7% | 15161 | 41.5 | 7580/7581 | 1.00 | 6812 | PnL long vs short — đóng trong 2h; lệch là chọn |
| `wins_8h_both` | **+15.034%** | **60.6%** | 1.43 | 72.9% | 14921 | 40.9 | 7378/7543 | 0.98 | 8998 | Số lệnh thắng 8h; ≥3 mỗi phía |
| `pnl_4h` | **+14.097%** | **60.2%** | 1.43 | 72.5% | 14324 | 39.2 | 7111/7213 | 0.99 | 10686 | PnL đóng trong 4h |
| `wr_8h` | **+14.080%** | **52.0%** | 1.39 | 72.8% | 15284 | 41.9 | 7507/7777 | 0.97 | 7374 | WR long vs short 8h; lệch ≥10pp; ≥4 mỗi phía |
| `pnl_8h_1.5x` | **+12.930%** | **58.5%** | 1.40 | 72.7% | 14854 | 40.7 | 7287/7567 | 0.96 | 9033 | PnL 8h + cả 2 phía; chỉ chặn nếu lead ≥1.5× |
| `wr_24h` | **+11.623%** | **56.0%** | 1.42 | 73.0% | 14326 | 39.2 | 6983/7343 | 0.95 | 12791 | WR 24h; lệch ≥10pp; ≥6 mỗi phía |
| `pnl_8h_both` | **+11.027%** | **58.7%** | 1.39 | 72.6% | 14687 | 40.2 | 7230/7457 | 0.97 | 9915 | PnL 8h — chỉ chặn khi cửa sổ có ≥3 long VÀ ≥3 short |
| `pnl+mtm_8h_both` | **+9.154%** | **59.5%** | 1.40 | 72.0% | 13869 | 38.0 | 6724/7145 | 0.94 | 14122 | PnL+MTM 8h; cần 2 phía |
| `breadth_mid` | **+8.751%** | **19.0%** | 1.39 | 73.8% | 14078 | 38.6 | 6888/7190 | 0.96 | 15248 | Số coin close>mid vs <mid (trend thị trường, không phải sách) |
| `pnl+mtm_8h` | **+8.732%** | **59.5%** | 1.42 | 72.5% | 13456 | 36.9 | 6497/6959 | 0.93 | 16366 | PnL đã đóng 8h + MTM lệnh đang mở |
| `pnl_24h` | **+8.617%** | **36.3%** | 1.46 | 72.3% | 12674 | 34.7 | 5679/6995 | 0.81 | 21184 | PnL đóng trong 24h |
| `pnl_24h_1.5x` | **+8.450%** | **45.4%** | 1.35 | 72.1% | 14053 | 38.5 | 6687/7366 | 0.91 | 13858 | PnL 24h + 2 phía; lead ≥1.5× |
| `pnl_24h_both` | **+8.312%** | **31.8%** | 1.30 | 71.8% | 13737 | 37.6 | 6660/7077 | 0.94 | 15435 | PnL 24h — ≥5 mỗi phía mới vote |
| `pnl_8h` | **+7.221%** | **63.9%** | 1.34 | 72.4% | 13324 | 36.5 | 6534/6790 | 0.96 | 16713 | PnL đóng trong 8h (vài vòng hold) |

## Đọc

- Baseline: %/ngày **+33.450%**, MaxDD **49.9%**, L/S 8446/8459
- Best vừa hạ DD vừa không khóa sách (`ls_bal`≥0.25): `pnl_2h` → +15.706%/ngày, MaxDD 45.1%

Paper only — không ảnh hưởng bot live.
