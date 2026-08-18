# Regime switch + cap lot — LINK 1k/10x/0.5%

- Sinh lúc: 2026-08-18 14:11:50 +07
- **Regime:** long khi TREND_UP + nến đỏ, short khi TREND_DOWN + nến xanh; không giữ 2 chiều.
- **Long-only / Short-only:** cùng entry, chỉ 1 chiều.
- Skip avg đã lời · đóng khi 3 khung đảo · NO_TREND giữ lệnh không add.
- Vốn 1000 USDT · leverage 10x · 0.5% equity/lệnh · phí 0.04%/side

## 90 ngày

- Giá: 9.6030 → 9.4250 (-1.85%)
- Nến TREND_UP / TREND_DOWN / NO_TREND: 3708 / 4171 / 18041

| Mode | Cap lot | Vốn cuối | PnL | % | Round | WR | Peak lot | Max DD | Phí | Skip cap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Regime switch (1 chiều theo 3TF) | 5 | **1008.23** | +8.23 | +0.8% | 6 | 100% | L5/S5 | -5 | 0.1 | 3741 |
| Regime switch (1 chiều theo 3TF) | 10 | **1013.09** | +13.09 | +1.3% | 6 | 100% | L10/S10 | -8 | 0.2 | 1403 |
| Long-only (TREND_UP → exit TREND_DOWN) | 5 | **1004.14** | +4.14 | +0.4% | 3 | 100% | L5/S0 | -3 | 0.1 | 1756 |
| Long-only (TREND_UP → exit TREND_DOWN) | 10 | **1006.84** | +6.84 | +0.7% | 3 | 100% | L10/S0 | -4 | 0.1 | 518 |
| Short-only (TREND_DOWN → exit TREND_UP) | 5 | **1004.08** | +4.08 | +0.4% | 3 | 100% | L0/S5 | -4 | 0.1 | 1985 |
| Short-only (TREND_DOWN → exit TREND_UP) | 10 | **1006.21** | +6.21 | +0.6% | 3 | 100% | L0/S10 | -6 | 0.1 | 885 |

## 365 ngày

- Giá: 24.5680 → 9.4250 (-61.64%)
- Nến TREND_UP / TREND_DOWN / NO_TREND: 11915 / 18700 / 74505

| Mode | Cap lot | Vốn cuối | PnL | % | Round | WR | Peak lot | Max DD | Phí | Skip cap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Regime switch (1 chiều theo 3TF) | 5 | **995.03** | -4.97 | -0.5% | 45 | 34% | L5/S5 | -21 | 0.8 | 11706 |
| Regime switch (1 chiều theo 3TF) | 10 | **996.75** | -3.25 | -0.3% | 45 | 36% | L10/S10 | -26 | 1.3 | 8957 |
| Long-only (TREND_UP → exit TREND_DOWN) | 5 | **987.05** | -12.95 | -1.3% | 23 | 23% | L5/S0 | -22 | 0.4 | 4525 |
| Long-only (TREND_UP → exit TREND_DOWN) | 10 | **978.76** | -21.24 | -2.1% | 23 | 27% | L10/S0 | -37 | 0.7 | 3031 |
| Short-only (TREND_DOWN → exit TREND_UP) | 5 | **1008.09** | +8.09 | +0.8% | 22 | 45% | L0/S5 | -11 | 0.3 | 7181 |
| Short-only (TREND_DOWN → exit TREND_UP) | 10 | **1018.39** | +18.39 | +1.8% | 22 | 45% | L0/S10 | -17 | 0.6 | 5926 |

## So sánh nhanh — Regime switch

| Cửa sổ | Cap | PnL | % | Max DD | vs Long-only | vs Short-only |
| --- | --- | --- | --- | --- | --- | --- |
| 90d | 5 | **+8.23** | +0.8% | -5 | +4.09 vs long | +4.16 vs short |
| 90d | 10 | **+13.09** | +1.3% | -8 | +6.25 vs long | +6.88 vs short |
| 365d | 5 | **-4.97** | -0.5% | -21 | +7.98 vs long | -13.05 vs short |
| 365d | 10 | **-3.25** | -0.3% | -26 | +18.00 vs long | -21.63 vs short |

