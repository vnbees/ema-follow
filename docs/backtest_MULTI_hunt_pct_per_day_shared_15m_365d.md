# Hunt higher %/day — shared-wallet multi-coin (no BTW)

- Sinh luc: 2026-08-21 09:10:28 +07
- Coins: LINKUSDT, HYPEUSDT, SUIUSDT, DOGEUSDT, SOLUSDT · capital **1000$ chung** · 15m · 365d
- Muc tieu: tim path nang %/ngay (khong chi nap von). 1%/ngay la moc tham chieu, khong bat buoc dat.
- Moi config: body_size_rr05-style filter (tru khi note khac) tren **1 vi shared**.

| Rank | Config | %/ngày | %/tháng | %/năm | Net | MaxDD | PF | Wipe | n | lệnh/ngày | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `H_pyr_1pct` | **+26.658%** | +811.39% | +9730.0% | **+97297.4** | 52.7% | 1.56 | 0.64 | 7853 | 21.5 | 1% + pyramid×2 stack≤3 |
| 2 | `L_push` | **+10.924%** | +332.50% | +3987.3% | **+39871.5** | 27.3% | 1.54 | 0.65 | 5241 | 14.4 | 1.5% + top2 + max5 + pyr stack2 |
| 3 | `J_concentrated` | **+3.194%** | +97.22% | +1165.9% | **+11658.4** | 21.6% | 1.54 | 0.65 | 3368 | 9.2 | 2% + top1/bar + max3 (tap trung) |
| 4 | `G_pyramid2x` | **+2.801%** | +85.24% | +1022.2% | **+10221.9** | 27.2% | 1.46 | 0.69 | 7853 | 21.5 | 0.5% + pyramid×2 stack≤3 + close-all TP |
| 5 | `E_margin_1.5` | **+2.644%** | +80.48% | +965.0% | **+9650.1** | 11.4% | 1.56 | 0.64 | 4245 | 11.6 | margin 1.5% + body_rr max8 |
| 6 | `F_margin_2_mae1` | **+2.177%** | +66.27% | +794.7% | **+7946.4** | 9.9% | 1.25 | 0.80 | 5847 | 16.0 | margin 2% + MAE cut 1R + max6 |
| 7 | `D_margin_1pct` | **+1.062%** | +32.33% | +387.7% | **+3877.2** | 7.6% | 1.54 | 0.65 | 4245 | 11.6 | margin 1% + body_rr max10 |
| 8 | `K_bal_path` | **+0.553%** | +16.85% | +202.0% | **+2020.1** | 5.1% | 1.26 | 0.79 | 5843 | 16.0 | 1% + top3/bar + max6 + MAE1R (can bang) |
| 9 | `I_pyr_1pct_mae15` | **+0.429%** | +13.06% | +156.6% | **+1566.0** | 28.6% | 1.09 | 0.92 | 10086 | 27.6 | 1% + pyramid×2 + MAE1.5R |
| 10 | `A_base_sep_like` | **+0.333%** | +10.15% | +121.7% | **+1216.7** | 3.8% | 1.53 | 0.65 | 4245 | 11.6 | ref: body_rr, 0.5%, max_open5 shared |
| 11 | `B_more_slots` | **+0.333%** | +10.15% | +121.7% | **+1216.7** | 3.8% | 1.53 | 0.65 | 4245 | 11.6 | 0.5% + max_open10 (nhieu coin song song) |
| 12 | `C_top2_quality` | **+0.317%** | +9.65% | +115.7% | **+1157.2** | 3.8% | 1.51 | 0.66 | 4229 | 11.6 | 0.5% max10 nhung chi top-2 pot_rr/bar |

## Ket luan

- Best %/day: `H_pyr_1pct` → **+26.658%/ngày** (maxDD 52.7%, net +97297.4)
- Best risk-adjusted (%/day / DD): `L_push` → +10.924%/day / DD 27.3%
- Config ≥0.5%/ngày: `D_margin_1pct`, `E_margin_1.5`, `F_margin_2_mae1`, `G_pyramid2x`, `J_concentrated`, `K_bal_path`, `L_push`

### Cach hieu

1. **Shared wallet + nhieu slot** tang %/ngay bang concurrency, khong can nap von.
2. **Margin 1–1.5%** thuong la sweet spot truoc khi DD no.
3. **Pyramid 2x** co the phong %/ngay manh nhung DD/wipe tang — can MAE cut.
4. **1%/ngày** neu dat duoc chi trong BT high-risk; live can giam ky vong xuong 0.2–0.4%/ngày sustainable.

