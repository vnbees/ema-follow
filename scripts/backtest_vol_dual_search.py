#!/usr/bin/env python3
"""Pure OHLCV dual-direction search — price + quote_volume only (15m, 20 coin pool).

No RSI/ADX/BB/Donchian. Uses rolling extrema, candle shape, vol_ratio.
Fill: entry@close, SL-first same bar, 1% risk @ SL, shared wallet.

Usage:
  .venv/bin/python scripts/backtest_vol_dual_search.py --days 90
  .venv/bin/python scripts/backtest_vol_dual_search.py --days 365 --top 20
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "bt_klines_15m"
THREE_Y_START_MS = 1692828900000
BAR_MS = 15 * 60 * 1000

_bt = importlib.util.spec_from_file_location("bt", ROOT / "scripts/backtest_sr_rr_30d.py")
bt = importlib.util.module_from_spec(_bt)
assert _bt.loader is not None
sys.modules["bt"] = bt
_bt.loader.exec_module(bt)

CAPITAL = bt.CAPITAL
LEVERAGE = bt.LEVERAGE
MAX_OPEN = bt.MAX_OPEN
WARMUP_BARS = bt.WARMUP_BARS
SYMBOLS_20 = bt.SYMBOLS_20
pnl = bt.pnl


def enrich(df: pd.DataFrame, lookback: int) -> pd.DataFrame:
    out = bt.enrich(df, swing_window=lookback)
    o, h, l, c = out["open"], out["high"], out["low"], out["close"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    out["body_pct"] = body / rng
    out["upper_wick"] = h - pd.concat([o, c], axis=1).max(axis=1)
    out["lower_wick"] = pd.concat([o, c], axis=1).min(axis=1) - l
    out["range_pct"] = (out["swing_high"] - out["swing_low"]) / c * 100
    out["pos_in_range"] = (c - out["swing_low"]) / (out["swing_high"] - out["swing_low"]).replace(0, np.nan)
    out["roc_lb"] = c.pct_change(lookback) * 100
    out["vol_ma8"] = out["vol_ratio"].rolling(8, min_periods=4).mean()
    out["vol_declining"] = out["vol_ratio"] < out["vol_ma8"]
    out["prev_high"] = h.shift(1)
    out["prev_low"] = l.shift(1)
    out["prev_close"] = c.shift(1)
    return out


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
        df = enrich(raw, lookback=96)
        last = int(df["ts"].max())
        wf = last - (days + 12) * 86400 * 1000
        df = df[df["ts"] >= wf - WARMUP_BARS * BAR_MS].copy().reset_index(drop=True)
        if len(df) < WARMUP_BARS + 200:
            continue
        dfs[sym] = df
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    eval_start = common[-1] - days * 86400 * 1000
    return dfs, eval_start, common[-1]


@dataclass
class Cfg:
    name: str
    method: str
    lookback: int = 96
    min_vol: float = 1.2
    max_vol: float = 99.0
    tp_atr: float = 1.5
    sl_atr: float = 0.5
    min_rr: float = 1.5
    range_max: float = 99.0  # only trade if range_pct <= this (%)
    pos_lo: float = 0.0
    pos_hi: float = 1.0
    rebalance: int = 0  # bars, for basket
    extra: dict | None = None


def plan(side: str, entry: float, atr: float, cfg: Cfg, sl_anchor: float | None = None) -> tuple[float, float, float] | None:
    if atr <= 0 or np.isnan(atr):
        return None
    if side == "long":
        sl = (sl_anchor if sl_anchor is not None else entry) - cfg.sl_atr * atr
        risk = entry - sl
        if risk <= 0:
            return None
        tp = entry + cfg.tp_atr * atr
    else:
        sl = (sl_anchor if sl_anchor is not None else entry) + cfg.sl_atr * atr
        risk = sl - entry
        if risk <= 0:
            return None
        tp = entry - cfg.tp_atr * atr
    if (cfg.tp_atr / cfg.sl_atr) < cfg.min_rr - 1e-9:
        return None
    return entry, sl, tp


def add_leg(out: list, sym: str, side: str, b, cfg: Cfg, sl_anchor: float | None = None, tag: str = "") -> None:
    px, a = float(b["close"]), float(b["atr"])
    p = plan(side, px, a, cfg, sl_anchor)
    if not p:
        return
    e, sl, tp = p
    out.append({"sym": sym, "side": side, "entry": e, "sl": sl, "tp": tp, "rr": cfg.tp_atr / cfg.sl_atr, "tag": tag})


def vol_ok(b, cfg: Cfg) -> bool:
    vr = float(b["vol_ratio"])
    if np.isnan(vr):
        return False
    return cfg.min_vol <= vr <= cfg.max_vol


def range_ok(b, cfg: Cfg) -> bool:
    rp = float(b.get("range_pct", np.nan))
    if np.isnan(rp):
        return False
    return rp <= cfg.range_max


def pos_ok(b, cfg: Cfg) -> bool:
    p = float(b.get("pos_in_range", np.nan))
    return not np.isnan(p) and cfg.pos_lo <= p <= cfg.pos_hi


def signals(cfg: Cfg, sym: str, b, prev, opens_by_sym: dict, mom: dict | None) -> list[dict]:
    out: list[dict] = []
    if np.isnan(b["atr"]) or not vol_ok(b, cfg):
        return out
    if not range_ok(b, cfg):
        return out

    px, o = float(b["close"]), float(b["open"])
    hi, lo = float(b["high"]), float(b["low"])
    a = float(b["atr"])
    sup, res = float(b["support"]), float(b["resistance"])
    green, red = px > o, px < o
    cur = opens_by_sym.get(sym, [])
    n_l = sum(1 for t in cur if t["side"] == "long")
    n_s = sum(1 for t in cur if t["side"] == "short")
    m = cfg.method
    ex = cfg.extra or {}

    if m == "brk_vol":
        if prev is None:
            return out
        pc = float(prev["close"])
        if px > res and pc <= res and green and n_l == 0:
            add_leg(out, sym, "long", b, cfg, sl_anchor=res, tag="brk_up")
        elif px < sup and pc >= sup and red and n_s == 0:
            add_leg(out, sym, "short", b, cfg, sl_anchor=sup, tag="brk_dn")

    elif m == "fade_extreme":
        tol = ex.get("tol", 0.25) * a
        if lo <= sup + tol and green and pos_ok(b, Cfg("", "", pos_lo=0, pos_hi=0.25)) and n_l == 0:
            add_leg(out, sym, "long", b, cfg, sl_anchor=sup, tag="fade_lo")
        if hi >= res - tol and red and pos_ok(b, Cfg("", "", pos_lo=0.75, pos_hi=1.0)) and n_s == 0:
            add_leg(out, sym, "short", b, cfg, sl_anchor=res, tag="fade_hi")

    elif m == "wick_trap":
        body = abs(px - o)
        uw, lw = float(b["upper_wick"]), float(b["lower_wick"])
        min_w = ex.get("wick_mult", 1.5) * max(body, a * 0.05)
        if uw >= min_w and red and px < (hi + lo) / 2 and pos_ok(b, Cfg("", "", pos_lo=0.6, pos_hi=1.0)) and n_s == 0:
            add_leg(out, sym, "short", b, cfg, sl_anchor=hi, tag="trap_top")
        if lw >= min_w and green and px > (hi + lo) / 2 and pos_ok(b, Cfg("", "", pos_lo=0.0, pos_hi=0.4)) and n_l == 0:
            add_leg(out, sym, "long", b, cfg, sl_anchor=lo, tag="trap_bot")

    elif m == "absorb":
        bp = float(b.get("body_pct", np.nan))
        if np.isnan(bp) or bp > ex.get("body_max", 0.35):
            return out
        tol = ex.get("tol", 0.2) * a
        if lo <= sup + tol and n_l == 0:
            add_leg(out, sym, "long", b, cfg, sl_anchor=sup, tag="abs_lo")
        if hi >= res - tol and n_s == 0:
            add_leg(out, sym, "short", b, cfg, sl_anchor=res, tag="abs_hi")

    elif m == "sqz_expansion":
        if prev is None:
            return out
        if float(prev.get("vol_ratio", 99)) > ex.get("sqz_vol", 0.75):
            return out
        if float(b["vol_ratio"]) < ex.get("exp_vol", 1.8):
            return out
        brk = ex.get("brk", 0.3) * a
        if px > float(prev["high"]) + brk and green and n_l == 0:
            add_leg(out, sym, "long", b, cfg, sl_anchor=float(prev["low"]), tag="exp_up")
        elif px < float(prev["low"]) - brk and red and n_s == 0:
            add_leg(out, sym, "short", b, cfg, sl_anchor=float(prev["high"]), tag="exp_dn")

    elif m == "vol_climax_rev":
        vr = float(b["vol_ratio"])
        if vr < ex.get("climax_vol", 2.5):
            return out
        chg = (px - o) / o * 100 if o else 0
        if chg > ex.get("min_move", 0.3) and red and n_s == 0:
            add_leg(out, sym, "short", b, cfg, sl_anchor=hi, tag="climax_s")
        elif chg < -ex.get("min_move", 0.3) and green and n_l == 0:
            add_leg(out, sym, "long", b, cfg, sl_anchor=lo, tag="climax_l")

    elif m == "dual_range":
        if not (range_ok(b, cfg) and float(b.get("range_pct", 99)) <= cfg.range_max):
            return out
        tol = ex.get("tol", 0.3) * a
        if lo <= sup + tol and green and n_l == 0:
            add_leg(out, sym, "long", b, cfg, sl_anchor=sup, tag="rng_l")
        if hi >= res - tol and red and n_s == 0:
            add_leg(out, sym, "short", b, cfg, sl_anchor=res, tag="rng_s")

    elif m == "inside_break":
        if prev is None:
            return out
        pp = prev  # need i-2 for inside — passed via b extras not available; use prev only
        # inside: prev range inside prev-prev approximated by narrow body
        pr = float(prev["high"]) - float(prev["low"])
        if pr <= 0 or pr > ex.get("inside_atr", 0.8) * a:
            return out
        if px > float(prev["high"]) and green and n_l == 0:
            add_leg(out, sym, "long", b, cfg, sl_anchor=float(prev["low"]), tag="in_up")
        elif px < float(prev["low"]) and red and n_s == 0:
            add_leg(out, sym, "short", b, cfg, sl_anchor=float(prev["high"]), tag="in_dn")

    return out


def basket_signals(cfg: Cfg, bar: dict, opens_by_sym: dict) -> list[dict]:
    if cfg.method != "vwm_ls":
        return []
    scores = {}
    for sym, b in bar.items():
        roc = float(b.get("roc_lb", np.nan))
        vr = float(b.get("vol_ratio", np.nan))
        if np.isnan(roc) or np.isnan(vr) or vr < cfg.min_vol:
            continue
        scores[sym] = roc * np.log1p(vr)
    if len(scores) < 8:
        return []
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    k = (cfg.extra or {}).get("k", 3)
    longs = {s for s, _ in ranked[:k]}
    shorts = {s for s, _ in ranked[-k:]}
    out = []
    for sym in longs:
        if any(t["side"] == "long" for t in opens_by_sym.get(sym, [])):
            continue
        add_leg(out, sym, "long", bar[sym], cfg, tag="vwm_l")
    for sym in shorts:
        if any(t["side"] == "short" for t in opens_by_sym.get(sym, [])):
            continue
        add_leg(out, sym, "short", bar[sym], cfg, tag="vwm_s")
    return out


def run(cfg: Cfg, dfs: dict[str, pd.DataFrame], eval_start: int) -> dict:
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed)
    days = max((common[-1] - eval_start) / 86400000.0, 1e-9)

    cash = CAPITAL
    opens: list[dict] = []
    trades: list[dict] = []
    peak = CAPITAL
    maxdd = 0.0

    def by_sym() -> dict[str, list]:
        d: dict[str, list] = {}
        for t in opens:
            d.setdefault(t["sym"], []).append(t)
        return d

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
            trades.append({"pnl": pl, "reason": reason, "side": t["side"], "sym": t["sym"]})

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
        if cfg.method == "vwm_ls" and cfg.rebalance > 0 and i % cfg.rebalance == 0:
            cands.extend(basket_signals(cfg, bar, by_sym()))
        else:
            for sym in symbols:
                prev = indexed[sym].iloc[i - 1] if i > 0 else None
                cands.extend(signals(cfg, sym, bar[sym], prev, by_sym(), None))

        sym_open = {sym: len(by_sym().get(sym, [])) for sym in symbols}
        cands.sort(key=lambda x: x["rr"], reverse=True)
        for cand in cands:
            if len(opens) >= MAX_OPEN:
                break
            if sym_open.get(cand["sym"], 0) >= 1:
                continue
            b = bar[cand["sym"]]
            risk = abs(cand["entry"] - cand["sl"])
            if risk <= 0:
                continue
            eq = max(cash + sum(t["margin"] for t in opens), 1.0)
            risk_usd = eq * bt.RISK_PCT
            qty = risk_usd / risk
            margin = qty * cand["entry"] / LEVERAGE
            if margin > eq * 0.12:
                margin = eq * 0.12
                qty = margin * LEVERAGE / cand["entry"]
            if margin < 1.0 or cash < margin:
                continue
            cash -= margin
            opens.append(
                {
                    **cand,
                    "qty": qty,
                    "margin": margin,
                    "entry_ts": ts,
                    "risk_usd": qty * risk,
                }
            )
            sym_open[cand["sym"]] = sym_open.get(cand["sym"], 0) + 1

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
        "long_pnl": sum(t["pnl"] for t in trades if t["side"] == "long"),
        "short_pnl": sum(t["pnl"] for t in trades if t["side"] == "short"),
        "final_eq": cash,
    }


def build_grid() -> list[Cfg]:
    cfgs: list[Cfg] = []
    for min_vol, tp, sl in itertools.product(
        [1.0, 1.3, 1.6, 2.0, 2.5],
        [1.5, 2.0, 2.5, 3.0],
        [0.35, 0.45, 0.55],
    ):
        rr = tp / sl
        base = dict(min_vol=min_vol, tp_atr=tp, sl_atr=sl, min_rr=rr)
        cfgs.append(Cfg(f"brk_vol v{min_vol} RR{tp/sl:.1f}", "brk_vol", **base))
        cfgs.append(Cfg(f"fade_ext v{min_vol} RR{tp/sl:.1f}", "fade_extreme", range_max=5.0, **base))
        cfgs.append(Cfg(f"wick_trap v{min_vol}", "wick_trap", **base))
        cfgs.append(Cfg(f"absorb v{min_vol}", "absorb", range_max=4.5, **base))
        cfgs.append(
            Cfg(
                f"sqz_exp v{min_vol}",
                "sqz_expansion",
                min_vol=1.5,
                max_vol=6.0,
                tp_atr=tp,
                sl_atr=sl,
                min_rr=rr,
                extra={"sqz_vol": 0.7, "exp_vol": 1.8},
            )
        )
        cfgs.append(
            Cfg(
                f"climax v{min_vol}",
                "vol_climax_rev",
                min_vol=2.0,
                max_vol=8.0,
                tp_atr=tp,
                sl_atr=sl,
                min_rr=rr,
                extra={"climax_vol": 2.5},
            )
        )
        cfgs.append(
            Cfg(
                f"dual_rng v{min_vol} r4",
                "dual_range",
                range_max=4.0,
                min_vol=min_vol,
                tp_atr=tp,
                sl_atr=sl,
                min_rr=rr,
            )
        )
        cfgs.append(
            Cfg(
                f"dual_rng v{min_vol} r3 RR3",
                "dual_range",
                range_max=3.0,
                min_vol=min_vol,
                tp_atr=3.0,
                sl_atr=0.4,
                min_rr=2.5,
            )
        )
    for reb in [8, 16, 32]:
        cfgs.append(
            Cfg(
                f"vwm_ls reb{reb}",
                "vwm_ls",
                min_vol=1.0,
                tp_atr=2.0,
                sl_atr=0.5,
                min_rr=2.0,
                rebalance=reb,
                extra={"k": 3},
            )
        )
    return cfgs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    print(f"Load {args.days}d pool...", flush=True)
    dfs, eval_start, eval_end = load_pool(args.days)
    print(
        f"Eval: {pd.to_datetime(eval_start, unit='ms', utc=True)} → {pd.to_datetime(eval_end, unit='ms', utc=True)}",
        flush=True,
    )
    grid = build_grid()
    print(f"Sweep {len(grid)} configs...", flush=True)

    rows = []
    for j, c in enumerate(grid):
        if (j + 1) % 50 == 0:
            print(f"  {j+1}/{len(grid)}", flush=True)
        rows.append(run(c, dfs, eval_start))

    rows.sort(key=lambda x: x["ret_pct"], reverse=True)
    pos = [r for r in rows if r["ret_pct"] > 0]
    print(f"\n=== TOP {args.top} / {len(grid)} ({args.days}d) — {len(pos)} positive ===", flush=True)
    for i, r in enumerate(rows[: args.top], 1):
        mark = " ✅" if r["ret_pct"] > 0 else ""
        print(
            f"  {i}. {r['name']}: {r['ret_pct']:+.2f}% ({r['pct_day']:+.3f}%/d) "
            f"PF={r['pf']:.2f} WR={r['wr']:.1f}% n={r['n']} MaxDD={r['maxdd']:.1f}% "
            f"L={r['long_pnl']:+.0f} S={r['short_pnl']:+.0f}{mark}",
            flush=True,
        )

    if pos:
        best = pos[0]
        print(f"\nBest profitable: {best['name']} → {best['ret_pct']:+.2f}%", flush=True)
    else:
        print("\nNo profitable config in sweep.", flush=True)
        print("Least bad:", rows[0]["name"], f"{rows[0]['ret_pct']:+.2f}%", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
