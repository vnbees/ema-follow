# sma34/144 v1.2 — báo cáo chi tiết

- Generated: **2026-09-18 17:20:29 +07**
- Script research (không sửa bot live): `scripts/research_beat_config_a_r4_sma.py`

## Logic

1. **Trend:** SMA(34) > SMA(144) → chỉ long; ngược lại → chỉ short
2. **Entry (15m):** wick chạm SMA34 + close bounce đúng phía + nến cùng chiều
3. **Lọc:** `vol_ratio ≥ 1.2` (quote volume / SMA20)
4. **TP soft:** chạm Donchian(20) band theo hướng (không hard SL)
5. **Rank:** sort `pot_rr` desc → mở tối đa **top_k=5**/bar · **max_open=10** · 1 vị thế/coin

## Cách sử dụng vốn (giống family Config A)

- Vốn ban đầu paper: **$1000** (shared wallet toàn pool)
- Leverage: **10x** · Fee: **0.04%/side** (trừ trong PnL)
- Margin mỗi lệnh: `min(equity × 1% × size_mult, equity × 15%, cash)`
- `size_mult = min(pot_rr, 2.0)` — RR tiềm năng cao → size lớn hơn (cap 2×)
- Notional ≈ margin × 10 · Compound: lời/lỗ quay lại equity
- **Không** rút skim trong backtest này

## Cửa sổ 365d · thực tế **365d** · **20 coin**

- Eval: `2025-09-16 12:15 +07` → `2026-09-16 12:15 +07`
- Pool: `BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, TRXUSDT, ADAUSDT, AVAXUSDT, DOTUSDT, LINKUSDT, LTCUSDT, BCHUSDT, XLMUSDT, ATOMUSDT, NEARUSDT, APTUSDT, SUIUSDT, ARBUSDT, OPUSDT, UNIUSDT`

### Hiệu quả

| Metric | Value |
| --- | ---: |
| Return | +244.8% |
| %/ngày (trên vốn gốc) | +0.671% |
| PF | 1.6748 |
| WR | 76.1% |
| MaxDD | 13.0% |
| Final equity | $3448 |
| Net PnL | $+2448 |
| Trades | 4999 |
| **Trades / ngày (t/d)** | **13.70** |
| Long / Short | 2154 / 2845 |
| Long PnL / Short PnL | $+881 / $+1567 |
| Avg win / Avg loss | $+1.60 / $-3.04 |
| Hold TB / median (giờ) | 4.8h / 2.8h |
| Exit TP / EOD | 4994 / 5 |

### Vốn đang dùng (trung bình khi đang trade)

| Metric | Value |
| --- | ---: |
| Số lệnh mở TB / P50 / P95 / max | 2.71 / 2 / 7 / 10 |
| Margin book TB (tổng ký quỹ đang lock) | $53 |
| Margin book P95 | $151 |
| Margin book / equity TB | 2.9% |
| Margin / lệnh (TB lúc vào) | $18.6 |
| Notional / lệnh TB (margin×10) | $186 |
| pot_rr TB / size_mult TB | 1.08 / 1.01 |

### Theo coin (sort net PnL)

| Symbol | Trades | t/d | WR | PF | Net $ | L/S | Hold TB h |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OPUSDT | 240 | 0.66 | 81.2% | 2.61 | +278 | 100/140 | 4.2 |
| APTUSDT | 245 | 0.67 | 77.6% | 2.08 | +196 | 106/139 | 5.0 |
| NEARUSDT | 245 | 0.67 | 79.6% | 1.88 | +180 | 107/138 | 4.6 |
| DOTUSDT | 245 | 0.67 | 80.8% | 2.01 | +174 | 97/148 | 4.4 |
| SUIUSDT | 276 | 0.76 | 78.3% | 1.80 | +173 | 104/172 | 4.7 |
| LTCUSDT | 266 | 0.73 | 81.2% | 2.22 | +158 | 113/153 | 4.3 |
| ARBUSDT | 247 | 0.68 | 77.7% | 1.55 | +157 | 102/145 | 4.6 |
| SOLUSDT | 265 | 0.73 | 74.3% | 1.79 | +144 | 130/135 | 4.7 |
| LINKUSDT | 260 | 0.71 | 76.5% | 1.73 | +144 | 109/151 | 4.6 |
| XRPUSDT | 231 | 0.63 | 77.1% | 2.04 | +119 | 102/129 | 4.6 |
| ETHUSDT | 255 | 0.70 | 72.5% | 1.75 | +115 | 122/133 | 5.1 |
| AVAXUSDT | 244 | 0.67 | 74.2% | 1.56 | +95 | 115/129 | 5.1 |
| BCHUSDT | 259 | 0.71 | 75.7% | 1.53 | +95 | 107/152 | 5.0 |
| UNIUSDT | 270 | 0.74 | 73.3% | 1.27 | +85 | 107/163 | 5.0 |
| XLMUSDT | 258 | 0.71 | 73.6% | 1.37 | +83 | 102/156 | 5.0 |
| ADAUSDT | 245 | 0.67 | 78.4% | 1.36 | +79 | 90/155 | 4.4 |
| ATOMUSDT | 248 | 0.68 | 73.4% | 1.30 | +68 | 102/146 | 5.0 |
| BNBUSDT | 240 | 0.66 | 76.2% | 1.69 | +67 | 109/131 | 4.8 |
| BTCUSDT | 239 | 0.65 | 71.5% | 1.35 | +39 | 118/121 | 4.7 |
| TRXUSDT | 221 | 0.61 | 67.9% | 0.98 | -1 | 112/109 | 5.1 |

