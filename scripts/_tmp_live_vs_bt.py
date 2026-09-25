#!/usr/bin/env python3
"""One-off: live performance summary for vs-backtest review."""
from __future__ import annotations

import statistics as st
from datetime import datetime
from zoneinfo import ZoneInfo

from src.database import (
    get_baseline_equity,
    get_connection,
    get_equity_peak,
    sum_successful_spot_transfers,
)
from src.donchian import store

VN = ZoneInfo("Asia/Ho_Chi_Minh")
store.ensure_schema()
baseline = float(get_baseline_equity() or 0)
skim_tot, skim_n = sum_successful_spot_transfers()
peak = float(get_equity_peak() or 0)

with get_connection() as c:
    first = c.execute(
        "SELECT recorded_at, equity FROM equity_snapshots ORDER BY recorded_at ASC LIMIT 1"
    ).fetchone()
    last = c.execute(
        "SELECT recorded_at, equity FROM equity_snapshots ORDER BY recorded_at DESC LIMIT 1"
    ).fetchone()
    snaps = c.execute(
        "SELECT recorded_at, equity FROM equity_snapshots ORDER BY recorded_at"
    ).fetchall()
    by_day: dict = {}
    for r in snaps:
        dt = datetime.fromisoformat(r["recorded_at"].replace("Z", "+00:00")).astimezone(VN).date()
        by_day[dt] = float(r["equity"])
    days = sorted(by_day)

    closed = c.execute(
        """
        SELECT COUNT(*) n,
               SUM(CASE WHEN pnl_usdt>0 THEN 1 ELSE 0 END) wins,
               SUM(CASE WHEN pnl_usdt<=0 THEN 1 ELSE 0 END) losses,
               COALESCE(SUM(pnl_usdt),0) sum_pnl,
               COALESCE(SUM(CASE WHEN pnl_usdt>0 THEN pnl_usdt ELSE 0 END),0) gp,
               COALESCE(SUM(CASE WHEN pnl_usdt<0 THEN pnl_usdt ELSE 0 END),0) gl,
               MIN(opened_at) first_open,
               MAX(closed_at) last_close
        FROM donchian_lots
        WHERE status='closed' AND IFNULL(close_reason,'') != 'RESET_FRESH'
        """
    ).fetchone()
    open_n = c.execute(
        "SELECT COUNT(*) FROM donchian_lots WHERE status='open'"
    ).fetchone()[0]
    reasons = c.execute(
        """
        SELECT close_reason, COUNT(*) n, COALESCE(SUM(pnl_usdt),0) pnl
        FROM donchian_lots WHERE status='closed'
        GROUP BY close_reason ORDER BY n DESC
        """
    ).fetchall()
    ok_skims = c.execute(
        """
        SELECT transfer_date, amount, day_pnl, sod_equity, eod_equity
        FROM spot_transfers WHERE status='success' ORDER BY transfer_date
        """
    ).fetchall()

eqs = [by_day[d] for d in days]
peak_eq = eqs[0]
maxdd = 0.0
trough = eqs[0]
for e in eqs:
    peak_eq = max(peak_eq, e)
    dd = (peak_eq - e) / peak_eq * 100 if peak_eq > 0 else 0
    if dd > maxdd:
        maxdd = dd
        trough = e

skim_by_day: dict[str, float] = {}
for r in ok_skims:
    skim_by_day[r["transfer_date"]] = skim_by_day.get(r["transfer_date"], 0) + float(r["amount"])
cum = 0.0
wealth = []
for d in days:
    cum += skim_by_day.get(d.isoformat(), 0)
    wealth.append(by_day[d] + cum)
pk = wealth[0]
mdd_tot = 0.0
for w in wealth:
    pk = max(pk, w)
    mdd_tot = max(mdd_tot, (pk - w) / pk * 100 if pk > 0 else 0)

fe, le = float(first["equity"]), float(last["equity"])
total_w = le + skim_tot
n_cal_days = (days[-1] - days[0]).days + 1

