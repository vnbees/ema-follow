# TP 2% — đóng khi hết trend vs đợi đảo chiều

- Sinh lúc: 2026-08-18 12:59:33 +07
- Add: skip khi avg đã lời. Long TREND_UP + nến đỏ, short TREND_DOWN + nến xanh, TP 2%.
- **Hết trend** = 3 khung không còn TREND_UP (long) / TREND_DOWN (short), kể cả NO_TREND.

## PnL — hết trend thì đóng

| Cửa sổ | Giá | Lots | Peak L/S | TP | Thoát | EOD | PnL | TP PnL | Thoát PnL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 7 ngày (2026-08-11→2026-08-18) | +12.1% | 158 | 30/0 | 13 | 143 | 2 | **-16** | +25 | -41 |
| 3 tháng (2026-05-20→2026-08-18) | -1.5% | 1795 | 30/24 | 165 | 1628 | 2 | **-233** | +317 | -550 |
| 6 tháng (2026-02-19→2026-08-18) | +8.9% | 3387 | 30/25 | 279 | 3106 | 2 | **-369** | +536 | -904 |
| 1 năm (2025-08-18→2026-08-18) | -61.8% | 7186 | 30/27 | 851 | 6333 | 2 | **-952** | +1634 | -2586 |

## PnL — đợi đảo chiều (bản cũ, skip avg)

| Cửa sổ | Lots | Peak L/S | TP | Đảo | EOD | PnL | TP PnL | Đảo PnL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 7 ngày | 133 | 81/0 | 54 | 0 | 79 | **+78** | +104 | +0 |
| 3 tháng | 1797 | 113/119 | 1334 | 384 | 79 | **+188** | +2562 | -2348 |
| 6 tháng | 3352 | 113/119 | 2349 | 924 | 79 | **-1163** | +4510 | -5647 |
| 1 năm | 7936 | 113/207 | 5693 | 2164 | 79 | **-3625** | +10932 | -14531 |

Chi tiết:
- [backtest_LINK_tp2pct_trendend_7d.md](backtest_LINK_tp2pct_trendend_7d.md)
- [backtest_LINK_tp2pct_trendend_90d.md](backtest_LINK_tp2pct_trendend_90d.md)
- [backtest_LINK_tp2pct_trendend_180d.md](backtest_LINK_tp2pct_trendend_180d.md)
- [backtest_LINK_tp2pct_trendend_365d.md](backtest_LINK_tp2pct_trendend_365d.md)

