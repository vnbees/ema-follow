# Channel + Counter + Volume — backtest 1y / 3y / 5y / max futures

- Generated: **2026-09-16 13:05:59 +07**
- Script: `scripts/backtest_price_vol_channel_run.py`
- Data: cache `data/bt_klines_15m/` (Binance USDT-M 15m, quote_volume)
- Strategy: rolling channel 20 · parallel exit · counter candle · body/ATR [0.3,1.2] · pot_rr filter · TP = opposite band
- Fill: entry@close · TP on band touch · no hard SL · shared wallet compound margin

## Giới hạn data

- Futures USDT-M **không có 10 năm** 15m (BTC từ ~2019-09 ≈ 7y).
- Pool 20 coin đầy đủ chỉ overlap ~3.4y (SUI list 2023-05).
- Cửa sổ ≥5y **tự drop** coin thiếu lịch sử (thường: SUI, ARB, APT, OP).

## 365 ngày yêu cầu · thực tế **365d** · **20 coin** (2025-09-16 12:15 +07 → 2026-09-16 12:15 +07)

- Symbols: `BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,TRXUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,LTCUSDT,BCHUSDT,XLMUSDT,ATOMUSDT,NEARUSDT,APTUSDT,SUIUSDT,ARBUSDT,OPUSDT,UNIUSDT`

| Config | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | Long PnL | Short PnL | Final eq |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B · ch20 vol≥1.0 rr0.5 margin1% max10 (no vol filter) | +3258.3% | +8.927% | 1.49 | 71.8% | 11547 | 31.6 | 37.4% | +14545 | +18039 | 28664 |
| A · ch20 vol≥1.2 rr0.5 margin1% max10 | +2545.7% | +6.974% | 1.54 | 71.5% | 10443 | 28.6 | 38.7% | +12135 | +13322 | 22876 |
| D · ch20 vol≥1.5 rr0.6 margin1% max10 (strict vol) | +746.9% | +2.046% | 1.49 | 70.2% | 6625 | 18.2 | 49.4% | +3273 | +4197 | 7858 |
| C · ch20 vol≥1.2 rr0.5 margin0.5% max20 (conservative) | +462.1% | +1.266% | 1.43 | 71.4% | 11901 | 32.6 | 21.6% | +1950 | +2670 | 5239 |

## 1095 ngày yêu cầu · thực tế **1095d** · **20 coin** (2023-09-17 12:15 +07 → 2026-09-16 12:15 +07)

- Symbols: `BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,TRXUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,LTCUSDT,BCHUSDT,XLMUSDT,ATOMUSDT,NEARUSDT,APTUSDT,SUIUSDT,ARBUSDT,OPUSDT,UNIUSDT`

| Config | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | Long PnL | Short PnL | Final eq |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B · ch20 vol≥1.0 rr0.5 margin1% max10 (no vol filter) | +16461600.2% | +15033.425% | 1.48 | 71.7% | 34701 | 31.7 | 49.4% | +74287307 | +90328695 | 140505324 |
| A · ch20 vol≥1.2 rr0.5 margin1% max10 | +7258521.6% | +6628.787% | 1.53 | 71.8% | 31506 | 28.8 | 52.3% | +34839700 | +37745516 | 62762669 |
| D · ch20 vol≥1.5 rr0.6 margin1% max10 (strict vol) | +215277.8% | +196.601% | 1.47 | 70.7% | 20074 | 18.3 | 50.8% | +961680 | +1191099 | 1998194 |
| C · ch20 vol≥1.2 rr0.5 margin0.5% max20 (conservative) | +52907.9% | +48.318% | 1.43 | 71.7% | 36495 | 33.3 | 35.5% | +237652 | +291427 | 494136 |

## 1825 ngày yêu cầu · thực tế **1825d** · **16 coin** (2021-09-17 12:15 +07 → 2026-09-16 12:15 +07)

- Symbols: `BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,TRXUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,LTCUSDT,BCHUSDT,XLMUSDT,ATOMUSDT,NEARUSDT,UNIUSDT`

| Config | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | Long PnL | Short PnL | Final eq |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B · ch20 vol≥1.0 rr0.5 margin1% max10 (no vol filter) | +8227588622.6% | +4508267.738% | 1.43 | 71.7% | 53266 | 29.2 | 49.2% | +35650858809 | +46625027418 | 74256751043 |
| A · ch20 vol≥1.2 rr0.5 margin1% max10 | +1126716247.1% | +617378.766% | 1.44 | 71.5% | 46397 | 25.4 | 46.3% | +5095730874 | +6171431597 | 10076058670 |
| D · ch20 vol≥1.5 rr0.6 margin1% max10 (strict vol) | +3799447.7% | +2081.889% | 1.37 | 70.5% | 27568 | 15.1 | 44.6% | +16936212 | +21058265 | 36032395 |
| C · ch20 vol≥1.2 rr0.5 margin0.5% max20 (conservative) | +569004.0% | +311.783% | 1.38 | 71.5% | 49172 | 26.9 | 26.0% | +2550227 | +3139813 | 5395781 |

## 2100 ngày yêu cầu · thực tế **2100d** · **16 coin** (2020-12-16 12:15 +07 → 2026-09-16 12:15 +07)

- Symbols: `BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,TRXUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,LTCUSDT,BCHUSDT,XLMUSDT,ATOMUSDT,NEARUSDT,UNIUSDT`

| Config | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | Long PnL | Short PnL | Final eq |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B · ch20 vol≥1.0 rr0.5 margin1% max10 (no vol filter) | +4143421853260.8% | +1973058025.362% | 1.43 | 71.6% | 61295 | 29.2 | 54.3% | +17953808248298 | +23480410284310 | 37395773585882 |
| A · ch20 vol≥1.2 rr0.5 margin1% max10 | +400754256171.4% | +190835360.082% | 1.44 | 71.5% | 53399 | 25.4 | 57.9% | +1812466917892 | +2195075643823 | 3583886404777 |
| D · ch20 vol≥1.5 rr0.6 margin1% max10 (strict vol) | +144026837.2% | +68584.208% | 1.37 | 70.4% | 31689 | 15.1 | 53.1% | +642008967 | +798259405 | 1365856116 |
| C · ch20 vol≥1.2 rr0.5 margin0.5% max20 (conservative) | +12958316.5% | +6170.627% | 1.38 | 71.4% | 56665 | 27.0 | 30.9% | +58082618 | +71500547 | 122861164 |

## Ghi chú

- Return % trên vốn $1000, **compound** (không rút lời) — số tuyệt đối phình trên cửa sổ dài.
- Metric so sánh ổn định hơn: **%/ngày, PF, WR, MaxDD, t/d**.
- Paper only.
