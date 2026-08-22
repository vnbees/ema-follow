# Donchian D20 — daily withdraw 1% equity (cache-only clone)

- Sinh luc: 2026-08-21 17:28:55 +07
- Script: `scripts/backtest_donchian_20coin_withdraw_daily.py` (clone; **khong** sua BT goc)
- Data: `data/bt_klines_15m/` cache only — **khong REST** (an toan voi bot live)
- Rule giong D20: body 0.3–1.2, pot_rr≥0.5, size_mult clip(0.5+pot_rr,0.5,2), 15m, 365d, capital 1000$
- Withdraw: moi ngay UTC, rut `min(1% × equity_bot, cash tu do)` sang **spot** (spot flat, khong sinh loi)
- MaxDD_bot: peak→trough tren equity futures/bot; MaxDD_total: bot + spot da rut

| Config | %/ngày total | %/ngày bot-only | Net total | End bot | End spot | MaxDD bot | MaxDD total | PF | WR | lệnh/ngày | WD sum | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `D20_no_wd` | **+33.450%** | +33.450% | **+122091.3** | 123091.3 | 0.0 | **49.9%** | **49.9%** | 1.48 | 73.4% | 46.3 | 0.0 | margin 1%, max20, no withdraw (baseline clone) |
| `D20_wd1pct` | **+2.566%** | +0.597% | **+9366.8** | 3178.7 | 7188.1 | **51.7%** | **33.8%** | 1.43 | 73.4% | 46.3 | 7188.1 | margin 1%, max20, withdraw 1% equity/UTC-day |
| `D20_max10_wd1pct` | **+0.977%** | -0.005% | **+3566.8** | 981.2 | 3585.6 | **45.7%** | **23.5%** | 1.35 | 72.4% | 34.8 | 3585.6 | margin 1%, max10, withdraw 1%/day |
| `A20_wd1pct` | **+0.393%** | -0.193% | **+1432.7** | 296.7 | 2136.0 | **72.2%** | **16.2%** | 1.42 | 73.4% | 46.3 | 2136.0 | margin 0.5%, max20, withdraw 1%/day |

## Ket luan

- Baseline D20 no WD: MaxDD_bot **49.9%**, %/ngày total **+33.450%**, end total **123091.3**
- D20 + WD 1%/ngày: MaxDD_bot **51.7%**, MaxDD_total **33.8%**, %/ngày total **+2.566%**, end bot **3178.7** + spot **7188.1** = total **10366.8**
- MaxDD_bot: 49.9% → 51.7% (+1.8 pp)
- DD episode (bot) wd run: peak≈1238 → trough≈599 (trough ~ 2025-10-11 04:15 +07)

Rut chi lay tu **cash** (khong force-close). Neu ky quy dang lock nhieu, co ngay rut < 1% equity.
Paper only; khong anh huong bot live.

