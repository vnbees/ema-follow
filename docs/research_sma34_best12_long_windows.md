# sma34/144 Best-12 — long windows (same as bot chính)

- Generated: **2026-09-18 18:16:40 +07**
- Strategy: sma34/144 v1.2 soft Donchian TP · $1k · 10x · fee 0.04%
- ALT pool target: `CRVUSDT, ONDOUSDT, POLUSDT, RENDERUSDT, XMRUSDT, ICPUSDT, HBARUSDT, DOGEUSDT, AAVEUSDT, FILUSDT, GALAUSDT, SEIUSDT`
- Windows: 365=1y, 1095=3y, 1825=5y, 2100≈max (coins without history are dropped)

## Summary

| Window | Pool | n_syms | Return | PF | WR | MaxDD | t/d | Final $ |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 365d | **Best-12*** | 12 | +117.5% | 1.5215 | 74.5% | 27.4% | 8.93 | $2175 |
| 365d | majors | 20 | +244.8% | 1.6748 | 76.1% | 13.0% | 13.70 | $3448 |
| 1095d | **Best-12*** | 9 | +579.0% | 1.4624 | 75.3% | 27.5% | 6.46 | $6790 |
| 1095d | majors | 20 | +4358.1% | 1.5823 | 76.0% | 14.4% | 13.35 | $44581 |
| 1825d | **Best-12*** | 7 | +879.0% | 1.4353 | 74.4% | 27.8% | 5.02 | $9790 |
| 1825d | majors | 16 | +9648.3% | 1.4946 | 74.9% | 18.9% | 10.92 | $97483 |
| 2100d | **Best-12*** | 5 | +622.3% | 1.4525 | 74.4% | 21.7% | 3.62 | $7223 |
| 2100d | majors | 16 | +29136.5% | 1.4943 | 74.8% | 25.7% | 10.70 | $292365 |

\* n_syms may be <12 on long windows if listing too new.

## 365d · Best-12 · thực tế **365d** · **12 coin**

- Eval: `2025-09-18 17:30 +07` → `2026-09-18 17:30 +07`
- Pool: `CRVUSDT, ONDOUSDT, POLUSDT, RENDERUSDT, XMRUSDT, ICPUSDT, HBARUSDT, DOGEUSDT, AAVEUSDT, FILUSDT, GALAUSDT, SEIUSDT`

| Metric | ALT | Majors |
| --- | ---: | ---: |
| Return | +117.5% | +244.8% |
| PF | 1.5215 | 1.6748 |
| WR | 74.5% | 76.1% |
| MaxDD | 27.4% | 13.0% |
| t/d | 8.93 | 13.70 |
| Trades | 3259 | 4999 |
| Final $ | $2175 | $3448 |
| n_syms | 12 | 20 |

### Per coin (ALT)

| Symbol | n | WR | PF | Net $ |
| --- | ---: | ---: | ---: | ---: |
| ONDOUSDT | 250 | 75.6% | 2.07 | +155 |
| CRVUSDT | 312 | 78.2% | 1.74 | +153 |
| POLUSDT | 279 | 76.0% | 1.75 | +124 |
| XMRUSDT | 299 | 71.9% | 1.73 | +115 |
| RENDERUSDT | 234 | 76.5% | 1.70 | +114 |
| ICPUSDT | 259 | 78.8% | 1.44 | +98 |
| HBARUSDT | 260 | 74.2% | 1.72 | +94 |
| FILUSDT | 274 | 72.6% | 1.35 | +78 |
| DOGEUSDT | 298 | 70.8% | 1.41 | +75 |
| AAVEUSDT | 255 | 73.7% | 1.29 | +75 |
| GALAUSDT | 276 | 73.9% | 1.27 | +61 |
| SEIUSDT | 263 | 72.6% | 1.18 | +34 |

## 1095d · Best-12 · thực tế **1095d** · **9 coin**

- Eval: `2023-09-19 17:30 +07` → `2026-09-18 17:30 +07`
- Pool: `CRVUSDT, XMRUSDT, ICPUSDT, HBARUSDT, DOGEUSDT, AAVEUSDT, FILUSDT, GALAUSDT, SEIUSDT`

