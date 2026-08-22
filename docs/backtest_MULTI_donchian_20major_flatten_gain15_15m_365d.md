# Donchian D20 — flatten-all when equity +1.5% from anchor

- Sinh luc: 2026-08-21 17:38:41 +07
- Script: `scripts/backtest_donchian_20coin_flatten_gain15.py` (clone; khong sua BT goc / bot)
- Cache only: `data/bt_klines_15m/`
- Rule entry giong D20 (body/pot_rr/size_mult). Flatten: moi khi `equity ≥ anchor × 1.015` → dong het lot @ close, `anchor = cash` moi, tiep tuc trade (skip entry cung bar).
- Anchor ban dau = 1000$; sau moi flatten gan lai bang equity (cash) luc do.

| Config | %/ngày | Net | End | MaxDD | DD peak→trough | Flat events | n | lệnh/ngày | PF | WR | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `D20_base` | **+33.450%** | **+122091.3** | 123091.3 | **49.9%** | 1973→989 | 0 | 16905 | 46.3 | 1.48 | 73.4% | D20 no flatten (baseline) |
| `D20_flat15` | **+29.433%** | **+107431.3** | 108431.3 | **49.9%** | 1899→952 | 254 | 17866 | 48.9 | 1.50 | 71.3% | D20 flatten-all when eq ≥ anchor×1.015 |
| `D20_flat15_max10` | **+8.840%** | **+32267.4** | 33267.4 | **35.2%** | 1827→1184 | 196 | 13441 | 36.8 | 1.39 | 71.0% | D20 max10 + flatten +1.5% |
| `A20_flat15` | **+2.725%** | **+9945.8** | 10945.8 | **25.1%** | 1395→1045 | 147 | 17513 | 48.0 | 1.45 | 72.2% | A20 0.5% + flatten +1.5% |

## Ket luan

- Baseline: %/ngày **+33.450%**, MaxDD **49.9%**, net **+122091.3**
- Flatten +1.5%: %/ngày **+29.433%**, MaxDD **49.9%**, net **+107431.3**, so lan flatten **254** (tong pnl luc flatten -31797.0)
- MaxDD: 49.9% → 49.9% (+0.0 pp); %/ngày: +33.450% → +29.433%

Flatten @ close (khong phai high/low) — paper; live co slip. Khong anh huong bot.

