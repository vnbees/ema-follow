# D20 — daily skim 40% + DD≥20% pause withdraw

- Sinh luc: 2026-08-21 20:15:44 +07
- Script: `scripts/backtest_donchian_20coin_wd_skim40.py` (cache-only)
- Pool/rule: D20_like_bot (1%, max20, body/pot filters), 15m ~365d, capital 1000$
- Chốt ngày: **Asia/Ho_Chi_Minh**
- Rule rút cuối ngày:
  - nếu `day_pnl <= 0` hoặc `DD_from_peak >= 20%` → rút 0
  - else `rút = min(cash_free, day_pnl × 0.4, equity_đầu_ngày × 0.015)`
- `% tháng` = tổng rút trong tháng / **total (bot+spot) đầu tháng**

## Tong hop

| Metric | Value |
| --- | --- |
| %/ngày total | **+5.967%** |
| End bot / spot / total | 12174.0 / 10605.4 / **22779.4** |
| MaxDD bot / total | 50.2% / 39.5% |
| Tổng rút / số ngày rút | 10605.4 / 261/366 |
| Ngày rút / tháng (min–max–avg) | **8 – 29 – 20.1** |
| Rút % đầu tháng (min–max–avg) | **5.46% – 27.48% – 13.56%** |

## Theo tháng

| Tháng | Ngày lịch | Ngày rút | Rút $ | % vs đầu tháng | % vs cuối tháng | End total | Ghi chú skip |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2025-08 | 11 | **8** | 88.5 | **8.85%** | 7.04% | 1257 | no_profit:3 |
| 2025-09 | 30 | **22** | 215.6 | **17.15%** | 12.86% | 1676 | no_profit:8 |
| 2025-10 | 31 | **10** | 131.7 | **7.86%** | 8.27% | 1593 | dd_pause:7, no_profit:14 |
| 2025-11 | 30 | **24** | 437.8 | **27.48%** | 14.83% | 2951 | no_profit:6 |
| 2025-12 | 31 | **23** | 495.8 | **16.80%** | 13.29% | 3731 | no_profit:8 |
| 2026-01 | 31 | **23** | 607.2 | **16.27%** | 11.68% | 5198 | no_profit:8 |
| 2026-02 | 28 | **19** | 670.7 | **12.90%** | 10.43% | 6430 | no_profit:9 |
| 2026-03 | 31 | **24** | 1138.9 | **17.71%** | 11.97% | 9511 | no_profit:7 |
| 2026-04 | 30 | **22** | 1129.1 | **11.87%** | 9.92% | 11379 | no_profit:8 |
| 2026-05 | 31 | **20** | 1004.7 | **8.83%** | 8.04% | 12491 | no_profit:11 |
| 2026-06 | 30 | **23** | 1480.3 | **11.85%** | 9.56% | 15488 | no_profit:7 |
| 2026-07 | 31 | **29** | 2062.4 | **13.32%** | 9.85% | 20931 | no_profit:2 |
| 2026-08 | 21 | **14** | 1142.8 | **5.46%** | 5.02% | 22779 | no_profit:7 |

## Min / max

- Ít ngày rút nhất: **2025-08** → 8 ngày, rút 88.5$ (**8.85%** đầu tháng)
- Nhiều ngày rút nhất: **2026-07** → 29 ngày, rút 2062.4$ (**13.32%** đầu tháng)
- % tháng thấp nhất: **2026-08** → 5.46% (1142.8$, 14 ngày)
- % tháng cao nhất: **2025-11** → 27.48% (437.8$, 24 ngày)

Paper only — không ảnh hưởng bot live.
