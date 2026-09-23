# sma34/144 v1.2 — alternate universe (disjoint from bot majors)

- Generated: **2026-09-18 17:46:45 +07**
- Script: `scripts/research_sma34_alt_universe.py`
- Mục tiêu: pool **không trùng** 20 majors bot chính, biên độ (ATR%/HL%) gần majors, backtest kỹ sma34/144 v1.2

## 1) Biên độ pool bot chính (365d)

- **15m** ATR% median: **0.467%** (P10–P90: 0.292–0.615)
- **1d** ATR% median (dùng screen): **5.71%** (P10–P90: 3.41–7.21)
- **1d** HL% median: **4.94%** (P10–P90: 3.26–6.25)

| Symbol | ATR% 15m | HL% 15m | RV ann% |
| --- | ---: | ---: | ---: |
| TRXUSDT | 0.139 | 0.128 | 23.9 |
| BTCUSDT | 0.270 | 0.242 | 44.8 |
| BNBUSDT | 0.295 | 0.269 | 54.8 |
| LTCUSDT | 0.377 | 0.346 | 71.9 |
| ETHUSDT | 0.380 | 0.335 | 62.4 |
| XRPUSDT | 0.390 | 0.358 | 70.6 |
| BCHUSDT | 0.424 | 0.374 | 70.5 |
| SOLUSDT | 0.445 | 0.400 | 71.0 |
| AVAXUSDT | 0.455 | 0.412 | 80.4 |
| LINKUSDT | 0.458 | 0.419 | 83.2 |
| XLMUSDT | 0.476 | 0.440 | 87.7 |
| ATOMUSDT | 0.477 | 0.444 | 78.7 |
| ADAUSDT | 0.521 | 0.477 | 89.5 |
| DOTUSDT | 0.540 | 0.495 | 99.7 |
| SUIUSDT | 0.555 | 0.500 | 94.5 |
| UNIUSDT | 0.585 | 0.535 | 107.7 |
| APTUSDT | 0.597 | 0.549 | 96.0 |
| ARBUSDT | 0.614 | 0.562 | 104.3 |
| OPUSDT | 0.627 | 0.580 | 109.6 |
| NEARUSDT | 0.664 | 0.617 | 104.0 |

## 2) Screening alt peers

- Screened top liquid USDT-M perps (exclude majors/stables/1000x)
- Amplitude filter vs majors band; pick top 20 by score+liquidity

### Selected pool

| Rank | Symbol | score↓ | ATR% (1d) | HL% (1d) | 24h quote $ |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | HBARUSDT | 0.055 | 5.27 | 4.62 | 38,503,574 |
| 2 | ETCUSDT | 0.060 | 5.23 | 4.64 | 30,087,713 |
| 3 | VTHOUSDT | 0.093 | 5.64 | 4.74 | 17,514,569 |
| 4 | CAKEUSDT | 0.094 | 4.98 | 4.74 | 26,252,067 |
| 5 | IOSTUSDT | 0.152 | 5.75 | 4.86 | 28,175,607 |
| 6 | DOGEUSDT | 0.181 | 5.69 | 4.99 | 424,315,450 |
| 7 | XMRUSDT | 0.189 | 5.36 | 5.28 | 52,819,090 |
| 8 | POLUSDT | 0.223 | 5.71 | 5.11 | 30,775,073 |
| 9 | TRUMPUSDT | 0.336 | 5.98 | 5.23 | 84,482,785 |
| 10 | LSKUSDT | 0.414 | 5.99 | 5.46 | 328,498,406 |
| 11 | GUSDT | 0.511 | 6.44 | 5.39 | 247,915,360 |
| 12 | SEIUSDT | 0.514 | 6.20 | 5.59 | 17,290,551 |
| 13 | KSMUSDT | 0.613 | 6.61 | 5.56 | 45,305,305 |
| 14 | ASTERUSDT | 0.627 | 6.41 | 5.77 | 55,016,055 |
| 15 | AVAUSDT | 0.653 | 6.38 | 5.87 | 322,175,478 |
| 16 | AAVEUSDT | 0.661 | 6.37 | 5.90 | 136,686,883 |
| 17 | ICPUSDT | 0.785 | 6.93 | 5.83 | 22,665,654 |
| 18 | SUSHIUSDT | 0.865 | 6.95 | 6.05 | 14,344,042 |
| 19 | FILUSDT | 0.927 | 7.16 | 6.07 | 103,065,336 |
| 20 | WLFIUSDT | 0.933 | 7.02 | 6.20 | 24,563,563 |

