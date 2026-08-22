#!/usr/bin/env python3
"""D20 shared BT: daily skim withdraw (green day + DD gate).

Rule (end of local day +07):
  if day_pnl <= 0 or DD_from_peak >= 20%: withdraw = 0
  else: withdraw = min(cash_free, day_pnl * 0.4, equity_sod * 0.015)

Cache-only. Does not modify live bot or other BT scripts.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "bt_klines_15m"
DOCS = ROOT / "docs"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")

SYMBOLS_20 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "TRXUSDT", "ADAUSDT",
    "AVAXUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT", "BCHUSDT", "XLMUSDT", "ATOMUSDT",
    "NEARUSDT", "APTUSDT", "SUIUSDT", "ARBUSDT", "OPUSDT", "UNIUSDT",
]
BAR_MS = 15 * 60 * 1000
LOOKBACK_DAYS = 365
CAPITAL = 1000.0
MIN_BARS = 8000

SKIM = 0.40
CAP_PCT = 0.015
DD_PAUSE = 0.20


def _load_hunt():
    path = ROOT / "scripts" / "backtest_hunt_pct_per_day.py"
    spec = importlib.util.spec_from_file_location("hunt_pct_skim", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_pct_skim"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_from_cache(hunt, symbols: list[str]) -> dict[str, pd.DataFrame]:
    dfs: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        files = sorted(CACHE_DIR.glob(f"{sym}_15m_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            print(f"  missing {sym}", flush=True)
            continue
        raw = pd.read_csv(files[0])
        df = hunt.prepare(raw)
        last = (int(df["ts"].max()) // BAR_MS) * BAR_MS
        wf = last - LOOKBACK_DAYS * 86400 * 1000
        df = df[df["ts"] >= wf].copy().reset_index(drop=True)
        if len(df) < MIN_BARS:
            continue
        dfs[sym] = df
        print(f"  {sym} bars={len(df)}", flush=True)
    return dfs


def run(hunt, dfs: dict[str, pd.DataFrame]) -> dict:
    ts_sets = [set(df["ts"].tolist()) for df in dfs.values()]
    common = sorted(set.intersection(*ts_sets)) if ts_sets else []
    if len(common) < 500:
        return {"error": "no common ts"}

    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed.keys())
    cash = CAPITAL
    spot = 0.0
    opens: list[dict] = []
    trades: list[dict] = []
    state = {sym: {"trend": None, "waiting": False} for sym in symbols}

    peak_bot = CAPITAL
    maxdd_bot = 0.0
    peak_total = CAPITAL
    maxdd_total = 0.0

    # day boundary in +07
    def local_day_key(ts: int) -> str:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")

    def month_key(day: str) -> str:
        return day[:7]

    last_day: str | None = None
    sod_equity: float | None = None  # equity at start of current day (first bar after rollover)
    day_wd_events: list[dict] = []

    def equity_bot(mark: dict[str, float]) -> float:
        eq = cash
        for t in opens:
            eq += t["margin"] + hunt._pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def locked() -> float:
        return sum(t["margin"] for t in opens)

    def stack(sym: str) -> int:
        return sum(1 for t in opens if t["sym"] == sym)

    def fmt_ts(ts: int) -> str:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d %H:%M %Z")

    def try_withdraw(mark: dict[str, float], day: str, ts: int) -> None:
        nonlocal cash, spot, sod_equity, peak_bot
        if sod_equity is None:
            return
        eq = max(equity_bot(mark), 0.0)
        day_pnl = eq - sod_equity
        dd = (peak_bot - eq) / peak_bot if peak_bot > 0 else 0.0
        reason = "ok"
        take = 0.0
        if day_pnl <= 0:
            reason = "no_profit"
        elif dd >= DD_PAUSE:
            reason = "dd_pause"
        else:
            take = min(max(cash, 0.0), day_pnl * SKIM, sod_equity * CAP_PCT)
            if take <= 1e-9:
                reason = "no_cash"
                take = 0.0
            else:
                cash -= take
                spot += take
        day_wd_events.append(
            {
                "day": day,
                "month": month_key(day),
                "ts": ts,
                "sod": sod_equity,
                "eod": eq,
                "day_pnl": day_pnl,
                "dd": dd,
                "take": take,
                "reason": reason,
                "bot_after": equity_bot(mark) if take else eq,
                "spot_after": spot,
                "total_after": (equity_bot(mark) if take else eq) + spot,
            }
        )

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}
        day = local_day_key(ts)

        # --- exits ---
        still: list[dict] = []
        for t in opens:
            sym = t["sym"]
            b = bar[sym]
            if np.isnan(b["dc_upper"]) or np.isnan(b["dc_lower"]):
                still.append(t)
                continue
            hi, lo = float(b["high"]), float(b["low"])
            up, dn = float(b["dc_upper"]), float(b["dc_lower"])
            side = t["side"]
            tp = up if side == "long" else dn
            if (side == "long" and hi >= tp) or (side == "short" and lo <= tp):
                pnl = hunt._pnl(side, t["entry"], tp, t["qty"])
                cash += t["margin"] + pnl
                trades.append({"pnl": pnl})
            else:
                still.append(t)
        opens = still

        # --- day rollover: settle previous day withdraw, then set new SOD ---
        if last_day is not None and day != last_day:
            try_withdraw(mark, last_day, ts)
            sod_equity = equity_bot(mark)
        elif sod_equity is None:
            sod_equity = equity_bot(mark)
        last_day = day

        # --- entries ---
        candidates: list[dict] = []
        for sym in symbols:
            b = bar[sym]
            if np.isnan(b["dc_upper"]) or np.isnan(b["dc_lower"]) or np.isnan(b["atr"]):
                continue
            px, o = float(b["close"]), float(b["open"])
            up, dn, mid = float(b["dc_upper"]), float(b["dc_lower"]), float(b["dc_middle"])
            w = float(b["dc_width"])
            a = float(b["atr"])
            pe = bool(b["parallel_exit"])
            par = bool(b["bands_parallel"])
            st = state[sym]
            if pe:
                st["trend"] = "up" if px > mid else "down"
                st["waiting"] = True
            if st["waiting"] and st["trend"] and stack(sym) == 0:
                counter = (st["trend"] == "up" and px < o) or (st["trend"] == "down" and px > o)
                if counter and not par and w > 1e-12:
                    side = "long" if st["trend"] == "up" else "short"
                    tp_near = up if side == "long" else dn
                    sl_opp = dn if side == "long" else up
                    pot = abs(tp_near - px) / max(abs(px - sl_opp), 1e-12)
                    body = abs(px - o) / a if a > 0 else 0.0
                    if 0.3 <= body <= 1.2 and pot >= 0.5:
                        candidates.append(
                            {"sym": sym, "side": side, "px": px, "pot": pot, "sl0": sl_opp}
                        )
        candidates.sort(key=lambda x: x["pot"], reverse=True)

        for cand in candidates:
            if len(opens) >= 20:
                break
            if stack(cand["sym"]) > 0:
                continue
            sm = float(np.clip(0.5 + cand["pot"], 0.5, 2.0))
            eq = max(cash + locked(), 0.0)
            notional = min(eq * 0.01 * hunt.LEVERAGE * sm, cash * hunt.LEVERAGE)
            if notional < 1e-6:
                continue
            margin = notional / hunt.LEVERAGE
            if cash < margin - 1e-12:
                continue
            cash -= margin
            opens.append(
                {
                    "sym": cand["sym"],
                    "side": cand["side"],
                    "entry": cand["px"],
                    "qty": notional / cand["px"],
                    "margin": margin,
                    "sl0": cand["sl0"],
                }
            )
            state[cand["sym"]]["waiting"] = False

        for sym in symbols:
            if stack(sym) > 0:
                state[sym]["waiting"] = False

        bot_eq = equity_bot(mark)
        total_eq = bot_eq + spot
        if bot_eq > peak_bot:
            peak_bot = bot_eq
        maxdd_bot = max(maxdd_bot, (peak_bot - bot_eq) / peak_bot if peak_bot > 0 else 0.0)
        if total_eq > peak_total:
            peak_total = total_eq
        maxdd_total = max(
            maxdd_total, (peak_total - total_eq) / peak_total if peak_total > 0 else 0.0
        )

    # settle last day
    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    if last_day is not None:
        try_withdraw(last_mark, last_day, common[-1])

    for t in list(opens):
        pnl = hunt._pnl(t["side"], t["entry"], last_mark[t["sym"]], t["qty"])
        cash += t["margin"] + pnl
        trades.append({"pnl": pnl})
    opens = []

    # monthly aggregates
    by_month: dict[str, list[dict]] = defaultdict(list)
    for e in day_wd_events:
        by_month[e["month"]].append(e)

    month_rows = []
    for m in sorted(by_month.keys()):
        evs = by_month[m]
        takes = [e["take"] for e in evs]
        wd_days = sum(1 for t in takes if t > 1e-9)
        wd_sum = sum(takes)
        # % of assets: use total equity at month start (first day's SOD) and at month end
        first = evs[0]
        last = evs[-1]
        start_total = first["sod"] + (first["spot_after"] - first["take"])  # spot before that day's wd
        # reconstruct spot before first day of month: spot_after - take on that day, but prior spot
        # Better: start_total ≈ first.sod + spot_before; spot_before = first.spot_after - first.take
        spot_before = first["spot_after"] - first["take"]
        start_total = first["sod"] + spot_before
        end_total = last["total_after"]
        # monthly withdraw as % of start-of-month total and of end-of-month total
        pct_vs_start = (wd_sum / start_total * 100) if start_total > 0 else 0.0
        pct_vs_end = (wd_sum / end_total * 100) if end_total > 0 else 0.0
        # also vs average bot SOD on withdraw days / all days
        avg_sod = float(np.mean([e["sod"] for e in evs]))
        pct_vs_avg_bot = (wd_sum / avg_sod * 100) if avg_sod > 0 else 0.0
        reasons = defaultdict(int)
        for e in evs:
            if e["take"] > 1e-9:
                reasons["withdraw"] += 1
            else:
                reasons[e["reason"]] += 1
        month_rows.append(
            {
                "month": m,
                "days": len(evs),
                "wd_days": wd_days,
                "wd_sum": wd_sum,
                "wd_min_day": min((t for t in takes if t > 1e-9), default=0.0),
                "wd_max_day": max(takes),
                "start_total": start_total,
                "end_total": end_total,
                "end_bot": last["bot_after"],
                "end_spot": last["spot_after"],
                "pct_vs_start": pct_vs_start,
                "pct_vs_end": pct_vs_end,
                "pct_vs_avg_bot": pct_vs_avg_bot,
                "reasons": dict(reasons),
            }
        )

    wd_days_list = [r["wd_days"] for r in month_rows]
    pct_start_list = [r["pct_vs_start"] for r in month_rows]
    days = max((common[-1] - common[0]) / 86400000.0, 1e-9)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    end_bot = cash
    end_total = cash + spot
    total_net = end_total - CAPITAL

    return {
        "start": fmt_ts(common[0]),
        "end": fmt_ts(common[-1]),
        "days": days,
        "n": len(trades),
        "wr": (len(wins) / len(trades) * 100) if trades else 0.0,
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "end_bot": end_bot,
        "end_spot": spot,
        "end_total": end_total,
        "total_net": total_net,
        "pct_day_total": (total_net / CAPITAL * 100) / days,
        "maxdd_bot": maxdd_bot * 100,
        "maxdd_total": maxdd_total * 100,
        "withdraw_sum": sum(e["take"] for e in day_wd_events),
        "withdraw_days": sum(1 for e in day_wd_events if e["take"] > 1e-9),
        "calendar_days": len(day_wd_events),
        "month_rows": month_rows,
        "wd_days_min": min(wd_days_list) if wd_days_list else 0,
        "wd_days_max": max(wd_days_list) if wd_days_list else 0,
        "wd_days_avg": float(np.mean(wd_days_list)) if wd_days_list else 0.0,
        "pct_month_min": min(pct_start_list) if pct_start_list else 0.0,
        "pct_month_max": max(pct_start_list) if pct_start_list else 0.0,
        "pct_month_avg": float(np.mean(pct_start_list)) if pct_start_list else 0.0,
        "day_events": day_wd_events,
    }


def main() -> int:
    hunt = _load_hunt()
    print("Load cache 20 majors...", flush=True)
    dfs = load_from_cache(hunt, SYMBOLS_20)
    if len(dfs) < 10:
        print("not enough symbols", flush=True)
        return 1
    print("run D20 skim40 + DD20 pause...", flush=True)
    st = run(hunt, dfs)
    if "error" in st:
        print(st["error"])
        return 1

    print(
        f"end_total={st['end_total']:.1f} bot={st['end_bot']:.1f} spot={st['end_spot']:.1f} "
        f"%/day_total={st['pct_day_total']:+.3f}% maxDD_bot={st['maxdd_bot']:.1f}% "
        f"maxDD_tot={st['maxdd_total']:.1f}% wd_sum={st['withdraw_sum']:.1f} "
        f"wd_days={st['withdraw_days']}/{st['calendar_days']}",
        flush=True,
    )
    print(
        f"per-month wd_days: min={st['wd_days_min']} max={st['wd_days_max']} avg={st['wd_days_avg']:.1f}",
        flush=True,
    )
    print(
        f"per-month wd % of start-total: min={st['pct_month_min']:.2f}% "
        f"max={st['pct_month_max']:.2f}% avg={st['pct_month_avg']:.2f}%",
        flush=True,
    )

    lines = [
        "# D20 — daily skim 40% + DD≥20% pause withdraw",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_wd_skim40.py` (cache-only)",
        "- Pool/rule: D20_like_bot (1%, max20, body/pot filters), 15m ~365d, capital 1000$",
        "- Chốt ngày: **Asia/Ho_Chi_Minh**",
        "- Rule rút cuối ngày:",
        "  - nếu `day_pnl <= 0` hoặc `DD_from_peak >= 20%` → rút 0",
        "  - else `rút = min(cash_free, day_pnl × 0.4, equity_đầu_ngày × 0.015)`",
        "- `% tháng` = tổng rút trong tháng / **total (bot+spot) đầu tháng**",
        "",
        "## Tong hop",
        "",
        f"| Metric | Value |",
        f"| --- | --- |",
        f"| %/ngày total | **{st['pct_day_total']:+.3f}%** |",
        f"| End bot / spot / total | {st['end_bot']:.1f} / {st['end_spot']:.1f} / **{st['end_total']:.1f}** |",
        f"| MaxDD bot / total | {st['maxdd_bot']:.1f}% / {st['maxdd_total']:.1f}% |",
        f"| Tổng rút / số ngày rút | {st['withdraw_sum']:.1f} / {st['withdraw_days']}/{st['calendar_days']} |",
        f"| Ngày rút / tháng (min–max–avg) | **{st['wd_days_min']} – {st['wd_days_max']} – {st['wd_days_avg']:.1f}** |",
        f"| Rút % đầu tháng (min–max–avg) | **{st['pct_month_min']:.2f}% – {st['pct_month_max']:.2f}% – {st['pct_month_avg']:.2f}%** |",
        "",
        "## Theo tháng",
        "",
        "| Tháng | Ngày lịch | Ngày rút | Rút $ | % vs đầu tháng | % vs cuối tháng | End total | Ghi chú skip |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in st["month_rows"]:
        skip = {k: v for k, v in r["reasons"].items() if k != "withdraw"}
        skip_s = ", ".join(f"{k}:{v}" for k, v in sorted(skip.items())) or "—"
        lines.append(
            f"| {r['month']} | {r['days']} | **{r['wd_days']}** | {r['wd_sum']:.1f} | "
            f"**{r['pct_vs_start']:.2f}%** | {r['pct_vs_end']:.2f}% | {r['end_total']:.0f} | {skip_s} |"
        )

    # highlight min/max months
    by_days = sorted(st["month_rows"], key=lambda r: r["wd_days"])
    by_pct = sorted(st["month_rows"], key=lambda r: r["pct_vs_start"])
    lines += [
        "",
        "## Min / max",
        "",
        f"- Ít ngày rút nhất: **{by_days[0]['month']}** → {by_days[0]['wd_days']} ngày, "
        f"rút {by_days[0]['wd_sum']:.1f}$ (**{by_days[0]['pct_vs_start']:.2f}%** đầu tháng)",
        f"- Nhiều ngày rút nhất: **{by_days[-1]['month']}** → {by_days[-1]['wd_days']} ngày, "
        f"rút {by_days[-1]['wd_sum']:.1f}$ (**{by_days[-1]['pct_vs_start']:.2f}%** đầu tháng)",
        f"- % tháng thấp nhất: **{by_pct[0]['month']}** → {by_pct[0]['pct_vs_start']:.2f}% "
        f"({by_pct[0]['wd_sum']:.1f}$, {by_pct[0]['wd_days']} ngày)",
        f"- % tháng cao nhất: **{by_pct[-1]['month']}** → {by_pct[-1]['pct_vs_start']:.2f}% "
        f"({by_pct[-1]['wd_sum']:.1f}$, {by_pct[-1]['wd_days']} ngày)",
        "",
        "Paper only — không ảnh hưởng bot live.",
        "",
    ]
    out = DOCS / "backtest_MULTI_donchian_20major_wd_skim40_dd20_15m_365d.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