## Cửa sổ 1095d · thực tế **1095d** · **20 coin**

- Eval: `2023-09-17 12:15 +07` → `2026-09-16 12:15 +07`
- Pool: `BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, TRXUSDT, ADAUSDT, AVAXUSDT, DOTUSDT, LINKUSDT, LTCUSDT, BCHUSDT, XLMUSDT, ATOMUSDT, NEARUSDT, APTUSDT, SUIUSDT, ARBUSDT, OPUSDT, UNIUSDT`

### Hiệu quả

| Metric | Value |
| --- | ---: |
| Return | +4358.1% |
| %/ngày (trên vốn gốc) | +3.980% |
| PF | 1.5823 |
| WR | 76.0% |
| MaxDD | 14.4% |
| Final equity | $44581 |
| Net PnL | $+43581 |
| Trades | 14613 |
| **Trades / ngày (t/d)** | **13.35** |
| Long / Short | 6937 / 7676 |
| Long PnL / Short PnL | $+17723 / $+25858 |
| Avg win / Avg loss | $+10.66 / $-21.33 |
| Hold TB / median (giờ) | 4.8h / 2.8h |
| Exit TP / EOD | 14608 / 5 |

### Vốn đang dùng (trung bình khi đang trade)

| Metric | Value |
| --- | ---: |
| Số lệnh mở TB / P50 / P95 / max | 2.66 / 2 / 7 / 10 |
| Margin book TB (tổng ký quỹ đang lock) | $327 |
| Margin book P95 | $1313 |
| Margin book / equity TB | 2.9% |
| Margin / lệnh (TB lúc vào) | $116.4 |
| Notional / lệnh TB (margin×10) | $1164 |
| pot_rr TB / size_mult TB | 1.07 / 1.02 |

### Theo coin (sort net PnL)

| Symbol | Trades | t/d | WR | PF | Net $ | L/S | Hold TB h |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| OPUSDT | 718 | 0.66 | 79.0% | 2.10 | +4387 | 329/389 | 4.5 |
| APTUSDT | 771 | 0.70 | 78.0% | 1.98 | +3750 | 353/418 | 4.8 |
| NEARUSDT | 756 | 0.69 | 79.5% | 1.90 | +3673 | 365/391 | 4.4 |
| SUIUSDT | 728 | 0.66 | 76.9% | 1.73 | +3168 | 340/388 | 5.0 |
| SOLUSDT | 719 | 0.66 | 76.6% | 1.81 | +2914 | 349/370 | 4.5 |
| DOTUSDT | 717 | 0.65 | 77.1% | 1.76 | +2740 | 319/398 | 4.7 |
| LTCUSDT | 797 | 0.73 | 77.7% | 1.83 | +2579 | 396/401 | 4.5 |
| ARBUSDT | 691 | 0.63 | 77.7% | 1.46 | +2499 | 284/407 | 4.7 |
| XRPUSDT | 692 | 0.63 | 78.6% | 1.94 | +2462 | 325/367 | 4.5 |
| AVAXUSDT | 679 | 0.62 | 78.2% | 1.60 | +2036 | 322/357 | 4.6 |
| LINKUSDT | 739 | 0.67 | 76.9% | 1.45 | +1964 | 365/374 | 4.6 |
| XLMUSDT | 786 | 0.72 | 74.9% | 1.44 | +1844 | 351/435 | 4.8 |
| UNIUSDT | 762 | 0.70 | 75.9% | 1.29 | +1715 | 348/414 | 4.9 |
| ETHUSDT | 720 | 0.66 | 73.8% | 1.50 | +1706 | 364/356 | 5.2 |
| BCHUSDT | 771 | 0.70 | 74.3% | 1.44 | +1666 | 332/439 | 5.1 |
| ATOMUSDT | 752 | 0.69 | 75.8% | 1.38 | +1627 | 351/401 | 4.9 |
| ADAUSDT | 710 | 0.65 | 76.3% | 1.29 | +1328 | 326/384 | 4.8 |
| BNBUSDT | 735 | 0.67 | 73.1% | 1.38 | +893 | 386/349 | 4.9 |
| BTCUSDT | 715 | 0.65 | 71.2% | 1.39 | +825 | 375/340 | 4.9 |
| TRXUSDT | 655 | 0.60 | 67.8% | 0.89 | -195 | 357/298 | 5.5 |