| Metric | ALT | Majors |
| --- | ---: | ---: |
| Return | +579.0% | +4358.1% |
| PF | 1.4624 | 1.5823 |
| WR | 75.3% | 76.0% |
| MaxDD | 27.5% | 14.4% |
| t/d | 6.46 | 13.35 |
| Trades | 7076 | 14613 |
| Final $ | $6790 | $44581 |
| n_syms | 9 | 20 |

### Per coin (ALT)

| Symbol | n | WR | PF | Net $ |
| --- | ---: | ---: | ---: | ---: |
| CRVUSDT | 904 | 77.5% | 1.63 | +987 |
| XMRUSDT | 904 | 74.7% | 1.85 | +851 |
| ICPUSDT | 771 | 77.6% | 1.58 | +788 |
| DOGEUSDT | 754 | 73.9% | 1.59 | +686 |
| FILUSDT | 788 | 75.1% | 1.38 | +565 |
| SEIUSDT | 753 | 74.9% | 1.42 | +558 |
| AAVEUSDT | 747 | 73.8% | 1.28 | +502 |
| HBARUSDT | 722 | 74.2% | 1.38 | +453 |
| GALAUSDT | 733 | 75.6% | 1.24 | +401 |

## 1825d · Best-12 · thực tế **1825d** · **7 coin**

- Eval: `2021-09-19 17:30 +07` → `2026-09-18 17:30 +07`
- Pool: `CRVUSDT, XMRUSDT, HBARUSDT, DOGEUSDT, AAVEUSDT, FILUSDT, GALAUSDT`

| Metric | ALT | Majors |
| --- | ---: | ---: |
| Return | +879.0% | +9648.3% |
| PF | 1.4353 | 1.4946 |
| WR | 74.4% | 74.9% |
| MaxDD | 27.8% | 18.9% |
| t/d | 5.02 | 10.92 |
| Trades | 9163 | 19932 |
| Final $ | $9790 | $97483 |
| n_syms | 7 | 16 |

### Per coin (ALT)

| Symbol | n | WR | PF | Net $ |
| --- | ---: | ---: | ---: | ---: |
| CRVUSDT | 1418 | 76.0% | 1.53 | +1713 |
| XMRUSDT | 1502 | 73.8% | 1.71 | +1537 |
| DOGEUSDT | 1234 | 73.5% | 1.53 | +1278 |
| FILUSDT | 1311 | 74.1% | 1.39 | +1182 |
| AAVEUSDT | 1229 | 74.9% | 1.33 | +1111 |
| GALAUSDT | 1209 | 74.7% | 1.31 | +1016 |
| HBARUSDT | 1260 | 73.6% | 1.36 | +953 |

## 2100d · Best-12 · thực tế **2100d** · **5 coin**

- Eval: `2020-12-18 17:30 +07` → `2026-09-18 17:30 +07`
- Pool: `CRVUSDT, XMRUSDT, DOGEUSDT, AAVEUSDT, FILUSDT`

| Metric | ALT | Majors |
| --- | ---: | ---: |
| Return | +622.3% | +29136.5% |
| PF | 1.4525 | 1.4943 |
| WR | 74.4% | 74.8% |
| MaxDD | 21.7% | 25.7% |
| t/d | 3.62 | 10.70 |
| Trades | 7599 | 22462 |
| Final $ | $7223 | $292365 |
| n_syms | 5 | 16 |

### Per coin (ALT)

| Symbol | n | WR | PF | Net $ |
| --- | ---: | ---: | ---: | ---: |
| CRVUSDT | 1592 | 75.4% | 1.45 | +1443 |
| XMRUSDT | 1689 | 73.8% | 1.64 | +1391 |
| DOGEUSDT | 1387 | 74.3% | 1.55 | +1264 |
| FILUSDT | 1515 | 74.3% | 1.41 | +1140 |
| AAVEUSDT | 1416 | 74.0% | 1.30 | +985 |

