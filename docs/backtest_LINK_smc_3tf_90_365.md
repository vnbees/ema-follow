# SMC + 3TF Filter — LINK 5m · 30 / 90 / 365 ngày

- Sinh lúc: 2026-08-18 14:57:57 +07
- **SMC base**: sweep_low → long, sweep_high → short · box 50 nến · range ≤ 2%
- **3TF filter**: 5m + 1h + 4h aligned label (`TREND_UP / TREND_DOWN / NO_TREND`)
- SL 0.2% ngoài râu · TP = biên đối diện · Risk 1%/lệnh · 10x · phí 0.04%/side
- So sánh thêm: SMC thuần (không filter) từ backtest trước

## Tổng hợp

| Cửa sổ | Filter | Signals | Trades | WR | PnL | % | Max DD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 30d | **No filter** | 334 | 149 | 33% | +104.5 | +10.4% | -251 |
| 30d | Strict (L: TREND_UP only, S: TREND_DOWN only) | 4 | 4 | 25% | **-14.9** | **-1.5%** | -38 |
| 30d | NO_TREND only (L+S chỉ khi sideway) | 226 | 122 | 30% | **-138.9** | **-13.9%** | -283 |
| 30d | Aligned (L: UP|NO_TREND, S: DOWN|NO_TREND) | 230 | 125 | 30% | **-140.8** | **-14.1%** | -311 |
| 90d | **No filter** | 862 | 419 | 29% | -383.3 | -38.3% | -460 |
| 90d | Strict (L: TREND_UP only, S: TREND_DOWN only) | 10 | 10 | 40% | **+37.7** | **+3.8%** | -40 |
| 90d | NO_TREND only (L+S chỉ khi sideway) | 585 | 319 | 26% | **-496.6** | **-49.7%** | -521 |
| 90d | Aligned (L: UP|NO_TREND, S: DOWN|NO_TREND) | 595 | 324 | 27% | **-472.5** | **-47.3%** | -498 |
| 365d | **No filter** | 3083 | 1528 | 26% | -961.1 | -96.1% | -966 |
| 365d | Strict (L: TREND_UP only, S: TREND_DOWN only) | 50 | 47 | 36% | **+20.3** | **+2.0%** | -82 |
| 365d | NO_TREND only (L+S chỉ khi sideway) | 2181 | 1191 | 25% | **-952.0** | **-95.2%** | -954 |
| 365d | Aligned (L: UP|NO_TREND, S: DOWN|NO_TREND) | 2231 | 1214 | 25% | **-951.6** | **-95.2%** | -954 |

## 30 ngày

- Cửa sổ: 2026-07-19 14:55 → 2026-08-18 14:50
- Giá: 8.3650 → 9.4020 (+12.40%)
- 3TF TREND_UP / TREND_DOWN / NO_TREND: 1833 / 734 / 6073

## 90 ngày

- Cửa sổ: 2026-05-20 14:55 → 2026-08-18 14:50
- Giá: 9.5740 → 9.4020 (-1.80%)
- 3TF TREND_UP / TREND_DOWN / NO_TREND: 3710 / 4171 / 18039

## 365 ngày

- Cửa sổ: 2025-08-18 14:55 → 2026-08-18 14:50
- Giá: 24.6910 → 9.4020 (-61.92%)
- 3TF TREND_UP / TREND_DOWN / NO_TREND: 11917 / 18700 / 74503

