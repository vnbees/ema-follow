# Wave-3b — MAE / hold cuts (from wave-3 feature splits)

- Sinh luc: 2026-08-21 08:52:11 +07
- Insight baseline: **MAE>1R** và **hold>16** là nơi đốt PnL (Σ loss rất lớn).

| Rank | Rule | ΣPnL | Δbase | med RR | med Edge | med WR | Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `body_size_rr05` | **+1130.5** | +114.3 | 0.565 | **+0.224** | 75% | body[0.3,1.2]+size∝RR+minRR0.5 |
| 2 | `mae1_soft16` | **+940.5** | -75.7 | 0.812 | **+0.205** | 63% | MAE≥1R + soft16 R<0 |
| 3 | `soft16_r0` | **+924.7** | -91.5 | 0.721 | **+0.193** | 66% | after 16 bars if R<0 exit |
| 4 | `mae1_body_size` | **+1000.2** | -16.0 | 0.638 | **+0.186** | 68% | MAE1R + body + size∝RR |
| 5 | `mae15_soft16` | **+893.9** | -122.3 | 0.749 | **+0.180** | 65% | MAE≥1.5R + soft16 R<0 |
| 6 | `max_hold_16` | **+878.1** | -138.1 | 0.679 | **+0.172** | 67% | force exit @16 bars |
| 7 | `baseline` | **+1016.2** | +0.0 | 0.430 | **+0.172** | 80% | ref |
| 8 | `mae_cut_1.0R` | **+1085.6** | +69.4 | 0.560 | **+0.167** | 72% | cut MAE≥1R (opp-band risk) |
| 9 | `mae15_body_size` | **+771.8** | -244.4 | 0.703 | **+0.167** | 65% | MAE1.5R + body + size∝RR + RR≥0.5 |
| 10 | `mae_cut_2.0R` | **+992.2** | -24.0 | 0.445 | **+0.155** | 78% | cut MAE≥2R |
| 11 | `mae_cut_1.5R` | **+1025.3** | +9.1 | 0.476 | **+0.151** | 76% | cut MAE≥1.5R |
| 12 | `max_hold_24` | **+986.9** | -29.3 | 0.507 | **+0.148** | 75% | force exit @24 bars |

## Per coin

### `body_size_rr05` — body[0.3,1.2]+size∝RR+minRR0.5

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 873 | 77% | 0.575 | +0.272 | **+244.2** | TP_BAND:873 |
| HYPEUSDT | 820 | 71% | 0.488 | +0.086 | **+95.0** | TP_BAND:819, EOD:1 |
| BTWUSDT | 165 | 77% | 0.578 | +0.278 | **+259.2** | TP_BAND:164, EOD:1 |
| SUIUSDT | 875 | 75% | 0.558 | +0.216 | **+221.7** | TP_BAND:875 |
| DOGEUSDT | 839 | 75% | 0.571 | +0.233 | **+190.2** | TP_BAND:839 |
| SOLUSDT | 839 | 73% | 0.524 | +0.157 | **+120.2** | TP_BAND:839 |

### `mae1_soft16` — MAE≥1R + soft16 R<0

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 1934 | 63% | 0.740 | +0.164 | **+102.0** | TP_BAND:1227, SOFT_TIME:355, MAE_CUT:352 |
| HYPEUSDT | 1852 | 65% | 0.773 | +0.239 | **+215.6** | TP_BAND:1208, MAE_CUT:334, SOFT_TIME:309, EOD:1 |
| BTWUSDT | 327 | 67% | 1.004 | +0.510 | **+281.8** | TP_BAND:218, SOFT_TIME:55, MAE_CUT:53, EOD:1 |
| SUIUSDT | 1848 | 62% | 0.837 | +0.235 | **+158.1** | TP_BAND:1153, SOFT_TIME:360, MAE_CUT:334, EOD:1 |
| DOGEUSDT | 1834 | 59% | 0.846 | +0.162 | **+82.0** | TP_BAND:1090, SOFT_TIME:403, MAE_CUT:340, EOD:1 |
| SOLUSDT | 1908 | 62% | 0.786 | +0.174 | **+101.0** | TP_BAND:1185, SOFT_TIME:367, MAE_CUT:356 |

### `soft16_r0` — after 16 bars if R<0 exit

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 1797 | 66% | 0.676 | +0.166 | **+112.8** | TP_BAND:1190, SOFT_TIME:607 |
| HYPEUSDT | 1713 | 68% | 0.698 | +0.220 | **+212.4** | TP_BAND:1159, SOFT_TIME:553, EOD:1 |
| BTWUSDT | 307 | 70% | 0.775 | +0.348 | **+239.9** | TP_BAND:214, SOFT_TIME:92, EOD:1 |
| SUIUSDT | 1730 | 66% | 0.749 | +0.233 | **+176.5** | TP_BAND:1141, SOFT_TIME:588, EOD:1 |
| DOGEUSDT | 1726 | 63% | 0.743 | +0.154 | **+90.7** | TP_BAND:1087, SOFT_TIME:638, EOD:1 |
| SOLUSDT | 1760 | 65% | 0.685 | +0.144 | **+92.3** | TP_BAND:1143, SOFT_TIME:617 |