<details><summary>All amplitude-matched candidates</summary>

| Symbol | score | ATR% | HL% | 24h qv |
| --- | ---: | ---: | ---: | ---: |
| HBARUSDT | 0.055 | 5.27 | 4.62 | 38,503,574 |
| ETCUSDT | 0.060 | 5.23 | 4.64 | 30,087,713 |
| VTHOUSDT | 0.093 | 5.64 | 4.74 | 17,514,569 |
| CAKEUSDT | 0.094 | 4.98 | 4.74 | 26,252,067 |
| IOSTUSDT | 0.152 | 5.75 | 4.86 | 28,175,607 |
| DOGEUSDT | 0.181 | 5.69 | 4.99 | 424,315,450 |
| XMRUSDT | 0.189 | 5.36 | 5.28 | 52,819,090 |
| POLUSDT | 0.223 | 5.71 | 5.11 | 30,775,073 |
| TRUMPUSDT | 0.336 | 5.98 | 5.23 | 84,482,785 |
| LSKUSDT | 0.414 | 5.99 | 5.46 | 328,498,406 |
| GUSDT | 0.511 | 6.44 | 5.39 | 247,915,360 |
| SEIUSDT | 0.514 | 6.20 | 5.59 | 17,290,551 |
| KSMUSDT | 0.613 | 6.61 | 5.56 | 45,305,305 |
| ASTERUSDT | 0.627 | 6.41 | 5.77 | 55,016,055 |
| AVAUSDT | 0.653 | 6.38 | 5.87 | 322,175,478 |
| AAVEUSDT | 0.661 | 6.37 | 5.90 | 136,686,883 |
| ICPUSDT | 0.785 | 6.93 | 5.83 | 22,665,654 |
| SUSHIUSDT | 0.865 | 6.95 | 6.05 | 14,344,042 |
| FILUSDT | 0.927 | 7.16 | 6.07 | 103,065,336 |
| WLFIUSDT | 0.933 | 7.02 | 6.20 | 24,563,563 |
| ONDOUSDT | 0.938 | 6.87 | 6.33 | 191,724,887 |
| GALAUSDT | 0.976 | 7.13 | 6.24 | 31,481,859 |
| RENDERUSDT | 1.054 | 7.23 | 6.40 | 21,519,627 |
| RAYSOLUSDT | 1.062 | 7.03 | 6.58 | 71,181,784 |
| FFUSDT | 1.124 | 7.16 | 6.66 | 16,210,067 |
| CRVUSDT | 1.153 | 7.40 | 6.56 | 36,783,476 |
| HYPEUSDT | 1.169 | 7.18 | 6.78 | 954,208,864 |
| LDOUSDT | 1.207 | 7.47 | 6.66 | 17,454,287 |
| ONEUSDT | 1.209 | 7.73 | 6.47 | 764,349,612 |
| MORPHOUSDT | 1.210 | 7.50 | 6.65 | 15,563,501 |
| ARUSDT | 1.238 | 7.55 | 6.69 | 15,366,708 |
| INJUSDT | 1.281 | 7.54 | 6.83 | 58,573,648 |
| TAOUSDT | 1.298 | 7.70 | 6.76 | 129,098,945 |
| JUPUSDT | 1.322 | 7.60 | 6.91 | 32,676,499 |
| STRKUSDT | 1.338 | 7.77 | 6.82 | 38,933,238 |
| BABYUSDT | 1.338 | 7.94 | 6.68 | 19,426,399 |
| COTIUSDT | 1.342 | 7.81 | 6.80 | 173,395,192 |
| FETUSDT | 1.356 | 7.75 | 6.89 | 58,037,963 |
| PENDLEUSDT | 1.425 | 7.66 | 7.16 | 30,188,693 |
| ZENUSDT | 1.448 | 7.88 | 7.06 | 33,180,317 |
| WIFUSDT | 1.481 | 8.19 | 6.92 | 16,846,683 |
| TIAUSDT | 1.502 | 8.17 | 7.00 | 36,530,975 |
| STGUSDT | 1.518 | 8.15 | 7.06 | 31,086,920 |
| ZROUSDT | 1.588 | 8.11 | 7.30 | 30,847,482 |
| ETHFIUSDT | 1.717 | 8.18 | 7.63 | 30,805,142 |
| WLDUSDT | 1.732 | 8.27 | 7.61 | 215,418,767 |
| PENGUUSDT | 1.780 | 8.40 | 7.65 | 53,946,737 |
| DASHUSDT | 1.847 | 8.81 | 7.53 | 136,291,890 |
| REZUSDT | 1.849 | 8.62 | 7.68 | 24,292,692 |
| AEROUSDT | 1.866 | 8.55 | 7.79 | 31,562,766 |
| ENAUSDT | 1.893 | 8.71 | 7.74 | 270,127,173 |

