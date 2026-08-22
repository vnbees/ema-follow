#!/usr/bin/env python3
"""Hunt: daily withdraw 1% + lower MaxDD_total, keep total return as high as possible.

Cache-only clone. Does not modify other BT scripts or the live bot.

Variants:
- baseline D20 / A20 + WD
- fewer slots / milder margin
- DD gate (pause entries when drawdown from peak)
- half-size in drawdown
- withdraw only when above high-water mark (still target 1%/day when allowed)
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
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


def _load_hunt():
    path = ROOT / "scripts" / "backtest_hunt_pct_per_day.py"
    spec = importlib.util.spec_from_file_location("hunt_pct_wdh", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_pct_wdh"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_from_cache(hunt, symbols: list[str]) -> dict[str, pd.DataFrame]:
    dfs: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        files = sorted(CACHE_DIR.glob(f"{sym}_15m_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            continue
        raw = pd.read_csv(files[0])
        df = hunt.prepare(raw)
        last = (int(df["ts"].max()) // BAR_MS) * BAR_MS
        wf = last - LOOKBACK_DAYS * 86400 * 1000
        df = df[df["ts"] >= wf].copy().reset_index(drop=True)
        if len(df) >= MIN_BARS:
            dfs[sym] = df
    return dfs


@dataclass(frozen=True)
class Cfg:
    name: str
    note: str
    margin_pct: float = 0.01
    max_open: int = 20
    withdraw_pct: float = 0.01
    # pause new entries when (peak_bot - eq) / peak_bot >= dd_pause
    dd_pause: float = 0.0
    # when in DD >= dd_half, multiply margin sizing by 0.5
    dd_half: float = 0.0
    # only withdraw if bot_eq >= high_water * hwm_frac (1.0 = only at/above HWM)
    wd_hwm_only: bool = False
    # withdraw at most day's equity increase (still capped at withdraw_pct*eq)
    wd_profit_cap: bool = False


def run(hunt, dfs: dict[str, pd.DataFrame], cfg: Cfg) -> dict:
    ts_sets = [set(df["ts"].tolist()) for df in dfs.values()]
    common = sorted(set.intersection(*ts_sets)) if ts_sets else []
    if len(common) < 500:
        return {"name": cfg.name, "error": "no common ts"}

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
    hwm_bot = CAPITAL
    last_day: int | None = None
    day_start_eq = CAPITAL
    wd_sum = 0.0
    wd_events = 0
    pause_bars = 0

    def equity_bot(mark: dict[str, float]) -> float:
        eq = cash
        for t in opens:
            eq += t["margin"] + hunt._pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def locked() -> float:
        return sum(t["margin"] for t in opens)

    def stack(sym: str) -> int:
        return sum(1 for t in opens if t["sym"] == sym)

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}

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

        day = int(ts // 86400000)
        if cfg.withdraw_pct > 0 and last_day is not None and day != last_day:
            eq = max(equity_bot(mark), 0.0)
            allow_wd = True
            if cfg.wd_hwm_only and eq + 1e-9 < hwm_bot:
                allow_wd = False
            if allow_wd:
                target = eq * cfg.withdraw_pct
                if cfg.wd_profit_cap:
                    target = min(target, max(0.0, eq - day_start_eq))
                take = min(target, max(cash, 0.0))
                if take > 1e-9:
                    cash -= take
                    spot += take
                    wd_sum += take
                    wd_events += 1
            day_start_eq = equity_bot(mark)
        elif last_day is None:
            day_start_eq = equity_bot(mark)
        last_day = day

        eq_now = equity_bot(mark)
        if eq_now > peak_bot:
            peak_bot = eq_now
        if eq_now > hwm_bot:
            hwm_bot = eq_now
        dd_from_peak = (peak_bot - eq_now) / peak_bot if peak_bot > 0 else 0.0
        allow_entry = True
        size_scale = 1.0
        if cfg.dd_pause > 0 and dd_from_peak >= cfg.dd_pause:
            allow_entry = False
            pause_bars += 1
        if cfg.dd_half > 0 and dd_from_peak >= cfg.dd_half:
            size_scale = 0.5

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
            if allow_entry and st["waiting"] and st["trend"] and stack(sym) == 0:
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
            if len(opens) >= cfg.max_open:
                break
            if stack(cand["sym"]) > 0:
                continue
            sm = float(np.clip(0.5 + cand["pot"], 0.5, 2.0))
            eq = max(cash + locked(), 0.0)
            notional = min(
                eq * cfg.margin_pct * hunt.LEVERAGE * sm * size_scale,
                cash * hunt.LEVERAGE,
            )
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

    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    for t in list(opens):
        pnl = hunt._pnl(t["side"], t["entry"], last_mark[t["sym"]], t["qty"])
        cash += t["margin"] + pnl
        trades.append({"pnl": pnl})
    opens = []

    days = max((common[-1] - common[0]) / 86400000.0, 1e-9)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    end_bot = cash
    end_total = cash + spot
    total_net = end_total - CAPITAL

    return {
        "name": cfg.name,
        "note": cfg.note,
        "n": len(trades),
        "wr": (len(wins) / len(trades) * 100) if trades else 0.0,
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "trades_per_day": len(trades) / days,
        "end_bot": end_bot,
        "end_spot": spot,
        "end_total": end_total,
        "total_net": total_net,
        "pct_day_total": (total_net / CAPITAL * 100) / days,
        "maxdd_bot": maxdd_bot * 100,
        "maxdd_total": maxdd_total * 100,
        "wd_sum": wd_sum,
        "wd_events": wd_events,
        "pause_bars": pause_bars,
        "score": (total_net / CAPITAL * 100) / days / max(maxdd_total * 100, 1.0),
    }


def main() -> int:
    hunt = _load_hunt()
    print("Load cache (no REST)...", flush=True)
    dfs = load_from_cache(hunt, SYMBOLS_20)
    print(f"symbols={len(dfs)}", flush=True)
    if len(dfs) < 10:
        return 1

    cfgs = [
        Cfg("D20_wd", "ref: 1% max20 + WD1%/d", 0.01, 20, 0.01),
        Cfg("A20_wd", "ref: 0.5% max20 + WD1%", 0.005, 20, 0.01),
        Cfg("M1_max12_wd", "1% max12 + WD1%", 0.01, 12, 0.01),
        Cfg("M1_max8_wd", "1% max8 + WD1%", 0.01, 8, 0.01),
        Cfg("M075_max12_wd", "0.75% max12 + WD1%", 0.0075, 12, 0.01),
        Cfg("M075_max15_wd", "0.75% max15 + WD1%", 0.0075, 15, 0.01),
        Cfg("M1_max15_dd15_wd", "1% max15 + pause DD≥15% + WD1%", 0.01, 15, 0.01, dd_pause=0.15),
        Cfg("M1_max20_dd12_wd", "1% max20 + pause DD≥12% + WD1%", 0.01, 20, 0.01, dd_pause=0.12),
        Cfg("M1_max20_half10_wd", "1% max20 + half-size DD≥10% + WD1%", 0.01, 20, 0.01, dd_half=0.10),
        Cfg("M1_max12_half10_wd", "1% max12 + half DD≥10% + WD1%", 0.01, 12, 0.01, dd_half=0.10),
        Cfg("M1_max15_dd15_half10_wd", "1% max15 pause15% half10% + WD1%", 0.01, 15, 0.01, dd_pause=0.15, dd_half=0.10),
        Cfg("M1_max20_wd_hwm", "1% max20 WD1% only at/above HWM", 0.01, 20, 0.01, wd_hwm_only=True),
        Cfg("M1_max20_wd_profit", "1% max20 WD≤min(1%eq, day profit)", 0.01, 20, 0.01, wd_profit_cap=True),
        Cfg("M075_max12_dd12_wd", "0.75% max12 pause DD≥12% + WD1%", 0.0075, 12, 0.01, dd_pause=0.12),
        Cfg("M1_max10_half10_wd", "1% max10 half DD≥10% + WD1%", 0.01, 10, 0.01, dd_half=0.10),
    ]

    rows = []
    for cfg in cfgs:
        print(f"run {cfg.name}...", flush=True)
        st = run(hunt, dfs, cfg)
        rows.append(st)
        if "error" in st:
            print(f"  ERROR {st['error']}", flush=True)
            continue
        print(
            f"  %/d_total={st['pct_day_total']:+.3f}% maxDD_tot={st['maxdd_total']:.1f}% "
            f"maxDD_bot={st['maxdd_bot']:.1f}% end_tot={st['end_total']:.0f} "
            f"score={st['score']:.3f} wd={st['wd_sum']:.0f}",
            flush=True,
        )

    ok = [r for r in rows if "error" not in r]
    # Prefer MaxDD_total <= 25, then max pct_day; also show best score
    low_dd = [r for r in ok if r["maxdd_total"] <= 25]
    best_low = max(low_dd, key=lambda r: r["pct_day_total"]) if low_dd else None
    best_score = max(ok, key=lambda r: r["score"]) if ok else None
    ref = next((r for r in ok if r["name"] == "D20_wd"), None)

    lines = [
        "# Hunt: WD 1%/ngày + hạ MaxDD_total, giữ hiệu suất total",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_wd_riskhunt.py` (clone; cache-only; khong sua bot/BT goc)",
        "- Moi config deu **withdraw 1% equity/UTC-day** (tru khi HWM/profit-cap chan bot)",
        "- Muc tieu: MaxDD **total** (bot+spot) thap; %/ngày **total** cao nhat co the (khong so voi D20 no-WD)",
        "",
        "| Rank | Config | %/ngày total | MaxDD total | MaxDD bot | End total | Spot | PF | WR | t/d | score | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    ranked = sorted(ok, key=lambda r: (-r["pct_day_total"], r["maxdd_total"]))
    for i, st in enumerate(ranked, 1):
        lines.append(
            f"| {i} | `{st['name']}` | **{st['pct_day_total']:+.3f}%** | **{st['maxdd_total']:.1f}%** | "
            f"{st['maxdd_bot']:.1f}% | {st['end_total']:.0f} | {st['end_spot']:.0f} | "
            f"{st['pf']:.2f} | {st['wr']:.1f}% | {st['trades_per_day']:.1f} | {st['score']:.3f} | {st['note']} |"
        )

    lines += ["", "## Goi y", ""]
    if ref:
        lines.append(
            f"- Ref `D20_wd`: %/ngày total **{ref['pct_day_total']:+.3f}%**, MaxDD_total **{ref['maxdd_total']:.1f}%**"
        )
    if best_low:
        lines.append(
            f"- Tot nhat trong nhom MaxDD_total≤25%: `{best_low['name']}` → "
            f"**{best_low['pct_day_total']:+.3f}%/ngày**, MaxDD_total **{best_low['maxdd_total']:.1f}%**, "
            f"end **{best_low['end_total']:.0f}** — {best_low['note']}"
        )
    if best_score:
        lines.append(
            f"- Best score (%/day / MaxDD_total): `{best_score['name']}` → "
            f"{best_score['pct_day_total']:+.3f}% / DD {best_score['maxdd_total']:.1f}% (score {best_score['score']:.3f})"
        )
    lines += [
        "",
        "Luu y: WD 1%/ngày **bat buoc** cat da compound → khong the giu +33%/ngày nhu D20 no-WD. "
        "So sanh cong bang trong nhom co WD.",
        "Paper; khong anh huong bot live.",
        "",
    ]
    out = DOCS / "backtest_MULTI_donchian_20major_wd1pct_riskhunt_15m_365d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
