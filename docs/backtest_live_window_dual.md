# Backtest giai đoạn live: market Config A (cũ) vs limit 2 chiều (mới)

- Cửa sổ: **2026-08-28T09:00 → 2026-10-10 03:30 UTC** (42.8 ngày), 20 coin, nến 15m
- Vốn đầu = equity live lúc bắt đầu: **1051.47 USDT** · 10x · margin 1% × size_mult (cap 15%)
- Phí: market taker 0.05%, limit maker 0.02%. Không tính funding.
- Cũ: top 5/cycle, max 10 lot, quy tắc ngược 6h, lệnh 2 (0,4 ATR). Mới: mọi tín hiệu → LONG + SHORT limit @ close, khớp nếu nến sau xuyên giá, hủy sau 1 nến, max 10 coin, không giới hạn lot. Limit 1 chiều: như Mới nhưng chỉ đặt chiều của tín hiệu.
- TP: band Donchian live (19 nến trước + nến đang chạy), không SL, không chốt trên nến vào.
- Tín hiệu đối chiếu với `check_signal` của bot: 0 nến lệch.

| | Live thật | BT cũ (market) | BT mới (2 chiều) | BT limit 1 chiều |
|---|---|---|---|---|
| Lệnh đã đóng | 1181 | 1536 | 2704 | 1530 |
| Winrate | 69.0% | 71.2% | 71.2% | 69.5% |
| Profit factor | 1.00 | 1.13 | 0.87 | 1.10 |
| Lãi đã chốt (USDT) | +1.48 | +229.53 | -279.59 | +174.64 |
| Lãi/lỗ tạm tính cuối kỳ | -66.97 (8 lot) | -102.46 (7 lot) | -57.31 (11 lot) | -57.69 (5 lot) |
| **Tổng** | **-65.50** | **+127.07** | **-336.90** | **+116.95** |
| Lệnh lỗ nặng nhất | -36.51 | -51.19 | -83.30 | -43.25 |
| Equity thấp nhất | 888.81 | 1003.02 | 668.98 | 1018.82 |
| **Max drawdown equity** | 19.1% | **18.1%** | **41.1%** | **16.1%** |
| Lúc drawdown sâu nhất (UTC) | 10-08 05:00 | 09-18 20:45 | 09-25 12:00 | 09-18 20:30 |
| Số lot mở cùng lúc tối đa | — | 10 | 30 | 19 |
| Margin bị khóa tối đa (% equity) | — | 20.8% | 41.4% | 28.7% |
| Available thấp nhất (% equity) | — | 77.7% | 59.8% | 74.5% |
| Lệnh limit đặt / khớp | — | — | 1403 / 2715 | 1603 / 1535 |

## Tách theo loại lệnh (đã đóng)

| Kịch bản | Loại | Số lệnh | Winrate | Lãi đã chốt | TB giữ (giờ) | Còn mở cuối kỳ |
|---|---|---|---|---|---|---|
| BT cũ | lệnh 2 | 531 | 73.8% | +68.63 | 4.6 | 2 |
| BT cũ | quy tắc ngược | 59 | 67.8% | -15.76 | 6.9 | 1 |
| BT cũ | lệnh thường | 946 | 69.9% | +176.66 | 6.4 | 4 |
| BT mới | chân ngược chiều tín hiệu | 1360 | 72.2% | -523.19 | 4.9 | 6 |
| BT mới | chân cùng chiều tín hiệu | 1344 | 70.2% | +243.60 | 6.0 | 5 |
| BT limit 1 chiều | chân cùng chiều tín hiệu | 1530 | 69.5% | +174.64 | 6.1 | 5 |

Live thật: equity snapshot gồm cả phần đã rút sang spot (nếu có) và nhiều lần đổi config trong kỳ, nên chỉ để tham chiếu.