</details>

## 3) Backtest sma34/144 v1.2 (shared wallet $1k · 10x · fee 0.04%)

Logic identical to majors detail report: SMA34/144 trend + wick bounce + vol≥1.2 + soft Donchian TP.

### Summary vs majors baseline

| Window | Pool | n_syms | Return | PF | WR | MaxDD | t/d | Final $ |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 90d | **ALT** | 20 | +23.6% | 1.1955 | 74.1% | 32.1% | 15.71 | $1236 |
| 90d | majors | 20 | +48.3% | 2.0459 | 77.0% | 3.3% | 14.18 | $1483 |
| 180d | **ALT** | 20 | +72.1% | 1.3053 | 74.2% | 32.1% | 15.57 | $1721 |
| 180d | majors | 20 | +90.2% | 1.8343 | 76.7% | 7.8% | 14.04 | $1902 |
| 365d | **ALT** | 20 | +194.9% | 1.3169 | 73.8% | 32.1% | 15.20 | $2949 |
| 365d | majors | 20 | +244.8% | 1.6748 | 76.1% | 13.0% | 13.70 | $3448 |

## Window 90d · ALT pool · thực tế **90d** · **20 coin**

- Eval: `2026-06-20 17:15 +07` → `2026-09-18 17:15 +07`
- Pool: `HBARUSDT, ETCUSDT, VTHOUSDT, CAKEUSDT, IOSTUSDT, DOGEUSDT, XMRUSDT, POLUSDT, TRUMPUSDT, LSKUSDT, GUSDT, SEIUSDT, KSMUSDT, ASTERUSDT, AVAUSDT, AAVEUSDT, ICPUSDT, SUSHIUSDT, FILUSDT, WLFIUSDT`

### Hiệu quả

| Metric | Value |
| --- | ---: |
| Return | +23.6% |
| %/ngày | +0.262% |
| PF | 1.1955 |
| WR | 74.1% |
| MaxDD | 32.1% |
| Final equity | $1236 |
| Net PnL | $+236 |
| Trades | 1414 |
| Trades / ngày | **15.71** |
| Long / Short | 658 / 756 |
| Long PnL / Short PnL | $+390 / $-154 |
| Avg win / Avg loss | $+1.38 / $-3.30 |
| Hold TB / median (giờ) | 5.1h / 3.2h |
| Exit TP / EOD | 1410 / 4 |

### Vốn đang dùng

| Metric | Value |
| --- | ---: |
| Open TB / P50 / P95 / max | 3.15 / 3 / 7 / 10 |
| Margin book TB | $43 |
| Margin book P95 | $101 |

### Theo coin (sort net PnL)

