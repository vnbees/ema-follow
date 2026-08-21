# Donchian body_size_rr05 — 20 majors shared wallet vs 5-coin ref

- Sinh luc: 2026-08-21 10:13:02 +07
- Interval **15m** · lookback **365d** · capital **1000$** chung · fee 0.04%/side · 10x
- Filter: body ATR ∈ [0.3, 1.2], pot_rr ≥ 0.5, size_mult = clip(0.5+pot_rr, 0.5, 2)
- Pool 20: BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT, TRXUSDT, ADAUSDT, AVAXUSDT, DOTUSDT, LINKUSDT, LTCUSDT, BCHUSDT, XLMUSDT, ATOMUSDT, NEARUSDT, APTUSDT, SUIUSDT, ARBUSDT, OPUSDT, UNIUSDT
- Co data: ADAUSDT, APTUSDT, ARBUSDT, ATOMUSDT, AVAXUSDT, BCHUSDT, BNBUSDT, BTCUSDT, DOTUSDT, ETHUSDT, LINKUSDT, LTCUSDT, NEARUSDT, OPUSDT, SOLUSDT, SUIUSDT, TRXUSDT, UNIUSDT, XLMUSDT, XRPUSDT
- Ref 5: LINKUSDT, HYPEUSDT, SUIUSDT, DOGEUSDT, SOLUSDT
- Kline cache: `data/bt_klines_15m/` (tránh fetch lại / giảm rate-limit)
- **Lưu ý:** `shared_backtest` align theo **intersection** timestamp → cửa sổ = coin ngắn nhất trong pool.

| Config | #coin | %/ngày | %/tháng | %/năm | Net | MaxDD | PF | Wipe | WR | n | lệnh/ngày | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `D5_ref` | 5 | **+1.064%** | +32.39% | +388.4% | **+3883.6** | 7.6% | 1.54 | 0.65 | 74.1% | 4245 | 11.6 | ref: 5 coin, margin 1%, max_open10 |
| `D20_like_bot` | 20 | **+33.451%** | +1018.15% | +12209.5% | **+122091.3** | 49.9% | 1.48 | 0.68 | 73.4% | 16905 | 46.3 | 20 majors, margin 1%, max_open20 (live-like) |
| `D20_max10` | 20 | **+10.151%** | +308.98% | +3705.2% | **+37051.2** | 35.7% | 1.41 | 0.71 | 72.4% | 12713 | 34.8 | 20 majors, margin 1%, max_open10 (slot cap) |
| `A20_half` | 20 | **+2.893%** | +88.05% | +1055.8% | **+10557.9** | 25.1% | 1.46 | 0.69 | 73.4% | 16905 | 46.3 | 20 majors, margin 0.5%, max_open20 |

## Ket luan

- 5-coin ref D: **+1.064%/ngày**, MaxDD 7.6%, 11.6 lệnh/ngày, PF 1.54
- 20-major live-like D: **+33.451%/ngày**, MaxDD 49.9%, 46.3 lệnh/ngày, PF 1.48
- Ty le %/ngày (20 / 5): **31.44x** (khong tuyen tinh voi so coin).

Paper only — live top-N theo volume co the khac pool majors co dinh nay.

