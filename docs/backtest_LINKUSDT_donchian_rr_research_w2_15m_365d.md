# RR research wave-2 — LINKUSDT 15m 365d

- Sinh luc: 2026-08-21 08:37:02 +07
- Cua so: 2025-08-21 08:30 -> 2026-08-21 08:15
- Gia 26.2610 -> 10.7460 (-59.08%)
- Wave-2: pullback sau, channel pos, ATR distance, width regime, wait/counter2, SL mid, exit parallel, scale-out, ATR trail

## Bang so sanh (sort RR edge)

| Rank | Variant | n | WR% | RR | Edge | PF | Exp | PnL | AvgW | AvgL | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `sl_mid_rr0.5` | 1843 | 31.7 | **2.562** | **+0.412** | 1.19 | +0.0176 | **+32.4** | +0.344 | -0.134 | SL mid + min RR 0.5 (vs opp) |
| 2 | `width_gt2_rr0.5` | 583 | 74.3 | **0.685** | **+0.339** | 1.98 | +0.2436 | **+142.0** | +0.663 | -0.968 | Width≥2% + min RR 0.5 |
| 3 | `wait2_rr0.5` | 868 | 75.5 | **0.594** | **+0.269** | 1.83 | +0.1831 | **+158.9** | +0.536 | -0.903 | Wait 2 + min RR 0.5 |
| 4 | `deep_combo` | 663 | 73.3 | **0.632** | **+0.268** | 1.74 | +0.1697 | **+112.5** | +0.546 | -0.864 | Beyond mid + chan≤0.45 + RR≥0.5 |
| 5 | `wait_4` | 981 | 79.1 | **0.519** | **+0.255** | 1.96 | +0.1794 | **+176.0** | +0.462 | -0.891 | Doi 4 nen sau trend |
| 6 | `min_rr_0.75` | 819 | 74.2 | **0.598** | **+0.251** | 1.72 | +0.1723 | **+141.1** | +0.553 | -0.925 | wave1 best edge |
| 7 | `min_tp_1.5atr` | 928 | 75.0 | **0.583** | **+0.249** | 1.75 | +0.1683 | **+156.2** | +0.524 | -0.900 | Dist TP ≥ 1.5×ATR |
| 8 | `quality_combo` | 876 | 76.6 | **0.549** | **+0.244** | 1.80 | +0.1727 | **+151.2** | +0.508 | -0.926 | RR≥0.5 + TP≥1ATR + width 1–4% |
| 9 | `min_rr_0.5` | 933 | 76.1 | **0.550** | **+0.236** | 1.75 | +0.1713 | **+159.8** | +0.524 | -0.953 | wave1 best PnL |
| 10 | `chan_pos_0.3` | 519 | 74.4 | **0.576** | **+0.232** | 1.67 | +0.1630 | **+84.6** | +0.545 | -0.946 | Channel pos ≤0.3 |
| 11 | `patient_combo` | 757 | 75.0 | **0.564** | **+0.231** | 1.69 | +0.1583 | **+119.9** | +0.515 | -0.914 | Wait2 + counter2 + RR≥0.5 |
| 12 | `beyond_mid_rr0.5` | 713 | 72.4 | **0.604** | **+0.222** | 1.58 | +0.1441 | **+102.8** | +0.541 | -0.895 | Beyond mid + min RR 0.5 |
| 13 | `beyond_mid` | 713 | 72.4 | **0.604** | **+0.222** | 1.58 | +0.1438 | **+102.5** | +0.541 | -0.896 | Chi vao khi px qua mid (pullback sau) |
| 14 | `chan_pos_0.5` | 716 | 72.5 | **0.601** | **+0.222** | 1.58 | +0.1441 | **+103.2** | +0.539 | -0.897 | Channel pos ≤0.5 |
| 15 | `wait_2` | 1140 | 79.1 | **0.480** | **+0.216** | 1.82 | +0.1485 | **+169.2** | +0.417 | -0.869 | Doi 2 nen sau trend moi vao |
| 16 | `sl_mid` | 2270 | 49.0 | **1.249** | **+0.207** | 1.20 | +0.0252 | **+57.3** | +0.310 | -0.248 | SL = mid@entry, TP band |
| 17 | `chan0.4_rr0.5` | 621 | 74.1 | **0.552** | **+0.202** | 1.58 | +0.1414 | **+87.8** | +0.521 | -0.944 | Chan≤0.4 + min RR 0.5 |
| 18 | `chan_pos_0.4` | 621 | 74.1 | **0.552** | **+0.202** | 1.58 | +0.1412 | **+87.7** | +0.521 | -0.944 | Long/short chi khi o 40% band phia SL |
| 19 | `min_tp_1atr` | 1101 | 77.1 | **0.497** | **+0.200** | 1.68 | +0.1419 | **+156.3** | +0.457 | -0.918 | Dist TP ≥ 1×ATR |
| 20 | `counter_2` | 929 | 76.5 | **0.499** | **+0.192** | 1.63 | +0.1373 | **+127.5** | +0.465 | -0.933 | Can 2 nen nguoc lien tiep |
| 21 | `sl0.8pct` | 2023 | 61.7 | **0.797** | **+0.178** | 1.29 | +0.0505 | **+102.1** | +0.367 | -0.460 | SL 0.8% + TP band |
| 22 | `baseline` | 1274 | 79.7 | **0.421** | **+0.167** | 1.66 | +0.1223 | **+155.8** | +0.386 | -0.916 | TP band (ref) |
| 23 | `width_1_4pct` | 1167 | 80.9 | **0.399** | **+0.163** | 1.69 | +0.1248 | **+145.7** | +0.378 | -0.946 | Width 1–4% price |
| 24 | `width_1_3pct` | 1089 | 80.3 | **0.396** | **+0.151** | 1.62 | +0.1096 | **+119.4** | +0.357 | -0.902 | Width 1–3% price |
| 25 | `exit_parallel` | 2499 | 55.1 | **0.899** | **+0.083** | 1.10 | +0.0156 | **+39.0** | +0.307 | -0.342 | Thoat khi band song song lai |
| 26 | `atr_trail_2` | 1731 | 55.9 | **0.758** | **-0.030** | 0.96 | -0.0065 | **-11.2** | +0.291 | -0.383 | Arm 0.5ATR, trail 2×ATR |
| 27 | `atr_trail_1` | 1523 | 63.2 | **0.486** | **-0.095** | 0.84 | -0.0199 | **-30.3** | +0.161 | -0.331 | Arm 1ATR, trail 1×ATR |
| 28 | `atr_trail_15` | 1771 | 45.7 | **0.825** | **-0.362** | 0.69 | -0.0456 | **-80.8** | +0.227 | -0.276 | Arm 0.5ATR, trail 1.5×ATR |
| 29 | `scale_mid_rr0.5` | 3788 | 7.9 | **1.954** | **-9.630** | 0.17 | -0.0325 | **-123.1** | +0.083 | -0.042 | Scale mid + min RR 0.5 |
| 30 | `scale_mid` | 4938 | 3.5 | **1.076** | **-26.467** | 0.04 | -0.0639 | **-315.6** | +0.074 | -0.069 | 50% @ mid, 50% @ band + BE |