| Symbol | Trades | t/d | WR | PF | Net $ | L/S | Hold TB h |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| VTHOUSDT | 70 | 0.78 | 71.4% | 1.92 | +69 | 45/25 | 5.5 |
| FILUSDT | 71 | 0.79 | 71.8% | 3.55 | +63 | 26/45 | 4.9 |
| AAVEUSDT | 73 | 0.81 | 75.3% | 2.32 | +59 | 41/32 | 4.9 |
| POLUSDT | 70 | 0.78 | 75.7% | 3.12 | +48 | 30/40 | 4.8 |
| DOGEUSDT | 81 | 0.90 | 72.8% | 3.06 | +42 | 32/49 | 4.7 |
| AVAUSDT | 71 | 0.79 | 74.6% | 2.17 | +38 | 33/38 | 4.8 |
| GUSDT | 82 | 0.91 | 78.0% | 1.78 | +36 | 46/36 | 4.5 |
| HBARUSDT | 62 | 0.69 | 79.0% | 2.48 | +30 | 22/40 | 5.0 |
| SUSHIUSDT | 56 | 0.62 | 75.0% | 1.75 | +25 | 29/27 | 5.0 |
| ASTERUSDT | 74 | 0.82 | 67.6% | 2.49 | +24 | 40/34 | 4.9 |
| TRUMPUSDT | 77 | 0.86 | 72.7% | 1.51 | +23 | 29/48 | 4.9 |
| ICPUSDT | 74 | 0.82 | 75.7% | 1.40 | +23 | 32/42 | 4.6 |
| WLFIUSDT | 68 | 0.76 | 75.0% | 2.43 | +21 | 22/46 | 5.7 |
| KSMUSDT | 80 | 0.89 | 76.2% | 1.19 | +13 | 42/38 | 5.9 |
| SEIUSDT | 67 | 0.74 | 67.2% | 1.15 | +5 | 28/39 | 5.9 |
| XMRUSDT | 66 | 0.73 | 62.1% | 0.83 | -7 | 34/32 | 6.0 |
| ETCUSDT | 66 | 0.73 | 77.3% | 0.85 | -8 | 27/39 | 5.4 |
| CAKEUSDT | 55 | 0.61 | 76.4% | 0.70 | -18 | 27/28 | 5.1 |
| LSKUSDT | 67 | 0.74 | 73.1% | 0.73 | -44 | 34/33 | 5.2 |
| IOSTUSDT | 84 | 0.93 | 83.3% | 0.38 | -206 | 39/45 | 3.9 |

## Window 180d · ALT pool · thực tế **180d** · **20 coin**

- Eval: `2026-03-22 17:15 +07` → `2026-09-18 17:15 +07`
- Pool: `HBARUSDT, ETCUSDT, VTHOUSDT, CAKEUSDT, IOSTUSDT, DOGEUSDT, XMRUSDT, POLUSDT, TRUMPUSDT, LSKUSDT, GUSDT, SEIUSDT, KSMUSDT, ASTERUSDT, AVAUSDT, AAVEUSDT, ICPUSDT, SUSHIUSDT, FILUSDT, WLFIUSDT`

### Hiệu quả

| Metric | Value |
| --- | ---: |
| Return | +72.1% |
| %/ngày | +0.401% |
| PF | 1.3053 |
| WR | 74.2% |
| MaxDD | 32.1% |
| Final equity | $1721 |
| Net PnL | $+721 |
| Trades | 2802 |
| Trades / ngày | **15.57** |
| Long / Short | 1275 / 1527 |
| Long PnL / Short PnL | $+615 / $+107 |
| Avg win / Avg loss | $+1.48 / $-3.27 |
| Hold TB / median (giờ) | 5.0h / 3.0h |
| Exit TP / EOD | 2798 / 4 |

### Vốn đang dùng

| Metric | Value |
| --- | ---: |
| Open TB / P50 / P95 / max | 3.10 / 3 / 7 / 10 |
| Margin book TB | $49 |
| Margin book P95 | $117 |

### Theo coin (sort net PnL)

