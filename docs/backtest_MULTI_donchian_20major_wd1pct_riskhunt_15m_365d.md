# Hunt: WD 1%/ngày + hạ MaxDD_total, giữ hiệu suất total

- Sinh luc: 2026-08-21 17:49:25 +07
- Script: `scripts/backtest_donchian_20coin_wd_riskhunt.py` (clone; cache-only; khong sua bot/BT goc)
- Moi config deu **withdraw 1% equity/UTC-day** (tru khi HWM/profit-cap chan bot)
- Muc tieu: MaxDD **total** (bot+spot) thap; %/ngày **total** cao nhat co the (khong so voi D20 no-WD)

| Rank | Config | %/ngày total | MaxDD total | MaxDD bot | End total | Spot | PF | WR | t/d | score | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `M1_max20_wd_hwm` | **+25.665%** | **49.1%** | 49.9% | 94677 | 10561 | 1.47 | 73.4% | 46.3 | 0.523 | 1% max20 WD1% only at/above HWM |
| 2 | `M1_max20_wd_profit` | **+5.655%** | **37.9%** | 50.4% | 21642 | 10642 | 1.45 | 73.4% | 46.3 | 0.149 | 1% max20 WD≤min(1%eq, day profit) |
| 3 | `D20_wd` | **+2.566%** | **33.8%** | 51.7% | 10367 | 7188 | 1.43 | 73.4% | 46.3 | 0.076 | ref: 1% max20 + WD1%/d |
| 4 | `M1_max12_wd` | **+1.442%** | **28.6%** | 45.1% | 6265 | 4597 | 1.39 | 73.0% | 40.2 | 0.051 | 1% max12 + WD1% |
| 5 | `M075_max15_wd` | **+0.741%** | **24.8%** | 48.2% | 3704 | 2999 | 1.38 | 73.1% | 44.8 | 0.030 | 0.75% max15 + WD1% |
| 6 | `M075_max12_wd` | **+0.656%** | **20.9%** | 52.4% | 3395 | 2787 | 1.37 | 73.0% | 40.2 | 0.031 | 0.75% max12 + WD1% |
| 7 | `M1_max20_half10_wd` | **+0.551%** | **17.4%** | 70.0% | 3012 | 2641 | 1.41 | 73.4% | 46.3 | 0.032 | 1% max20 + half-size DD≥10% + WD1% |
| 8 | `M1_max8_wd` | **+0.462%** | **29.4%** | 71.4% | 2685 | 2258 | 1.29 | 71.5% | 28.5 | 0.016 | 1% max8 + WD1% |
| 9 | `A20_wd` | **+0.393%** | **16.2%** | 72.2% | 2433 | 2136 | 1.42 | 73.4% | 46.3 | 0.024 | ref: 0.5% max20 + WD1% |
| 10 | `M1_max12_half10_wd` | **+0.363%** | **29.4%** | 78.9% | 2325 | 2080 | 1.32 | 73.0% | 40.2 | 0.012 | 1% max12 + half DD≥10% + WD1% |
| 11 | `M1_max10_half10_wd` | **+0.363%** | **11.6%** | 82.6% | 2324 | 2114 | 1.35 | 72.4% | 34.8 | 0.031 | 1% max10 half DD≥10% + WD1% |
| 12 | `M1_max15_dd15_half10_wd` | **+0.133%** | **11.0%** | 96.6% | 1487 | 1447 | 1.40 | 74.0% | 5.6 | 0.012 | 1% max15 pause15% half10% + WD1% |
| 13 | `M1_max15_dd15_wd` | **+0.112%** | **33.9%** | 97.0% | 1409 | 1373 | 1.21 | 73.3% | 6.2 | 0.003 | 1% max15 + pause DD≥15% + WD1% |
| 14 | `M1_max20_dd12_wd` | **+0.111%** | **36.6%** | 97.2% | 1405 | 1369 | 1.21 | 73.7% | 6.4 | 0.003 | 1% max20 + pause DD≥12% + WD1% |
| 15 | `M075_max12_dd12_wd` | **+0.083%** | **22.0%** | 96.9% | 1304 | 1270 | 1.26 | 73.5% | 5.6 | 0.004 | 0.75% max12 pause DD≥12% + WD1% |

## Goi y

- Ref `D20_wd`: %/ngày total **+2.566%**, MaxDD_total **33.8%**
- Tot nhat trong nhom MaxDD_total≤25%: `M075_max15_wd` → **+0.741%/ngày**, MaxDD_total **24.8%**, end **3704** — 0.75% max15 + WD1%
- Best score (%/day / MaxDD_total): `M1_max20_wd_hwm` → +25.665% / DD 49.1% (score 0.523)

Luu y: WD 1%/ngày **bat buoc** cat da compound → khong the giu +33%/ngày nhu D20 no-WD. So sanh cong bang trong nhom co WD.
Paper; khong anh huong bot live.

