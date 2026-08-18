# TP 2% 3 khung — 3 tháng / 6 tháng / 1 năm

- Sinh lúc: 2026-08-18 12:35:00 +07
- Rule: TREND_DOWN + nến xanh → short TP 2%; TREND_UP + nến đỏ → long TP 2%; đóng hết khi 3 khung đảo. So sánh **add mọi nến** vs **không add khi avg đã lời**.

## PnL (bản skip avg lời)

| Cửa sổ | Giá | Lots | Peak L/S | TP | Đảo | EOD | PnL tổng | Đóng | EOD MTM |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3 tháng (2026-05-20→2026-08-18) | -1.4% | 1795 | 113/119 | 1334 | 384 | 77 | **+204** | +214 | -10 |
| 6 tháng (2026-02-19→2026-08-18) | +9.4% | 3350 | 113/119 | 2349 | 924 | 77 | **-1147** | -1137 | -10 |
| 1 năm (2025-08-18→2026-08-18) | -61.7% | 7934 | 113/207 | 5693 | 2164 | 77 | **-3609** | -3599 | -10 |

## PnL (add mọi nến)

| Cửa sổ | Lots | Peak L/S | TP | Đảo | EOD | PnL tổng | Đóng | EOD MTM |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3 tháng | 3779 | 258/171 | 3033 | 570 | 176 | **+2363** | +2451 | -88 |
| 6 tháng | 7181 | 258/171 | 5322 | 1683 | 176 | **-412** | -323 | -88 |
| 1 năm | 14797 | 258/253 | 11073 | 3548 | 176 | **-3506** | -3418 | -88 |

Chi tiết:
- [backtest_LINK_tp2pct_3tf_90d.md](backtest_LINK_tp2pct_3tf_90d.md)
- [backtest_LINK_tp2pct_3tf_180d.md](backtest_LINK_tp2pct_3tf_180d.md)
- [backtest_LINK_tp2pct_3tf_365d.md](backtest_LINK_tp2pct_3tf_365d.md)

