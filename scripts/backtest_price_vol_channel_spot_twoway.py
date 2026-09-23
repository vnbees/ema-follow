#!/usr/bin/env python3
"""Config A — daily 0h spot settle: none | live skim | twoway 40%.

Settle once per Asia/Ho_Chi_Minh calendar day (first bar of new day ≈ 0h+07).

Modes:
  none   — no transfers (all-in futures compound)
  live   — live bot skim (1-way): green only
            rút = min(cash, day_pnl×40%, SOD×1.5%); skip if day_pnl≤0 or DD≥20%
  twoway — green: futures→spot 40% day_pnl (cash cap)
           red:   spot→futures 40% |day_pnl| only if spot đủ; else skip

Does NOT modify live bot.

Usage:
  .venv/bin/python scripts/backtest_price_vol_channel_spot_twoway.py
  .venv/bin/python scripts/backtest_price_vol_channel_spot_twoway.py --windows 365,1095
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")

spec = importlib.util.spec_from_file_location("pvc_tw", ROOT / "scripts/backtest_price_vol_channel.py")
pvc = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["pvc_tw"] = pvc
spec.loader.exec_module(pvc)

channel_enrich = pvc.channel_enrich
Cfg = pvc.Cfg
load_pool = pvc.load_pool
pnl = pvc.pnl
CAPITAL = pvc.CAPITAL
LEVERAGE = pvc.LEVERAGE

SKIM = 0.40
LIVE_DAY_CAP = 0.015  # SOD × 1.5%
LIVE_DD_PAUSE = 0.20  # pause skim when DD from peak ≥ 20%
CFG_A = Cfg(
    "A · ch20 vol≥1.2 rr0.5 margin1% max10",
    period=20,
    min_vol=1.2,
    min_pot_rr=0.5,
    margin_pct=0.01,
    max_open=10,
    size_mult_cap=2.0,
)


def local_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d %H:%M %Z")


def run_mode(
    cfg: Cfg,
    raw_dfs: dict,
    eval_start: int,
    *,
    mode: str,
) -> dict:
    """mode: 'none' | 'live' | 'twoway'"""
    if mode not in ("none", "live", "twoway"):
        raise ValueError(f"unknown mode {mode}")
    dfs = {
        sym: channel_enrich(df, cfg.period, cfg.slope_lb, cfg.parallel_tol)
        for sym, df in raw_dfs.items()
    }
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed)
    first_eval = next((t for t in common if t >= eval_start), common[-1])
    days = max((common[-1] - first_eval) / 86400000.0, 1e-9)

    cash = CAPITAL
    spot = 0.0
    opens: list[dict] = []
    trades: list[dict] = []
    state = {sym: {"trend": None, "waiting": False} for sym in symbols}

    peak_fut = CAPITAL
    maxdd_fut = 0.0
    peak_tot = CAPITAL
    maxdd_tot = 0.0

    last_day: str | None = None
    sod_equity: float | None = None
    events: list[dict] = []
    eod_total: list[tuple[int, float, float, float]] = []  # ts, fut, spot, total

    def equity_fut(mark: dict[str, float]) -> float:
        eq = cash
        for t in opens:
            eq += t["margin"] + pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def close_trade(t: dict, px: float, reason: str, ts: int) -> None:
        nonlocal cash
        pl = pnl(t["side"], t["entry"], px, t["qty"])
        cash += t["margin"] + pl
        if ts >= eval_start:
            trades.append({"pnl": pl, "reason": reason, "side": t["side"], "sym": t["sym"]})

    def settle(mark: dict[str, float], day: str, ts: int) -> None:
        nonlocal cash, spot, sod_equity
        if sod_equity is None or mode == "none":
            return
        if ts < eval_start:
            return
        eq = max(equity_fut(mark), 0.0)
        day_pnl = eq - sod_equity
        amount = 0.0
        direction = "none"
        reason = "flat"
        peak_use = max(peak_fut, eq, 1e-12)
        dd_pct = max(0.0, (peak_use - eq) / peak_use)

        if mode == "live":
            # 1-way skim matching src/spot_transfer.py decide_skim
            if day_pnl <= 1e-9:
                reason = "no_profit"
            elif dd_pct >= LIVE_DD_PAUSE:
                reason = "dd_pause"
            else:
                want = min(day_pnl * SKIM, sod_equity * LIVE_DAY_CAP)
                take = min(max(cash, 0.0), want)
                if take > 1e-9:
                    cash -= take
                    spot += take
                    amount = take
                    direction = "to_spot"
                    reason = "green_skim"
                else:
                    reason = "green_no_cash"
        elif mode == "twoway":
            if day_pnl > 1e-9:
                want = day_pnl * SKIM
                take = min(max(cash, 0.0), want)
                if take > 1e-9:
                    cash -= take
                    spot += take
                    amount = take
                    direction = "to_spot"
                    reason = "green_skim"
                else:
                    reason = "green_no_cash"
            elif day_pnl < -1e-9:
                want = abs(day_pnl) * SKIM
                if spot + 1e-9 >= want:
                    spot -= want
                    cash += want
                    amount = want
                    direction = "to_futures"
                    reason = "red_topup"
                else:
                    reason = "red_insufficient_spot"

        events.append(
            {
                "day": day,
                "ts": ts,
                "sod": sod_equity,
                "eod": eq,
                "day_pnl": day_pnl,
                "dd_pct": dd_pct,
                "amount": amount,
                "direction": direction,
                "reason": reason,
                "fut_after": equity_fut(mark),
                "spot_after": spot,
                "total_after": equity_fut(mark) + spot,
            }
        )

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}
        day = local_day(ts)

        # exits
        still = []
        for t in opens:
            b = bar[t["sym"]]
            hi, lo = float(b["high"]), float(b["low"])
            up, dn = float(b["ch_upper"]), float(b["ch_lower"])
            side = t["side"]
            tp = up if side == "long" else dn
            if (side == "long" and hi >= tp) or (side == "short" and lo <= tp):
                close_trade(t, tp, "TP", ts)
            else:
                still.append(t)
        opens = still

        # day rollover @ first bar of new local day (~0h +07)
        if last_day is not None and day != last_day:
            settle(mark, last_day, ts)
            sod_equity = equity_fut(mark)
        elif sod_equity is None:
            sod_equity = equity_fut(mark)
        last_day = day

        if ts < eval_start:
            for sym in symbols:
                b = bar[sym]
                if bool(b.get("channel_expand", False)):
                    px = float(b["close"])
                    mid = float(b["ch_mid"])
                    state[sym]["trend"] = "up" if px > mid else "down"
                    state[sym]["waiting"] = True
            continue

        cands: list[dict] = []
        for sym in symbols:
            b = bar[sym]
            if np.isnan(b["ch_upper"]) or np.isnan(b["atr"]) or np.isnan(b.get("vol_ratio", np.nan)):
                continue
            px, o = float(b["close"]), float(b["open"])
            up, dn, mid = float(b["ch_upper"]), float(b["ch_lower"]), float(b["ch_mid"])
            a, w = float(b["atr"]), float(b["ch_width"])
            st = state[sym]

            if bool(b.get("channel_expand", False)):
                st["trend"] = "up" if px > mid else "down"
                st["waiting"] = True

            if not st["waiting"] or not st["trend"]:
                continue
            if any(t["sym"] == sym for t in opens):
                continue
            if float(b["vol_ratio"]) < cfg.min_vol:
                continue
            if cfg.skip_parallel and bool(b.get("bands_parallel", False)):
                continue

            counter = (st["trend"] == "up" and px < o) or (st["trend"] == "down" and px > o)
            if not counter or w <= 1e-12 or a <= 0:
                continue
            side = "long" if st["trend"] == "up" else "short"
            body = abs(px - o) / a
            if not (cfg.body_lo <= body <= cfg.body_hi):
                continue
            tp = up if side == "long" else dn
            sl0 = dn if side == "long" else up
            pot_rr = abs(tp - px) / max(abs(px - sl0), 1e-12)
            if pot_rr < cfg.min_pot_rr:
                continue
            if cfg.ema_filter:
                e21, e89, e200 = float(b["ema21"]), float(b["ema89"]), float(b["ema200"])
                if cfg.ema_filter == "stack":
                    ok = (e21 > e89 > e200) if side == "long" else (e21 < e89 < e200)
                elif cfg.ema_filter == "stack_soft":
                    ok = (e21 > e89) if side == "long" else (e21 < e89)
                elif cfg.ema_filter == "ema200":
                    ok = (px > e200) if side == "long" else (px < e200)
                else:
                    ok = True
                if not ok:
                    continue
            cands.append(
                {"sym": sym, "side": side, "entry": px, "tp": tp, "sl": sl0, "pot_rr": pot_rr}
            )

        cands.sort(key=lambda x: x["pot_rr"], reverse=True)
        for cand in cands[: cfg.top_k]:
            if len(opens) >= cfg.max_open:
                break
            if any(t["sym"] == cand["sym"] for t in opens):
                continue
            eq = max(cash + sum(t["margin"] for t in opens), 1.0)
            mult = min(cand["pot_rr"], cfg.size_mult_cap) if cfg.size_by_rr else 1.0
            margin = min(eq * cfg.margin_pct * mult, eq * 0.15, cash)
            if margin < 1.0:
                continue
            qty = margin * LEVERAGE / cand["entry"]
            cash -= margin
            opens.append({**cand, "qty": qty, "margin": margin, "entry_ts": ts})
            state[cand["sym"]]["waiting"] = False

        fut = equity_fut(mark)
        tot = fut + spot
        peak_fut = max(peak_fut, fut)
        maxdd_fut = max(maxdd_fut, (peak_fut - fut) / peak_fut if peak_fut > 0 else 0.0)
        peak_tot = max(peak_tot, tot)
        maxdd_tot = max(maxdd_tot, (peak_tot - tot) / peak_tot if peak_tot > 0 else 0.0)

        # last bar of each local day → eod snapshot
        if i + 1 >= len(common) or local_day(common[i + 1]) != day:
            eod_total.append((ts, fut, spot, tot))

    # settle last day
    if last_day is not None and opens is not None:
        last_mark = {s: float(indexed[s].iloc[-1]["close"]) for s in symbols}
        settle(last_mark, last_day, common[-1])
        if opens:
            for t in list(opens):
                close_trade(t, last_mark[t["sym"]], "EOD", common[-1])
            opens = []

    fut_final = cash
    tot_final = fut_final + spot
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in trades)

    skim_out = sum(e["amount"] for e in events if e["direction"] == "to_spot")
    topup_in = sum(e["amount"] for e in events if e["direction"] == "to_futures")
    n_green = sum(1 for e in events if e["reason"] == "green_skim")
    n_red = sum(1 for e in events if e["reason"] == "red_topup")
    n_skip_red = sum(1 for e in events if e["reason"] == "red_insufficient_spot")
    n_dd_pause = sum(1 for e in events if e["reason"] == "dd_pause")
    n_green_days = sum(1 for e in events if e["day_pnl"] > 0)
    n_red_days = sum(1 for e in events if e["day_pnl"] < 0)

    return {
        "mode": mode,
        "days": days,
        "n_syms": len(symbols),
        "n": len(trades),
        "wr": 100 * len(wins) / len(trades) if trades else 0,
        "pf": gw / gl if gl > 0 else 0,
        "net": net,
        "fut_final": fut_final,
        "spot_final": spot,
        "total_final": tot_final,
        "ret_fut_pct": (fut_final / CAPITAL - 1) * 100,
        "ret_tot_pct": (tot_final / CAPITAL - 1) * 100,
        "pct_day_tot": (tot_final / CAPITAL - 1) * 100 / days,
        "maxdd_fut": maxdd_fut * 100,
        "maxdd_tot": maxdd_tot * 100,
        "tpd": len(trades) / days,
        "skim_out": skim_out,
        "topup_in": topup_in,
        "n_green_skim": n_green,
        "n_red_topup": n_red,
        "n_skip_red": n_skip_red,
        "n_dd_pause": n_dd_pause,
        "n_green_days": n_green_days,
        "n_red_days": n_red_days,
        "n_settle_days": len(events),
        "events": events,
        "eod_total": eod_total,
        "eval_start": first_eval,
        "eval_end": common[-1],
    }


def _row(days: int, label: str, r: dict) -> str:
    return (
        f"| {days}d | {label} | ${r['fut_final']:.0f} | ${r['spot_final']:.0f} | "
        f"**${r['total_final']:.0f}** | {r['ret_tot_pct']:+.1f}% | {r['maxdd_fut']:.1f}% | "
        f"{r['maxdd_tot']:.1f}% | ${r['skim_out']:.0f} | ${r['topup_in']:.0f} | "
        f"{r['n_green_skim']} | {r['n_red_topup']} | {r['n_skip_red']} | {r['n_dd_pause']} |"
    )


def main() -> int:
    from collections import defaultdict

    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="365,1095,1825")
    args = ap.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Config A — settle 0h: none vs **live skim** vs twoway",
        "",
        f"- Generated: **{now}**",
        "- Script: `scripts/backtest_price_vol_channel_spot_twoway.py`",
        "- Strategy: **Config A** · $1000 · 10x · fee 0.04% · chốt **0h +07**",
        "",
        "### Modes",
        "",
        "| Mode | Rule |",
        "| --- | --- |",
        "| **none** | Không rút/nạp — compound all-in futures |",
        "| **live** | Skim 1 chiều như bot: `min(cash, day_pnl×40%, SOD×1.5%)`; pause nếu DD≥20%; ngày đỏ không nạp |",
        "| **twoway** | Xanh rút 40% lãi; đỏ nạp 40% lỗ từ spot (thiếu → bỏ qua); không day_cap / DD-pause |",
        "",
        "## Summary",
        "",
        "| Window | Mode | Fut $ | Spot $ | **Total $** | Ret tot | MaxDD fut | MaxDD tot | Skim→spot | Topup←spot | #skim | #topup | #skip red | #dd_pause |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for days in windows:
        print(f"\n=== {days}d ===", flush=True)
        raw, es, _ = load_pool(days)
        print(f"  Eval {fmt_ts(es)} → …", flush=True)

        results = {}
        for mode in ("none", "live", "twoway"):
            print(f"  run {mode}…", flush=True)
            r = run_mode(CFG_A, raw, es, mode=mode)
            results[mode] = r
            print(
                f"    {mode:7s} fut=${r['fut_final']:.0f} spot=${r['spot_final']:.0f} "
                f"tot=${r['total_final']:.0f} DD_f={r['maxdd_fut']:.1f}% DD_t={r['maxdd_tot']:.1f}% "
                f"skim=${r['skim_out']:.0f} topup=${r['topup_in']:.0f} "
                f"pause={r['n_dd_pause']} skip_red={r['n_skip_red']}",
                flush=True,
            )

        base, live, tw = results["none"], results["live"], results["twoway"]
        lines.append(_row(days, "none", base))
        lines.append(_row(days, "**live**", live))
        lines.append(_row(days, "twoway", tw))

        lines += [
            "",
            f"## {days}d · so sánh 3 mode",
            "",
            f"- Eval: `{fmt_ts(base['eval_start'])}` → `{fmt_ts(base['eval_end'])}` · **{base['days']:.0f}d** · {base['n_syms']} coin",
            "",
            "| Metric | none | **live** | twoway |",
            "| --- | ---: | ---: | ---: |",
            f"| Total wealth | ${base['total_final']:.0f} | **${live['total_final']:.0f}** | ${tw['total_final']:.0f} |",
            f"| Futures | ${base['fut_final']:.0f} | ${live['fut_final']:.0f} | ${tw['fut_final']:.0f} |",
            f"| Spot | ${base['spot_final']:.0f} | ${live['spot_final']:.0f} | ${tw['spot_final']:.0f} |",
            f"| MaxDD futures | {base['maxdd_fut']:.1f}% | {live['maxdd_fut']:.1f}% | {tw['maxdd_fut']:.1f}% |",
            f"| MaxDD total | {base['maxdd_tot']:.1f}% | {live['maxdd_tot']:.1f}% | {tw['maxdd_tot']:.1f}% |",
            f"| %/ngày total | {base['pct_day_tot']:+.3f}% | {live['pct_day_tot']:+.3f}% | {tw['pct_day_tot']:+.3f}% |",
            f"| Skim→spot | $0 | ${live['skim_out']:.0f} | ${tw['skim_out']:.0f} |",
            f"| Topup←spot | $0 | $0 | ${tw['topup_in']:.0f} |",
            f"| #skim / #dd_pause | — | {live['n_green_skim']} / {live['n_dd_pause']} | {tw['n_green_skim']} / 0 |",
            f"| #topup / #skip red | — | 0 / 0 | {tw['n_red_topup']} / {tw['n_skip_red']} |",
            f"| PF / t/d | {base['pf']:.3f} / {base['tpd']:.1f} | {live['pf']:.3f} / {live['tpd']:.1f} | {tw['pf']:.3f} / {tw['tpd']:.1f} |",
            "",
        ]

        by_m: dict[str, dict] = defaultdict(lambda: {"skim": 0.0, "pause": 0, "g": 0, "nop": 0})
        for e in live["events"]:
            m = e["day"][:7]
            if e["direction"] == "to_spot":
                by_m[m]["skim"] += e["amount"]
                by_m[m]["g"] += 1
            if e["reason"] == "dd_pause":
                by_m[m]["pause"] += 1
            if e["reason"] == "no_profit":
                by_m[m]["nop"] += 1
        lines += [
            "### Live skim — theo tháng",
            "",
            "| Tháng | Skim→spot | #skim | #dd_pause | #no_profit |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for m in sorted(by_m):
            x = by_m[m]
            lines.append(f"| {m} | ${x['skim']:.0f} | {x['g']} | {x['pause']} | {x['nop']} |")
        lines.append("")

        top_live = sorted(
            [e for e in live["events"] if e["direction"] == "to_spot"],
            key=lambda e: e["amount"],
            reverse=True,
        )[:5]
        if top_live:
            lines += [
                "### Live — top skim ngày",
                "",
                "| Day | day_pnl | skim | DD% |",
                "| --- | ---: | ---: | ---: |",
            ]
            for e in top_live:
                lines.append(
                    f"| {e['day']} | {e['day_pnl']:+.1f} | ${e['amount']:.1f} | {e['dd_pct']*100:.1f}% |"
                )
            lines.append("")

    lines += [
        "## Đọc kết quả",
        "",
        "- **Total wealth** = futures + spot — so công bằng khi có rút.",
        "- **live** rút chậm (cap 1.5% SOD) → giữ nhiều vốn futures → total gần **none** hơn **twoway**.",
        "- **twoway** rút mạnh + nạp ngày đỏ → total thấp hơn nhưng MaxDD total thường êm hơn.",
        "- `#dd_pause`: số ngày xanh nhưng live **không rút** vì đang DD≥20% từ peak.",
        "",
        "Paper only — không đổi bot live.",
        "",
    ]
    path = DOCS / "backtest_channel_vol_A_spot_twoway.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
