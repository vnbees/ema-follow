# Config A + EMA 21 / 89 / 200 — backtest

- Generated: **2026-09-18 11:58:36 +07**
- Script: `scripts/backtest_price_vol_channel_ema_filter.py`
- Baseline = live Config A (channel + counter + vol≥1.2, TP band, size_mult=min(pot_rr,2))
- **stack**: long chỉ khi EMA21>EMA89>EMA200; short khi đảo ngược
- **stack_soft**: long khi EMA21>EMA89; short khi EMA21<EMA89
- **ema200**: long khi close>EMA200; short khi close<EMA200

## 365d · thực tế **365d** · **20 coin** (2025-09-16 12:15 +07 → 2026-09-16 12:15 +07)

| Variant | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | ΔPF | ΔMaxDD | Final eq |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A baseline (no EMA) | +2545.7% | +6.974% | 1.54 | 71.5% | 10443 | 28.6 | 38.7% | +0.00 | +0.0pp | 22876 |
| A + EMA stack 21>89>200 | +643.6% | +1.763% | 1.58 | 70.3% | 6627 | 18.2 | 22.4% | +0.04 | -16.2pp | 6692 |
| A + EMA soft 21 vs 89 | +1216.2% | +3.332% | 1.66 | 70.6% | 8234 | 22.6 | 34.1% | +0.12 | -4.6pp | 11847 |
| A + price vs EMA200 | +734.1% | +2.011% | 1.56 | 71.0% | 7593 | 20.8 | 25.9% | +0.02 | -12.8pp | 7506 |

## 1095d · thực tế **1095d** · **20 coin** (2023-09-17 12:15 +07 → 2026-09-16 12:15 +07)

| Variant | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | ΔPF | ΔMaxDD | Final eq |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A baseline (no EMA) | +7258521.6% | +6628.787% | 1.53 | 71.8% | 31506 | 28.8 | 52.3% | +0.00 | +0.0pp | 62762669 |
| A + EMA stack 21>89>200 | +168389.0% | +153.780% | 1.56 | 71.1% | 20653 | 18.9 | 22.4% | +0.03 | -29.8pp | 1516166 |
| A + EMA soft 21 vs 89 | +1127449.8% | +1029.635% | 1.65 | 71.3% | 25353 | 23.2 | 34.1% | +0.12 | -18.2pp | 10148953 |
| A + price vs EMA200 | +232873.2% | +212.670% | 1.55 | 71.5% | 23361 | 21.3 | 25.9% | +0.02 | -26.4pp | 2096553 |

## 1825d · thực tế **1825d** · **16 coin** (2021-09-17 12:15 +07 → 2026-09-16 12:15 +07)

| Variant | Return | %/ngày | PF | WR | Trades | t/d | MaxDD | ΔPF | ΔMaxDD | Final eq |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A baseline (no EMA) | +1126716247.1% | +617378.766% | 1.44 | 71.5% | 46397 | 25.4 | 46.3% | +0.00 | +0.0pp | 10076058670 |
| A + EMA stack 21>89>200 | +1040556.4% | +570.168% | 1.43 | 70.4% | 28378 | 15.5 | 34.9% | -0.01 | -11.4pp | 9574571 |
| A + EMA soft 21 vs 89 | +13593950.1% | +7448.740% | 1.47 | 70.9% | 35359 | 19.4 | 33.7% | +0.03 | -12.6pp | 125072218 |
| A + price vs EMA200 | +2178954.2% | +1193.947% | 1.41 | 71.0% | 32419 | 17.8 | 36.1% | -0.02 | -10.2pp | 20048413 |

## Kết luận

- **EMA stack 21>89>200**: MaxDD giảm mạnh nhất (1y 38.7→**22.4%**, 3y 52.3→**22.4%**), PF giữ/↑ nhẹ (+0.03~0.04). Đổi lại return và t/d giảm ~35–40% (chặn lệnh counter-trend còn edge).
- **EMA soft 21 vs 89**: PF tốt nhất (+0.12 trên 1y/3y), MaxDD giảm vừa (1y −4.6pp, 3y −18pp), giữ nhiều trade hơn stack → cân bằng tốt nếu muốn lọc nhẹ.
- **Price vs EMA200**: gần stack về DD nhưng PF gần như không đổi; kém stack trên 1y/3y.
- **5y**: mọi filter đều cắt DD (~10–13pp) nhưng PF gần baseline hoặc hơi ↓ — edge dài hạn chủ yếu từ volume channel, không từ EMA.
- **Live**: nếu ưu tiên DD thấp hơn return tối đa → cân nhắc **stack**; nếu muốn giữ tần suất gần hiện tại → **stack_soft**. Không kỳ vọng return paper giống baseline.
