# TP 2% 3 khung 1 năm — so sánh skip khi avg đã lời

- Sinh lúc: 2026-08-18 12:35:00 +07
- Cửa sổ: **2025-08-18 12:30 → 2026-08-18 12:30**
- Giá: 24.6480 → 9.4480 (-61.67%)
- 3 khung hiện tại: **NO_TREND**

## Rule chung

TREND_DOWN + nến xanh → short TP 2%. TREND_UP + nến đỏ → long TP 2%. Đóng hết khi 3 khung đảo. Biến thể: **không add nếu avg vị thế đã lời**.

## So sánh

| | Add mọi nến | **Không add khi avg lời** |
| --- | --- | --- |
| Số lot | 14797 | **7934** |
| Peak long / short | 258 / 253 | **113 / 207** |
| Skip | 0 | L 2995 / S 3868 |
| TP 2% | 11073 | 5693 |
| Đảo 3 khung | 3548 | 2164 |
| EOD còn mở | 176 | 77 |
| PnL tổng | -3506.44 | **-3608.96** |
| PnL đã đóng | -3418.37 | -3599.23 |
| PnL EOD | -88.08 | -9.73 |
| Phí | 1183.47 | 634.90 |

## A — Add mọi nến đỏ/xanh

- Long add 5765 (skip 0, peak 258 lot) · Short add 9032 (skip 0, peak 253 lot)
- **PnL tổng: -3506.4429 USDT** (đóng -3418.3664 · EOD -88.0765 · phí 1183.4706)
- Long: -1983.7738 (TP 4069, đảo 1520, EOD 176) · WR đóng 4069/5589 = 73%
- Short: -1522.6691 (TP 7004, đảo 2028, EOD 0)

| Side | Lots | TP 2% | Đảo 3 khung | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 5765 | 4069 | 1520 | 176 | -1983.7738 | 73% |
| short | 9032 | 7004 | 2028 | 0 | -1522.6691 | 78% |
| **tổng** | **14797** | 11073 | 3548 | 176 | **-3506.4429** | 76% |

## B — Không add khi avg đã lời

- Long add 2770 (skip 2995, peak 113 lot) · Short add 5164 (skip 3868, peak 207 lot)
- **PnL tổng: -3608.9583 USDT** (đóng -3599.2261 · EOD -9.7322 · phí 634.8951)
- Long: -1489.1951 (TP 1877, đảo 816, EOD 77) · WR đóng 1877/2693 = 70%
- Short: -2119.7632 (TP 3816, đảo 1348, EOD 0)

| Side | Lots | TP 2% | Đảo 3 khung | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 2770 | 1877 | 816 | 77 | -1489.1951 | 70% |
| short | 5164 | 3816 | 1348 | 0 | -2119.7632 | 74% |
| **tổng** | **7934** | 5693 | 2164 | 77 | **-3608.9583** | 72% |

(Bản B có 7934 lot — bỏ bảng chi tiết.)