### `mae1_body_size` — MAE1R + body + size∝RR

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 1488 | 68% | 0.631 | +0.167 | **+119.7** | TP_BAND:1072, MAE_CUT:416 |
| HYPEUSDT | 1506 | 71% | 0.639 | +0.227 | **+254.9** | TP_BAND:1111, MAE_CUT:394, EOD:1 |
| BTWUSDT | 261 | 73% | 0.717 | +0.350 | **+281.9** | TP_BAND:199, MAE_CUT:61, EOD:1 |
| SUIUSDT | 1457 | 69% | 0.664 | +0.206 | **+174.6** | TP_BAND:1066, MAE_CUT:390, EOD:1 |
| DOGEUSDT | 1375 | 66% | 0.638 | +0.127 | **+82.2** | TP_BAND:978, MAE_CUT:397 |
| SOLUSDT | 1466 | 68% | 0.605 | +0.124 | **+86.9** | TP_BAND:1045, MAE_CUT:421 |

### `mae15_soft16` — MAE≥1.5R + soft16 R<0

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 1842 | 65% | 0.701 | +0.161 | **+104.6** | TP_BAND:1196, SOFT_TIME:460, MAE_CUT:186 |
| HYPEUSDT | 1748 | 66% | 0.706 | +0.199 | **+188.0** | TP_BAND:1160, SOFT_TIME:407, MAE_CUT:180, EOD:1 |
| BTWUSDT | 315 | 69% | 0.865 | +0.420 | **+263.9** | TP_BAND:217, SOFT_TIME:67, MAE_CUT:30, EOD:1 |
| SUIUSDT | 1769 | 64% | 0.766 | +0.213 | **+156.1** | TP_BAND:1139, SOFT_TIME:455, MAE_CUT:174, EOD:1 |
| DOGEUSDT | 1770 | 61% | 0.791 | +0.161 | **+87.2** | TP_BAND:1087, SOFT_TIME:483, MAE_CUT:199, EOD:1 |
| SOLUSDT | 1818 | 63% | 0.733 | +0.158 | **+94.2** | TP_BAND:1155, SOFT_TIME:472, MAE_CUT:191 |

### `max_hold_16` — force exit @16 bars

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 1812 | 67% | 0.634 | +0.146 | **+102.9** | TP_BAND:1145, MAX_HOLD:667 |
| HYPEUSDT | 1722 | 68% | 0.660 | +0.197 | **+197.3** | TP_BAND:1118, MAX_HOLD:603, EOD:1 |
| BTWUSDT | 312 | 72% | 0.699 | +0.306 | **+229.3** | TP_BAND:208, MAX_HOLD:103, EOD:1 |
| SUIUSDT | 1746 | 68% | 0.698 | +0.219 | **+174.1** | TP_BAND:1097, MAX_HOLD:648, EOD:1 |
| DOGEUSDT | 1745 | 64% | 0.709 | +0.144 | **+88.0** | TP_BAND:1063, MAX_HOLD:681, EOD:1 |
| SOLUSDT | 1777 | 67% | 0.617 | +0.125 | **+86.6** | TP_BAND:1107, MAX_HOLD:670 |

### `baseline` — ref

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 1274 | 80% | 0.421 | +0.167 | **+155.8** | TP_BAND:1274 |
| HYPEUSDT | 1236 | 80% | 0.403 | +0.147 | **+195.5** | TP_BAND:1235, EOD:1 |
| BTWUSDT | 221 | 81% | 0.366 | +0.124 | **+136.5** | TP_BAND:220, EOD:1 |
| SUIUSDT | 1239 | 80% | 0.462 | +0.216 | **+222.9** | TP_BAND:1238, EOD:1 |
| DOGEUSDT | 1228 | 79% | 0.443 | +0.179 | **+156.1** | TP_BAND:1228 |
| SOLUSDT | 1271 | 79% | 0.438 | +0.176 | **+149.4** | TP_BAND:1271 |

