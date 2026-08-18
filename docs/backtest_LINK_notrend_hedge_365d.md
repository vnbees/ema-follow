# NO_TREND hedge 2 chiều — 1 năm

- Sinh lúc: 2026-08-18 13:12:41 +07
- Cửa sổ: **2025-08-18 13:10 → 2026-08-18 13:10**
- Giá: 24.4580 → 9.4150 (-61.51%)
- Nến: NO_TREND 74515 · TREND_UP 11905 · TREND_DOWN 18700

## Rule

Chỉ vào khi **3 khung = NO_TREND**. Mỗi lần vào mở **long 100 + short 100** (cùng giá close). Từng chân chốt **TP 2%**. Còn lại đóng hết khi 3 khung thành TREND_UP hoặc TREND_DOWN. Không vào khi đang có trend.

- A: **1 cặp** — chỉ mở khi cả hai sổ trống (không chồng lot).
- B: **Scale** — mỗi nến NO_TREND add thêm 2 chân, **skip chân nào avg đã lời**.

## So sánh

| | A 1 cặp | **B Scale skip avg lời** |
| --- | --- | --- |
| Lots | 2742 | **70323** |
| Peak 2 chân | 2 | **952** |
| TP 2% | 237 (+455) | 20431 (+39228) |
| Đóng vì có trend | 2503 (-658) | 49889 (-33948) |
| EOD | 2 | 3 |
| PnL tổng | -203.34 | **+5279.17** |
| WR đóng | 45% | 56% |
| Phí | 219.30 | 5626.93 |

## A — 1 cặp long+short khi sổ trống

- Nến NO_TREND 74515 · có trend 30605 · lần mở hedge 1371
- Long add 1371 (skip 0, peak 1) · Short add 1371 (skip 0, peak 1) · peak 2 chân 2 lot ≈ 200 USDT
- **PnL tổng: -203.3438 USDT** · TP +455.04 · hết trend -658.22 · EOD -0.16 · phí 219.30
- Equity min/max -203/+13 · Max DD -216 · MTM tệ nhất -19
- Long -177.8735 (TP 121, trend 1249, EOD 1) · Short -25.4703 (TP 116, trend 1254, EOD 1)

| Side | Lots | TP 2% | Có trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 1371 | 121 | 1249 | 1 | -177.8735 | 40% |
| short | 1371 | 116 | 1254 | 1 | -25.4703 | 50% |
| **tổng** | **2742** | 237 | 2503 | 2 | **-203.3438** | 45% |

## B — Scale mỗi nến NO_TREND, skip avg lời

- Nến NO_TREND 74515 · có trend 30605 · lần mở hedge 60665
- Long add 34475 (skip 40040, peak 869) · Short add 35848 (skip 38667, peak 564) · peak 2 chân 952 lot ≈ 95200 USDT
- **PnL tổng: +5279.1655 USDT** · TP +39227.64 · hết trend -33948.24 · EOD -0.24 · phí 5626.93
- Equity min/max -738/+5326 · Max DD -4642 · MTM tệ nhất -2274
- Long +4051.8625 (TP 10140, trend 24334, EOD 1) · Short +1227.3030 (TP 10291, trend 25555, EOD 2)

| Side | Lots | TP 2% | Có trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 34475 | 10140 | 24334 | 1 | +4051.8625 | 54% |
| short | 35848 | 10291 | 25555 | 2 | +1227.3030 | 58% |
| **tổng** | **70323** | 20431 | 49889 | 3 | **+5279.1655** | 56% |

