# NO_TREND hedge — vốn 100 USDT, 1 USDT / lệnh

- Sinh lúc: 2026-08-18 13:20:58 +07
- Ký quỹ 1:1 (không đòn bẩy): mỗi lot khóa 1 USDT, hết tiền mặt thì không add. Equity ≤ 0 → đóng hết, dừng.
- Scale: skip chân avg đã lời. 1 cặp: chỉ 2 lot khi sổ trống.

## Scale (1 USDT/lệnh, trần vốn 100)

| Cửa sổ | Giá | Vốn cuối | PnL | % | Peak lot | Skip hết vốn | Max DD | Equity min | Phí | Liquidated |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 7 ngày (2026-08-11→2026-08-18) | +12.1% | **101.06** | +1.06 | +1.1% | 100 | 40 | -1.01 | 99.55 | 0.61 | no |
| 3 tháng (2026-05-20→2026-08-18) | -1.6% | **116.49** | +16.49 | +16.5% | 116 | 8090 | -8.27 | 98.33 | 7.53 | no |
| 6 tháng (2026-02-19→2026-08-18) | +8.3% | **98.59** | -1.41 | -1.4% | 101 | 19705 | -19.81 | 81.99 | 14.82 | no |
| 1 năm (2025-08-18→2026-08-18) | -61.3% | **96.26** | -3.74 | -3.7% | 113 | 37987 | -32.77 | 79.81 | 31.65 | no |

## 1 cặp (1 USDT long + 1 USDT short)

| Cửa sổ | Vốn cuối | PnL | Peak | Lots | TP | Phí |
| --- | --- | --- | --- | --- | --- | --- |
| 7 ngày | **99.96** | -0.04 | 2 | 76 | 2 | 0.06 |
| 3 tháng | **99.67** | -0.33 | 2 | 710 | 41 | 0.57 |
| 6 tháng | **98.84** | -1.16 | 2 | 1390 | 87 | 1.11 |
| 1 năm | **97.96** | -2.04 | 2 | 2742 | 237 | 2.19 |

Ghi chú: Binance futures thường min notional ~5 USDT — 1 USDT/lệnh là giả lập kích thước, không chắc đặt được live.

