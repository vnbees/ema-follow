# TP 2% — đóng khi hết trend vs đợi đảo chiều (1 năm)

- Sinh lúc: 2026-08-18 12:59:33 +07
- Cửa sổ: **2025-08-18 12:55 → 2026-08-18 12:55**
- Giá: 24.7130 → 9.4290 (-61.85%)
- 3 khung hiện tại: **TREND_UP**

## Rule

Add: TREND_UP + nến đỏ → long TP 2%; TREND_DOWN + nến xanh → short TP 2%. **Không add khi avg đã lời.**

- A: đóng hết khi 3 khung **đảo chiều** (long đợi TREND_DOWN, short đợi TREND_UP). Giữ qua NO_TREND.
- B: đóng hết khi 3 khung **hết trend** (long thoát ngay NO_TREND/TREND_DOWN).

## So sánh (cùng skip avg lời)

| | A Đợi đảo chiều | **B Hết trend thì đóng** |
| --- | --- | --- |
| Số lot | 7936 | **7186** |
| Peak long / short | 113 / 207 | **30 / 27** |
| TP 2% | 5693 (+10932) | 851 (+1634) |
| Thoát sớm | 2164 (-14531) | 6333 (-2586) |
| EOD còn mở | 79 | 2 |
| PnL tổng | -3624.80 | **-952.27** |
| WR đóng | 72% | 33% |
| WR thoát sớm | 0% | 24% |
| Phí | 635.05 | 574.87 |

## A — Đợi đảo chiều (giữ qua NO_TREND)

- Long add 2772 (skip 2995, peak 113 lot) · Short add 5164 (skip 3868, peak 207 lot)
- **PnL tổng: -3624.8012 USDT** (đóng -3599.2261 · EOD -25.5751 · phí 635.0489)
- TP +10932.11 · thoát sớm -14531.34 (WR thoát 0%)
- Long: -1505.0380 (TP 1877, thoát 816, EOD 79) · WR đóng 1877/2693 = 70%
- Short: -2119.7632 (TP 3816, thoát 1348, EOD 0)

| Side | Lots | TP 2% | Đảo 3 khung | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 2772 | 1877 | 816 | 79 | -1505.0380 | 70% |
| short | 5164 | 3816 | 1348 | 0 | -2119.7632 | 74% |
| **tổng** | **7936** | 5693 | 2164 | 79 | **-3624.8012** | 72% |

## B — Hết trend thì đóng

- Long add 2729 (skip 3038, peak 30 lot) · Short add 4457 (skip 4575, peak 27 lot)
- **PnL tổng: -952.2654 USDT** (đóng -951.8938 · EOD -0.3716 · phí 574.8690)
- TP +1634.26 · thoát sớm -2586.16 (WR thoát 24%)
- Long: -420.6819 (TP 211, thoát 2516, EOD 2) · WR đóng 811/2727 = 30%
- Short: -531.5835 (TP 640, thoát 3817, EOD 0)

| Side | Lots | TP 2% | Hết trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 2729 | 211 | 2516 | 2 | -420.6819 | 30% |
| short | 4457 | 640 | 3817 | 0 | -531.5835 | 35% |
| **tổng** | **7186** | 851 | 6333 | 2 | **-952.2654** | 33% |

