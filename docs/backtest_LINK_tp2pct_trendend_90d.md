# TP 2% — đóng khi hết trend vs đợi đảo chiều (3 tháng)

- Sinh lúc: 2026-08-18 12:59:32 +07
- Cửa sổ: **2026-05-20 12:55 → 2026-08-18 12:55**
- Giá: 9.5750 → 9.4290 (-1.52%)
- 3 khung hiện tại: **TREND_UP**

## Rule

Add: TREND_UP + nến đỏ → long TP 2%; TREND_DOWN + nến xanh → short TP 2%. **Không add khi avg đã lời.**

- A: đóng hết khi 3 khung **đảo chiều** (long đợi TREND_DOWN, short đợi TREND_UP). Giữ qua NO_TREND.
- B: đóng hết khi 3 khung **hết trend** (long thoát ngay NO_TREND/TREND_DOWN).

## So sánh (cùng skip avg lời)

| | A Đợi đảo chiều | **B Hết trend thì đóng** |
| --- | --- | --- |
| Số lot | 1797 | **1795** |
| Peak long / short | 113 / 119 | **30 / 24** |
| TP 2% | 1334 (+2562) | 165 (+317) |
| Thoát sớm | 384 (-2348) | 1628 (-550) |
| EOD còn mở | 79 | 2 |
| PnL tổng | +188.13 | **-233.12** |
| WR đóng | 78% | 29% |
| WR thoát sớm | 0% | 22% |
| Phí | 143.81 | 143.60 |

## A — Đợi đảo chiều (giữ qua NO_TREND)

- Long add 664 (skip 1103, peak 113 lot) · Short add 1133 (skip 881, peak 119 lot)
- **PnL tổng: +188.1268 USDT** (đóng +213.7019 · EOD -25.5751 · phí 143.8133)
- TP +2561.61 · thoát sớm -2347.90 (WR thoát 0%)
- Long: +179.4147 (TP 463, thoát 122, EOD 79) · WR đóng 463/585 = 79%
- Short: +8.7120 (TP 871, thoát 262, EOD 0)

| Side | Lots | TP 2% | Đảo 3 khung | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 664 | 463 | 122 | 79 | +179.4147 | 79% |
| short | 1133 | 871 | 262 | 0 | +8.7120 | 77% |
| **tổng** | **1797** | 1334 | 384 | 79 | **+188.1268** | 78% |

## B — Hết trend thì đóng

- Long add 751 (skip 1016, peak 30 lot) · Short add 1044 (skip 970, peak 24 lot)
- **PnL tổng: -233.1207 USDT** (đóng -232.7491 · EOD -0.3716 · phí 143.5966)
- TP +316.87 · thoát sớm -549.62 (WR thoát 22%)
- Long: -109.0331 (TP 38, thoát 711, EOD 2) · WR đóng 210/749 = 28%
- Short: -124.0876 (TP 127, thoát 917, EOD 0)

| Side | Lots | TP 2% | Hết trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 751 | 38 | 711 | 2 | -109.0331 | 28% |
| short | 1044 | 127 | 917 | 0 | -124.0876 | 30% |
| **tổng** | **1795** | 165 | 1628 | 2 | **-233.1207** | 29% |

