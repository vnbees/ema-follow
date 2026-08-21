# RR research — LINKUSDT 15m 365d

- Sinh luc: 2026-08-21 08:32:17 +07
- Cua so: 2025-08-21 08:30 -> 2026-08-21 08:15
- Gia 26.2610 -> 10.7460 (-59.08%)
- Entry chung: parallel→non-parallel + nen nguoc; MAX_OPEN=1; size 0.50%×10x
- Muc tieu: nang **RR = avgW/|avgL|** (va RR edge = RR − RR_hoa_von)
- Same-bar SL+TP: uu tien SL (pessimistic)

## Bang so sanh (sort theo RR edge)

| Rank | Variant | n | WR% | RR | RR_BE | Edge | PF | Exp/lot | PnL | AvgW | AvgL | MaxL | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `min_rr_0.75` | 819 | 74.2 | **0.598** | 0.347 | **+0.251** | 1.72 | +0.1723 | **+141.1** | +0.553 | -0.925 | -9.23 | Baseline + chỉ vào nếu pot RR≥0.75 |
| 2 | `min_rr_0.5` | 933 | 76.1 | **0.550** | 0.314 | **+0.236** | 1.75 | +0.1713 | **+159.8** | +0.524 | -0.953 | -4.30 | Baseline + chỉ vào nếu pot RR≥0.5 |
| 3 | `min_rr_1.0` | 716 | 72.5 | **0.602** | 0.380 | **+0.222** | 1.59 | +0.1444 | **+103.4** | +0.540 | -0.897 | -9.02 | Baseline + chỉ vào nếu pot RR≥1.0 |
| 4 | `min_rr_0.4` | 1015 | 77.7 | **0.483** | 0.286 | **+0.197** | 1.69 | +0.1544 | **+156.7** | +0.487 | -1.008 | -9.46 | Baseline + chỉ vào nếu pot RR≥0.4 |
| 5 | `baseline` | 1274 | 79.7 | **0.421** | 0.254 | **+0.167** | 1.66 | +0.1223 | **+155.8** | +0.386 | -0.916 | -9.58 | TP near band (hiện tại) |
| 6 | `time_stop_16` | 1812 | 67.2 | **0.634** | 0.488 | **+0.146** | 1.30 | +0.0568 | **+102.9** | +0.367 | -0.578 | -4.42 | TP band + time stop 16 nến (~4h) |
| 7 | `sl1%_tp_band` | 1911 | 65.7 | **0.652** | 0.523 | **+0.129** | 1.25 | +0.0475 | **+90.7** | +0.364 | -0.559 | -0.59 | SL 1% + TP band sống |
| 8 | `time_stop_32` | 1475 | 77.6 | **0.417** | 0.288 | **+0.129** | 1.45 | +0.0909 | **+134.1** | +0.378 | -0.907 | -5.63 | TP band + time stop 32 nến (~8h) |
| 9 | `time_stop_8` | 2171 | 58.3 | **0.827** | 0.716 | **+0.111** | 1.16 | +0.0256 | **+55.6** | +0.328 | -0.396 | -3.85 | TP band + time stop 8 nến (~2h) |
| 10 | `sl2%_tp_band` | 1533 | 75.5 | **0.433** | 0.324 | **+0.110** | 1.34 | +0.0730 | **+112.0** | +0.382 | -0.882 | -1.16 | SL 2% + TP band sống |
| 11 | `fixed_rr_1.5` | 273 | 41.8 | **1.457** | 1.395 | **+0.063** | 1.04 | +0.0341 | **+9.3** | +1.902 | -1.305 | -6.37 | SL opp@entry, TP 1.5R cố định |
| 12 | `trail_mid` | 2341 | 42.8 | **1.302** | 1.336 | **-0.034** | 0.97 | -0.0030 | **-7.0** | +0.268 | -0.206 | -5.67 | Arm sau mid → trail exit mid |
| 13 | `min0.5_fixed_rr2` | 483 | 33.7 | **1.878** | 1.963 | **-0.085** | 0.96 | -0.0230 | **-11.1** | +1.504 | -0.801 | -2.91 | pot RR≥0.5 + fixed 2R |
| 14 | `fixed_rr_1.0` | 387 | 48.3 | **0.952** | 1.070 | **-0.118** | 0.89 | -0.0706 | **-27.3** | +1.179 | -1.239 | -6.34 | SL opp@entry, TP 1R cố định |
| 15 | `fixed_rr_2.0` | 212 | 31.6 | **1.798** | 2.164 | **-0.366** | 0.83 | -0.1495 | **-31.7** | +2.322 | -1.292 | -6.33 | SL opp@entry, TP 2R cố định |
| 16 | `atr15_rr_2` | 1290 | 32.0 | **1.687** | 2.123 | **-0.437** | 0.79 | -0.0602 | **-77.7** | +0.726 | -0.431 | -1.72 | SL 1.5×ATR, TP 2R |
| 17 | `atr_rr_2` | 1975 | 32.7 | **1.563** | 2.062 | **-0.499** | 0.76 | -0.0464 | **-91.7** | +0.445 | -0.285 | -1.16 | SL 1×ATR, TP 2R |
| 18 | `atr15_rr_3` | 990 | 23.8 | **2.624** | 3.195 | **-0.571** | 0.82 | -0.0604 | **-59.8** | +1.164 | -0.444 | -1.73 | SL 1.5×ATR, TP 3R |
| 19 | `atr_rr_3` | 1676 | 24.2 | **2.302** | 3.128 | **-0.826** | 0.74 | -0.0581 | **-97.4** | +0.668 | -0.290 | -1.16 | SL 1×ATR, TP 3R |
| 20 | `fixed_rr_3.0` | 170 | 20.6 | **2.290** | 3.857 | **-1.568** | 0.59 | -0.4402 | **-74.8** | +3.123 | -1.364 | -6.33 | SL opp@entry, TP 3R cố định |
| 21 | `be_after_mid` | 2490 | 1.4 | **9.131** | 68.167 | **-59.036** | 0.13 | -0.0366 | **-91.3** | +0.392 | -0.043 | -1.44 | Sau mid → SL=BE, TP band |
| 22 | `tp_opp_band` | 2499 | 0.0 | **0.000** | inf | **+nan** | 0.00 | -0.3493 | **-872.9** | +0.000 | -0.349 | -6.28 | SL opp@entry, TP = entry±1×width |
| 23 | `tp_width_0.5` | 1 | 100.0 | **inf** | 0.000 | **+nan** | inf | +28.9743 | **+29.0** | +28.974 | +0.000 | +0.00 | TP = band + 0.5×width |
| 24 | `tp_width_1.0` | 1 | 100.0 | **inf** | 0.000 | **+nan** | inf | +28.9743 | **+29.0** | +28.974 | +0.000 | +0.00 | TP = band + 1.0×width |
| 25 | `min0.5_tp_w0.5` | 1 | 100.0 | **inf** | 0.000 | **+nan** | inf | +29.2529 | **+29.3** | +29.253 | +0.000 | +0.00 | pot RR≥0.5 + TP band+0.5w |

