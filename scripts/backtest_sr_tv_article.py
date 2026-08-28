#!/usr/bin/env python3
"""Backtest S/R methods inspired by BIGTAKER TV article (Volume, FVG, MA, POC, Fib).

Article: https://vn.tradingview.com/chart/BTCUSD/mk1XPPGQ/

Methods tested (15m, 20 majors, 30d default):
  - ma50_bounce / ma200_bounce: EMA as dynamic S/R (article §3 MA)
  - poc_bounce: VWAP(96) as POC proxy, bounce with volume (article §3 POC)
  - fvg_fill: bullish/bearish FVG retest (article §3 FVG)
  - fib_bounce: 0.5 / 0.618 retracement from swing range (article §3 Fib)
  - confluence: swing + MA + POC align within tol (article §5 confluence)
  - vol_breakout: S/R break with volume >= 1.3 (article §5 volume breakout)

Fill rules match backtest_sr_rr_30d.py (entry@close, exact SL/TP, RR>=1).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "bt_klines_15m"
THREE_Y_START_MS = 1692828900000
BAR_MS = 15 * 60 * 1000

# Reuse engine constants
_bt = importlib.util.spec_from_file_location("bt_sr", ROOT / "scripts/backtest_sr_rr_30d.py")
bt = importlib.util.module_from_spec(_bt)
assert _bt.loader is not None
sys.modules["bt_sr"] = bt
_bt.loader.exec_module(bt)

CAPITAL = bt.CAPITAL
LEVERAGE = bt.LEVERAGE
RISK_PCT = bt.RISK_PCT
MAX_OPEN = bt.MAX_OPEN
WARMUP_BARS = bt.WARMUP_BARS
SYMBOLS_20 = bt.SYMBOLS_20
pnl = bt.pnl

SWING_WINDOW = 96
POC_WINDOW = 96  # 24h VWAP as POC proxy
EMA_FAST = 50
EMA_SLOW = 200
FVG_MAX_AGE = 48  # bars to keep unfilled FVG
FIB_LEVELS = (0.5, 0.618)


def enrich_tv(df: pd.DataFrame) -> pd.DataFrame:
    out = bt.enrich(df, swing_window=SWING_WINDOW)
    typical = (out["high"] + out["low"] + out["close"]) / 3.0
    vol = out["quote_volume"]
    out["poc"] = (typical * vol).rolling(POC_WINDOW, min_periods=POC_WINDOW).sum() / vol.rolling(
        POC_WINDOW, min_periods=POC_WINDOW
    ).sum()
    out["ema50"] = out["close"].ewm(span=EMA_FAST, adjust=False).mean()
    out["ema200"] = out["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    out["fib50"] = out["swing_high"] - 0.5 * (out["swing_high"] - out["swing_low"])
    out["fib618"] = out["swing_high"] - 0.618 * (out["swing_high"] - out["swing_low"])
    # FVG detection (3-candle)
    h2 = out["high"].shift(2)
    l2 = out["low"].shift(2)
    out["bull_fvg_top"] = out["low"]  # gap top = current low
    out["bull_fvg_bot"] = h2  # gap bottom = high[i-2]
    out["bull_fvg"] = (out["low"] > h2) & (out["close"] > out["open"])
    out["bear_fvg_top"] = l2
    out["bear_fvg_bot"] = out["high"]
    out["bear_fvg"] = (out["high"] < l2) & (out["close"] < out["open"])
    out["dist_poc"] = (out["close"] - out["poc"]).abs()
    out["dist_ema50"] = (out["close"] - out["ema50"]).abs()
    out["dist_ema200"] = (out["close"] - out["ema200"]).abs()
    return out


def near_level(px: float, lo: float, hi: float, level: float, tol_atr: float, atr: float) -> bool:
    if atr <= 0 or np.isnan(atr) or np.isnan(level):
        return False
    band = tol_atr * atr
    return lo <= level + band and hi >= level - band and abs(px - level) <= band * 1.5


def plan_from_level(
    side: str, entry: float, level: float, atr: float, min_rr: float, sl_atr: float
) -> tuple[float, float, float, float] | None:
    if atr <= 0 or np.isnan(atr):
        return None
    if side == "long":
        sl = level - sl_atr * atr
        risk = entry - sl
        if risk <= 0:
            return None
        tp = entry + min_rr * risk
        rr = (tp - entry) / risk
    else:
        sl = level + sl_atr * atr
        risk = sl - entry
        if risk <= 0:
            return None
        tp = entry - min_rr * risk
        rr = (entry - tp) / risk
    if rr < min_rr - 1e-9:
        return None
    return entry, sl, tp, risk


@dataclass
class Cfg:
    name: str
    method: str
    min_vol: float = 1.2
    tol: float = 0.35
    sl_atr: float = 0.5
    min_rr: float = 1.0
    breakout_vol: float = 1.3


def load_pool(days: int) -> tuple[dict[str, pd.DataFrame], int, int]:
    dfs: dict[str, pd.DataFrame] = {}
    for sym in SYMBOLS_20:
        files = sorted(
            CACHE_DIR.glob(f"{sym}_15m_{THREE_Y_START_MS}_*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            continue
        raw = pd.read_csv(files[0])
        if "quote_volume" not in raw.columns:
            continue
        df = enrich_tv(raw)
        last = int(df["ts"].max())
        wf = last - (days + 10) * 86400 * 1000
        df = df[df["ts"] >= wf - WARMUP_BARS * BAR_MS].copy().reset_index(drop=True)
        if len(df) < WARMUP_BARS + 100:
            continue
        dfs[sym] = df
    if len(dfs) < 10:
        raise RuntimeError("too few symbols")
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    eval_start = common[-1] - days * 86400 * 1000
    return dfs, eval_start, common[-1]


def _count_confluence(b, tol: float) -> tuple[int, list[str]]:
    """How many independent S/R sources align near price."""
    a = float(b["atr"])
    px, lo, hi = float(b["close"]), float(b["low"]), float(b["high"])
    hits: list[str] = []
    for name, lvl in [
        ("swing", float(b["support"]) if px >= float(b["poc"]) else float(b["resistance"])),
        ("poc", float(b["poc"])),
        ("ema50", float(b["ema50"])),
        ("ema200", float(b["ema200"])),
        ("fib50", float(b["fib50"])),
        ("fib618", float(b["fib618"])),
    ]:
        if near_level(px, lo, hi, lvl, tol, a):
            hits.append(name)
    return len(hits), hits


def signals_for_bar(sym: str, i: int, b, prev, cfg: Cfg, fvg_state: dict) -> list[dict]:
    if np.isnan(b["atr"]) or np.isnan(b.get("vol_ratio", np.nan)):
        return []
    if float(b["vol_ratio"]) < cfg.min_vol:
        return []

    px, lo, hi = float(b["close"]), float(b["low"]), float(b["high"])
    o = float(b["open"])
    a = float(b["atr"])
    is_green, is_red = px > o, px < o
    out: list[dict] = []

    def add(side: str, level: float, tag: str) -> None:
        plan = plan_from_level(side, px, level, a, cfg.min_rr, cfg.sl_atr)
        if not plan:
            return
        e, sl, tp, risk = plan
        rr = (tp - e) / risk if side == "long" else (e - tp) / risk
        out.append({"sym": sym, "side": side, "entry": e, "sl": sl, "tp": tp, "rr": rr, "risk": risk, "tag": tag})

    m = cfg.method

    if m == "ma50_bounce":
        if px > float(b["ema50"]) and near_level(px, lo, hi, float(b["ema50"]), cfg.tol, a) and is_green:
            add("long", float(b["ema50"]), "ma50")
        elif px < float(b["ema50"]) and near_level(px, lo, hi, float(b["ema50"]), cfg.tol, a) and is_red:
            add("short", float(b["ema50"]), "ma50")

    elif m == "ma200_bounce":
        if px > float(b["ema200"]) and near_level(px, lo, hi, float(b["ema200"]), cfg.tol, a) and is_green:
            add("long", float(b["ema200"]), "ma200")
        elif px < float(b["ema200"]) and near_level(px, lo, hi, float(b["ema200"]), cfg.tol, a) and is_red:
            add("short", float(b["ema200"]), "ma200")

    elif m == "poc_bounce":
        poc = float(b["poc"])
        if np.isnan(poc):
            return out
        if px > poc and near_level(px, lo, hi, poc, cfg.tol, a) and is_green:
            add("long", poc, "poc")
        elif px < poc and near_level(px, lo, hi, poc, cfg.tol, a) and is_red:
            add("short", poc, "poc")

    elif m == "fvg_fill":
        st = fvg_state.setdefault(sym, {"bull": [], "bear": []})
        if bool(b.get("bull_fvg", False)):
            top, bot = float(b["bull_fvg_top"]), float(b["bull_fvg_bot"])
            if top > bot:
                st["bull"].append((top, bot, i))
        if bool(b.get("bear_fvg", False)):
            top, bot = float(b["bear_fvg_top"]), float(b["bear_fvg_bot"])
            if bot > top:
                st["bear"].append((top, bot, i))
        st["bull"] = [(t, bot, j) for t, bot, j in st["bull"] if i - j <= FVG_MAX_AGE]
        st["bear"] = [(t, bot, j) for t, bot, j in st["bear"] if i - j <= FVG_MAX_AGE]
        for top, bot, _ in st["bull"]:
            if lo <= top and hi >= bot and is_green and px > (bot + top) / 2:
                add("long", bot, "fvg_bull")
                break
        for top, bot, _ in st["bear"]:
            if hi >= top and lo <= bot and is_red and px < (bot + top) / 2:
                add("short", bot, "fvg_bear")
                break

    elif m == "fib_bounce":
        for lvl, tag in [(float(b["fib50"]), "fib50"), (float(b["fib618"]), "fib618")]:
            if np.isnan(lvl):
                continue
            if near_level(px, lo, hi, lvl, cfg.tol, a) and is_green and px > float(b["ema200"]):
                add("long", lvl, tag)
            elif near_level(px, lo, hi, lvl, cfg.tol, a) and is_red and px < float(b["ema200"]):
                add("short", lvl, tag)

    elif m == "confluence":
        n, hits = _count_confluence(b, cfg.tol)
        if n >= 3:
            if is_green and px > float(b["poc"]):
                lvl = float(b["support"])
                add("long", lvl, "+".join(hits[:3]))
            elif is_red and px < float(b["poc"]):
                lvl = float(b["resistance"])
                add("short", lvl, "+".join(hits[:3]))

    elif m == "vol_breakout":
        if prev is None:
            return out
        if float(b["vol_ratio"]) < cfg.breakout_vol:
            return out
        prev_c = float(prev["close"])
        res, sup = float(prev["resistance"]), float(prev["support"])
        if px > res and prev_c <= res and is_green:
            plan = plan_from_level("long", px, res, a, cfg.min_rr, cfg.sl_atr)
            if plan:
                e, sl, tp, risk = plan
                out.append(
                    {
                        "sym": sym,
                        "side": "long",
                        "entry": e,
                        "sl": sl,
                        "tp": tp,
                        "rr": (tp - e) / risk,
                        "risk": risk,
                        "tag": "brk_up",
                    }
                )
        elif px < sup and prev_c >= sup and is_red:
            plan = plan_from_level("short", px, sup, a, cfg.min_rr, cfg.sl_atr)
            if plan:
                e, sl, tp, risk = plan
                out.append(
                    {
                        "sym": sym,
                        "side": "short",
                        "entry": e,
                        "sl": sl,
                        "tp": tp,
                        "rr": (e - tp) / risk,
                        "risk": risk,
                        "tag": "brk_dn",
                    }
                )

    return out


def run(cfg: Cfg, dfs: dict[str, pd.DataFrame], eval_start: int) -> dict:
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed)
    days = max((common[-1] - eval_start) / 86400000.0, 1e-9)
    fvg_state: dict = {}

    cash = CAPITAL
    opens: list[dict] = []
    trades: list[dict] = []
    peak = CAPITAL
    maxdd = 0.0

    def stack(sym: str) -> int:
        return sum(1 for t in opens if t["sym"] == sym)

    def equity(mark: dict[str, float]) -> float:
        eq = cash
        for t in opens:
            eq += t["margin"] + pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def close_trade(t: dict, px: float, reason: str, ts: int) -> None:
        nonlocal cash
        pl = pnl(t["side"], t["entry"], px, t["qty"])
        cash += t["margin"] + pl
        if ts >= eval_start:
            trades.append({"pnl": pl, "reason": reason})

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}

        still = []
        for t in opens:
            if ts <= t["entry_ts"]:
                still.append(t)
                continue
            b = bar[t["sym"]]
            hi, lo = float(b["high"]), float(b["low"])
            side, sl, tp = t["side"], t["sl"], t["tp"]
            hit_sl = (side == "long" and lo <= sl) or (side == "short" and hi >= sl)
            hit_tp = (side == "long" and hi >= tp) or (side == "short" and lo <= tp)
            if hit_sl and hit_tp:
                close_trade(t, sl, "SL", ts)
            elif hit_sl:
                close_trade(t, sl, "SL", ts)
            elif hit_tp:
                close_trade(t, tp, "TP", ts)
            else:
                still.append(t)
        opens = still

        if ts < eval_start:
            continue

        cands: list[dict] = []
        for sym in symbols:
            if stack(sym) > 0:
                continue
            prev = indexed[sym].iloc[i - 1] if i > 0 else None
            cands.extend(signals_for_bar(sym, i, bar[sym], prev, cfg, fvg_state))

        cands.sort(key=lambda x: x["rr"], reverse=True)
        for cand in cands:
            if len(opens) >= MAX_OPEN:
                break
            eq = max(cash + sum(t["margin"] for t in opens), 1.0)
            risk_usd = eq * RISK_PCT
            qty = risk_usd / cand["risk"]
            margin = qty * cand["entry"] / LEVERAGE
            if margin > eq * 0.15:
                margin = eq * 0.15
                qty = margin * LEVERAGE / cand["entry"]
            if margin < 1.0 or cash < margin:
                continue
            cash -= margin
            opens.append(
                {
                    "sym": cand["sym"],
                    "side": cand["side"],
                    "entry": cand["entry"],
                    "sl": cand["sl"],
                    "tp": cand["tp"],
                    "qty": qty,
                    "margin": margin,
                    "entry_ts": ts,
                    "plan_rr": cand["rr"],
                    "risk_usd": qty * cand["risk"],
                }
            )

        eq = equity(mark)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in trades)
    return {
        "name": cfg.name,
        "days": days,
        "n": len(trades),
        "wr": len(wins) / len(trades) * 100 if trades else 0,
        "pf": gw / gl if gl > 0 else float("inf"),
        "ret_pct": net / CAPITAL * 100,
        "pct_day": net / CAPITAL * 100 / days,
        "maxdd": maxdd * 100,
        "tpd": len(trades) / days,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    cfgs = [
        Cfg("MA50 bounce vol1.2", method="ma50_bounce"),
        Cfg("MA200 bounce vol1.2", method="ma200_bounce"),
        Cfg("POC/VWAP bounce vol1.2", method="poc_bounce"),
        Cfg("FVG fill vol1.2", method="fvg_fill"),
        Cfg("Fib 50/618 + trend vol1.2", method="fib_bounce"),
        Cfg("Confluence ≥3 vol1.2", method="confluence"),
        Cfg("Vol breakout S/R vol1.3", method="vol_breakout", min_vol=1.0, breakout_vol=1.3),
        Cfg("MA50 bounce vol1.5", method="ma50_bounce", min_vol=1.5),
        Cfg("Confluence ≥3 vol1.5", method="confluence", min_vol=1.5),
    ]

    print(f"Load pool ({args.days}d)...", flush=True)
    dfs, eval_start, eval_end = load_pool(args.days)
    print(f"Eval: {pd.to_datetime(eval_start, unit='ms', utc=True)} → {pd.to_datetime(eval_end, unit='ms', utc=True)}", flush=True)
    print(f"Capital=${CAPITAL:.0f} risk=1%@SL RR>=1 swing={SWING_WINDOW} POC=VWAP({POC_WINDOW})\n", flush=True)

    rows = []
    for c in cfgs:
        print(f"run {c.name}...", flush=True)
        r = run(c, dfs, eval_start)
        rows.append(r)
        print(
            f"  ret={r['ret_pct']:+.2f}% ({r['pct_day']:+.3f}%/d) WR={r['wr']:.1f}% PF={r['pf']:.2f} "
            f"MaxDD={r['maxdd']:.1f}% n={r['n']} t/d={r['tpd']:.1f}",
            flush=True,
        )

    best = max(rows, key=lambda x: x["ret_pct"])
    print(f"\n=== Best: {best['name']} → {best['ret_pct']:+.2f}% / {best['days']:.0f}d ===", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
