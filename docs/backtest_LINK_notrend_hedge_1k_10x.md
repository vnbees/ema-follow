# NO_TREND hedge — vốn 1000 USDT, 10x, 0.5% cap / lệnh

- Sinh lúc: 2026-08-18 13:28:16 +07
- Vốn ban đầu **1000 USDT**, đòn bẩy **10x** (ký quỹ = notional/10, hedge 2 chân đều khóa margin).
- Mỗi lệnh notional = **0.5% equity hiện tại** (lúc 1000 USDT → 5 USDT/lệnh, margin 0.50). Sizing theo vốn: lãi thì lot to hơn, lỗ thì nhỏ hơn.
- Hết buying power thì không add. Thanh lý nếu equity ≤ 0.4% tổng notional (MMR) hoặc wick ngược net position.
- Scale: skip chân avg đã lời. 1 cặp: chỉ mở khi sổ trống.

## Scale

| Cửa sổ | Giá | Vốn cuối | PnL | % | Peak lot | Peak notional | Max DD | Equity min | Phí | Liq |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 7 ngày (2026-08-11→2026-08-18) | +12.6% | **1005.77** | +5.77 | +0.6% | 127 | 637 | -5.09 | 997.71 | 3.23 | no |
| 3 tháng (2026-05-20→2026-08-18) | -1.1% | **1201.10** | +201.10 | +20.1% | 609 | 3360 | -84.79 | 972.35 | 71.39 | no |
| 6 tháng (2026-02-19→2026-08-18) | +8.5% | **1072.56** | +72.56 | +7.3% | 714 | 3488 | -168.30 | 868.29 | 135.15 | no |
| 1 năm (2025-08-18→2026-08-18) | -61.2% | **1265.85** | +265.85 | +26.6% | 952 | 5085 | -264.91 | 962.71 | 318.52 | no |

## 1 cặp (long+short, 0.5% cap mỗi chân)

| Cửa sổ | Vốn cuối | PnL | % | Peak | Lots | TP | Phí | Liq |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 7 ngày | **999.78** | -0.22 | -0.02% | 2 | 76 | 2 | 0.30 | no |
| 3 tháng | **998.33** | -1.67 | -0.17% | 2 | 710 | 41 | 2.84 | no |
| 6 tháng | **994.22** | -5.78 | -0.58% | 2 | 1390 | 87 | 5.54 | no |
| 1 năm | **989.87** | -10.13 | -1.01% | 2 | 2742 | 237 | 10.91 | no |

Ghi chú: 0.5% của 1000 = 5 USDT notional ≈ min Binance LINK. Cross 10x, MMR 0.4%.

