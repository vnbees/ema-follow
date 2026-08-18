# TP 2% 3 khung 6 tháng — so sánh skip khi avg đã lời

- Sinh lúc: 2026-08-18 12:34:59 +07
- Cửa sổ: **2026-02-19 12:30 → 2026-08-18 12:30**
- Giá: 8.6330 → 9.4480 (+9.44%)
- 3 khung hiện tại: **NO_TREND**

## Rule chung

TREND_DOWN + nến xanh → short TP 2%. TREND_UP + nến đỏ → long TP 2%. Đóng hết khi 3 khung đảo. Biến thể: **không add nếu avg vị thế đã lời**.

## So sánh

| | Add mọi nến | **Không add khi avg lời** |
| --- | --- | --- |
| Số lot | 7181 | **3350** |
| Peak long / short | 258 / 171 | **113 / 119** |
| Skip | 0 | L 2086 / S 1745 |
| TP 2% | 5322 | 2349 |
| Đảo 3 khung | 1683 | 924 |
| EOD còn mở | 176 | 77 |
| PnL tổng | -411.53 | **-1146.91** |
| PnL đã đóng | -323.45 | -1137.18 |
| PnL EOD | -88.08 | -9.73 |
| Phí | 575.13 | 268.37 |

## A — Add mọi nến đỏ/xanh

- Long add 3784 (skip 0, peak 258 lot) · Short add 3397 (skip 0, peak 171 lot)
- **PnL tổng: -411.5250 USDT** (đóng -323.4485 · EOD -88.0765 · phí 575.1333)
- Long: +595.3116 (TP 2815, đảo 793, EOD 176) · WR đóng 2815/3608 = 78%
- Short: -1006.8367 (TP 2507, đảo 890, EOD 0)

| Side | Lots | TP 2% | Đảo 3 khung | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 3784 | 2815 | 793 | 176 | +595.3116 | 78% |
| short | 3397 | 2507 | 890 | 0 | -1006.8367 | 74% |
| **tổng** | **7181** | 5322 | 1683 | 176 | **-411.5250** | 76% |

## B — Không add khi avg đã lời

- Long add 1698 (skip 2086, peak 113 lot) · Short add 1652 (skip 1745, peak 119 lot)
- **PnL tổng: -1146.9123 USDT** (đóng -1137.1802 · EOD -9.7322 · phí 268.3686)
- Long: -114.3552 (TP 1217, đảo 404, EOD 77) · WR đóng 1217/1621 = 75%
- Short: -1032.5571 (TP 1132, đảo 520, EOD 0)

| Side | Lots | TP 2% | Đảo 3 khung | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 1698 | 1217 | 404 | 77 | -114.3552 | 75% |
| short | 1652 | 1132 | 520 | 0 | -1032.5571 | 69% |
| **tổng** | **3350** | 2349 | 924 | 77 | **-1146.9123** | 72% |

(Bản B có 3350 lot — bỏ bảng chi tiết.)