### `mae_cut_1.0R` — cut MAE≥1R (opp-band risk)

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 1660 | 72% | 0.536 | +0.146 | **+123.4** | TP_BAND:1262, MAE_CUT:398 |
| HYPEUSDT | 1655 | 74% | 0.548 | +0.202 | **+259.6** | TP_BAND:1274, MAE_CUT:380, EOD:1 |
| BTWUSDT | 287 | 77% | 0.675 | +0.370 | **+304.0** | TP_BAND:229, MAE_CUT:57, EOD:1 |
| SUIUSDT | 1594 | 72% | 0.585 | +0.188 | **+179.3** | TP_BAND:1208, MAE_CUT:385, EOD:1 |
| DOGEUSDT | 1526 | 70% | 0.571 | +0.145 | **+108.4** | TP_BAND:1146, MAE_CUT:380 |
| SOLUSDT | 1638 | 72% | 0.523 | +0.131 | **+110.9** | TP_BAND:1238, MAE_CUT:400 |

### `mae15_body_size` — MAE1.5R + body + size∝RR + RR≥0.5

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 1083 | 66% | 0.707 | +0.197 | **+142.3** | TP_BAND:763, MAE_CUT:320 |
| HYPEUSDT | 1049 | 63% | 0.700 | +0.113 | **+104.0** | TP_BAND:717, MAE_CUT:331, EOD:1 |
| BTWUSDT | 192 | 67% | 0.809 | +0.309 | **+199.7** | TP_BAND:139, MAE_CUT:52, EOD:1 |
| SUIUSDT | 1080 | 66% | 0.723 | +0.204 | **+179.4** | TP_BAND:773, MAE_CUT:307 |
| DOGEUSDT | 1027 | 65% | 0.686 | +0.137 | **+93.4** | TP_BAND:722, MAE_CUT:305 |
| SOLUSDT | 1048 | 63% | 0.661 | +0.080 | **+53.0** | TP_BAND:716, MAE_CUT:332 |

### `mae_cut_2.0R` — cut MAE≥2R

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 1415 | 78% | 0.425 | +0.137 | **+134.3** | TP_BAND:1251, MAE_CUT:164 |
| HYPEUSDT | 1395 | 78% | 0.447 | +0.169 | **+234.4** | TP_BAND:1237, MAE_CUT:157, EOD:1 |
| BTWUSDT | 253 | 80% | 0.478 | +0.225 | **+234.5** | TP_BAND:221, MAE_CUT:31, EOD:1 |
| SUIUSDT | 1349 | 78% | 0.443 | +0.156 | **+179.4** | TP_BAND:1200, MAE_CUT:148, EOD:1 |
| DOGEUSDT | 1331 | 76% | 0.470 | +0.155 | **+134.4** | TP_BAND:1178, MAE_CUT:153 |
| SOLUSDT | 1379 | 76% | 0.405 | +0.081 | **+75.3** | TP_BAND:1195, MAE_CUT:184 |

### `mae_cut_1.5R` — cut MAE≥1.5R

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 1487 | 76% | 0.451 | +0.132 | **+125.8** | TP_BAND:1249, MAE_CUT:238 |
| HYPEUSDT | 1476 | 77% | 0.475 | +0.180 | **+244.2** | TP_BAND:1247, MAE_CUT:228, EOD:1 |
| BTWUSDT | 263 | 78% | 0.561 | +0.285 | **+267.6** | TP_BAND:222, MAE_CUT:40, EOD:1 |
| SUIUSDT | 1415 | 76% | 0.486 | +0.170 | **+183.2** | TP_BAND:1196, MAE_CUT:218, EOD:1 |
| DOGEUSDT | 1386 | 74% | 0.477 | +0.133 | **+114.2** | TP_BAND:1162, MAE_CUT:224 |
| SOLUSDT | 1460 | 74% | 0.447 | +0.101 | **+90.2** | TP_BAND:1205, MAE_CUT:255 |

### `max_hold_24` — force exit @24 bars

| Symbol | n | WR | RR | Edge | PnL | Reasons |
| --- | --- | --- | --- | --- | --- | --- |
| LINKUSDT | 1586 | 75% | 0.481 | +0.142 | **+131.8** | TP_BAND:1181, MAX_HOLD:405 |
| HYPEUSDT | 1511 | 75% | 0.461 | +0.128 | **+172.0** | TP_BAND:1142, MAX_HOLD:368, EOD:1 |
| BTWUSDT | 277 | 78% | 0.557 | +0.281 | **+261.6** | TP_BAND:215, MAX_HOLD:61, EOD:1 |
| SUIUSDT | 1533 | 75% | 0.531 | +0.195 | **+208.9** | TP_BAND:1147, MAX_HOLD:385, EOD:1 |
| DOGEUSDT | 1490 | 72% | 0.540 | +0.155 | **+122.7** | TP_BAND:1073, MAX_HOLD:417 |
| SOLUSDT | 1537 | 73% | 0.483 | +0.104 | **+89.9** | TP_BAND:1123, MAX_HOLD:414 |

## Ket luan 3b

- Baseline ΣPnL +1016.2
- Best edge: `body_size_rr05` edge +0.224 Σ +1130.5
- Best PnL: `body_size_rr05` Σ +1130.5
- Best tradeoff: `body_size_rr05`

