# TP 2% — đóng khi hết trend vs đợi đảo chiều (7 ngày)

- Sinh lúc: 2026-08-18 12:59:32 +07
- Cửa sổ: **2026-08-11 12:55 → 2026-08-18 12:55**
- Giá: 8.4140 → 9.4290 (+12.06%)
- 3 khung hiện tại: **TREND_UP**

## Rule

Add: TREND_UP + nến đỏ → long TP 2%; TREND_DOWN + nến xanh → short TP 2%. **Không add khi avg đã lời.**

- A: đóng hết khi 3 khung **đảo chiều** (long đợi TREND_DOWN, short đợi TREND_UP). Giữ qua NO_TREND.
- B: đóng hết khi 3 khung **hết trend** (long thoát ngay NO_TREND/TREND_DOWN).

## So sánh (cùng skip avg lời)

| | A Đợi đảo chiều | **B Hết trend thì đóng** |
| --- | --- | --- |
| Số lot | 133 | **158** |
| Peak long / short | 81 / 0 | **30 / 0** |
| TP 2% | 54 (+104) | 13 (+25) |
| Thoát sớm | 0 (+0) | 143 (-41) |
| EOD còn mở | 79 | 2 |
| PnL tổng | +78.06 | **-16.02** |
| WR đóng | 100% | 31% |
| WR thoát sớm | 0% | 25% |
| Phí | 10.68 | 12.64 |

## A — Đợi đảo chiều (giữ qua NO_TREND)

- Long add 133 (skip 324, peak 81 lot) · Short add 0 (skip 0, peak 0 lot)
- **PnL tổng: +78.0617 USDT** (đóng +103.6368 · EOD -25.5751 · phí 10.6755)
- TP +103.64 · thoát sớm +0.00 (WR thoát 0%)
- Long: +78.0617 (TP 54, thoát 0, EOD 79) · WR đóng 54/54 = 100%
- Short: +0.0000 (TP 0, thoát 0, EOD 0)

| Side | Lots | TP 2% | Đảo 3 khung | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 133 | 54 | 0 | 79 | +78.0617 | 100% |
| short | 0 | 0 | 0 | 0 | +0.0000 | 0% |
| **tổng** | **133** | 54 | 0 | 79 | **+78.0617** | 100% |

## B — Hết trend thì đóng

- Long add 158 (skip 299, peak 30 lot) · Short add 0 (skip 0, peak 0 lot)
- **PnL tổng: -16.0230 USDT** (đóng -15.6514 · EOD -0.3716 · phí 12.6386)
- TP +24.95 · thoát sớm -40.60 (WR thoát 25%)
- Long: -16.0230 (TP 13, thoát 143, EOD 2) · WR đóng 49/156 = 31%
- Short: +0.0000 (TP 0, thoát 0, EOD 0)

| Side | Lots | TP 2% | Hết trend | EOD | PnL | WR đóng |
| --- | --- | --- | --- | --- | --- | --- |
| long | 158 | 13 | 143 | 2 | -16.0230 | 31% |
| short | 0 | 0 | 0 | 0 | +0.0000 | 0% |
| **tổng** | **158** | 13 | 143 | 2 | **-16.0230** | 31% |

