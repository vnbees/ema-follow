#!/usr/bin/env python3
"""30d backtest — tìm phương pháp 2 chiều / hedge / momentum (20 coin, 15m).

Thị trường chỉ lên hoặc xuống — test nhiều cách khai thác cả 2 phía:
  1. squeeze_straddle  — BB squeeze → mở long+short cùng lúc, TP/SL đối xứng
  2. range_dual        — sideway: long support + short resistance (có thể 2 chân)
  3. mom_ls            — long top momentum, short bottom momentum (market-neutral)
  4. trend_both        — breakout lên/xuống swing, trade cả 2 hướng theo tín hiệu
  5. rsi_dual          — RSI<30 long / RSI>70 short, nhiều coin song song
  6. hedge_scalp       — NO_TREND proxy (ADX thấp): long+short cùng giá, TP 1%

Fill: entry@close, SL/TP exact, SL-first same bar.
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
RISK_PCT = bt.RISK_PCT
MAX_OPEN = bt.MAX_OPEN
WARMUP_BARS = bt.WARMUP_BARS
SYMBOLS_20 = bt.SYMBOLS_20
pnl = bt.pnl

SWING = 96
BB_PERIOD = 20
ADX_PERIOD = 14
RSI_PERIOD = 14


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    d = series.diff()
    up = d.clip(lower=0).rolling(period, min_periods=period).mean()
    dn = (-d.clip(upper=0)).rolling(period, min_periods=period).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - df["close"].shift(1)).abs(), (df["low"] - df["close"].shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(period, min_periods=period).mean()
    pdi = 100 * pd.Series(pdm, index=df.index).rolling(period).mean() / atr
    mdi = 100 * pd.Series(mdm, index=df.index).rolling(period).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.rolling(period, min_periods=period).mean()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = bt.enrich(df, swing_window=SWING)
    mid = out["close"].rolling(BB_PERIOD, min_periods=BB_PERIOD).mean()
    std = out["close"].rolling(BB_PERIOD, min_periods=BB_PERIOD).std()
    out["bb_upper"] = mid + 2 * std
    out["bb_lower"] = mid - 2 * std
    out["bb_width"] = (out["bb_upper"] - out["bb_lower"]) / mid.replace(0, np.nan)
    out["bb_width_pct"] = out["bb_width"].rolling(96, min_periods=48).rank(pct=True)
    out["adx"] = adx(out, ADX_PERIOD)
    out["rsi"] = rsi(out["close"], RSI_PERIOD)
    out["roc96"] = out["close"].pct_change(96) * 100
    out["range_pct"] = (out["swing_high"] - out["swing_low"]) / out["close"] * 100
    out["mid_range"] = (out["swing_high"] + out["swing_low"]) / 2
    return out


SYMBOLS_TOP3 = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def is_sideway(b, range_max: float, adx_max: float) -> bool:
    if np.isnan(b.get("range_pct", np.nan)) or np.isnan(b.get("adx", np.nan)):
        return False
    return float(b["range_pct"]) <= range_max and float(b["adx"]) <= adx_max


@dataclass
class Cfg:
    name: str
    method: str
    min_vol: float = 1.0
    tp_atr: float = 1.0
    sl_atr: float = 0.5
    max_dual_per_sym: int = 2  # long+short on same symbol
    range_max: float = 4.0
    adx_max: float = 22.0
    regime_min_pct: float = 0.0  # 0=off; 0.6 = need 60% coins sideway to trade


def load_pool(days: int, symbols: list[str] | None = None) -> tuple[dict[str, pd.DataFrame], int, int]:
    pool = symbols or SYMBOLS_20
    dfs: dict[str, pd.DataFrame] = {}
    for sym in pool:
        files = sorted(CACHE_DIR.glob(f"{sym}_15m_{THREE_Y_START_MS}_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            continue
        raw = pd.read_csv(files[0])
        if "quote_volume" not in raw.columns:
            continue
        df = enrich(raw)
        last = int(df["ts"].max())
        wf = last - (days + 10) * 86400 * 1000
        df = df[df["ts"] >= wf - WARMUP_BARS * BAR_MS].copy().reset_index(drop=True)
        if len(df) < WARMUP_BARS + 100:
            continue
        dfs[sym] = df
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    eval_start = common[-1] - days * 86400 * 1000
    return dfs, eval_start, common[-1]


def plan_leg(side: str, entry: float, atr: float, cfg: Cfg, sl_level: float | None = None) -> tuple[float, float, float] | None:
    if atr <= 0 or np.isnan(atr):
        return None
    if side == "long":
        sl = (sl_level if sl_level is not None else entry) - cfg.sl_atr * atr
        risk = entry - sl
        if risk <= 0:
            return None
        tp = entry + cfg.tp_atr * atr
    else:
        sl = (sl_level if sl_level is not None else entry) + cfg.sl_atr * atr
        risk = sl - entry
        if risk <= 0:
            return None
        tp = entry - cfg.tp_atr * atr
    return entry, sl, tp, risk


def open_leg(cands: list, sym: str, side: str, entry: float, atr: float, cfg: Cfg, sl_level: float | None = None, tag: str = "") -> None:
    plan = plan_leg(side, entry, atr, cfg, sl_level)
    if not plan:
        return
    e, sl, tp, risk = plan
    rr = cfg.tp_atr / cfg.sl_atr
    cands.append({"sym": sym, "side": side, "entry": e, "sl": sl, "tp": tp, "rr": rr, "risk": risk, "tag": tag})


def signals_bar(
    cfg: Cfg,
    sym: str,
    i: int,
    b,
    prev,
    opens_by_sym: dict[str, list],
    mom_ranks: dict[str, float] | None,
) -> list[dict]:
    out: list[dict] = []
    if np.isnan(b["atr"]) or np.isnan(b.get("vol_ratio", np.nan)):
        return out
    if float(b["vol_ratio"]) < cfg.min_vol:
        return out

    px = float(b["close"])
    lo, hi = float(b["low"]), float(b["high"])
    a = float(b["atr"])
    o = float(b["open"])
    is_green, is_red = px > o, px < o
    sup, res = float(b["support"]), float(b["resistance"])
    cur = opens_by_sym.get(sym, [])
    n_long = sum(1 for t in cur if t["side"] == "long")
    n_short = sum(1 for t in cur if t["side"] == "short")

    m = cfg.method

    if m == "squeeze_straddle":
        # BB width in bottom 15% + vol spike → straddle both ways
        if float(b.get("bb_width_pct", 1)) > 0.15:
            return out
        if float(b["vol_ratio"]) < 1.2:
            return out
        if n_long == 0 and n_short == 0:
            open_leg(out, sym, "long", px, a, cfg, tag="straddle")
            open_leg(out, sym, "short", px, a, cfg, tag="straddle")

    elif m == "range_dual":
        if not is_sideway(b, cfg.range_max, cfg.adx_max):
            return out
        tol = 0.3 * a
        if lo <= sup + tol and is_green and n_long == 0:
            open_leg(out, sym, "long", px, a, cfg, sl_level=sup, tag="range_sup")
        if hi >= res - tol and is_red and n_short == 0:
            open_leg(out, sym, "short", px, a, cfg, sl_level=res, tag="range_res")

    elif m == "trend_both":
        if prev is None:
            return out
        prev_c = float(prev["close"])
        res, sup = float(prev["resistance"]), float(prev["support"])
        if px > res and prev_c <= res and is_green and float(b["vol_ratio"]) >= 1.2 and n_long == 0:
            open_leg(out, sym, "long", px, a, cfg, sl_level=res, tag="brk_up")
        elif px < sup and prev_c >= sup and is_red and float(b["vol_ratio"]) >= 1.2 and n_short == 0:
            open_leg(out, sym, "short", px, a, cfg, sl_level=sup, tag="brk_dn")

    elif m == "rsi_dual":
        r = float(b["rsi"])
        if np.isnan(r):
            return out
        if r <= 30 and is_green and n_long == 0:
            open_leg(out, sym, "long", px, a, cfg, tag="rsi_os")
        elif r >= 70 and is_red and n_short == 0:
            open_leg(out, sym, "short", px, a, cfg, tag="rsi_ob")

    elif m == "hedge_scalp":
        # Low trend (ADX<18): open long+short at same price, tight TP 0.8 ATR
        if float(b.get("adx", 99)) > 18:
            return out
        if n_long == 0 and n_short == 0:
            tight = Cfg(cfg.name, cfg.method, cfg.min_vol, tp_atr=0.8, sl_atr=0.4)
            open_leg(out, sym, "long", px, a, tight, tag="hedge")
            open_leg(out, sym, "short", px, a, tight, tag="hedge")

    return out


def mom_ls_signals(cfg: Cfg, bar: dict, opens_by_sym: dict, mom_ranks: dict[str, float]) -> list[dict]:
    """Long top 3 ROC, short bottom 3 — rebalance every 16 bars built into caller."""
    if not mom_ranks:
        return []
    ranked = sorted(mom_ranks.items(), key=lambda x: x[1], reverse=True)
    longs = {s for s, _ in ranked[:3]}
    shorts = {s for s, _ in ranked[-3:]}
    out: list[dict] = []
    for sym in longs:
        if sym not in bar:
            continue
        cur = opens_by_sym.get(sym, [])
        if any(t["side"] == "long" for t in cur):
            continue
        b = bar[sym]
        if np.isnan(b["atr"]):
            continue
        open_leg(out, sym, "long", float(b["close"]), float(b["atr"]), cfg, tag="mom_long")
    for sym in shorts:
        if sym not in bar:
            continue
        cur = opens_by_sym.get(sym, [])
        if any(t["side"] == "short" for t in cur):
            continue
        b = bar[sym]
        if np.isnan(b["atr"]):
            continue
        open_leg(out, sym, "short", float(b["close"]), float(b["atr"]), cfg, tag="mom_short")
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
    rebalance_every = 16  # 4h
    regime_bars = 0
    regime_skips = 0

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

        mom_ranks = {sym: float(bar[sym]["roc96"]) for sym in symbols if not np.isnan(bar[sym]["roc96"])}

        regime_ok = True
        if cfg.method == "range_dual" and cfg.regime_min_pct > 0:
            n_ok = sum(1 for sym in symbols if is_sideway(bar[sym], cfg.range_max, cfg.adx_max))
            regime_ok = n_ok / len(symbols) >= cfg.regime_min_pct
            if regime_ok:
                regime_bars += 1
            else:
                regime_skips += 1

        cands: list[dict] = []
        if regime_ok and cfg.method == "mom_ls" and i % rebalance_every == 0:
            cands.extend(mom_ls_signals(cfg, bar, by_sym(), mom_ranks))
        elif regime_ok:
            for sym in symbols:
                prev = indexed[sym].iloc[i - 1] if i > 0 else None
                cands.extend(signals_bar(cfg, sym, i, bar[sym], prev, by_sym(), mom_ranks))

        # cap per symbol for dual strategies
        sym_counts: dict[str, int] = {}
        for t in opens:
            sym_counts[t["sym"]] = sym_counts.get(t["sym"], 0) + 1

        cands.sort(key=lambda x: x["rr"], reverse=True)
        for cand in cands:
            if len(opens) >= MAX_OPEN * 2:  # allow more for hedge
                break
            if sym_counts.get(cand["sym"], 0) >= cfg.max_dual_per_sym:
                continue
            eq = max(cash + sum(t["margin"] for t in opens), 1.0)
            risk_usd = eq * RISK_PCT
            qty = risk_usd / cand["risk"]
            margin = qty * cand["entry"] / LEVERAGE
            if margin > eq * 0.10:
                margin = eq * 0.10
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
                    "tag": cand.get("tag", ""),
                }
            )
            sym_counts[cand["sym"]] = sym_counts.get(cand["sym"], 0) + 1

        eq = equity(mark)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in trades)
    long_pnl = sum(t["pnl"] for t in trades if t["side"] == "long")
    short_pnl = sum(t["pnl"] for t in trades if t["side"] == "short")
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
        "long_pnl": long_pnl,
        "short_pnl": short_pnl,
        "final_eq": cash,
        "regime_bars": regime_bars,
        "regime_skips": regime_skips,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--regime", action="store_true", help="Run range dual + regime filter 30/60/90d")
    parser.add_argument("--top3", action="store_true", help="Use BTC ETH SOL only")
    args = parser.parse_args()

    if args.regime:
        pool_label = "top3" if args.top3 else "20 coin"
        symbols = SYMBOLS_TOP3 if args.top3 else None
        cfgs = [
            Cfg(
                "range dual RR2 (no regime)",
                "range_dual",
                min_vol=1.2,
                tp_atr=2.0,
                sl_atr=0.4,
                range_max=3.0,
                adx_max=18.0,
            ),
            Cfg(
                "range dual + regime 60%",
                "range_dual",
                min_vol=1.2,
                tp_atr=2.0,
                sl_atr=0.4,
                range_max=3.0,
                adx_max=18.0,
                regime_min_pct=0.60,
            ),
            Cfg(
                "range dual + regime 70%",
                "range_dual",
                min_vol=1.2,
                tp_atr=2.0,
                sl_atr=0.4,
                range_max=3.0,
                adx_max=18.0,
                regime_min_pct=0.70,
            ),
        ]
        for days in [30, 60, 90]:
            print(f"\n{'='*60}\n{pool_label} · {days}d\n{'='*60}", flush=True)
            dfs, eval_start, eval_end = load_pool(days, symbols=symbols)
            print(f"Eval: {pd.to_datetime(eval_start, unit='ms', utc=True)} → {pd.to_datetime(eval_end, unit='ms', utc=True)}", flush=True)
            for c in cfgs:
                r = run(c, dfs, eval_start)
                mark = " ✅" if r["ret_pct"] > 0 else ""
                pct_regime = r["regime_bars"] / max(r["regime_bars"] + r["regime_skips"], 1) * 100
                print(
                    f"  {c.name}: {r['ret_pct']:+.2f}% ({r['pct_day']:+.3f}%/d) PF={r['pf']:.2f} "
                    f"WR={r['wr']:.1f}% n={r['n']} MaxDD={r['maxdd']:.1f}% "
                    f"regime_on={pct_regime:.0f}% L={r['long_pnl']:+.0f} S={r['short_pnl']:+.0f}{mark}",
                    flush=True,
                )
        return 0

    cfgs = [
        Cfg("squeeze straddle L+S", "squeeze_straddle", min_vol=1.2),
        Cfg("range dual L@sup S@res", "range_dual", min_vol=1.0, tp_atr=1.2, sl_atr=0.4),
        Cfg("momentum L top3 S bot3", "mom_ls", min_vol=0.8, tp_atr=1.5, sl_atr=0.75),
        Cfg("breakout both ways", "trend_both", min_vol=1.2, tp_atr=1.5, sl_atr=0.5),
        Cfg("RSI dual OS/OB", "rsi_dual", min_vol=1.0, tp_atr=1.0, sl_atr=0.5),
        Cfg("hedge scalp ADX<18 L+S", "hedge_scalp", min_vol=0.8),
        Cfg("range dual RR2", "range_dual", min_vol=1.0, tp_atr=2.0, sl_atr=0.5),
        Cfg("squeeze straddle RR2", "squeeze_straddle", min_vol=1.3, tp_atr=2.0, sl_atr=0.5),
    ]

    print(f"Load pool ({args.days}d)...", flush=True)
    dfs, eval_start, eval_end = load_pool(args.days)
    print(f"Eval: {pd.to_datetime(eval_start, unit='ms', utc=True)} → {pd.to_datetime(eval_end, unit='ms', utc=True)}", flush=True)
    print(f"Capital=${CAPITAL} risk=1%@SL lev={LEVERAGE}x max_open={MAX_OPEN*2} (hedge)\n", flush=True)

    rows = []
    for c in cfgs:
        print(f"run {c.name}...", flush=True)
        r = run(c, dfs, eval_start)
        rows.append(r)
        print(
            f"  ret={r['ret_pct']:+.2f}% ({r['pct_day']:+.3f}%/d) WR={r['wr']:.1f}% PF={r['pf']:.2f} "
            f"MaxDD={r['maxdd']:.1f}% n={r['n']} L={r['long_pnl']:+.0f} S={r['short_pnl']:+.0f} eq={r['final_eq']:.0f}",
            flush=True,
        )

    rows.sort(key=lambda x: x["ret_pct"], reverse=True)
    print("\n=== Xếp hạng 30d ===", flush=True)
    for i, r in enumerate(rows, 1):
        mark = " ✅" if r["ret_pct"] > 0 else ""
        print(
            f"  {i}. {r['name']}: {r['ret_pct']:+.2f}% PF={r['pf']:.2f} WR={r['wr']:.1f}% n={r['n']}{mark}",
            flush=True,
        )
    best = rows[0]
    print(f"\nTốt nhất: {best['name']} → {best['ret_pct']:+.2f}% | final equity ${best['final_eq']:.2f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