| Symbol | Trades | t/d | WR | PF | Net $ | L/S | Hold TB h |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| VTHOUSDT | 152 | 0.84 | 73.7% | 2.02 | +128 | 86/66 | 4.8 |
| AAVEUSDT | 137 | 0.76 | 75.2% | 1.89 | +99 | 68/69 | 5.2 |
| FILUSDT | 142 | 0.79 | 72.5% | 2.19 | +92 | 59/83 | 4.9 |
| GUSDT | 164 | 0.91 | 79.3% | 1.95 | +92 | 87/77 | 4.3 |
| WLFIUSDT | 143 | 0.79 | 76.2% | 2.70 | +83 | 56/87 | 5.2 |
| AVAUSDT | 142 | 0.79 | 73.9% | 2.01 | +81 | 69/73 | 5.2 |
| DOGEUSDT | 153 | 0.85 | 71.2% | 2.09 | +63 | 69/84 | 5.4 |
| TRUMPUSDT | 141 | 0.78 | 71.6% | 1.68 | +62 | 48/93 | 5.1 |
| POLUSDT | 130 | 0.72 | 71.5% | 1.68 | +55 | 61/69 | 5.7 |
| ICPUSDT | 134 | 0.74 | 79.1% | 1.48 | +52 | 57/77 | 4.6 |
| ASTERUSDT | 148 | 0.82 | 71.6% | 2.06 | +52 | 63/85 | 4.8 |
| XMRUSDT | 147 | 0.82 | 72.8% | 1.59 | +49 | 70/77 | 4.9 |
| KSMUSDT | 150 | 0.83 | 76.0% | 1.37 | +49 | 73/77 | 5.6 |
| HBARUSDT | 114 | 0.63 | 73.7% | 1.55 | +35 | 46/68 | 5.1 |
| SUSHIUSDT | 133 | 0.74 | 70.7% | 1.27 | +29 | 60/73 | 5.4 |
| CAKEUSDT | 117 | 0.65 | 75.2% | 1.07 | +6 | 56/61 | 4.8 |
| SEIUSDT | 128 | 0.71 | 68.0% | 1.00 | -0 | 53/75 | 5.5 |
| ETCUSDT | 128 | 0.71 | 71.1% | 0.82 | -20 | 60/68 | 5.6 |
| LSKUSDT | 142 | 0.79 | 75.4% | 0.86 | -34 | 65/77 | 4.9 |
| IOSTUSDT | 157 | 0.87 | 82.8% | 0.47 | -252 | 69/88 | 4.0 |

## Window 365d · ALT pool · thực tế **364d** · **20 coin**

- Eval: `2025-09-19 19:00 +07` → `2026-09-18 17:15 +07`
- Pool: `HBARUSDT, ETCUSDT, VTHOUSDT, CAKEUSDT, IOSTUSDT, DOGEUSDT, XMRUSDT, POLUSDT, TRUMPUSDT, LSKUSDT, GUSDT, SEIUSDT, KSMUSDT, ASTERUSDT, AVAUSDT, AAVEUSDT, ICPUSDT, SUSHIUSDT, FILUSDT, WLFIUSDT`

### Hiệu quả

| Metric | Value |
| --- | ---: |
| Return | +194.9% |
| %/ngày | +0.536% |
| PF | 1.3169 |
| WR | 73.8% |
| MaxDD | 32.1% |
| Final equity | $2949 |
| Net PnL | $+1949 |
| Trades | 5533 |
| Trades / ngày | **15.20** |
| Long / Short | 2415 / 3118 |
| Long PnL / Short PnL | $+1196 / $+753 |
| Avg win / Avg loss | $+1.98 / $-4.25 |
| Hold TB / median (giờ) | 5.0h / 3.2h |
| Exit TP / EOD | 5529 / 4 |

### Vốn đang dùng

| Metric | Value |
| --- | ---: |
| Open TB / P50 / P95 / max | 3.03 / 3 / 7 / 10 |
| Margin book TB | $62 |
| Margin book P95 | $168 |

### Theo coin (sort net PnL)