## Ket luan nhanh

- Baseline: n=1274 · WR 79.7% · RR **0.421** · edge +0.167 · PnL +155.8
- RR cao nhat (n≥50): `fixed_rr_1.5` → RR **1.457** (WR 41.8%, PnL +9.3, n=273)
- Edge tot nhat (n≥50): `min_rr_0.75` → edge **+0.251** (RR 0.598, PnL +141.1)
- Tradeoff tot (RR↑, PnL ≥80% baseline): `min_rr_0.75` → RR **0.598** · edge +0.251 · PnL +141.1
- PnL cao nhat: `min_rr_0.5` → PnL **+159.8** (RR 0.550)

### Doc ket qua

- Variant `n < 50` chi de tham khao (de overfitting / may man).
- Ep RR bang TP xa / ATR 2R–3R: RR len ~1.5–2.6 nhung **edge am**, PnL am — khong tradeable.
- Cach nang RR **giu edge**: loc entry `min_rr_*` (bo setup TP qua gan vs SL).
- `time_stop` / `sl_%` nang RR bang cat loss som → WR giam, PnL giam vs baseline.

## Reason breakdown (baseline + winners)

- `min_rr_0.75` (n=819): skip_rr=3201 · TP:819
- `min_rr_0.5` (n=933): skip_rr=2170 · TP:933
- `baseline` (n=1274): skip_rr=0 · TP:1274
- `fixed_rr_1.5` (n=273): skip_rr=0 · SL:158, TP:114, EOD:1

## Goi y ap dung

1. **Khuyen nghi thuc dung:** `min_rr_0.75` — loc pot RR luc entry, giu TP band.
2. Muon RR ~0.6–0.8: `time_stop_8` / `sl1%` — chap nhan PnL thap hon.
3. Khong nen ep fixed 2R/3R hay ATR-RR tren logic Donchian nay (edge am).
4. Wider TP (`tp_width`) khong SL: xem lai bang sau khi fix (TP xa → it cham TP, RR/WR thay doi).

