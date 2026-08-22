# D20 — breadth mid: hard SKIP vs FLIP side

- Sinh luc: 2026-08-22 12:04:37 +07
- Script: `scripts/backtest_donchian_20coin_breadth_flip.py` (cache-only)
- D20_like_bot 15m 365d 1000$ · vote mid như live (`ratio=1.3`, `min_n=12`).
- **hard**: tín hiệu ngược vote → bỏ (live hiện tại).
- **flip**: tín hiệu ngược vote → **lật long↔short**, TP/opp band theo side mới, vào lệnh.
- **flip_rr**: như flip nhưng `pot_rr` sau lật phải ≥ 0.5.

| Config | %/ngày | MaxDD | PF | WR | n | t/d | L/S | skip | flip_ok | drop | pnlN/F | Note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | --- |
| `D20_base` | **+33.450%** | **49.9%** | 1.48 | 73.4% | 16905 | 46.3 | 8446/8459 | 0 | 0 | 0 | +122091/+0 | không breadth (baseline live cũ) |
| `breadth_flip` | **+22.567%** | **25.3%** | 1.37 | 75.1% | 17768 | 48.7 | 8646/9122 | 0 | 6010 | 0 | +55317/+27051 | ngược breadth → LẬT side vào (không check pot sau flip) |
| `flip_ratio1.5` | **+22.567%** | **25.3%** | 1.37 | 75.1% | 17768 | 48.7 | 8646/9122 | 0 | 6010 | 0 | +55317/+27051 | lật; vote cần lead ≥1.5× |
| `breadth_flip_rr` | **+21.221%** | **25.8%** | 1.36 | 74.2% | 16803 | 46.0 | 8156/8647 | 0 | 4775 | 3172 | +54709/+22748 | ngược breadth → lật; pot_rr flip ≥0.5 mới vào |
| `breadth_hard` | **+8.751%** | **19.0%** | 1.39 | 73.8% | 14078 | 38.6 | 6888/7190 | 15248 | 0 | 0 | +31942/+0 | ngược breadth → SKIP (live hiện tại) |
| `hard_ratio1.5` | **+8.751%** | **19.0%** | 1.39 | 73.8% | 14078 | 38.6 | 6888/7190 | 15248 | 0 | 0 | +31942/+0 | skip; vote lead ≥1.5× |

## Đọc

- Baseline D20: **+33.450%**/ngày, MaxDD **49.9%**, 46.3 lệnh/ngày
- Live hard: **+8.751%**/ngày, MaxDD **19.0%**, 38.6 lệnh/ngày
- Best flip: `breadth_flip` → +22.567%/ngày, MaxDD 25.3%, flip_ok=6010, pnl flip=+27051

Paper only — không ảnh hưởng bot live.
