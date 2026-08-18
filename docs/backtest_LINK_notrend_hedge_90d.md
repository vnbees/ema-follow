# NO_TREND hedge 2 chiều — 3 tháng

- Sinh lúc: 2026-08-18 13:12:36 +07
- Cửa sổ: **2026-05-20 13:10 → 2026-08-18 13:10**
- Giá: 9.5900 → 9.4150 (-1.82%)
- Nến: NO_TREND 18051 · TREND_UP 3698 · TREND_DOWN 4171

## Rule

Chỉ vào khi **3 khung = NO_TREND**. Mỗi lần vào mở **long 100 + short 100** (cùng giá close). Từng chân chốt **TP 2%**. Còn lại đóng hết khi 3 khung thành TREND_UP hoặc TREND_DOWN. Không vào khi đang có trend.

- A: **1 cặp** — chỉ mở khi cả hai sổ trống (không chồng lot).
- B: **Scale** — mỗi nến NO_TREND add thêm 2 chân, **skip chân nào avg đã lời**.

## So sánh

| | A 1 cặp | **B Scale skip avg lời** |
| --- | --- | --- |
| Lots | 710 | **15897** |
| Peak 2 chân | 2 | **610** |
| TP 2% | 41 (+79) | 4290 (+8237) |
| Đóng vì có trend | 667 (-112) | 11604 (-4472) |
| EOD | 2 | 3 |
| PnL tổng | -33.37 | **+3765.09** |
| WR đóng | 45% | 57% |
| Phí | 56.79 | 1271.31 |

## A — 1 cặp long+short khi sổ trống

- Nến NO_TREND 18051 · có trend 7869 · lần mở hedge 355
- Long add 355 (skip 0, peak 1) · Short add 355 (skip 0, peak 1) · peak 2 chân 2 lot ≈ 200 USDT
- **PnL tổng: -33.3678 USDT** · TP +78.72 · hết trend -111.93 · EOD -0.16 · phí 56.79
- Equity min/max -33/+3 · Max DD -37 · MTM tệ nhất -10
- Long -34.4699 (TP 20, trend 334, EOD 1) · Short +1.1022 (TP 21, trend 333, EOD 1)

| Side | Lots | TP 2% | Có trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 355 | 20 | 334 | 1 | -34.4699 | 42% |
| short | 355 | 21 | 333 | 1 | +1.1022 | 47% |
| **tổng** | **710** | 41 | 667 | 2 | **-33.3678** | 45% |

## B — Scale mỗi nến NO_TREND, skip avg lời

- Nến NO_TREND 18051 · có trend 7869 · lần mở hedge 13935
- Long add 7882 (skip 10169, peak 530) · Short add 8015 (skip 10036, peak 441) · peak 2 chân 610 lot ≈ 61000 USDT
- **PnL tổng: +3765.0874 USDT** · TP +8237.29 · hết trend -4471.96 · EOD -0.24 · phí 1271.31
- Equity min/max -552/+3812 · Max DD -1458 · MTM tệ nhất -1694
- Long +1320.9877 (TP 1840, trend 6041, EOD 1) · Short +2444.0997 (TP 2450, trend 5563, EOD 2)

| Side | Lots | TP 2% | Có trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 7882 | 1840 | 6041 | 1 | +1320.9877 | 53% |
| short | 8015 | 2450 | 5563 | 2 | +2444.0997 | 61% |
| **tổng** | **15897** | 4290 | 11604 | 3 | **+3765.0874** | 57% |

