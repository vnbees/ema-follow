# TP 2% 3 khung 3 tháng — so sánh skip khi avg đã lời

- Sinh lúc: 2026-08-18 12:34:59 +07
- Cửa sổ: **2026-05-20 12:30 → 2026-08-18 12:30**
- Giá: 9.5810 → 9.4480 (-1.39%)
- 3 khung hiện tại: **NO_TREND**

## Rule chung

TREND_DOWN + nến xanh → short TP 2%. TREND_UP + nến đỏ → long TP 2%. Đóng hết khi 3 khung đảo. Biến thể: **không add nếu avg vị thế đã lời**.

## So sánh

| | Add mọi nến | **Không add khi avg lời** |
| --- | --- | --- |
| Số lot | 3779 | **1795** |
| Peak long / short | 258 / 171 | **113 / 119** |
| Skip | 0 | L 1103 / S 881 |
| TP 2% | 3033 | 1334 |
| Đảo 3 khung | 570 | 384 |
| EOD còn mở | 176 | 77 |
| PnL tổng | +2363.28 | **+203.97** |
| PnL đã đóng | +2451.36 | +213.70 |
| PnL EOD | -88.08 | -9.73 |
| Phí | 302.74 | 143.66 |

## A — Add mọi nến đỏ/xanh

- Long add 1765 (skip 0, peak 258 lot) · Short add 2014 (skip 0, peak 171 lot)
- **PnL tổng: +2363.2843 USDT** (đóng +2451.3608 · EOD -88.0765 · phí 302.7352)
- Long: +1710.1068 (TP 1423, đảo 166, EOD 176) · WR đóng 1423/1589 = 90%
- Short: +653.1776 (TP 1610, đảo 404, EOD 0)

| Side | Lots | TP 2% | Đảo 3 khung | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 1765 | 1423 | 166 | 176 | +1710.1068 | 90% |
| short | 2014 | 1610 | 404 | 0 | +653.1776 | 80% |
| **tổng** | **3779** | 3033 | 570 | 176 | **+2363.2843** | 84% |

## B — Không add khi avg đã lời

- Long add 662 (skip 1103, peak 113 lot) · Short add 1133 (skip 881, peak 119 lot)
- **PnL tổng: +203.9697 USDT** (đóng +213.7019 · EOD -9.7322 · phí 143.6596)
- Long: +195.2577 (TP 463, đảo 122, EOD 77) · WR đóng 463/585 = 79%
- Short: +8.7120 (TP 871, đảo 262, EOD 0)

| Side | Lots | TP 2% | Đảo 3 khung | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 662 | 463 | 122 | 77 | +195.2577 | 79% |
| short | 1133 | 871 | 262 | 0 | +8.7120 | 77% |
| **tổng** | **1795** | 1334 | 384 | 77 | **+203.9697** | 78% |

(Bản B có 1795 lot — bỏ bảng chi tiết.)

