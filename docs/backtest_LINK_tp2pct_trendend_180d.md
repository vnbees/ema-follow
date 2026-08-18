# TP 2% — đóng khi hết trend vs đợi đảo chiều (6 tháng)

- Sinh lúc: 2026-08-18 12:59:32 +07
- Cửa sổ: **2026-02-19 12:55 → 2026-08-18 12:55**
- Giá: 8.6570 → 9.4290 (+8.92%)
- 3 khung hiện tại: **TREND_UP**

## Rule

Add: TREND_UP + nến đỏ → long TP 2%; TREND_DOWN + nến xanh → short TP 2%. **Không add khi avg đã lời.**

- A: đóng hết khi 3 khung **đảo chiều** (long đợi TREND_DOWN, short đợi TREND_UP). Giữ qua NO_TREND.
- B: đóng hết khi 3 khung **hết trend** (long thoát ngay NO_TREND/TREND_DOWN).

## So sánh (cùng skip avg lời)

| | A Đợi đảo chiều | **B Hết trend thì đóng** |
| --- | --- | --- |
| Số lot | 3352 | **3387** |
| Peak long / short | 113 / 119 | **30 / 25** |
| TP 2% | 2349 (+4510) | 279 (+536) |
| Thoát sớm | 924 (-5647) | 3106 (-904) |
| EOD còn mở | 79 | 2 |
| PnL tổng | -1162.76 | **-368.55** |
| WR đóng | 72% | 30% |
| WR thoát sớm | 0% | 24% |
| Phí | 268.52 | 270.98 |

## A — Đợi đảo chiều (giữ qua NO_TREND)

- Long add 1700 (skip 2086, peak 113 lot) · Short add 1652 (skip 1745, peak 119 lot)
- **PnL tổng: -1162.7553 USDT** (đóng -1137.1802 · EOD -25.5751 · phí 268.5223)
- TP +4510.01 · thoát sớm -5647.19 (WR thoát 0%)
- Long: -130.1982 (TP 1217, thoát 404, EOD 79) · WR đóng 1217/1621 = 75%
- Short: -1032.5571 (TP 1132, thoát 520, EOD 0)

| Side | Lots | TP 2% | Đảo 3 khung | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 1700 | 1217 | 404 | 79 | -130.1982 | 75% |
| short | 1652 | 1132 | 520 | 0 | -1032.5571 | 69% |
| **tổng** | **3352** | 2349 | 924 | 79 | **-1162.7553** | 72% |

## B — Hết trend thì đóng

- Long add 1709 (skip 2077, peak 30 lot) · Short add 1678 (skip 1719, peak 25 lot)
- **PnL tổng: -368.5453 USDT** (đóng -368.1738 · EOD -0.3716 · phí 270.9761)
- TP +535.71 · thoát sớm -903.89 (WR thoát 24%)
- Long: -165.3393 (TP 119, thoát 1588, EOD 2) · WR đóng 526/1707 = 31%
- Short: -203.2060 (TP 160, thoát 1518, EOD 0)

| Side | Lots | TP 2% | Hết trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 1709 | 119 | 1588 | 2 | -165.3393 | 31% |
| short | 1678 | 160 | 1518 | 0 | -203.2060 | 29% |
| **tổng** | **3387** | 279 | 3106 | 2 | **-368.5453** | 30% |

