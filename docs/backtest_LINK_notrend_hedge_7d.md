# NO_TREND hedge 2 chiều — 7 ngày

- Sinh lúc: 2026-08-18 13:12:35 +07
- Cửa sổ: **2026-08-11 13:10 → 2026-08-18 13:10**
- Giá: 8.4270 → 9.4150 (+11.72%)
- Nến: NO_TREND 1061 · TREND_UP 955 · TREND_DOWN 0

## Rule

Chỉ vào khi **3 khung = NO_TREND**. Mỗi lần vào mở **long 100 + short 100** (cùng giá close). Từng chân chốt **TP 2%**. Còn lại đóng hết khi 3 khung thành TREND_UP hoặc TREND_DOWN. Không vào khi đang có trend.

- A: **1 cặp** — chỉ mở khi cả hai sổ trống (không chồng lot).
- B: **Scale** — mỗi nến NO_TREND add thêm 2 chân, **skip chân nào avg đã lời**.

## So sánh

| | A 1 cặp | **B Scale skip avg lời** |
| --- | --- | --- |
| Lots | 76 | **803** |
| Peak 2 chân | 2 | **127** |
| TP 2% | 2 (+4) | 13 (+25) |
| Đóng vì có trend | 72 (-8) | 787 (+92) |
| EOD | 2 | 3 |
| PnL tổng | -4.40 | **+116.94** |
| WR đóng | 43% | 56% |
| Phí | 6.08 | 64.33 |

## A — 1 cặp long+short khi sổ trống

- Nến NO_TREND 1061 · có trend 955 · lần mở hedge 38
- Long add 38 (skip 0, peak 1) · Short add 38 (skip 0, peak 1) · peak 2 chân 2 lot ≈ 200 USDT
- **PnL tổng: -4.3963 USDT** · TP +3.84 · hết trend -8.08 · EOD -0.16 · phí 6.08
- Equity min/max -4/+0 · Max DD -5 · MTM tệ nhất -2
- Long -0.2980 (TP 0, trend 37, EOD 1) · Short -4.0983 (TP 2, trend 35, EOD 1)

| Side | Lots | TP 2% | Có trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 38 | 0 | 37 | 1 | -0.2980 | 51% |
| short | 38 | 2 | 35 | 1 | -4.0983 | 35% |
| **tổng** | **76** | 2 | 72 | 2 | **-4.3963** | 43% |

## B — Scale mỗi nến NO_TREND, skip avg lời

- Nến NO_TREND 1061 · có trend 955 · lần mở hedge 729
- Long add 573 (skip 488, peak 111) · Short add 230 (skip 831, peak 27) · peak 2 chân 127 lot ≈ 12700 USDT
- **PnL tổng: +116.9381 USDT** · TP +24.96 · hết trend +92.22 · EOD -0.24 · phí 64.33
- Equity min/max -46/+124 · Max DD -101 · MTM tệ nhất -95
- Long +160.5662 (TP 5, trend 567, EOD 1) · Short -43.6282 (TP 8, trend 220, EOD 2)

| Side | Lots | TP 2% | Có trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 573 | 5 | 567 | 1 | +160.5662 | 72% |
| short | 230 | 8 | 220 | 2 | -43.6282 | 18% |
| **tổng** | **803** | 13 | 787 | 3 | **+116.9381** | 56% |

