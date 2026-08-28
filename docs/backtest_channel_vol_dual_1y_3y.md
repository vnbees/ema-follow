# Channel + Counter + Volume — backtest 1y & ~3y (fresh)

- Generated: **2026-08-28 15:09:05 +07**
- Script: `scripts/backtest_price_vol_channel_run.py`
- Data: cache `data/bt_klines_15m/` (20 majors, 15m, quote_volume)
- Strategy: rolling channel 20 · parallel exit · counter candle · body/ATR [0.3,1.2] · pot_rr filter · TP = opposite band
- Fill: entry@close · TP on band touch · no hard SL · shared wallet compound margin

## 365 ngày (2025-08-28 12:15 +07 → 2026-08-28 12:15 +07)

| Config | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | Long PnL | Short PnL | Final eq |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B · ch20 vol≥1.0 rr0.5 margin1% max10 (no vol filter) | +2841.2% | +7.784% | 1.45 | 71.6% | 11540 | 31.6 | 37.4% | +12067 | +16345 | 24696 |
| A · ch20 vol≥1.2 rr0.5 margin1% max10 | +2173.3% | +5.954% | 1.47 | 71.3% | 10403 | 28.5 | 38.7% | +10089 | +11643 | 18531 |
| D · ch20 vol≥1.5 rr0.6 margin1% max10 (strict vol) | +585.9% | +1.605% | 1.41 | 69.9% | 6583 | 18.0 | 49.4% | +2587 | +3272 | 6317 |
| C · ch20 vol≥1.2 rr0.5 margin0.5% max20 (conservative) | +414.9% | +1.137% | 1.40 | 71.3% | 11832 | 32.4 | 21.6% | +1718 | +2431 | 4635 |

## 1095 ngày (2023-08-29 12:15 +07 → 2026-08-28 12:15 +07)

| Config | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | Long PnL | Short PnL | Final eq |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B · ch20 vol≥1.0 rr0.5 margin1% max10 (no vol filter) | +14545676.0% | +13283.722% | 1.45 | 71.6% | 34717 | 31.7 | 49.4% | +62848517 | +82608243 | 122136706 |
| A · ch20 vol≥1.2 rr0.5 margin1% max10 | +6100762.0% | +5571.472% | 1.46 | 71.7% | 31479 | 28.7 | 52.3% | +28637299 | +32370320 | 49732337 |
| D · ch20 vol≥1.5 rr0.6 margin1% max10 (strict vol) | +166606.2% | +152.152% | 1.40 | 70.6% | 20078 | 18.3 | 50.8% | +750731 | +915331 | 1535294 |
| C · ch20 vol≥1.2 rr0.5 margin0.5% max20 (conservative) | +47853.7% | +43.702% | 1.39 | 71.6% | 36481 | 33.3 | 35.5% | +214507 | +264030 | 431593 |

## Ghi chú

- Return % tính trên vốn ban đầu $1000, **compound** theo margin_pct (không rút lời).
- ~3y = 1095 ngày eval (cache có ~1100 ngày từ 2023-08-23).
- Paper only.
