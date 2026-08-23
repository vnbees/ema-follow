# D20 breadth — lookback **1095d (~3 năm)** vs 365d ref

- Sinh luc: 2026-08-23 16:51:49 +07
- Script: `scripts/backtest_donchian_20coin_breadth_flip_3y.py`
- Interval **15m** · lookback **1095d** · capital **1000$** · fee 0.04%/side · 10x
- Pool: **20** majors có đủ data · common window **~1095 ngày**
- Coins: ADAUSDT, APTUSDT, ARBUSDT, ATOMUSDT, AVAXUSDT, BCHUSDT, BNBUSDT, BTCUSDT, DOTUSDT, ETHUSDT, LINKUSDT, LTCUSDT, NEARUSDT, OPUSDT, SOLUSDT, SUIUSDT, TRXUSDT, UNIUSDT, XLMUSDT, XRPUSDT
- Rule giống live: body ATR, pot_rr, margin 1%×size_mult, max_open 20, breadth flip/hard
- Fetch: public `/fapi/v1/klines` only · page_sleep=1.0s · symbol_sleep=5.0s · respect `data/binance_rate_limit_until_ms` if bot in cooldown

| Config | %/ngày | MaxDD | PF | WR | n | t/d | flip_ok | pnlN/F | Note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `D20_base` | **+519469.258%** | **50.1%** | 1.50 | 73.0% | 50744 | 46.3 | 0 | +5688134262/+0 | không breadth (baseline) |
| `breadth_flip` | **+580172.815%** | **37.2%** | 1.40 | 74.7% | 53127 | 48.5 | 17667 | +4326617757/+2026214135 | flip side (live default) |
| `breadth_hard` | **+74930.816%** | **19.7%** | 1.42 | 73.5% | 42645 | 38.9 | 0 | +820484631/+0 | skip ngược vote |

## So với backtest 365d (cùng pool logic, cửa sổ ngắn hơn)

| Config | 365d %/ngày | 365d MaxDD | 3y %/ngày | 3y MaxDD | Δ%/ngày | ΔMaxDD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `D20_base` | +33.450% | 49.9% | **+519469.258%** | **50.1%** | +519435.808 | +0.2pp |
| `breadth_flip` | +22.567% | 25.3% | **+580172.815%** | **37.2%** | +580150.248 | +11.9pp |
| `breadth_hard` | +8.751% | 19.0% | **+74930.816%** | **19.7%** | +74922.065 | +0.7pp |

Paper only — không ảnh hưởng bot live.
