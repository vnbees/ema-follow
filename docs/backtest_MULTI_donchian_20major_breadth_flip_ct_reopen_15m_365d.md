# D20 breadth_flip — đóng / reopen lệnh open ngược trend (breadth vote)

- Sinh luc: 2026-08-23 15:43:30 +07
- Script: `scripts/backtest_donchian_20coin_breadth_flip_ct_reopen.py` (cache-only)
- Base: **breadth_flip** (entry flip như live). Breadth vote mỗi 15m (`ratio=1.3`, `min_n=12`).
- **Case 1 (`flip_close_ct`)**: vote LONG/SHORT → **đóng mọi lot open ngược vote** @ bar close.
- **Case 2 (`flip_close_reopen`)**: như case 1 + **mở lại cùng margin/qty** theo chiều vote.
- Neutral vote → không đụng open book.

| Config | %/ngày | MaxDD | PF | WR | n | t/d | ct_close | reopen | pnl_ct | flip_ok | Note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `breadth_flip` | **+22.567%** | **25.3%** | 1.37 | 75.1% | 17768 | 48.7 | 0 | 0 | +0 | 6010 | baseline flip (live) — không đụng open book |
| `flip_close_ct` | **+0.169%** | **34.0%** | 1.03 | 42.1% | 29991 | 82.2 | 18962 | 0 | -19263 | 10344 | case 1: mỗi 15m đóng hết open ngược breadth vote |
| `flip_close_reopen` | **+4.419%** | **32.1%** | 1.11 | 47.7% | 56290 | 154.2 | 31033 | 31033 | -146833 | 8381 | case 2: đóng CT + mở lại cùng margin/qty theo vote |

## So với baseline flip

- Baseline `breadth_flip`: **+22.567%**/ngày, MaxDD **25.3%**, PF 1.37, 48.7 lệnh/ngày
- `flip_close_ct`: Δ%/ngày **-22.398**, ΔMaxDD **+8.7pp**, ct_close=18962, reopen=0, pnl từ CT close=-19263
- `flip_close_reopen`: Δ%/ngày **-18.148**, ΔMaxDD **+6.8pp**, ct_close=31033, reopen=31033, pnl từ CT close=-146833

Paper only — không ảnh hưởng bot live.
