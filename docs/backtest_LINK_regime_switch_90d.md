# Regime switch — 90 ngày

- Cửa sổ: 2026-05-20 14:10 → 2026-08-18 14:10

## Tổng hợp

- Giá: 9.6030 → 9.4250 (-1.85%)
- Nến TREND_UP / TREND_DOWN / NO_TREND: 3708 / 4171 / 18041

| Mode | Cap lot | Vốn cuối | PnL | % | Round | WR | Peak lot | Max DD | Phí | Skip cap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Regime switch (1 chiều theo 3TF) | 5 | **1008.23** | +8.23 | +0.8% | 6 | 100% | L5/S5 | -5 | 0.1 | 3741 |
| Regime switch (1 chiều theo 3TF) | 10 | **1013.09** | +13.09 | +1.3% | 6 | 100% | L10/S10 | -8 | 0.2 | 1403 |
| Long-only (TREND_UP → exit TREND_DOWN) | 5 | **1004.14** | +4.14 | +0.4% | 3 | 100% | L5/S0 | -3 | 0.1 | 1756 |
| Long-only (TREND_UP → exit TREND_DOWN) | 10 | **1006.84** | +6.84 | +0.7% | 3 | 100% | L10/S0 | -4 | 0.1 | 518 |
| Short-only (TREND_DOWN → exit TREND_UP) | 5 | **1004.08** | +4.08 | +0.4% | 3 | 100% | L0/S5 | -4 | 0.1 | 1985 |
| Short-only (TREND_DOWN → exit TREND_UP) | 10 | **1006.21** | +6.21 | +0.6% | 3 | 100% | L0/S10 | -6 | 0.1 | 885 |

## Regime switch (1 chiều theo 3TF) — cap 5 lot

- Long add 15 (skip 0) · Short add 15 (skip 14)
- Peak lot long 5 / short 5 · peak notional 25 · margin 3
- Equity min/max 999/1009 · MTM tệ nhất -1

| # | Side | Lots | Avg | Exit | PnL | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | short | 5 | 9.5662 | 8.2110 | +3.5229 | TREND_UP |
| 2 | long | 5 | 8.2056 | 7.8770 | -1.0243 | TREND_DOWN |
| 3 | short | 5 | 7.8990 | 7.7430 | +0.4751 | TREND_UP |
| 4 | long | 5 | 7.7330 | 8.3320 | +1.9214 | TREND_DOWN |
| 5 | short | 5 | 8.3558 | 8.3230 | +0.0785 | TREND_UP |
| 6 | long | 5 | 8.3358 | 9.4250 | +3.2614 | EOD_OPEN |

## Regime switch (1 chiều theo 3TF) — cap 10 lot

- Long add 26 (skip 1227) · Short add 27 (skip 1102)
- Peak lot long 10 / short 10 · peak notional 50 · margin 5
- Equity min/max 998/1015 · MTM tệ nhất -3

| # | Side | Lots | Avg | Exit | PnL | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | short | 7 | 9.5794 | 8.2110 | +4.9733 | TREND_UP |
| 2 | long | 10 | 8.1970 | 7.8770 | -2.0008 | TREND_DOWN |
| 3 | short | 10 | 7.9087 | 7.7430 | +1.0107 | TREND_UP |
| 4 | long | 6 | 7.7323 | 8.3320 | +2.3108 | TREND_DOWN |
| 5 | short | 10 | 8.3670 | 8.3230 | +0.2243 | TREND_UP |
| 6 | long | 10 | 8.3299 | 9.4250 | +6.5730 | EOD_OPEN |

## Long-only (TREND_UP → exit TREND_DOWN) — cap 5 lot

- Long add 15 (skip 0) · Short add 0 (skip 0)
- Peak lot long 5 / short 0 · peak notional 25 · margin 3
- Equity min/max 998/1005 · MTM tệ nhất -1

| # | Side | Lots | Avg | Exit | PnL | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | long | 5 | 8.2056 | 7.8770 | -1.0207 | TREND_DOWN |
| 2 | long | 5 | 7.7330 | 8.3320 | +1.9138 | TREND_DOWN |
| 3 | long | 5 | 8.3358 | 9.4250 | +3.2482 | EOD_OPEN |

## Long-only (TREND_UP → exit TREND_DOWN) — cap 10 lot

- Long add 26 (skip 1227) · Short add 0 (skip 0)
- Peak lot long 10 / short 0 · peak notional 50 · margin 5
- Equity min/max 997/1009 · MTM tệ nhất -3

| # | Side | Lots | Avg | Exit | PnL | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | long | 10 | 8.1970 | 7.8770 | -1.9909 | TREND_DOWN |
| 2 | long | 6 | 7.7323 | 8.3320 | +2.2970 | TREND_DOWN |
| 3 | long | 10 | 8.3299 | 9.4250 | +6.5324 | EOD_OPEN |

## Short-only (TREND_DOWN → exit TREND_UP) — cap 5 lot

- Long add 0 (skip 0) · Short add 15 (skip 14)
- Peak lot long 0 / short 5 · peak notional 25 · margin 3
- Equity min/max 999/1007 · MTM tệ nhất -1

| # | Side | Lots | Avg | Exit | PnL | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | short | 5 | 9.5662 | 8.2110 | +3.5229 | TREND_UP |
| 2 | short | 5 | 7.8990 | 7.7430 | +0.4755 | TREND_UP |
| 3 | short | 5 | 8.3558 | 8.3230 | +0.0785 | TREND_UP |

## Short-only (TREND_DOWN → exit TREND_UP) — cap 10 lot

- Long add 0 (skip 0) · Short add 27 (skip 1102)
- Peak lot long 0 / short 10 · peak notional 50 · margin 5
- Equity min/max 998/1011 · MTM tệ nhất -2

| # | Side | Lots | Avg | Exit | PnL | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | short | 7 | 9.5794 | 8.2110 | +4.9733 | TREND_UP |
| 2 | short | 10 | 7.9087 | 7.7430 | +1.0127 | TREND_UP |
| 3 | short | 10 | 8.3670 | 8.3230 | +0.2242 | TREND_UP |

