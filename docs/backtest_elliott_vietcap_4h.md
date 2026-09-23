# Elliott Wave (Vietcap rules) — backtest trên cache hiện có

- Generated: **2026-09-17 16:48:10 +07**
- Nguồn lý thuyết: [Vietcap – Áp dụng nguyên lý sóng Elliott](https://www.vietcap.com.vn/kien-thuc/ap-dung-nguyen-ly-song-elliott-trong-giao-dich-dau-tu-chung-khoan)
- Data: `data/bt_klines_15m/` → resample **4h** · ZigZag ATR×2.5
- Entry: sau W2→**W3**, sau W4→**W5**, sau WA→**WB** (Tip 1 bài viết)
- Filter Fib + 3 quy tắc cứng Elliott; risk 1% equity / lệnh; fee 0.04%/side; 1 position / coin
- Entry @ close nến **sau khi ZigZag xác nhận** pivot (không vào tại đáy/đỉnh chưa confirm)
- **Lưu ý:** đếm sóng Elliott mang tính chủ quan — đây là bản **quy tắc hóa gần đúng**, không phải đếm tay. Không thay Donchian live trừ khi edge rõ và ổn định.

## 365d · tf **4h** · eval từ 2025-09-16 · **19 coin**

- Tổng lệnh: **737** · coin dương **9/19** · TB/coin: WR **50.5%** · PF mean/med **1.63/0.95** · Return **-2.5%** · MaxDD **8.9%** (mỗi coin ví $1000 riêng)

### Theo loại sóng (Tip 1)

| Kind | Trades | WR | Net $ (sum coins) |
| --- | ---: | ---: | ---: |
| W3 | 278 | 33.5% | -232.1 |
| W5 | 17 | 41.2% | +2.2 |
| WB | 442 | 62.0% | -240.1 |

### Theo coin (sort return)

| Symbol | Trades | WR | PF | Return | MaxDD |
| --- | ---: | ---: | ---: | ---: | ---: |
| SOLUSDT | 64 | 59.4% | 1.30 | +8.3% | 4.3% |
| OPUSDT | 6 | 83.3% | 8.37 | +7.6% | 1.9% |
| ADAUSDT | 6 | 50.0% | 2.73 | +5.3% | 3.0% |
| ATOMUSDT | 8 | 62.5% | 2.50 | +4.7% | 4.1% |
| XLMUSDT | 6 | 66.7% | 3.08 | +4.2% | 5.3% |
| SUIUSDT | 7 | 28.6% | 1.68 | +2.7% | 4.4% |
| APTUSDT | 7 | 28.6% | 1.55 | +2.2% | 4.1% |
| ARBUSDT | 6 | 33.3% | 1.51 | +2.1% | 4.5% |
| ETHUSDT | 26 | 46.2% | 1.11 | +1.6% | 5.5% |
| LINKUSDT | 46 | 54.3% | 0.93 | -1.4% | 7.7% |
| LTCUSDT | 65 | 55.4% | 0.95 | -1.4% | 8.2% |
| UNIUSDT | 53 | 52.8% | 0.89 | -2.6% | 11.1% |
| DOTUSDT | 71 | 50.7% | 0.84 | -5.6% | 12.5% |
| NEARUSDT | 59 | 54.2% | 0.64 | -9.2% | 11.7% |
| BTCUSDT | 49 | 42.9% | 0.67 | -9.3% | 13.0% |
| BNBUSDT | 63 | 47.6% | 0.59 | -12.7% | 17.0% |
| XRPUSDT | 70 | 45.7% | 0.62 | -13.3% | 15.6% |
| TRXUSDT | 67 | 49.3% | 0.54 | -14.9% | 16.8% |
| BCHUSDT | 58 | 48.3% | 0.43 | -15.2% | 17.5% |

## 1095d · tf **4h** · eval từ 2023-09-17 · **20 coin**

- Tổng lệnh: **3190** · coin dương **5/20** · TB/coin: WR **51.7%** · PF mean/med **0.89/0.90** · Return **-8.0%** · MaxDD **21.4%** (mỗi coin ví $1000 riêng)

### Theo loại sóng (Tip 1)

| Kind | Trades | WR | Net $ (sum coins) |
| --- | ---: | ---: | ---: |
| W3 | 1170 | 35.6% | -525.0 |
| W5 | 64 | 32.8% | -123.7 |
| WB | 1956 | 61.7% | -958.5 |

### Theo coin (sort return)

| Symbol | Trades | WR | PF | Return | MaxDD |
| --- | ---: | ---: | ---: | ---: | ---: |
| LTCUSDT | 195 | 58.5% | 1.18 | +16.5% | 12.4% |
| ATOMUSDT | 135 | 57.8% | 1.15 | +9.3% | 19.3% |
| XRPUSDT | 199 | 53.3% | 1.08 | +8.8% | 21.9% |
| LINKUSDT | 146 | 52.7% | 1.04 | +2.9% | 11.4% |
| NEARUSDT | 119 | 53.8% | 1.01 | +0.4% | 13.6% |
| APTUSDT | 133 | 51.9% | 0.98 | -1.5% | 17.2% |
| ETHUSDT | 132 | 50.0% | 0.93 | -4.3% | 12.1% |
| UNIUSDT | 214 | 53.7% | 0.94 | -5.2% | 21.4% |
| AVAXUSDT | 97 | 52.6% | 0.87 | -5.7% | 16.0% |
| ARBUSDT | 100 | 52.0% | 0.87 | -5.8% | 20.8% |
| OPUSDT | 145 | 52.4% | 0.90 | -6.0% | 15.3% |
| ADAUSDT | 121 | 51.2% | 0.89 | -6.2% | 17.7% |
| SOLUSDT | 174 | 56.9% | 0.91 | -6.7% | 22.4% |
| XLMUSDT | 146 | 50.7% | 0.85 | -9.3% | 20.2% |
| SUIUSDT | 143 | 48.3% | 0.77 | -14.7% | 22.1% |
| BCHUSDT | 169 | 49.7% | 0.68 | -23.0% | 28.1% |
| TRXUSDT | 196 | 50.5% | 0.74 | -23.2% | 31.2% |
| BNBUSDT | 226 | 47.3% | 0.74 | -26.8% | 39.9% |
| DOTUSDT | 209 | 47.8% | 0.68 | -29.4% | 31.6% |
| BTCUSDT | 191 | 43.5% | 0.65 | -30.6% | 34.2% |

## Kết luận

- So với bot Donchian live: đây là chiến lược **khác** (swing theo sóng), tần suất thấp hơn nhiều.
- Nếu PF < 1 hoặc WR thấp trên nhiều cửa sổ → **không** thay thế Config A hiện tại.
