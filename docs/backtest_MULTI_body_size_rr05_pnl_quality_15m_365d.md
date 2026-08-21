# body_size_rr05 — lời vs lỗ, tần suất, % ngày/tháng/năm

- Sinh luc: 2026-08-21 08:59:02 +07
- Rule: body ATR ∈ [0.3,1.2] · pot RR ≥ 0.5 · size_mult = clip(0.5+pot_rr, 0.5, 2)
- Capital 1000 · base margin 0.50%×10x · fee 0.04%/side · MAX_OPEN=1
- Coins: LINKUSDT, HYPEUSDT, BTWUSDT, SUIUSDT, DOGEUSDT, SOLUSDT
- **wipe_ratio** = gross_loss / gross_win ( &lt; 1 ⇒ tổng lời chưa bị lỗ nuốt hết )
- % ngày/tháng/năm = linear từ total return / số ngày data (không compound phức tạp)

## 1. Bang tong hop body_size_rr05

| Symbol | Days | n | /ngày | WR | Wins | Losses | GrossW | GrossL | Wipe | PF | RR | Net | %tot | %/ngày | %/tháng | %/năm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 365 | 873 | **2.39** | 77% | 670 | 203 | +516.6 | +272.4 | **0.53** | 1.90 | 0.575 | **+244.2** | +24.4% | **+0.067%** | **+2.04%** | **+24.4%** |
| HYPEUSDT | 365 | 820 | **2.25** | 71% | 585 | 235 | +535.9 | +441.0 | **0.82** | 1.22 | 0.488 | **+95.0** | +9.5% | **+0.026%** | **+0.79%** | **+9.5%** |
| BTWUSDT | 77 | 165 | **2.13** | 77% | 127 | 38 | +537.8 | +278.6 | **0.52** | 1.93 | 0.578 | **+259.2** | +25.9% | **+0.335%** | **+10.18%** | **+122.1%** |
| SUIUSDT | 365 | 875 | **2.40** | 75% | 652 | 223 | +573.4 | +351.7 | **0.61** | 1.63 | 0.558 | **+221.7** | +22.2% | **+0.061%** | **+1.85%** | **+22.2%** |
| DOGEUSDT | 365 | 839 | **2.30** | 75% | 627 | 212 | +465.8 | +275.6 | **0.59** | 1.69 | 0.571 | **+190.2** | +19.0% | **+0.052%** | **+1.59%** | **+19.0%** |
| SOLUSDT | 365 | 839 | **2.30** | 73% | 614 | 225 | +400.6 | +280.4 | **0.70** | 1.43 | 0.524 | **+120.2** | +12.0% | **+0.033%** | **+1.00%** | **+12.0%** |

## 2. Lời có bị lỗ nuốt hết không?

| | Gross win | Gross loss | Wipe (L/W) | Profit factor | Net |
| --- | --- | --- | --- | --- | --- |
| **body_size_rr05 (SUM 6 coin)** | +3030.1 | +1899.7 | **0.63** | **1.60** | **+1130.5** |
| baseline (SUM 6 coin) | +2565.7 | +1549.5 | 0.60 | 1.66 | +1016.2 |

- Wipe &lt; 1 ⇒ **không** bị lỗ nuốt hết lời. body_size_rr05 wipe=**0.63** (lỗ chỉ bằng ~63% tổng lời).
- PF=1.60 ⇒ mỗi 1 USDT lỗ, kiếm được ~1.60 USDT lời.

## 3. Tan suat & profit % (equal-weight moi coin 1000$)

| Metric | body_size_rr05 | baseline |
| --- | --- | --- |
| Trades tong | 4411 | 6469 |
| WR | 74.2% | 79.6% |
| Lenh/ngay / coin (TB) | **2.29** | 3.33 |
| Lenh/ngay neu chay ca 6 coin | **13.8** | 20.0 |
| Net SUM | **+1130.5** / 6000 | +1016.2 / 6000 |
| % tong (tren tong capital) | +18.84% | +16.94% |
| %/ngay (EW TB cac coin) | **+0.096%** | +0.070% |
| %/thang (×30.44) | **+2.91%** | +2.12% |
| %/nam (×365) | **+34.9%** | +25.4% |

## 4. So sanh tung coin vs baseline

| Symbol | Filter n | Base n | Filter net | Base net | Filter wipe | Base wipe | Filter %/năm | Base %/năm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 873 | 1274 | +244.2 | +155.8 | 0.53 | 0.60 | +24.4% | +15.6% |
| HYPEUSDT | 820 | 1236 | +95.0 | +195.5 | 0.82 | 0.64 | +9.5% | +19.5% |
| BTWUSDT | 165 | 221 | +259.2 | +136.5 | 0.52 | 0.66 | +122.1% | +64.3% |
| SUIUSDT | 875 | 1239 | +221.7 | +222.9 | 0.61 | 0.53 | +22.2% | +22.3% |
| DOGEUSDT | 839 | 1228 | +190.2 | +156.1 | 0.59 | 0.59 | +19.0% | +15.6% |
| SOLUSDT | 839 | 1271 | +120.2 | +149.4 | 0.70 | 0.60 | +12.0% | +14.9% |