| Symbol | Trades | t/d | WR | PF | Net $ | L/S | Hold TB h |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GUSDT | 298 | 0.82 | 77.5% | 2.44 | +313 | 158/140 | 4.4 |
| WLFIUSDT | 287 | 0.79 | 77.0% | 2.85 | +271 | 114/173 | 4.8 |
| VTHOUSDT | 306 | 0.84 | 74.2% | 1.88 | +269 | 147/159 | 4.8 |
| ASTERUSDT | 284 | 0.78 | 75.7% | 2.23 | +232 | 123/161 | 4.9 |
| TRUMPUSDT | 289 | 0.79 | 74.4% | 1.76 | +174 | 97/192 | 4.8 |
| POLUSDT | 275 | 0.76 | 75.6% | 1.78 | +172 | 122/153 | 5.1 |
| XMRUSDT | 299 | 0.82 | 71.9% | 1.66 | +145 | 144/155 | 4.9 |
| ICPUSDT | 257 | 0.71 | 78.6% | 1.46 | +138 | 102/155 | 4.8 |
| AAVEUSDT | 252 | 0.69 | 73.4% | 1.38 | +129 | 128/124 | 5.2 |
| HBARUSDT | 255 | 0.70 | 73.7% | 1.73 | +126 | 100/155 | 5.1 |
| FILUSDT | 267 | 0.73 | 72.3% | 1.43 | +121 | 108/159 | 5.0 |
| DOGEUSDT | 294 | 0.81 | 70.4% | 1.52 | +119 | 119/175 | 5.5 |
| AVAUSDT | 289 | 0.79 | 74.4% | 1.38 | +116 | 140/149 | 5.0 |
| SEIUSDT | 262 | 0.72 | 72.5% | 1.17 | +43 | 109/153 | 4.9 |
| SUSHIUSDT | 244 | 0.67 | 69.7% | 1.14 | +40 | 110/134 | 5.6 |
| KSMUSDT | 283 | 0.78 | 72.4% | 1.10 | +39 | 123/160 | 5.6 |
| CAKEUSDT | 250 | 0.69 | 73.6% | 1.06 | +17 | 109/141 | 5.0 |
| ETCUSDT | 256 | 0.70 | 71.1% | 1.05 | +12 | 110/146 | 5.4 |
| LSKUSDT | 286 | 0.79 | 73.4% | 0.92 | -46 | 132/154 | 4.8 |
| IOSTUSDT | 300 | 0.82 | 73.7% | 0.52 | -478 | 120/180 | 5.1 |

## Kết luận vận hành song song

- ALT pool **disjoint** majors → tránh netting cùng symbol trên cùng account hedge mode.
- Vẫn nên sub-account / wallet riêng nếu chạy 2 bot (shared risk + dashboard tách).
- Chọn ALT nếu PF/MaxDD/t-d ổn định trên ≥2 cửa sổ và per-coin không bị 1–2 coin kéo cả pool.


## 4) Refined quality pool (exclude meme/toxic)

- Quality midcaps: `HBARUSDT, ETCUSDT, CAKEUSDT, DOGEUSDT, XMRUSDT, POLUSDT, SEIUSDT, AAVEUSDT, ICPUSDT, SUSHIUSDT, FILUSDT, ONDOUSDT, GALAUSDT, RENDERUSDT, CRVUSDT, HYPEUSDT`

| Window | Pool | Return | PF | WR | MaxDD | t/d |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 90d | Q16 | +36.7% | 1.6152 | 74.1% | 7.3% | 11.54 |
| 180d | Q16 | +68.9% | 1.5256 | 73.9% | 10.9% | 11.67 |
| 365d | Q16 | +137.3% | 1.4166 | 73.9% | 28.6% | 11.61 |
| 90d_refined | R16 | +36.7% | 1.6152 | 74.1% | 7.3% | 11.54 |
| 180d_refined | R16 | +68.9% | 1.5256 | 73.9% | 10.9% | 11.67 |
| 365d_refined | R16 | +137.3% | 1.4166 | 73.9% | 28.6% | 11.61 |

### Q16 · 365d per coin

