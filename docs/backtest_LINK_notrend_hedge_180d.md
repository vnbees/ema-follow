# NO_TREND hedge 2 chiều — 6 tháng

- Sinh lúc: 2026-08-18 13:12:38 +07
- Cửa sổ: **2026-02-19 13:10 → 2026-08-18 13:10**
- Giá: 8.6840 → 9.4150 (+8.42%)
- Nến: NO_TREND 36989 · TREND_UP 7862 · TREND_DOWN 6989

## Rule

Chỉ vào khi **3 khung = NO_TREND**. Mỗi lần vào mở **long 100 + short 100** (cùng giá close). Từng chân chốt **TP 2%**. Còn lại đóng hết khi 3 khung thành TREND_UP hoặc TREND_DOWN. Không vào khi đang có trend.

- A: **1 cặp** — chỉ mở khi cả hai sổ trống (không chồng lot).
- B: **Scale** — mỗi nến NO_TREND add thêm 2 chân, **skip chân nào avg đã lời**.

## So sánh

| | A 1 cặp | **B Scale skip avg lời** |
| --- | --- | --- |
| Lots | 1390 | **33972** |
| Peak 2 chân | 2 | **715** |
| TP 2% | 87 (+167) | 8958 (+17198) |
| Đóng vì có trend | 1301 (-283) | 25011 (-15511) |
| EOD | 2 | 3 |
| PnL tổng | -115.88 | **+1687.65** |
| WR đóng | 44% | 55% |
| Phí | 111.19 | 2719.18 |

## A — 1 cặp long+short khi sổ trống

- Nến NO_TREND 36989 · có trend 14851 · lần mở hedge 695
- Long add 695 (skip 0, peak 1) · Short add 695 (skip 0, peak 1) · peak 2 chân 2 lot ≈ 200 USDT
- **PnL tổng: -115.8801 USDT** · TP +167.05 · hết trend -282.77 · EOD -0.16 · phí 111.19
- Equity min/max -116/+2 · Max DD -118 · MTM tệ nhất -13
- Long -71.7270 (TP 40, trend 654, EOD 1) · Short -44.1531 (TP 47, trend 647, EOD 1)

| Side | Lots | TP 2% | Có trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 695 | 40 | 654 | 1 | -71.7270 | 42% |
| short | 695 | 47 | 647 | 1 | -44.1531 | 46% |
| **tổng** | **1390** | 87 | 1301 | 2 | **-115.8801** | 44% |

## B — Scale mỗi nến NO_TREND, skip avg lời

- Nến NO_TREND 36989 · có trend 14851 · lần mở hedge 29499
- Long add 17855 (skip 19134, peak 530) · Short add 16117 (skip 20872, peak 502) · peak 2 chân 715 lot ≈ 71500 USDT
- **PnL tổng: +1687.6501 USDT** · TP +17198.47 · hết trend -15510.58 · EOD -0.24 · phí 2719.18
- Equity min/max -2629/+1734 · Max DD -3392 · MTM tệ nhất -2274
- Long +2547.5859 (TP 5034, trend 12820, EOD 1) · Short -859.9358 (TP 3924, trend 12191, EOD 2)

| Side | Lots | TP 2% | Có trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 17855 | 5034 | 12820 | 1 | +2547.5859 | 56% |
| short | 16117 | 3924 | 12191 | 2 | -859.9358 | 54% |
| **tổng** | **33972** | 8958 | 25011 | 3 | **+1687.6501** | 55% |

