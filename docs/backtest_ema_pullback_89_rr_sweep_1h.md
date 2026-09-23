# EMA pullback 89 — RR sweep (>1)

- Generated: **2026-09-18 12:50:57 +07**
- Script: `scripts/backtest_ema_pullback_89.py`
- TF: **1h** · RR tested: **2.0**
- Entry: stack + touch EMA89 · Exit: SL / TP(rr×R) / STACK_BREAK
- SL modes: `ema200`, `atr`, `swing`

## 365d · tf **1h** · từ 2025-09-16

### SL=`ema200`

| RR | Trades | WR | PF mean/med | Return | MaxDD | coin+ | TP% | SL% | Stack% |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 1937 | 30.5% | 0.90/0.83 | -6.1% | 17.9% | 5/20 | 29% | 48% | 24% |

- Best by (PF med → return → −DD): **RR=2** · PF med **0.83** · ret **-6.1%** · MaxDD **17.9%** · coin+ **5/20**

### SL=`atr`

| RR | Trades | WR | PF mean/med | Return | MaxDD | coin+ | TP% | SL% | Stack% |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 2533 | 33.1% | 0.87/0.87 | -10.4% | 23.8% | 5/20 | 33% | 64% | 2% |

- Best by (PF med → return → −DD): **RR=2** · PF med **0.87** · ret **-10.4%** · MaxDD **23.8%** · coin+ **5/20**

### SL=`swing`

| RR | Trades | WR | PF mean/med | Return | MaxDD | coin+ | TP% | SL% | Stack% |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 2402 | 32.0% | 0.89/0.85 | -8.3% | 21.8% | 4/20 | 31% | 56% | 13% |

- Best by (PF med → return → −DD): **RR=2** · PF med **0.85** · ret **-8.3%** · MaxDD **21.8%** · coin+ **4/20**

## 1095d · tf **1h** · từ 2023-09-17

### SL=`ema200`

| RR | Trades | WR | PF mean/med | Return | MaxDD | coin+ | TP% | SL% | Stack% |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 5838 | 31.0% | 0.91/0.89 | -13.4% | 34.6% | 4/20 | 29% | 48% | 23% |

- Best by (PF med → return → −DD): **RR=2** · PF med **0.89** · ret **-13.4%** · MaxDD **34.6%** · coin+ **4/20**

### SL=`atr`

| RR | Trades | WR | PF mean/med | Return | MaxDD | coin+ | TP% | SL% | Stack% |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 7782 | 33.5% | 0.89/0.86 | -21.4% | 45.4% | 4/20 | 33% | 64% | 3% |

- Best by (PF med → return → −DD): **RR=2** · PF med **0.86** · ret **-21.4%** · MaxDD **45.4%** · coin+ **4/20**

### SL=`swing`

| RR | Trades | WR | PF mean/med | Return | MaxDD | coin+ | TP% | SL% | Stack% |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 7316 | 31.1% | 0.85/0.85 | -27.1% | 45.9% | 2/20 | 30% | 56% | 13% |

- Best by (PF med → return → −DD): **RR=2** · PF med **0.85** · ret **-27.1%** · MaxDD **45.9%** · coin+ **2/20**

## Đọc kết quả

- RR thấp hơn → TP% cao hơn, WR cao hơn, nhưng reward/lệnh nhỏ hơn.
- RR cao hơn → ít chạm TP, STACK_BREAK/SL chiếm phần lớn → PF thường không tăng tuyến tính.
- Chọn RR có PF med cao nhất và coin+ nhiều; so với Config A (PF~1.5) để quyết định có đáng live.