print("=== WINDOW ===")
print("first", first["recorded_at"], round(fe, 2))
print("last", last["recorded_at"], round(le, 2))
print("calendar_days", n_cal_days, "eod_points", len(days))
print("baseline", round(baseline, 2), "peak_setting", round(peak, 2))

print("\n=== WEALTH ===")
print(f"futures_chg {fe:.2f}->{le:.2f} ({(le / fe - 1) * 100:+.2f}%)")
print(f"skim {skim_tot:.2f} x{skim_n}")
print(f"total_wealth {total_w:.2f} vs baseline {baseline:.2f} ({(total_w / baseline - 1) * 100:+.2f}%)")
print(f"total vs first_eq {(total_w / fe - 1) * 100:+.2f}%")
if n_cal_days > 1:
    pct_day = (total_w / fe - 1) / (n_cal_days - 1) * 100
    geo = (total_w / fe) ** (1 / (n_cal_days - 1)) - 1
    print(f"%/day simple: {pct_day:.3f}%")
    print(f"%/day geo: {geo * 100:.3f}%")
print(f"MaxDD futures(eod): {maxdd:.1f}% trough~{trough:.0f}")
print(f"MaxDD total(fut+cum skim): {mdd_tot:.1f}%")

rets = []
for i in range(1, len(days)):
    rets.append((by_day[days[i]] / by_day[days[i - 1]] - 1) * 100)
if rets:
    print(
        f"fut day% mean={st.mean(rets):.3f} med={st.median(rets):.3f} "
        f"std={st.pstdev(rets):.2f} min={min(rets):.2f} max={max(rets):.2f}"
    )
    up = sum(1 for r in rets if r > 0)
    dn = sum(1 for r in rets if r < 0)
    print(f"up_days={up} down_days={dn} flat={len(rets) - up - dn}")
    bad = sum(1 for r in rets if r <= -7)
    print(f"days<=-7%: {bad} ({100 * bad / len(rets):.1f}%)")

print("\n=== TRADES ===")
cd = dict(closed)
print(cd)
n = int(cd["n"] or 0)
wins = int(cd["wins"] or 0)
gp = float(cd["gp"])
gl = float(cd["gl"])
wr = 100 * wins / n if n else 0
pf = (gp / abs(gl)) if gl < 0 else float("inf")
print(f"WR={wr:.1f}% PF={pf:.3f} avg_pnl={float(cd['sum_pnl']) / n if n else 0:.3f}")
print(f"open_lots={open_n}")
print("reasons:")
for r in reasons:
    print(" ", dict(r))
if n_cal_days > 0:
    print(f"closed_trades/day={n / n_cal_days:.2f}")

daily = store.daily_stats(40)
print("\n=== DAILY (last 15) ===")
for d in daily[:15]:
    print(d)

dp = [float(r["day_pnl"] or 0) for r in ok_skims]
print("\n=== SKIM ===")
print(
    f"n={len(dp)} mean_day_pnl={st.mean(dp) if dp else 0:.2f} "
    f"sum_day_pnl={sum(dp):.2f} "
    f"skim_of_green_day_pnl={100 * skim_tot / sum(dp) if sum(dp) else 0:.1f}%"
)

cut = datetime(2026, 9, 18, tzinfo=VN).date()
days2 = [d for d in days if d >= cut]
if len(days2) >= 2:
    fe2 = by_day[days2[0]]
    le2 = by_day[days2[-1]]
    skim2 = sum(v for k, v in skim_by_day.items() if k >= cut.isoformat())
    print("\n=== SINCE 2026-09-18 ===")
    print(
        f"days={len(days2)} fut {fe2:.2f}->{le2:.2f} ({(le2 / fe2 - 1) * 100:+.2f}%) "
        f"skim_in_window={skim2:.2f}"
    )
    tw2 = le2 + skim2
    print(f"approx total {tw2:.2f} vs start {fe2:.2f} ({(tw2 / fe2 - 1) * 100:+.2f}%)")
