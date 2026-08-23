# breadth_flip + skim spot — **live stack** (365d)

- Sinh luc: 2026-08-23 17:23:08 +07
- Script: `scripts/backtest_donchian_20coin_breadth_flip_wd_skim40.py` (cache-only)
- Rule trade: **breadth_flip** · D20 · body ATR · pot_rr · margin 1%×size_mult · max_open 20
- Rule rút (khớp live `spot_transfer.py`): cuối ngày +07
  - `day_pnl ≤ 0` hoặc `DD_from_peak ≥ 20%` → rút 0
  - else `rút = min(cash_free, day_pnl × 0.4, equity_SOD × 0.015)`
- Pool: 20 majors · 15m · ~365d · capital **1000$** · fee 0.04%/side · 10x

## Tổng hợp

| Metric | Value |
| --- | --- |
| %/ngày **total** (bot+spot) | **+4.825%** |
| End bot / spot / **total** | 9871 / 8741 / **18612** |
| MaxDD bot / **total** | 25.9% / **16.0%** |
| PF / WR / lệnh/ngày | 1.40 / 75.0% / 48.6 |
| Ngày rút | **260/365** |
| Rút % đầu tháng (min–max–avg) | **4.79% – 22.58% – 12.64%** |
| Ngày rút/tháng (min–max–avg) | **6 – 26 – 20.0** |
| Skip rút | no_profit: 104, dd_pause: 2 |

## Total (bot+spot) đầu tháng

| Tháng | Total | Bot | Spot | vs 1000$ |
| --- | ---: | ---: | ---: | ---: |
| 2025-08 | **1000** | 1000 | 0 | +0% |
| 2025-09 | **1265** | 1192 | 73 | +27% |
| 2025-10 | **1563** | 1275 | 287 | +56% |
| 2025-11 | **2058** | 1464 | 594 | +106% |
| 2025-12 | **3347** | 2293 | 1054 | +235% |
| 2026-01 | **4214** | 2685 | 1529 | +321% |
| 2026-02 | **5608** | 3439 | 2169 | +461% |
| 2026-03 | **7270** | 4257 | 3013 | +627% |
| 2026-04 | **9401** | 5561 | 3839 | +840% |
| 2026-05 | **10893** | 6236 | 4657 | +989% |
| 2026-06 | **11559** | 6010 | 5549 | +1056% |
| 2026-07 | **13377** | 6691 | 6685 | +1238% |
| 2026-08 | **16019** | 8090 | 7929 | +1502% |

## Rút theo tháng

| Tháng | Ngày rút | Rút $ | % đầu tháng | End total | Skip |
| --- | ---: | ---: | ---: | ---: | --- |
| 2025-08 | 6 | 73 | 7.26% | 1265 | no_profit:3 |
| 2025-09 | 20 | 223 | 17.64% | 1563 | no_profit:10 |
| 2025-10 | 20 | 304 | 19.48% | 2059 | no_profit:11 |
| 2025-11 | 24 | 465 | 22.58% | 3347 | no_profit:6 |
| 2025-12 | 24 | 483 | 14.44% | 4214 | no_profit:7 |
| 2026-01 | 24 | 671 | 15.91% | 5608 | no_profit:7 |
| 2026-02 | 19 | 826 | 14.73% | 7270 | no_profit:9 |
| 2026-03 | 20 | 803 | 11.05% | 9401 | dd_pause:2, no_profit:9 |
| 2026-04 | 26 | 899 | 9.56% | 10893 | no_profit:4 |
| 2026-05 | 21 | 802 | 7.37% | 11559 | no_profit:10 |
| 2026-06 | 21 | 1185 | 10.25% | 13377 | no_profit:9 |
| 2026-07 | 22 | 1240 | 9.27% | 16019 | no_profit:9 |
| 2026-08 | 13 | 767 | 4.79% | 18612 | no_profit:10 |

## Đọc nhanh

- Sau ~1 năm paper: **18.6×** vốn gốc (~47% đã rút sang spot).
- MaxDD **total** ~16% — thấp hơn flip không rút (~25%) nhờ tách lãi ra spot.
- `%/ngày total` ≈ 4–5% paper; live có thể thấp hơn (slippage, pool top-N động).

Paper only — không ảnh hưởng bot live.
