# Elliott Wave (Vietcap rules) — backtest trên cache hiện có

- Generated: **2026-09-17 16:48:14 +07**
- Nguồn lý thuyết: [Vietcap – Áp dụng nguyên lý sóng Elliott](https://www.vietcap.com.vn/kien-thuc/ap-dung-nguyen-ly-song-elliott-trong-giao-dich-dau-tu-chung-khoan)
- Data: `data/bt_klines_15m/` → resample **1h** · ZigZag ATR×2.5
- Entry: sau W2→**W3**, sau W4→**W5**, sau WA→**WB** (Tip 1 bài viết)
- Filter Fib + 3 quy tắc cứng Elliott; risk 1% equity / lệnh; fee 0.04%/side; 1 position / coin
- Entry @ close nến **sau khi ZigZag xác nhận** pivot (không vào tại đáy/đỉnh chưa confirm)
- **Lưu ý:** đếm sóng Elliott mang tính chủ quan — đây là bản **quy tắc hóa gần đúng**, không phải đếm tay. Không thay Donchian live trừ khi edge rõ và ổn định.

## 365d · tf **1h** · eval từ 2025-09-16 · **20 coin**

- Tổng lệnh: **4193** · coin dương **7/20** · TB/coin: WR **53.9%** · PF mean/med **1.12/0.94** · Return **-4.8%** · MaxDD **23.1%** (mỗi coin ví $1000 riêng)

### Theo loại sóng (Tip 1)

| Kind | Trades | WR | Net $ (sum coins) |
| --- | ---: | ---: | ---: |
| W3 | 1454 | 36.9% | -299.5 |
| W5 | 80 | 46.2% | +78.3 |
| WB | 2659 | 61.8% | -729.6 |

### Theo coin (sort return)

| Symbol | Trades | WR | PF | Return | MaxDD |
| --- | ---: | ---: | ---: | ---: | ---: |
| SUIUSDT | 234 | 55.6% | 1.21 | +24.6% | 16.4% |
| AVAXUSDT | 226 | 57.1% | 1.19 | +19.5% | 22.1% |
| APTUSDT | 16 | 62.5% | 3.30 | +14.5% | 3.2% |
| XRPUSDT | 221 | 56.1% | 1.11 | +12.1% | 18.9% |
| OPUSDT | 20 | 65.0% | 2.34 | +10.6% | 6.6% |
| DOTUSDT | 228 | 57.0% | 1.05 | +5.5% | 19.0% |
| LINKUSDT | 232 | 52.2% | 1.02 | +2.1% | 16.2% |
| UNIUSDT | 234 | 54.7% | 0.99 | -1.0% | 26.7% |
| SOLUSDT | 228 | 53.9% | 0.98 | -2.0% | 18.8% |
| ATOMUSDT | 217 | 56.2% | 0.96 | -3.9% | 23.0% |
| LTCUSDT | 202 | 51.5% | 0.92 | -7.2% | 17.6% |
| BNBUSDT | 210 | 53.3% | 0.92 | -7.5% | 29.8% |
| NEARUSDT | 221 | 51.1% | 0.90 | -11.3% | 29.3% |
| XLMUSDT | 246 | 50.8% | 0.86 | -15.4% | 24.9% |
| ARBUSDT | 240 | 51.2% | 0.84 | -17.6% | 22.2% |
| BTCUSDT | 249 | 51.0% | 0.83 | -18.8% | 30.2% |
| TRXUSDT | 229 | 50.7% | 0.79 | -20.9% | 35.9% |
| ETHUSDT | 255 | 49.8% | 0.79 | -22.6% | 31.0% |
| BCHUSDT | 235 | 51.1% | 0.74 | -24.5% | 30.7% |
| ADAUSDT | 250 | 47.2% | 0.71 | -31.4% | 40.5% |

## 1095d · tf **1h** · eval từ 2023-09-17 · **20 coin**

- Tổng lệnh: **12787** · coin dương **2/20** · TB/coin: WR **51.6%** · PF mean/med **0.88/0.86** · Return **-26.7%** · MaxDD **47.0%** (mỗi coin ví $1000 riêng)

### Theo loại sóng (Tip 1)

| Kind | Trades | WR | Net $ (sum coins) |
| --- | ---: | ---: | ---: |
| W3 | 4531 | 37.4% | -730.4 |
| W5 | 227 | 41.9% | +103.7 |
| WB | 8029 | 59.9% | -4720.0 |

### Theo coin (sort return)

| Symbol | Trades | WR | PF | Return | MaxDD |
| --- | ---: | ---: | ---: | ---: | ---: |
| XLMUSDT | 712 | 52.5% | 1.04 | +15.7% | 27.4% |
| XRPUSDT | 678 | 55.2% | 1.02 | +6.5% | 23.2% |
| UNIUSDT | 661 | 51.4% | 0.99 | -2.6% | 29.6% |
| DOTUSDT | 711 | 53.0% | 0.97 | -8.0% | 32.1% |
| APTUSDT | 401 | 52.1% | 0.94 | -9.3% | 27.8% |
| AVAXUSDT | 555 | 51.9% | 0.95 | -10.4% | 43.9% |
| NEARUSDT | 449 | 50.3% | 0.93 | -16.1% | 32.4% |
| LTCUSDT | 663 | 50.5% | 0.90 | -24.9% | 44.2% |
| ATOMUSDT | 683 | 52.7% | 0.90 | -25.4% | 41.2% |
| BNBUSDT | 690 | 53.9% | 0.86 | -34.0% | 53.3% |
| ARBUSDT | 567 | 51.3% | 0.83 | -34.8% | 43.3% |
| SOLUSDT | 720 | 51.4% | 0.86 | -35.4% | 48.1% |
| LINKUSDT | 610 | 50.2% | 0.83 | -36.0% | 46.2% |
| OPUSDT | 487 | 50.1% | 0.81 | -37.0% | 55.9% |
| BTCUSDT | 722 | 50.7% | 0.85 | -38.4% | 58.2% |
| TRXUSDT | 698 | 51.7% | 0.82 | -40.5% | 60.4% |
| BCHUSDT | 726 | 51.8% | 0.81 | -42.7% | 54.9% |
| SUIUSDT | 677 | 54.2% | 0.76 | -51.0% | 85.2% |
| ADAUSDT | 737 | 48.3% | 0.79 | -53.7% | 66.1% |
| ETHUSDT | 640 | 48.6% | 0.72 | -56.5% | 66.8% |

## Kết luận

- So với bot Donchian live: đây là chiến lược **khác** (swing theo sóng), tần suất thấp hơn nhiều.
- Nếu PF < 1 hoặc WR thấp trên nhiều cửa sổ → **không** thay thế Config A hiện tại.
