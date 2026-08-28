#!/usr/bin/env python3
"""S/R + volume backtest — explicit SL/TP, RR>=1 at entry, live-parity fills.

S/R = swing high/low only (rolling extrema over SWING_WINDOW bars).
No Donchian — support/resistance are pure price structure levels.

- Entry: bar close when signal fires (same as signal candle).
- Exit: exact SL or TP price when bar high/low touches level.
- Same-bar SL+TP: assume SL first (conservative).
- No TP on entry bar.
- Risk sizing: 1% equity per trade at SL (qty = risk_usd / |entry-sl|).
- Pool: 20 majors, shared wallet, max 1 position/symbol, max 10 open.

Usage:
  python scripts/backtest_sr_rr_30d.py
  python scripts/backtest_sr_rr_30d.py --days 30 --extend-cache
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "bt_klines_15m"
THREE_Y_START_MS = 1692828900000
BAR_MS = 15 * 60 * 1000
TZ = ZoneInfo("Asia/Ho_Chi_Minh")

CAPITAL = 1000.0
LEVERAGE = 10.0
FEE = 0.0004
RISK_PCT = 0.01  # 1% equity risk per trade at SL
MAX_OPEN = 10
SWING_WINDOW = 48
VOL_SMA = 20
ATR_PERIOD = 14
WARMUP_BARS = 500

SYMBOLS_20 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "TRXUSDT", "ADAUSDT",
    "AVAXUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT", "BCHUSDT", "XLMUSDT", "ATOMUSDT",
    "NEARUSDT", "APTUSDT", "SUIUSDT", "ARBUSDT", "OPUSDT", "UNIUSDT",
]


def _load_hunt():
    spec = importlib.util.spec_from_file_location("hunt_sr", ROOT / "scripts/backtest_hunt_pct_per_day.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_sr"] = mod
    spec.loader.exec_module(mod)
    return mod


def enrich(df: pd.DataFrame, swing_window: int = SWING_WINDOW) -> pd.DataFrame:
    out = df.copy()
    if "quote_volume" not in out.columns:
        raise ValueError("cache missing quote_volume")
    prev = out["close"].shift(1)
    tr = pd.concat(
        [out["high"] - out["low"], (out["high"] - prev).abs(), (out["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    out["vol_sma"] = out["quote_volume"].rolling(VOL_SMA, min_periods=VOL_SMA).mean()
    out["vol_ratio"] = out["quote_volume"] / out["vol_sma"].replace(0, np.nan)
    out["swing_high"] = out["high"].rolling(swing_window, min_periods=swing_window).max()
    out["swing_low"] = out["low"].rolling(swing_window, min_periods=swing_window).min()
    out["support"] = out["swing_low"]
    out["resistance"] = out["swing_high"]
    out["dist_support"] = (out["close"] - out["support"]).abs()
    out["dist_resist"] = (out["resistance"] - out["close"]).abs()
    return out


def pnl(side: str, entry: float, exit_px: float, qty: float) -> float:
    if side == "long":
        gross = (exit_px - entry) * qty
    else:
        gross = (entry - exit_px) * qty
    return gross - (entry + exit_px) * qty * FEE


@dataclass
class Cfg:
    name: str
    mode: str  # bounce | breakout
    min_vol: float = 1.2
    sr_tol: float = 0.35  # within N*ATR of S/R
    sl_atr: float = 0.5  # SL beyond S/R
    min_rr: float = 1.0  # minimum R:R at entry
    tp_mode: str = "fixed_rr"  # fixed_rr | opposite_sr
    require_candle: bool = True  # green at support / red at resistance


def near_support(b, tol: float) -> bool:
    a = float(b["atr"])
    if a <= 0 or np.isnan(a):
        return False
    return float(b["low"]) <= float(b["support"]) + tol * a or float(b["dist_support"]) <= tol * a


def near_resistance(b, tol: float) -> bool:
    a = float(b["atr"])
    if a <= 0 or np.isnan(a):
        return False
    return float(b["high"]) >= float(b["resistance"]) - tol * a or float(b["dist_resist"]) <= tol * a


def plan_long(b, cfg: Cfg) -> tuple[float, float, float] | None:
    """Return (entry, sl, tp) or None if RR < min_rr."""
    e = float(b["close"])
    a = float(b["atr"])
    sup = float(b["support"])
    if a <= 0 or np.isnan(a):
        return None
    sl = sup - cfg.sl_atr * a
    risk = e - sl
    if risk <= 0:
        return None
    if cfg.tp_mode == "opposite_sr":
        tp = float(b["resistance"])
        reward = tp - e
    else:
        reward = cfg.min_rr * risk
        tp = e + reward
    if reward / risk < cfg.min_rr - 1e-9:
        return None
    return e, sl, tp


def plan_short(b, cfg: Cfg) -> tuple[float, float, float] | None:
    e = float(b["close"])
    a = float(b["atr"])
    res = float(b["resistance"])
    if a <= 0 or np.isnan(a):
        return None
    sl = res + cfg.sl_atr * a
    risk = sl - e
    if risk <= 0:
        return None
    if cfg.tp_mode == "opposite_sr":
        tp = float(b["support"])
        reward = e - tp
    else:
        reward = cfg.min_rr * risk
        tp = e - reward
    if reward / risk < cfg.min_rr - 1e-9:
        return None
    return e, sl, tp


def load_pool(hunt, days: int, swing_window: int = SWING_WINDOW) -> tuple[dict[str, pd.DataFrame], int, int]:
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
        df = enrich(raw, swing_window=swing_window)
        last = int(df["ts"].max())
        wf = last - (days + 10) * 86400 * 1000  # +10d warmup
        df = df[df["ts"] >= wf - WARMUP_BARS * BAR_MS].copy().reset_index(drop=True)
        if len(df) < WARMUP_BARS + 100:
            continue
        dfs[sym] = df
        print(f"  {sym} bars={len(df)}", flush=True)
    if len(dfs) < 10:
        raise RuntimeError("too few symbols")
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    eval_start = common[-1] - days * 86400 * 1000
    return dfs, eval_start, common[-1]


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
            trades.append(
                {
                    "pnl": pl,
                    "reason": reason,
                    "side": t["side"],
                    "sym": t["sym"],
                    "entry_rr": t["plan_rr"],
                    "actual_r": pl / t["risk_usd"] if t["risk_usd"] > 0 else 0,
                }
            )

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}

        # --- exits ---
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
                close_trade(t, sl, "SL", ts)  # conservative
            elif hit_sl:
                close_trade(t, sl, "SL", ts)
            elif hit_tp:
                close_trade(t, tp, "TP", ts)
            else:
                still.append(t)
        opens = still

        if ts < eval_start:
            continue

        # --- signals ---
        cands: list[dict] = []
        for sym in symbols:
            if stack(sym) > 0:
                continue
            b = bar[sym]
            if np.isnan(b["atr"]) or np.isnan(b.get("vol_ratio", np.nan)):
                continue
            if float(b["vol_ratio"]) < cfg.min_vol:
                continue
            px, o = float(b["close"]), float(b["open"])
            is_green, is_red = px > o, px < o

            if cfg.mode == "bounce":
                if near_support(b, cfg.sr_tol) and (not cfg.require_candle or is_green):
                    plan = plan_long(b, cfg)
                    if plan:
                        e, sl, tp = plan
                        risk = e - sl
                        rr = (tp - e) / risk
                        cands.append({"sym": sym, "side": "long", "entry": e, "sl": sl, "tp": tp, "rr": rr, "risk": risk})
                elif near_resistance(b, cfg.sr_tol) and (not cfg.require_candle or is_red):
                    plan = plan_short(b, cfg)
                    if plan:
                        e, sl, tp = plan
                        risk = sl - e
                        rr = (e - tp) / risk
                        cands.append({"sym": sym, "side": "short", "entry": e, "sl": sl, "tp": tp, "rr": rr, "risk": risk})

            elif cfg.mode == "breakout":
                if i == 0:
                    continue
                prev = indexed[sym].iloc[i - 1]
                prev_c = float(prev["close"])
                # Use prior-bar S/R so breakout = close crosses level from previous bar.
                res, sup = float(prev["resistance"]), float(prev["support"])
                if px > res and prev_c <= res and (not cfg.require_candle or is_green):
                    e, sl = px, res - cfg.sl_atr * float(b["atr"])
                    risk = e - sl
                    if risk > 0:
                        tp = e + cfg.min_rr * risk
                        rr = (tp - e) / risk
                        if rr >= cfg.min_rr:
                            cands.append(
                                {"sym": sym, "side": "long", "entry": e, "sl": sl, "tp": tp, "rr": rr, "risk": risk}
                            )
                elif px < sup and prev_c >= sup and (not cfg.require_candle or is_red):
                    e, sl = px, sup + cfg.sl_atr * float(b["atr"])
                    risk = sl - e
                    if risk > 0:
                        tp = e - cfg.min_rr * risk
                        rr = (e - tp) / risk
                        if rr >= cfg.min_rr:
                            cands.append(
                                {"sym": sym, "side": "short", "entry": e, "sl": sl, "tp": tp, "rr": rr, "risk": risk}
                            )

        cands.sort(key=lambda x: x["rr"], reverse=True)

        for cand in cands:
            if len(opens) >= MAX_OPEN:
                break
            eq = max(cash + sum(t["margin"] for t in opens), 1.0)
            risk_usd = eq * RISK_PCT
            qty = risk_usd / cand["risk"]
            notional = qty * cand["entry"]
            margin = notional / LEVERAGE
            if margin > eq * 0.15:  # cap 15% equity margin per trade
                margin = eq * 0.15
                notional = margin * LEVERAGE
                qty = notional / cand["entry"]
                risk_usd = qty * cand["risk"]
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
                    "risk_usd": risk_usd,
                }
            )

        eq = equity(mark)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)

    # EOD close at mark (report separately)
    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    eod_pnl = 0.0
    for t in list(opens):
        pl = pnl(t["side"], t["entry"], last_mark[t["sym"]], t["qty"])
        cash += t["margin"] + pl
        eod_pnl += pl

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in trades)
    tp_n = sum(1 for t in trades if t["reason"] == "TP")
    sl_n = sum(1 for t in trades if t["reason"] == "SL")

    return {
        "name": cfg.name,
        "days": days,
        "n": len(trades),
        "wr": len(wins) / len(trades) * 100 if trades else 0,
        "pf": gw / gl if gl > 0 else float("inf"),
        "net": net,
        "ret_pct": net / CAPITAL * 100,
        "pct_day": net / CAPITAL * 100 / days,
        "maxdd": maxdd * 100,
        "tpd": len(trades) / days,
        "tp_n": tp_n,
        "sl_n": sl_n,
        "avg_plan_rr": float(np.mean([t["entry_rr"] for t in trades])) if trades else 0,
        "avg_actual_r": float(np.mean([t["actual_r"] for t in trades])) if trades else 0,
        "eod_open_pnl": eod_pnl,
        "final_eq": cash,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--extend-cache", action="store_true")
    parser.add_argument("--swing-window", type=int, default=SWING_WINDOW, help="S/R lookback bars (24=6h, 48=12h, 96=24h @15m)")
    parser.add_argument("--compare-swing", action="store_true", help="Run swing 24/48/96 with key variants")
    args = parser.parse_args()

    hunt = _load_hunt()
    if args.extend_cache:
        print("Extend cache...", flush=True)
        for i, sym in enumerate(SYMBOLS_20):
            files = sorted(CACHE_DIR.glob(f"{sym}_15m_{THREE_Y_START_MS}_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not files:
                continue
            path = files[0]
            raw = pd.read_csv(path)
            last_ts = int(raw["ts"].max())
            end_ms = int(time.time() * 1000)
            if last_ts < end_ms - BAR_MS:
                tail = hunt.fetch_klines(sym, last_ts + BAR_MS, end_ms)
                if not tail.empty:
                    pd.concat([raw, tail]).drop_duplicates("ts").sort_values("ts").to_csv(path, index=False)
            if i + 1 < len(SYMBOLS_20):
                time.sleep(0.3)

    cfgs = [
        Cfg("bounce RR1.0 vol1.2", mode="bounce", min_vol=1.2, min_rr=1.0, tp_mode="fixed_rr"),
        Cfg("bounce RR1.5 vol1.2", mode="bounce", min_vol=1.2, min_rr=1.5, tp_mode="fixed_rr"),
        Cfg("bounce RR1.0 vol1.5", mode="bounce", min_vol=1.5, min_rr=1.0, tp_mode="fixed_rr"),
        Cfg("bounce RR1.0 vol2.0 tol0.2", mode="bounce", min_vol=2.0, min_rr=1.0, sr_tol=0.2),
        Cfg("bounce RR1.0→opp S/R", mode="bounce", min_vol=1.2, min_rr=1.0, tp_mode="opposite_sr"),
        Cfg("breakout RR1.5 vol1.3", mode="breakout", min_vol=1.3, min_rr=1.5, tp_mode="fixed_rr"),
    ]
    if not args.compare_swing:
        cfgs.extend([
            Cfg("bounce RR1.0 tol0.25", mode="bounce", min_vol=1.2, min_rr=1.0, sr_tol=0.25),
            Cfg("bounce RR1.0 no candle", mode="bounce", min_vol=1.2, min_rr=1.0, require_candle=False),
            Cfg("breakout RR1.0 vol1.5", mode="breakout", min_vol=1.5, min_rr=1.0, tp_mode="fixed_rr"),
        ])

    swing_windows = [24, 48, 96] if args.compare_swing else [args.swing_window]
    all_rows: list[dict] = []

    for sw in swing_windows:
        hours = sw * 15 / 60
        print(f"\nLoad pool ({args.days}d eval, swing={sw} bars = {hours:.0f}h)...", flush=True)
        dfs, eval_start, eval_end = load_pool(hunt, args.days, swing_window=sw)
        print(f"Eval: {pd.to_datetime(eval_start, unit='ms', utc=True)} → {pd.to_datetime(eval_end, unit='ms', utc=True)}", flush=True)
        print(f"\n=== S/R swing={sw} ({hours:.0f}h) · {args.days}d · 20 coins ===", flush=True)
        print(f"Capital=${CAPITAL:.0f} risk/trade={RISK_PCT*100:.0f}%@SL lev={LEVERAGE:.0f}x max_open={MAX_OPEN}", flush=True)
        print("Fill: entry@close SL/TP@exact level SL-first if both hit same bar\n", flush=True)

        for c in cfgs:
            print(f"run {c.name}...", flush=True)
            r = run(c, dfs, eval_start)
            r["swing"] = sw
            all_rows.append(r)
            print(
                f"  ret={r['ret_pct']:+.2f}% ({r['pct_day']:+.3f}%/d) WR={r['wr']:.1f}% PF={r['pf']:.2f} "
                f"MaxDD={r['maxdd']:.1f}% n={r['n']} t/d={r['tpd']:.1f} TP/SL={r['tp_n']}/{r['sl_n']} "
                f"planRR={r['avg_plan_rr']:.2f} actualR={r['avg_actual_r']:+.2f}",
                flush=True,
            )

    if args.compare_swing:
        print("\n=== So sánh swing window (best variant mỗi window) ===", flush=True)
        for sw in swing_windows:
            subset = [r for r in all_rows if r["swing"] == sw]
            best = max(subset, key=lambda x: x["ret_pct"])
            hours = sw * 15 / 60
            print(
                f"  swing={sw} ({hours:.0f}h) best={best['name']}: "
                f"{best['ret_pct']:+.2f}% WR={best['wr']:.1f}% PF={best['pf']:.2f} n={best['n']}",
                flush=True,
            )
        overall = max(all_rows, key=lambda x: x["ret_pct"])
        print(
            f"\nTốt nhất tổng thể: swing={overall['swing']} {overall['name']} → "
            f"{overall['ret_pct']:+.2f}% over {overall['days']:.0f}d",
            flush=True,
        )
    else:
        best = max(all_rows, key=lambda x: x["ret_pct"])
        print(f"\n=== Best by return: {best['name']} ===", flush=True)
        print(
            f"  {best['ret_pct']:+.2f}% over {best['days']:.0f}d | WR {best['wr']:.1f}% | "
            f"PF {best['pf']:.2f} | MaxDD {best['maxdd']:.1f}% | {best['n']} trades",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
