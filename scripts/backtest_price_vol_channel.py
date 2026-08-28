#!/usr/bin/env python3
"""Price channel + volume dual-direction — pure OHLCV (15m, 20 coin shared wallet).

Logic (no RSI/ADX/MACD):
  1. Rolling channel = max(high,N) / min(low,N) — price structure only.
  2. Trend flip when channel bands stop moving parallel (expansion event).
  3. Entry: counter-color candle in trend direction + vol_ratio >= min_vol.
  4. Body filter: |close-open|/ATR in [body_lo, body_hi] — skip doji & huge bars.
  5. TP = opposite channel band; implicit SL = other band (donchian-style).
  6. Both long AND short from up/down trend events across 20 coins.

Usage:
  .venv/bin/python scripts/backtest_price_vol_channel.py --days 90
  .venv/bin/python scripts/backtest_price_vol_channel.py --days 365
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

_bt = importlib.util.spec_from_file_location("bt", ROOT / "scripts/backtest_sr_rr_30d.py")
bt = importlib.util.module_from_spec(_bt)
assert _bt.loader is not None
sys.modules["bt"] = bt
_bt.loader.exec_module(bt)

CAPITAL = bt.CAPITAL
LEVERAGE = bt.LEVERAGE
FEE = bt.FEE
MAX_OPEN = bt.MAX_OPEN
WARMUP_BARS = bt.WARMUP_BARS
SYMBOLS_20 = bt.SYMBOLS_20
pnl = bt.pnl


def channel_enrich(df: pd.DataFrame, period: int, slope_lb: int, parallel_tol: float) -> pd.DataFrame:
    out = df.copy()
    if "quote_volume" not in out.columns:
        raise ValueError("missing quote_volume")
    out["ch_upper"] = out["high"].rolling(period, min_periods=period).max()
    out["ch_lower"] = out["low"].rolling(period, min_periods=period).min()
    out["ch_mid"] = (out["ch_upper"] + out["ch_lower"]) / 2
    out["ch_width"] = out["ch_upper"] - out["ch_lower"]
    prev = out["close"].shift(1)
    tr = pd.concat(
        [out["high"] - out["low"], (out["high"] - prev).abs(), (out["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(14, min_periods=14).mean()
    out["vol_sma"] = out["quote_volume"].rolling(20, min_periods=20).mean()
    out["vol_ratio"] = out["quote_volume"] / out["vol_sma"].replace(0, np.nan)

    upper = out["ch_upper"].to_numpy()
    lower = out["ch_lower"].to_numpy()
    closes = out["close"].to_numpy()
    n = len(out)
    parallel = np.zeros(n, dtype=bool)

    def slope(arr: np.ndarray, i: int, ref: float) -> float:
        if i < slope_lb or ref <= 0:
            return 0.0
        return (arr[i] - arr[i - slope_lb]) / slope_lb / ref * 100.0

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        ref = closes[i]
        parallel[i] = abs(slope(upper, i, ref) - slope(lower, i, ref)) <= parallel_tol

    out["bands_parallel"] = parallel
    prev_p = np.roll(parallel, 1)
    prev_p[0] = False
    out["channel_expand"] = prev_p & (~parallel)
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
        dfs[sym] = raw
    # enrich after load with cfg period — default 20
    last = min(int(d["ts"].max()) for d in dfs.values())
    wf = last - (days + 12) * 86400 * 1000
    trimmed: dict[str, pd.DataFrame] = {}
    for sym, raw in dfs.items():
        df = raw[raw["ts"] >= wf - WARMUP_BARS * BAR_MS].copy().reset_index(drop=True)
        if len(df) < WARMUP_BARS + 200:
            continue
        trimmed[sym] = df
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in trimmed.values()]))
    eval_start = common[-1] - days * 86400 * 1000
    return trimmed, eval_start, common[-1]


@dataclass
class Cfg:
    name: str
    period: int = 20
    slope_lb: int = 5
    parallel_tol: float = 0.015
    min_vol: float = 1.0
    body_lo: float = 0.3
    body_hi: float = 1.2
    min_pot_rr: float = 0.5
    size_by_rr: bool = True
    margin_pct: float = 0.01
    max_open: int = 10
    skip_parallel: bool = True
    top_k: int = 5


def run(cfg: Cfg, raw_dfs: dict[str, pd.DataFrame], eval_start: int) -> dict:
    dfs = {
        sym: channel_enrich(df, cfg.period, cfg.slope_lb, cfg.parallel_tol)
        for sym, df in raw_dfs.items()
    }
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed)
    days = max((common[-1] - eval_start) / 86400000.0, 1e-9)

    cash = CAPITAL
    opens: list[dict] = []
    trades: list[dict] = []
    state = {sym: {"trend": None, "waiting": False} for sym in symbols}
    peak = CAPITAL
    maxdd = 0.0
    skipped = 0

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
            if sum(1 for t in opens if t["sym"] == sym) > 0:
                continue

            is_green, is_red = px > o, px < o
            counter = (st["trend"] == "up" and is_red) or (st["trend"] == "down" and is_green)
            if not counter:
                continue
            if cfg.skip_parallel and bool(b.get("bands_parallel", False)):
                skipped += 1
                continue
            if float(b["vol_ratio"]) < cfg.min_vol:
                continue
            body = abs(px - o) / a if a > 0 else 0
            if cfg.body_lo > 0 and not (cfg.body_lo <= body <= cfg.body_hi):
                continue

            side = "long" if st["trend"] == "up" else "short"
            tp_near = up if side == "long" else dn
            sl_opp = dn if side == "long" else up
            pot_rr = abs(tp_near - px) / max(abs(px - sl_opp), 1e-12)
            if pot_rr < cfg.min_pot_rr:
                continue
            cands.append({"sym": sym, "side": side, "entry": px, "tp": tp_near, "sl0": sl_opp, "pot_rr": pot_rr})

        cands.sort(key=lambda x: x["pot_rr"], reverse=True)
        for cand in cands[: cfg.top_k]:
            if len(opens) >= cfg.max_open:
                break
            if any(t["sym"] == cand["sym"] for t in opens):
                continue
            eq = max(cash + sum(t["margin"] for t in opens), 1.0)
            base_margin = eq * cfg.margin_pct
            mult = min(cand["pot_rr"], 2.0) if cfg.size_by_rr else 1.0
            margin = base_margin * mult
            margin = min(margin, eq * 0.15, cash)
            if margin < 1.0:
                continue
            qty = margin * LEVERAGE / cand["entry"]
            cash -= margin
            opens.append({**cand, "qty": qty, "margin": margin, "entry_ts": ts})
            state[cand["sym"]]["waiting"] = False

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
        "skipped": skipped,
        "final_eq": cash,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()

    print(f"Load {args.days}d...", flush=True)
    raw, eval_start, eval_end = load_pool(args.days)
    print(
        f"Eval: {pd.to_datetime(eval_start, unit='ms', utc=True)} → {pd.to_datetime(eval_end, unit='ms', utc=True)}",
        flush=True,
    )

    cfgs = [
        Cfg("ch20 vol1.0 body0.3-1.2 rr0.5", period=20, min_vol=1.0),
        Cfg("ch20 vol1.2 body0.3-1.2 rr0.5", period=20, min_vol=1.2),
        Cfg("ch20 vol1.0 body0.3-1.2 rr0.7", period=20, min_vol=1.0, min_pot_rr=0.7),
        Cfg("ch48 vol1.0 body0.3-1.2", period=48, min_vol=1.0),
        Cfg("ch48 vol1.2 body0.3-1.2", period=48, min_vol=1.2),
        Cfg("ch20 vol1.5 body0.3-1.0 rr0.6", period=20, min_vol=1.5, body_hi=1.0, min_pot_rr=0.6),
        Cfg("ch20 vol1.0 no parallel skip", period=20, min_vol=1.0, skip_parallel=False),
        Cfg("ch20 vol1.0 top3", period=20, min_vol=1.0, top_k=3),
        Cfg("ch20 vol1.0 margin0.5%", period=20, min_vol=1.0, margin_pct=0.005, max_open=20),
        Cfg("ch20 vol1.2 max20", period=20, min_vol=1.2, max_open=20, margin_pct=0.005),
    ]

    rows = []
    for c in cfgs:
        print(f"run {c.name}...", flush=True)
        r = run(c, raw, eval_start)
        rows.append(r)
        mark = " ✅" if r["ret_pct"] > 0 else ""
        print(
            f"  {r['ret_pct']:+.2f}% ({r['pct_day']:+.3f}%/d) PF={r['pf']:.2f} WR={r['wr']:.1f}% "
            f"n={r['n']} MaxDD={r['maxdd']:.1f}% L={r['long_pnl']:+.0f} S={r['short_pnl']:+.0f}{mark}",
            flush=True,
        )

    rows.sort(key=lambda x: x["ret_pct"], reverse=True)
    print(f"\n=== Best {args.days}d ===", flush=True)
    for r in rows[:5]:
        print(f"  {r['name']}: {r['ret_pct']:+.2f}% PF={r['pf']:.2f} n={r['n']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