## 5. Chi tiet tung coin (body_size_rr05)

### LINKUSDT

- Cua so: 2025-08-21 08:45 → 2026-08-21 08:30 (365.0d) · px -59.1%
- Lenh: **873** (~**2.39**/ngày) · WR 76.7% · W/L = 670/203
- Gross win **+516.64** · Gross loss **+272.40** · Wipe **0.53** · PF **1.90**
- Avg win +0.771 · Avg loss -1.342 · RR 0.575 · MaxW +11.53 · MaxL -7.70
- Net **+244.23** (+24.42% / 1000$) · Expectancy/lenh +0.2798
- **%/ngày +0.067%** · **%/tháng +2.04%** · **%/năm +24.4%** (linear)

### HYPEUSDT

- Cua so: 2025-08-21 08:45 → 2026-08-21 08:30 (365.0d) · px +72.6%
- Lenh: **820** (~**2.25**/ngày) · WR 71.3% · W/L = 585/235
- Gross win **+535.92** · Gross loss **+440.97** · Wipe **0.82** · PF **1.22**
- Avg win +0.916 · Avg loss -1.876 · RR 0.488 · MaxW +7.73 · MaxL -15.80
- Net **+94.95** (+9.50% / 1000$) · Expectancy/lenh +0.1158
- **%/ngày +0.026%** · **%/tháng +0.79%** · **%/năm +9.5%** (linear)

### BTWUSDT

- Cua so: 2026-06-04 21:15 → 2026-08-21 08:30 (77.5d) · px +2273.7%
- Lenh: **165** (~**2.13**/ngày) · WR 77.0% · W/L = 127/38
- Gross win **+537.80** · Gross loss **+278.59** · Wipe **0.52** · PF **1.93**
- Avg win +4.235 · Avg loss -7.331 · RR 0.578 · MaxW +17.97 · MaxL -38.61
- Net **+259.21** (+25.92% / 1000$) · Expectancy/lenh +1.5710
- **%/ngày +0.335%** · **%/tháng +10.18%** · **%/năm +122.1%** (linear)

### SUIUSDT

- Cua so: 2025-08-21 08:45 → 2026-08-21 08:30 (365.0d) · px -79.2%
- Lenh: **875** (~**2.40**/ngày) · WR 74.5% · W/L = 652/223
- Gross win **+573.40** · Gross loss **+351.71** · Wipe **0.61** · PF **1.63**
- Avg win +0.879 · Avg loss -1.577 · RR 0.558 · MaxW +6.67 · MaxL -20.19
- Net **+221.70** (+22.17% / 1000$) · Expectancy/lenh +0.2534
- **%/ngày +0.061%** · **%/tháng +1.85%** · **%/năm +22.2%** (linear)

### DOGEUSDT

- Cua so: 2025-08-21 08:45 → 2026-08-21 08:30 (365.0d) · px -63.2%
- Lenh: **839** (~**2.30**/ngày) · WR 74.7% · W/L = 627/212
- Gross win **+465.79** · Gross loss **+275.60** · Wipe **0.59** · PF **1.69**
- Avg win +0.743 · Avg loss -1.300 · RR 0.571 · MaxW +5.59 · MaxL -14.10
- Net **+190.19** (+19.02% / 1000$) · Expectancy/lenh +0.2267
- **%/ngày +0.052%** · **%/tháng +1.59%** · **%/năm +19.0%** (linear)

### SOLUSDT

- Cua so: 2025-08-21 08:45 → 2026-08-21 08:30 (365.0d) · px -53.2%
- Lenh: **839** (~**2.30**/ngày) · WR 73.2% · W/L = 614/225
- Gross win **+400.59** · Gross loss **+280.40** · Wipe **0.70** · PF **1.43**
- Avg win +0.652 · Avg loss -1.246 · RR 0.524 · MaxW +7.89 · MaxL -6.95
- Net **+120.19** (+12.02% / 1000$) · Expectancy/lenh +0.1433
- **%/ngày +0.033%** · **%/tháng +1.00%** · **%/năm +12.0%** (linear)

## 6. Doc ket qua

- Neu wipe_ratio ≈ 0.55–0.70 ⇒ lỗ bằng hơn nửa tổng lời, van con du loi rong.
- %/nam linear ≠ compound; live multi-coin dong thoi can chia capital / correl.
- BTW window ngan (~2–3 thang) → %/nam extrapolate de lac quan.

