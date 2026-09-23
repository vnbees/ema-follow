# Config A — DCA trên lệnh lỗ (feasibility)

- Generated: **2026-09-18 11:16:01 +07**
- Script: `scripts/backtest_price_vol_channel_dca.py`
- Baseline = Config A (vol≥1.2, TP band, size_mult=min(pot_rr,2), max10)
- DCA: thêm **tối đa 1 lần**, gộp avg entry; vẫn TP khi chạm band Donchian hiện tại
- `@opp` = giá chạm opp_band lúc vào lệnh · `@1R` = adverse @ close ≥ 1×|entry−opp|
- `stop2R` = sau khi đã DCA, cắt nếu adverse ≥ 2× R gốc (so orig entry)

## 365d · thực tế **365d** · **20 coin** (2025-09-16 → 2026-09-16)

| Variant | Return | PF | WR | MaxDD | ΔPF | ΔMaxDD | DCA adds | Trades có DCA | WR DCA | Net DCA | Final eq |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A baseline | +2545.7% | 1.54 | 71.5% | 38.7% | +0.00 | +0.0pp | 0 | 0 | 0% | +0 | 22876 |
| DCA @opp ×1 | +17165.8% | 1.66 | 76.3% | 76.6% | +0.12 | +38.0pp | 6102 | 6097 | 61% | +54112 | 131426 |
| DCA @opp ×0.5 | +6992.7% | 1.60 | 74.7% | 57.7% | +0.07 | +19.0pp | 6102 | 6097 | 59% | +5408 | 57678 |
| DCA @1R ×1 | +13517.1% | 1.67 | 77.4% | 76.2% | +0.13 | +37.6pp | 5222 | 5217 | 58% | +3562 | 103863 |
| DCA @opp ×1 + stop2R | +601.5% | 1.17 | 59.3% | 28.0% | -0.37 | -10.6pp | 7398 | 7397 | 30% | -15148 | 6540 |
| DCA @1R ×0.5 + stop2R | +1002.5% | 1.27 | 59.0% | 21.7% | -0.27 | -16.9pp | 6340 | 6339 | 19% | -27284 | 10319 |

## 1095d · thực tế **1095d** · **20 coin** (2023-09-17 → 2026-09-16)

| Variant | Return | PF | WR | MaxDD | ΔPF | ΔMaxDD | DCA adds | Trades có DCA | WR DCA | Net DCA | Final eq |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A baseline | +7258521.6% | 1.53 | 71.8% | 52.3% | +0.00 | +0.0pp | 0 | 0 | 0% | +0 | 62762669 |
| DCA @opp ×1 | +2837916526.8% | 1.65 | 76.5% | 102.2% | +0.12 | +49.9pp | 18547 | 18542 | 62% | +8879504221 | 21602014756 |
| DCA @opp ×0.5 | +192155377.9% | 1.60 | 75.0% | 77.4% | +0.07 | +25.1pp | 18547 | 18542 | 59% | +138743020 | 1562619535 |
| DCA @1R ×1 | +1128829425.0% | 1.66 | 77.5% | 101.9% | +0.13 | +49.6pp | 15888 | 15883 | 58% | +244987336 | 8610065894 |
| DCA @opp ×1 + stop2R | +599227.7% | 1.18 | 59.6% | 28.0% | -0.36 | -24.2pp | 22737 | 22736 | 30% | -14323590 | 5588224 |
| DCA @1R ×0.5 + stop2R | +1357242.9% | 1.28 | 59.3% | 21.7% | -0.26 | -30.5pp | 19390 | 19389 | 19% | -36259375 | 12703170 |

## Đọc nhanh / khả thi?

- DCA **khả thi kỹ thuật** (gộp lệnh, thêm margin) nhưng chỉ nên cân nhắc nếu **PF ↑ hoặc MaxDD ↓** vs baseline.
- Nếu Net DCA âm / WR DCA thấp → đang **đổ thêm tiền vào xu hướng sai** (phù hợp live: lỗ nặng = hold lâu + size lớn).
- Live risk: maint tăng, margin bị khóa, max_open/cash cạn khi nhiều DCA cùng lúc.
