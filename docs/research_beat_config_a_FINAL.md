# Research FINAL: beat Config A — SMA pullback winners

- Generated: **2026-09-18 16:59:50 +07**
- Live bot **unchanged**. Research scripts only.
- Yardstick: shared 20-coin wallet, same fees/sizing family as Config A
- Winner family: **SMA fast/slow trend + pullback to SMA_fast + bounce** (≠ Donchian counter)
- Exit: soft TP at Donchian band (no hard SL) — exit style like A, **entry different**
- Strict beat used in search: PF ≥ A+0.03 and MaxDD ≤ A+2 on 365d **and** 1095d

## Dual-window winners (from R4)

| Strategy | 1y PF | 1y WR | 1y Ret | 1y MaxDD | 3y PF | 3y WR | 3y Ret | 3y MaxDD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **Config A (baseline)** | **1.54** | 71.5% | +2546% | 38.7% | **1.53** | 71.8% | huge | 52.3% |
| sma34/144 v1.2 | 1.6748 | 76.1% | +244.8% | 13.0% | 1.5823 | 76.0% | +4358.1% | 14.4% |
| sma34/144 v1.2 +ema_soft | 1.6616 | 75.7% | +223.7% | 12.2% | 1.5683 | 75.8% | +3638.0% | 14.1% |
| sma50/200 body0.4-1.0 | 1.7140 | 75.8% | +95.1% | 6.8% | 1.5667 | 75.0% | +630.2% | 15.7% |
| sma50/200 body0.3-1.2 | 1.6636 | 75.4% | +140.7% | 15.3% | 1.5732 | 75.3% | +1589.6% | 17.9% |
| sma50/200 +stack | 1.7108 | 74.2% | +215.4% | 9.8% | 1.5643 | 74.3% | +2673.9% | 16.8% |
| sma50/200 +ema200 | 1.6821 | 74.5% | +253.9% | 17.6% | 1.5698 | 74.5% | +4819.6% | 18.1% |
| sma50/200 v1.5 rr0.8 | 1.6828 | 73.9% | +91.7% | 6.9% | 1.5760 | 74.1% | +670.1% | 14.0% |
| v1.2 rr0.8 body0.3-1.2 | 1.6910 | 75.2% | +117.6% | 15.0% | 1.5880 | 74.8% | +1085.8% | 16.8% |
| v1.5 body0.3-1.2 | 1.6683 | 75.9% | +64.9% | 7.7% | 1.5977 | 75.6% | +368.3% | 16.0% |
| sma50/200 cap1.5 | 1.6763 | 74.7% | +221.8% | 14.9% | 1.5619 | 74.7% | +3693.5% | 14.9% |

## 1825d (5y) validation · A PF=1.4378 MaxDD=46.3% · 16 coins

| Strategy | PF | WR | Return | MaxDD | n | vs A PF | vs A DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Config A | 1.4378 | 71.5% | +1126716247.1% | 46.3% | 46397 | — | — |
| v1.2 rr0.8 body0.3-1.2 | 1.5500 | 73.6% | +1430.7% | 19.7% | 8048 | +0.1122 | -26.6pp |
| sma50/200 body0.3-1.2 | 1.5359 | 74.3% | +2366.4% | 18.6% | 11668 | +0.0981 | -27.7pp |
| sma50/200 +ema200 | 1.5341 | 73.7% | +11257.4% | 19.4% | 16470 | +0.0963 | -26.9pp |
| sma50/200 +stack | 1.5300 | 73.6% | +6069.2% | 19.0% | 13886 | +0.0922 | -27.3pp |
| sma50/200 cap1.5 | 1.5282 | 73.8% | +7958.4% | 17.8% | 17248 | +0.0904 | -28.5pp |
| sma50/200 body0.4-1.0 | 1.5161 | 74.5% | +921.6% | 15.1% | 8604 | +0.0783 | -31.2pp |
| sma50/200 v1.5 rr0.8 | 1.5140 | 72.7% | +810.1% | 14.3% | 6435 | +0.0762 | -32.0pp |
| sma34/144 v1.2 +ema_soft | 1.4957 | 74.8% | +8422.8% | 18.9% | 18850 | +0.0578 | -27.4pp |
| sma34/144 v1.2 | 1.4946 | 74.9% | +9648.3% | 18.9% | 19932 | +0.0568 | -27.4pp |
| v1.5 body0.3-1.2 | 1.4672 | 74.2% | +388.2% | 13.0% | 6306 | +0.0294 | -33.3pp |

## Primary recommendation (paper)

### `sma34/144 v1.2` (logic rõ, dual-beat ổn)

**Logic (khác bot live):**
1. Trend: SMA34 > SMA144 → chỉ long; SMA34 < SMA144 → chỉ short
2. Entry: giá **chạm** SMA34 (wick) + **bounce** (close reclaim + đúng màu nến)
3. Lọc: vol_ratio ≥ 1.2
4. Rank pot_rr (TP = Donchian band gần, SL ẩn = band đối) · top5 · max10 · size∝min(pot_rr,2)
5. Exit: chạm Donchian band theo hướng (TP soft giống Config A — **không** hard SL)

| Window | PF | WR | Return | MaxDD | Trades |
| --- | ---: | ---: | ---: | ---: | ---: |
| 365d | **1.6748** | 76.1% | +244.8% | **13.0%** | 4999 |
| 1095d | **1.5823** | 76.0% | +4358.1% | **14.4%** | 14613 |
| 1825d | **1.4946** | 74.9% | +9648.3% | **18.9%** | 19932 |

### So với Config A

| | Config A | sma34/144 v1.2 |
| --- | ---: | ---: |
| 1y PF | 1.54 | **1.67** |
| 1y MaxDD | 38.7% | **13.0%** |
| 3y PF | 1.53 | **1.58** |
| 3y MaxDD | 52.3% | **14.4%** |
| 5y PF | 1.44 | **1.49** |
| 5y MaxDD | 46.3% | **18.9%** |

### Runner-up mạnh hơn về PF dài hạn: `sma50/200` + `rr≥0.8` + `body∈[0.3,1.2]`

| Window | PF | MaxDD |
| --- | ---: | ---: |
| 1y | 1.69 | 15.0% |
| 3y | **1.59** | 16.8% |
| 5y | **1.55** | 19.7% |

**Lưu ý:** Return tuyệt đối paper thường thấp hơn Config A (ít lệnh / ít compound), nhưng **PF cao hơn + MaxDD thấp hơn rất rõ** trên 1y/3y/5y → tốt hơn về chất lượng edge / risk. Bot live **không bị sửa**.

## Đã loại (R1–R3)

- ATR hard-SL strategies (turtle, RSI fade, HA, ROC, …): PF ≪ 1
- same_dir Donchian expand: PF ~1.35–1.40 (DD tốt hơn A nhưng PF kém)
- Soft-exit turtle/squeeze: PF cao nhưng n quá ít (~150–600), không ổn định 3y
- EMA89 pullback / B-Xtrender / Elliott (trước đó): fail

## Scripts (không đụng live)

- `scripts/research_beat_config_a.py` (R1)
- `scripts/research_beat_config_a_r2.py` (R2 soft-exit)
- `scripts/research_beat_config_a_r3.py` (R3 Keltner/BB — optional follow-up)
- `scripts/research_beat_config_a_r4_sma.py` (R4 deep-tune — **source of winners**)
