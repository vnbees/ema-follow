#!/usr/bin/env python3
"""breadth_flip + daily skim → spot — matches live stack (365d paper).

Cache-only. Does not modify live bot.
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
    spec = importlib.util.spec_from_file_location("hunt_bflip_skim", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_bflip_skim"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_bflip():
    path = ROOT / "scripts" / "backtest_donchian_20coin_breadth_flip.py"
    spec = importlib.util.spec_from_file_location("bflip_skim", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["bflip_skim"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_from_cache(hunt, symbols: list[str]) -> dict[str, pd.DataFrame]:
    dfs: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        files = sorted(
            list(CACHE_DIR.glob(f"{sym}_15m_1692828900000_*.csv"))
            or list(CACHE_DIR.glob(f"{sym}_15m_*.csv")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
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


def run(hunt, bflip, dfs: dict[str, pd.DataFrame]) -> dict:
    ts_sets = [set(df["ts"].tolist()) for df in dfs.values()]
    common = sorted(set.intersection(*ts_sets)) if ts_sets else []
    if len(common) < 500:
        return {"error": "no common ts"}

    cfg = bflip.Cfg("breadth_flip", "flip side (live)", "flip")
    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed.keys())
    cash = CAPITAL
    spot = 0.0
    opens: list[dict] = []
    trades: list[dict] = []
    state = {sym: {"trend": None, "waiting": False} for sym in symbols}
    peak_bot = CAPITAL
    peak_total = CAPITAL
    maxdd_bot = 0.0
    maxdd_total = 0.0

    def local_day_key(ts: int) -> str:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")

    def month_key(day: str) -> str:
        return day[:7]

    last_day: str | None = None
    sod_equity: float | None = None
    day_wd_events: list[dict] = []
    month_start: dict[str, dict] = {}

    def equity_bot(mark: dict[str, float]) -> float:
        eq = cash
        for t in opens:
            eq += t["margin"] + hunt._pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def locked() -> float:
        return sum(t["margin"] for t in opens)

    def stack(sym: str) -> int:
        return sum(1 for t in opens if t["sym"] == sym)

    def try_withdraw(mark: dict[str, float], day: str) -> None:
        nonlocal cash, spot
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
                "sod": sod_equity,
                "eod": eq,
                "day_pnl": day_pnl,
                "dd": dd,
                "take": take,
                "reason": reason,
                "bot_after": equity_bot(mark),
                "spot_after": spot,
                "total_after": equity_bot(mark) + spot,
            }
        )

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}
        day = local_day_key(ts)
        mk = month_key(day)
        if mk not in month_start:
            be = equity_bot(mark)
            month_start[mk] = {"bot": be, "spot": spot, "total": be + spot, "date": day}

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

        if last_day is not None and day != last_day:
            try_withdraw(mark, last_day)
            sod_equity = equity_bot(mark)
        elif sod_equity is None:
            sod_equity = equity_bot(mark)
        last_day = day

        bar_trends: dict[str, str | None] = {}
        raw_cands: list[dict] = []
        for sym in symbols:
            b = bar[sym]
            if np.isnan(b["dc_middle"]):
                bar_trends[sym] = None
            else:
                bar_trends[sym] = "up" if float(b["close"]) > float(b["dc_middle"]) else "down"
            if np.isnan(b["dc_upper"]) or np.isnan(b["dc_lower"]) or np.isnan(b["atr"]):
                continue
            px, o = float(b["close"]), float(b["open"])
            up, dn, mid = float(b["dc_upper"]), float(b["dc_lower"]), float(b["dc_middle"])
            w, a = float(b["dc_width"]), float(b["atr"])
            pe, par = bool(b["parallel_exit"]), bool(b["bands_parallel"])
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
                        raw_cands.append(
                            {"sym": sym, "side": side, "px": px, "pot": pot, "body": body, "up": up, "dn": dn}
                        )
        raw_cands.sort(key=lambda x: x["pot"], reverse=True)

        vote = bflip.breadth_vote(bar_trends, cfg.ratio, cfg.min_n)
        entries: list[dict] = []
        for cand in raw_cands:
            if vote is None or cand["side"] == vote:
                entries.append(cand)
                continue
            fc = bflip.flipped_candidate(cand, cand["up"], cand["dn"], cand["px"])
            if fc:
                entries.append(fc)
        entries.sort(key=lambda x: x["pot"], reverse=True)

        for cand in entries:
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
        maxdd_total = max(maxdd_total, (peak_total - total_eq) / peak_total if peak_total > 0 else 0.0)

    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    if last_day is not None:
        try_withdraw(last_mark, last_day)
    for t in list(opens):
        pnl = hunt._pnl(t["side"], t["entry"], last_mark[t["sym"]], t["qty"])
        cash += t["margin"] + pnl
        trades.append({"pnl": pnl})
    opens = []

    days = max((common[-1] - common[0]) / 86400000.0, 1e-9)
    bot_end = cash
    total_end = bot_end + spot
    net = total_end - CAPITAL
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]
    pf = (sum(wins) / abs(sum(losses))) if losses else float("inf")
    wr = len(wins) / len(trades) * 100 if trades else 0.0

    by_month: dict[str, list[dict]] = defaultdict(list)
    for e in day_wd_events:
        by_month[e["month"]].append(e)

    month_rows = []
    for m in sorted(by_month.keys()):
        evs = by_month[m]
        wd_sum = sum(e["take"] for e in evs)
        wd_days = sum(1 for e in evs if e["take"] > 1e-9)
        first = evs[0]
        spot_before = first["spot_after"] - first["take"]
        start_total = first["sod"] + spot_before
        pct_vs_start = (wd_sum / start_total * 100) if start_total > 0 else 0.0
        reasons = defaultdict(int)
        for e in evs:
            if e["take"] > 1e-9:
                reasons["withdraw"] += 1
            else:
                reasons[e["reason"]] += 1
        ms = month_start.get(m, {})
        month_rows.append(
            {
                "month": m,
                "days": len(evs),
                "wd_days": wd_days,
                "wd_sum": wd_sum,
                "pct_vs_start": pct_vs_start,
                "end_total": evs[-1]["total_after"],
                "start_total": ms.get("total", start_total),
                "start_bot": ms.get("bot", first["sod"]),
                "start_spot": ms.get("spot", spot_before),
                "skip": dict(reasons),
            }
        )

    wd_days_total = sum(1 for e in day_wd_events if e["take"] > 1e-9)
    skip = defaultdict(int)
    for e in day_wd_events:
        if e["take"] <= 1e-9:
            skip[e["reason"]] += 1

    pct_starts = [r["pct_vs_start"] for r in month_rows if r["wd_sum"] > 0]
    wd_per_month = [r["wd_days"] for r in month_rows]

    return {
        "days": days,
        "pct_day_total": net / CAPITAL * 100 / days,
        "bot_end": bot_end,
        "spot_end": spot,
        "total_end": total_end,
        "maxdd_bot": maxdd_bot * 100,
        "maxdd_total": maxdd_total * 100,
        "pf": pf,
        "wr": wr,
        "n": len(trades),
        "trades_per_day": len(trades) / days,
        "wd_days": wd_days_total,
        "wd_total": spot,
        "skip": dict(skip),
        "month_rows": month_rows,
        "month_start": month_start,
        "pct_start_min": min(pct_starts) if pct_starts else 0.0,
        "pct_start_max": max(pct_starts) if pct_starts else 0.0,
        "pct_start_avg": float(np.mean(pct_starts)) if pct_starts else 0.0,
        "wd_month_min": min(wd_per_month) if wd_per_month else 0,
        "wd_month_max": max(wd_per_month) if wd_per_month else 0,
        "wd_month_avg": float(np.mean(wd_per_month)) if wd_per_month else 0.0,
    }


def main() -> int:
    hunt = _load_hunt()
    bflip = _load_bflip()
    print("Load cache 20 majors...", flush=True)
    dfs = load_from_cache(hunt, SYMBOLS_20)
    if len(dfs) < 10:
        return 1

    print("run breadth_flip + skim40 + DD20...", flush=True)
    st = run(hunt, bflip, dfs)
    if "error" in st:
        print(st, flush=True)
        return 1

    lines = [
        "# breadth_flip + skim spot — **live stack** (365d)",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_breadth_flip_wd_skim40.py` (cache-only)",
        "- Rule trade: **breadth_flip** · D20 · body ATR · pot_rr · margin 1%×size_mult · max_open 20",
        "- Rule rút (khớp live `spot_transfer.py`): cuối ngày +07",
        "  - `day_pnl ≤ 0` hoặc `DD_from_peak ≥ 20%` → rút 0",
        "  - else `rút = min(cash_free, day_pnl × 0.4, equity_SOD × 0.015)`",
        "- Pool: 20 majors · 15m · ~365d · capital **1000$** · fee 0.04%/side · 10x",
        "",
        "## Tổng hợp",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| %/ngày **total** (bot+spot) | **+{st['pct_day_total']:.3f}%** |",
        f"| End bot / spot / **total** | {st['bot_end']:.0f} / {st['spot_end']:.0f} / **{st['total_end']:.0f}** |",
        f"| MaxDD bot / **total** | {st['maxdd_bot']:.1f}% / **{st['maxdd_total']:.1f}%** |",
        f"| PF / WR / lệnh/ngày | {st['pf']:.2f} / {st['wr']:.1f}% / {st['trades_per_day']:.1f} |",
        f"| Ngày rút | **{st['wd_days']}/{int(st['days'])}** |",
        f"| Rút % đầu tháng (min–max–avg) | **{st['pct_start_min']:.2f}% – {st['pct_start_max']:.2f}% – {st['pct_start_avg']:.2f}%** |",
        f"| Ngày rút/tháng (min–max–avg) | **{st['wd_month_min']} – {st['wd_month_max']} – {st['wd_month_avg']:.1f}** |",
        f"| Skip rút | no_profit: {st['skip'].get('no_profit', 0)}, dd_pause: {st['skip'].get('dd_pause', 0)} |",
        "",
        "## Total (bot+spot) đầu tháng",
        "",
        "| Tháng | Total | Bot | Spot | vs 1000$ |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for m in sorted(st["month_start"].keys()):
        r = st["month_start"][m]
        lines.append(
            f"| {m} | **{r['total']:.0f}** | {r['bot']:.0f} | {r['spot']:.0f} | {(r['total']/CAPITAL-1)*100:+.0f}% |"
        )

    lines += [
        "",
        "## Rút theo tháng",
        "",
        "| Tháng | Ngày rút | Rút $ | % đầu tháng | End total | Skip |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in st["month_rows"]:
        skip_parts = [f"{k}:{v}" for k, v in sorted(r["skip"].items()) if k != "withdraw"]
        lines.append(
            f"| {r['month']} | {r['wd_days']} | {r['wd_sum']:.0f} | {r['pct_vs_start']:.2f}% | {r['end_total']:.0f} | {', '.join(skip_parts) or '—'} |"
        )

    lines += [
        "",
        "## Đọc nhanh",
        "",
        f"- Sau ~1 năm paper: **{st['total_end']/CAPITAL:.1f}×** vốn gốc (~{st['spot_end']/st['total_end']*100:.0f}% đã rút sang spot).",
        f"- MaxDD **total** ~{st['maxdd_total']:.0f}% — thấp hơn flip không rút (~25%) nhờ tách lãi ra spot.",
        "- `%/ngày total` ≈ 4–5% paper; live có thể thấp hơn (slippage, pool top-N động).",
        "",
        "Paper only — không ảnh hưởng bot live.",
        "",
    ]

    out = DOCS / "backtest_MULTI_donchian_20major_breadth_flip_wd_skim40_15m_365d.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    print(
        f"  total={st['total_end']:.0f}$ bot={st['bot_end']:.0f}$ spot={st['spot_end']:.0f}$ "
        f"MaxDD total={st['maxdd_total']:.1f}%",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