| Symbol | n | WR | PF | Net $ |
| --- | ---: | ---: | ---: | ---: |
| CRVUSDT | 311 | 78.1% | 1.79 | +171 |
| ONDOUSDT | 249 | 75.5% | 2.10 | +170 |
| POLUSDT | 278 | 75.9% | 1.76 | +134 |
| RENDERUSDT | 234 | 76.5% | 1.70 | +122 |
| XMRUSDT | 299 | 71.9% | 1.71 | +120 |
| ICPUSDT | 257 | 78.6% | 1.44 | +104 |
| HBARUSDT | 259 | 74.1% | 1.71 | +100 |
| DOGEUSDT | 297 | 70.7% | 1.44 | +85 |
| AAVEUSDT | 253 | 73.5% | 1.30 | +84 |
| FILUSDT | 271 | 72.3% | 1.36 | +83 |
| GALAUSDT | 273 | 74.0% | 1.26 | +63 |
| HYPEUSDT | 247 | 74.9% | 1.15 | +40 |
| SEIUSDT | 263 | 72.6% | 1.17 | +34 |
| SUSHIUSDT | 244 | 69.7% | 1.14 | +30 |
| CAKEUSDT | 248 | 73.4% | 1.10 | +21 |
| ETCUSDT | 256 | 70.7% | 1.07 | +13 |

### Refined R16 · 365d

- Pool: `HBARUSDT, ETCUSDT, CAKEUSDT, DOGEUSDT, XMRUSDT, POLUSDT, SEIUSDT, AAVEUSDT, ICPUSDT, SUSHIUSDT, FILUSDT, ONDOUSDT, GALAUSDT, RENDERUSDT, CRVUSDT, HYPEUSDT`

| Symbol | n | WR | PF | Net $ |
| --- | ---: | ---: | ---: | ---: |
| CRVUSDT | 311 | 78.1% | 1.79 | +171 |
| ONDOUSDT | 249 | 75.5% | 2.10 | +170 |
| POLUSDT | 278 | 75.9% | 1.76 | +134 |
| RENDERUSDT | 234 | 76.5% | 1.70 | +122 |
| XMRUSDT | 299 | 71.9% | 1.71 | +120 |
| ICPUSDT | 257 | 78.6% | 1.44 | +104 |
| HBARUSDT | 259 | 74.1% | 1.71 | +100 |
| DOGEUSDT | 297 | 70.7% | 1.44 | +85 |
| AAVEUSDT | 253 | 73.5% | 1.30 | +84 |
| FILUSDT | 271 | 72.3% | 1.36 | +83 |
| GALAUSDT | 273 | 74.0% | 1.26 | +63 |
| HYPEUSDT | 247 | 74.9% | 1.15 | +40 |
| SEIUSDT | 263 | 72.6% | 1.17 | +34 |
| SUSHIUSDT | 244 | 69.7% | 1.14 | +30 |
| CAKEUSDT | 248 | 73.4% | 1.10 | +21 |
| ETCUSDT | 256 | 70.7% | 1.07 | +13 |

### Verdict

- Raw amplitude-matched 20: PF~1.32 MaxDD~32% (IOST kéo DD).
- Quality midcaps: PF=1.42 MaxDD=28.6%.
- Refined drop losers: PF=1.42 MaxDD=28.6% — vẫn dưới majors (PF 1.67 / DD 13%).
- Song song bot chính: dùng **refined quality pool** + sub-account; kỳ vọng PF/DD kém majors nhưng tránh netting.


## 5) Best-12 amplitude peers (by Q16 365d PnL)

- Pool: `CRVUSDT, ONDOUSDT, POLUSDT, RENDERUSDT, XMRUSDT, ICPUSDT, HBARUSDT, DOGEUSDT, AAVEUSDT, FILUSDT, GALAUSDT, SEIUSDT`

| Window | Return | PF | WR | MaxDD | t/d | vs majors PF/DD |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 90d | +39.0% | 1.9983 | 74.1% | 3.1% | 9.00 | majors 2.05/3.3% |
| 180d | +68.4% | 1.7677 | 74.4% | 8.4% | 8.95 | majors 1.83/7.8% |
| 365d | +117.3% | 1.5208 | 74.5% | 27.4% | 8.93 | majors 1.67/13.0% |

**Khuyến nghị song song:** dùng Best-12 (hoặc Q16) trên sub-account riêng; không trùng 20 majors.


## 6) Long windows (1y / 3y / 5y / max)

Xem báo cáo đầy đủ: [`research_sma34_best12_long_windows.md`](research_sma34_best12_long_windows.md)