## Cửa sổ 1825d · thực tế **1825d** · **16 coin**

- Eval: `2021-09-17 12:15 +07` → `2026-09-16 12:15 +07`
- Pool: `BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, TRXUSDT, ADAUSDT, AVAXUSDT, DOTUSDT, LINKUSDT, LTCUSDT, BCHUSDT, XLMUSDT, ATOMUSDT, NEARUSDT, UNIUSDT`

### Hiệu quả

| Metric | Value |
| --- | ---: |
| Return | +9648.3% |
| %/ngày (trên vốn gốc) | +5.287% |
| PF | 1.4946 |
| WR | 74.9% |
| MaxDD | 18.9% |
| Final equity | $97483 |
| Net PnL | $+96483 |
| Trades | 19932 |
| **Trades / ngày (t/d)** | **10.92** |
| Long / Short | 9334 / 10598 |
| Long PnL / Short PnL | $+39046 / $+57437 |
| Avg win / Avg loss | $+19.53 / $-39.01 |
| Hold TB / median (giờ) | 4.9h / 3.0h |
| Exit TP / EOD | 19929 / 3 |

### Vốn đang dùng (trung bình khi đang trade)

| Metric | Value |
| --- | ---: |
| Số lệnh mở TB / P50 / P95 / max | 2.23 / 2 / 6 / 10 |
| Margin book TB (tổng ký quỹ đang lock) | $517 |
| Margin book P95 | $2358 |
| Margin book / equity TB | 2.4% |
| Margin / lệnh (TB lúc vào) | $221.6 |
| Notional / lệnh TB (margin×10) | $2216 |
| pot_rr TB / size_mult TB | 1.07 / 1.02 |

### Theo coin (sort net PnL)

| Symbol | Trades | t/d | WR | PF | Net $ | L/S | Hold TB h |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| NEARUSDT | 1285 | 0.70 | 77.4% | 1.77 | +11462 | 581/704 | 4.6 |
| SOLUSDT | 1157 | 0.63 | 74.8% | 1.75 | +9370 | 546/611 | 4.9 |
| LTCUSDT | 1352 | 0.74 | 76.8% | 1.82 | +8707 | 659/693 | 4.7 |
| XRPUSDT | 1227 | 0.67 | 78.0% | 1.89 | +8154 | 529/698 | 4.6 |
| DOTUSDT | 1236 | 0.68 | 75.2% | 1.61 | +7822 | 548/688 | 4.9 |
| AVAXUSDT | 1201 | 0.66 | 75.8% | 1.62 | +7388 | 540/661 | 5.0 |
| XLMUSDT | 1305 | 0.72 | 75.0% | 1.45 | +6271 | 586/719 | 4.8 |
| ATOMUSDT | 1286 | 0.70 | 76.0% | 1.43 | +5874 | 584/702 | 4.8 |
| LINKUSDT | 1259 | 0.69 | 74.9% | 1.37 | +5657 | 602/657 | 4.8 |
| ETHUSDT | 1279 | 0.70 | 74.2% | 1.46 | +5344 | 643/636 | 5.0 |
| UNIUSDT | 1259 | 0.69 | 74.6% | 1.28 | +5292 | 586/673 | 5.0 |
| BCHUSDT | 1286 | 0.70 | 74.8% | 1.39 | +5133 | 572/714 | 5.1 |
| ADAUSDT | 1197 | 0.66 | 75.8% | 1.32 | +4759 | 522/675 | 4.8 |
| BTCUSDT | 1238 | 0.68 | 72.9% | 1.44 | +3072 | 613/625 | 4.9 |
| BNBUSDT | 1178 | 0.65 | 72.8% | 1.36 | +2891 | 590/588 | 5.1 |
| TRXUSDT | 1187 | 0.65 | 69.2% | 0.89 | -712 | 633/554 | 5.4 |

## Tóm tắt t/d & vốn vs Config A (tham chiếu)

| Window | t/d sma34/144 | t/d Config A (≈) | MaxDD sma | MaxDD A | PF sma | PF A |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 365d | **13.70** | ~28.6 | 13.0% | 38.7% | 1.67 | 1.54 |
| 1095d | **13.35** | ~28.8 | 14.4% | 52.3% | 1.58 | 1.53 |
| 1825d | **10.92** | ~25.4 | 18.9% | 46.3% | 1.49 | 1.44 |

**Đọc nhanh:** `sma34/144` vào lệnh **thưa hơn nhiều** so với Config A (~1/2–1/3 t/d) → compound chậm hơn nhưng MaxDD thấp hơn rõ; vốn lock TB thường chỉ một phần equity nhờ margin 1%×mult.
