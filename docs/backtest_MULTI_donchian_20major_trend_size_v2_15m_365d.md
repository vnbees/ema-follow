# D20 — follow-up soft-size / boost / lose-only / breadth

- Sinh luc: 2026-08-22 11:31:58 +07
- Script: `scripts/backtest_donchian_20coin_trend_size_v2.py`
- Sau hunt `trend_size`: sách-PnL soft size không thắng D20 → thử boost cùng chiều, shrink chỉ khi lỗ, cap counter, breadth.

| Config | %/ngày | MaxDD | PF | A/C/N | pnlA/C | Note |
| --- | ---: | ---: | ---: | --- | --- | --- |
| `breadth_boost1.5` | **+113.353%** | **59.0%** | 1.49 | 8834/5778/2293 | +238846/+152577 | breadth; cùng ×1.5, ngược ×1.0 |
| `pnl2h_boost1.5` | **+85.814%** | **76.7%** | 1.49 | 7122/4067/5716 | +174231/+69776 | PnL2h; cùng ×1.5, ngược ×1.0 |
| `pnl2h_boost1.5_c0.75` | **+60.449%** | **76.9%** | 1.47 | 7079/4110/5716 | +122964/+44207 | PnL2h; cùng ×1.5, ngược ×0.75 |
| `mtm_green_boost1.5` | **+43.778%** | **49.6%** | 1.48 | 1685/1465/13755 | +20124/+14653 | MTM green; cùng ×1.5, ngược ×1.0 |
| `D20_base` | **+33.450%** | **49.9%** | 1.48 | 0/0/16905 | +0/+0 | baseline |
| `breadth_boost1.25_c0.5` | **+23.655%** | **38.8%** | 1.46 | 8834/5778/2293 | +59188/+22054 | breadth cùng ×1.25 ngược ×0.5 |
| `pnl2h_lose_x0.25` | **+16.783%** | **49.9%** | 1.47 | 7060/4129/5716 | +33163/+7329 | PnL2h; ngược ×0.25 chỉ khi MTM phía đó <0 |
| `pnl2h_x0.25_cap15` | **+14.090%** | **50.6%** | 1.45 | 7047/4142/5716 | +28941/+4277 | PnL2h ×0.25 + counter margin ≤15% eq |
| `mtm_lose_x0.25` | **+12.225%** | **45.8%** | 1.48 | 9747/5139/2019 | +32439/+4747 | MTM both; ngược ×0.25 chỉ khi phía đó lỗ |
| `breadth_x0.40` | **+10.370%** | **31.0%** | 1.44 | 8834/5778/2293 | +25915/+9390 | breadth ngược ×0.40 |
| `breadth_x0.35` | **+9.365%** | **29.4%** | 1.44 | 8834/5778/2293 | +24190/+7652 | breadth ngược ×0.35 |
| `breadth_lose_x0.25` | **+8.154%** | **26.3%** | 1.44 | 8834/5778/2293 | +21919/+5744 | breadth; ngược ×0.25 chỉ khi phía đó lỗ |

- Baseline: **+33.450%**/ngày, MaxDD **49.9%**

Paper only — không ảnh hưởng bot live.