## Ket luan wave-2

- Baseline: RR 0.421 · edge +0.167 · PnL +155.8
- Best edge (n≥50): `sl_mid_rr0.5` RR 2.562 edge +0.412 PnL +32.4
- Best RR (edge>0, PF≥1): `sl_mid_rr0.5` RR 2.562 PnL +32.4
- Best tradeoff: `width_gt2_rr0.5` RR 0.685 edge +0.339 PnL +142.0
- Best PnL: `wait_4` PnL +176.0 RR 0.519

### Huong di them (so voi wave-1)

- Pullback sau (`beyond_mid` / `chan_pos_*`): nang pot RR bang cach vao sau hon trong channel.
- `min_tp_*atr` / width regime: bo setup TP qua sat.
- `scale_mid`: chot 1/2 som — RR theo leg co the doi, xem PnL tong.
- `atr_trail` / `exit_parallel` / `sl_mid`: doi hinh hoc risk/reward, de giam WR.

## Skip / reason (top)

- `sl_mid_rr0.5`: skip[rr:3650, parallel:933] · SL:1512, TP_BAND:331
- `width_gt2_rr0.5`: skip[rr:1692, width:3499, parallel:2041] · TP_BAND:583
- `wait2_rr0.5`: skip[rr:1685, wait:1058, parallel:672] · TP_BAND:868
- `deep_combo`: skip[rr:1, chan:300, mid:4301, parallel:1414] · TP_BAND:663
- `wait_4`: skip[wait:2297, parallel:670] · TP_BAND:981
- `min_rr_0.75`: skip[rr:3201, parallel:917] · TP_BAND:819
- `min_tp_1.5atr`: skip[tp_atr:1974, parallel:557] · TP_BAND:928
- `quality_combo`: skip[rr:1737, tp_atr:6, width:1117, parallel:806] · TP_BAND:876
- `min_rr_0.5`: skip[rr:2170, parallel:575] · TP_BAND:933
- `chan_pos_0.3`: skip[chan:6031, parallel:1994] · TP_BAND:519
- `patient_combo`: skip[rr:646, wait:212, counter2:2797, parallel:989] · TP_BAND:757
- `beyond_mid_rr0.5`: skip[rr:2, mid:4018, parallel:1163] · TP_BAND:713
- `beyond_mid`: skip[mid:4018, parallel:1163] · TP_BAND:713
- `chan_pos_0.5`: skip[chan:3994, parallel:1153] · TP_BAND:716
- `wait_2`: skip[wait:1025, parallel:243] · TP_BAND:1140
- `sl_mid`: skip[parallel:191] · SL:1277, TP_BAND:993
- `chan0.4_rr0.5`: skip[rr:1, chan:5009, parallel:1605] · TP_BAND:621
- `chan_pos_0.4`: skip[chan:5009, parallel:1605] · TP_BAND:621
- `min_tp_1atr`: skip[tp_atr:769, parallel:212] · TP_BAND:1101
- `counter_2`: skip[counter2:2063, parallel:583] · TP_BAND:929
- `sl0.8pct`: skip[parallel:164] · TP_BAND:1259, SL:764
- `baseline`: skip[parallel:111] · TP_BAND:1274
