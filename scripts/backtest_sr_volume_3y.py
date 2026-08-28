#!/usr/bin/env python3
"""Backtest S/R + volume filters on 3y cache (20 majors, 15m).

S/R: swing high/low only (rolling extrema over SWING_WINDOW bars).
Volume: quote_volume vs SMA(20).

Compares variants against live-like breadth_flip baseline.
Paper only — does not modify live bot.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "bt_klines_15m"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
THREE_Y_START_MS = 1692828900000
BAR_MS = 15 * 60 * 1000
LOOKBACK_DAYS = 1095
MIN_BARS = 30_000
WARMUP_BARS = 5000  # ~52d @ 15m — enough for swing + ATR warmup
CAPITAL = 1000.0
SWING_WINDOW = 48  # 12h @ 15m
VOL_SMA = 20


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Swing S/R + volume features; keeps volume columns."""
    out = df.copy()
    if "quote_volume" not in out.columns:
        raise ValueError("cache missing quote_volume — run scripts/refresh_bt_klines_volume_3y.py")
    out["vol_sma"] = out["quote_volume"].rolling(VOL_SMA, min_periods=VOL_SMA).mean()
    out["vol_ratio"] = out["quote_volume"] / out["vol_sma"].replace(0, np.nan)
    out["swing_high"] = out["high"].rolling(SWING_WINDOW, min_periods=SWING_WINDOW).max()
    out["swing_low"] = out["low"].rolling(SWING_WINDOW, min_periods=SWING_WINDOW).min()
    out["support"] = out["swing_low"]
    out["resistance"] = out["swing_high"]
    out["dist_support"] = (out["close"] - out["support"]).abs()
    out["dist_resist"] = (out["resistance"] - out["close"]).abs()
    return out


@dataclass
class Cfg:
    name: str
    mode: str = "live"  # live | vol_filter | sr_bounce | sr_breakout | sr_bounce_live
    min_vol: float = 1.0
    sr_atr_tol: float = 0.35  # within N*ATR of S/R for bounce
    breakout_vol: float = 1.3
    breadth: str = "flip"


def parse_date(s: str) -> int:
    """YYYY-MM-DD in Asia/Ho_Chi_Minh → bar-open ms."""
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=TZ)
    return int(dt.timestamp() * 1000)


def extend_cache(hunt, symbols: list[str], end_ms: int | None = None) -> None:
    """Append missing 15m bars to newest 3y cache file per symbol."""
    end_ms = end_ms or int(time.time() * 1000)
    for i, sym in enumerate(symbols):
        files = sorted(
            CACHE_DIR.glob(f"{sym}_15m_{THREE_Y_START_MS}_*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            print(f"  skip extend {sym} — no cache", flush=True)
            continue
        path = files[0]
        raw = pd.read_csv(path)
        last_ts = int(raw["ts"].max())
        if last_ts >= end_ms - BAR_MS:
            print(f"  {sym} cache fresh through {pd.to_datetime(last_ts, unit='ms', utc=True)}", flush=True)
            continue
        tail = hunt.fetch_klines(sym, last_ts + BAR_MS, end_ms)
        if tail.empty:
            print(f"  {sym} no new bars", flush=True)
            continue
        merged = (
            pd.concat([raw, tail], ignore_index=True)
            .drop_duplicates("ts")
            .sort_values("ts")
            .reset_index(drop=True)
        )
        merged.to_csv(path, index=False)
        print(
            f"  {sym} +{len(tail)} bars → {len(merged)} "
            f"end {pd.to_datetime(int(merged['ts'].max()), unit='ms', utc=True)}",
            flush=True,
        )
        if i + 1 < len(symbols):
            time.sleep(0.3)


def load_cache(
    hunt,
    symbols: list[str],
    *,
    from_ts: int | None = None,
    to_ts: int | None = None,
    min_bars: int | None = None,
) -> dict[str, pd.DataFrame]:
    min_bars = min_bars if min_bars is not None else MIN_BARS
    dfs: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        files = sorted(
            CACHE_DIR.glob(f"{sym}_15m_{THREE_Y_START_MS}_*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            continue
        raw = pd.read_csv(files[0])
        if "quote_volume" not in raw.columns:
            print(f"  skip {sym} — no volume", flush=True)
            continue
        df = hunt.prepare(raw)
        df = enrich(df)
        if to_ts is not None:
            df = df[df["ts"] <= to_ts].copy()
        if from_ts is not None:
            wf = from_ts - WARMUP_BARS * BAR_MS
        else:
            last = (int(df["ts"].max()) // BAR_MS) * BAR_MS
            wf = last - LOOKBACK_DAYS * 86400 * 1000
        df = df[df["ts"] >= wf].copy().reset_index(drop=True)
        if len(df) >= min_bars:
            dfs[sym] = df
            print(f"  {sym} bars={len(df)}", flush=True)
    return dfs


def run(
    cfg: Cfg,
    hunt,
    bflip,
    dfs: dict[str, pd.DataFrame],
    *,
    from_ts: int | None = None,
) -> dict:
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed)
    if from_ts is not None:
        eval_ts = [ts for ts in common if ts >= from_ts]
        if not eval_ts:
            raise ValueError(f"from_ts {from_ts} after cache end")
        days = max((eval_ts[-1] - eval_ts[0]) / 86400000.0, 1e-9)
    else:
        days = max((common[-1] - common[0]) / 86400000.0, 1e-9)

    cash = CAPITAL
    opens: list[dict] = []
    trades: list[dict] = []
    state = {sym: {"trend": None, "waiting": False} for sym in symbols}
    peak = CAPITAL
    maxdd = 0.0
    recording = from_ts is None

    def locked():
        return sum(t["margin"] for t in opens)

    def stack(sym):
        return sum(1 for t in opens if t["sym"] == sym)

    def equity(mark):
        eq = cash
        for t in opens:
            eq += t["margin"] + hunt._pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def close_pos(t, px, reason):
        nonlocal cash
        pnl = hunt._pnl(t["side"], t["entry"], px, t["qty"])
        cash += t["margin"] + pnl
        if recording:
            trades.append({"pnl": pnl, "reason": reason})

    def vol_ok(b, min_vol: float) -> bool:
        vr = b.get("vol_ratio", np.nan)
        return not np.isnan(vr) and float(vr) >= min_vol

    def near_support(b, tol_atr: float) -> bool:
        a = float(b["atr"])
        if a <= 0 or np.isnan(a):
            return False
        lo, sup = float(b["low"]), float(b["support"])
        return lo <= float(b["support"]) + tol_atr * a or float(b["dist_support"]) <= tol_atr * a

    def near_resistance(b, tol_atr: float) -> bool:
        a = float(b["atr"])
        if a <= 0 or np.isnan(a):
            return False
        hi = float(b["high"])
        return hi >= float(b["resistance"]) - tol_atr * a or float(b["dist_resist"]) <= tol_atr * a

    for i, ts in enumerate(common):
        if from_ts is not None and ts == from_ts:
            cash = CAPITAL
            opens = []
            trades = []
            peak = CAPITAL
            maxdd = 0.0
            recording = True

        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}

        still = []
        for t in opens:
            if not recording:
                still.append(t)
                continue
            b = bar[t["sym"]]
            hi, lo = float(b["high"]), float(b["low"])
            up, dn = float(b["dc_upper"]), float(b["dc_lower"])
            mid = float(b["dc_middle"])
            side = t["side"]
            if cfg.mode == "sr_bounce":
                # TP mid band; SL beyond S/R
                if side == "long":
                    if hi >= mid:
                        close_pos(t, mid, "TP_MID")
                        continue
                    if lo <= float(b["support"]) - 0.5 * float(b["atr"]):
                        close_pos(t, float(b["support"]) - 0.5 * float(b["atr"]), "SL_SUPPORT")
                        continue
                else:
                    if lo <= mid:
                        close_pos(t, mid, "TP_MID")
                        continue
                    if hi >= float(b["resistance"]) + 0.5 * float(b["atr"]):
                        close_pos(t, float(b["resistance"]) + 0.5 * float(b["atr"]), "SL_RESIST")
                        continue
                still.append(t)
            elif cfg.mode == "sr_breakout":
                tp = up if side == "long" else dn
                if (side == "long" and hi >= tp) or (side == "short" and lo <= tp):
                    close_pos(t, tp, "TP_BAND")
                else:
                    # trail: exit if re-enter band (failed breakout)
                    if side == "long" and float(b["close"]) < float(b["resistance"]):
                        close_pos(t, float(b["close"]), "FAIL_BREAK")
                    elif side == "short" and float(b["close"]) > float(b["support"]):
                        close_pos(t, float(b["close"]), "FAIL_BREAK")
                    else:
                        still.append(t)
            else:
                tp = up if side == "long" else dn
                if (side == "long" and hi >= tp) or (side == "short" and lo <= tp):
                    close_pos(t, tp, "TP_BAND")
                else:
                    still.append(t)
        opens = still

        bar_trends: dict[str, str | None] = {}
        raw_cands: list[dict] = []

        for sym in symbols:
            b = bar[sym]
            if np.isnan(b["dc_middle"]):
                bar_trends[sym] = None
                continue
            px, o = float(b["close"]), float(b["open"])
            mid = float(b["dc_middle"])
            bar_trends[sym] = "up" if px > mid else "down"

            if np.isnan(b["dc_upper"]) or np.isnan(b["dc_lower"]) or np.isnan(b["atr"]):
                continue
            if np.isnan(b.get("vol_ratio", np.nan)):
                continue

            up, dn = float(b["dc_upper"]), float(b["dc_lower"])
            w, a = float(b["dc_width"]), float(b["atr"])
            pe, par = bool(b["parallel_exit"]), bool(b["bands_parallel"])
            st = state[sym]

            if cfg.mode == "sr_bounce":
                is_green = px > o
                is_red = px < o
                if near_support(b, cfg.sr_atr_tol) and is_green and vol_ok(b, cfg.min_vol) and px > mid:
                    side = "long"
                    pot = abs(up - px) / max(abs(px - dn), 1e-12)
                    raw_cands.append({"sym": sym, "side": side, "px": px, "pot": pot, "up": up, "dn": dn})
                elif near_resistance(b, cfg.sr_atr_tol) and is_red and vol_ok(b, cfg.min_vol) and px < mid:
                    side = "short"
                    pot = abs(px - dn) / max(abs(up - px), 1e-12)
                    raw_cands.append({"sym": sym, "side": side, "px": px, "pot": pot, "up": up, "dn": dn})
                continue

            if cfg.mode == "sr_breakout":
                prev = indexed[sym].iloc[i - 1] if i > 0 else b
                prev_close = float(prev["close"])
                resist, sup = float(b["resistance"]), float(b["support"])
                if px > resist and prev_close <= resist and vol_ok(b, cfg.breakout_vol):
                    pot = abs(up - px) / max(abs(px - dn), 1e-12)
                    raw_cands.append({"sym": sym, "side": "long", "px": px, "pot": pot, "up": up, "dn": dn})
                elif px < sup and prev_close >= sup and vol_ok(b, cfg.breakout_vol):
                    pot = abs(px - dn) / max(abs(up - px), 1e-12)
                    raw_cands.append({"sym": sym, "side": "short", "px": px, "pot": pot, "up": up, "dn": dn})
                continue

            # live / vol_filter / sr_bounce_live
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
                    if not (0.3 <= body <= 1.2 and pot >= 0.5):
                        continue
                    if cfg.mode == "vol_filter" and not vol_ok(b, cfg.min_vol):
                        continue
                    if cfg.mode == "sr_bounce_live":
                        if side == "long" and not (near_support(b, cfg.sr_atr_tol) and vol_ok(b, cfg.min_vol)):
                            continue
                        if side == "short" and not (near_resistance(b, cfg.sr_atr_tol) and vol_ok(b, cfg.min_vol)):
                            continue
                    raw_cands.append(
                        {"sym": sym, "side": side, "px": px, "pot": pot, "body": body, "up": up, "dn": dn}
                    )

        raw_cands.sort(key=lambda x: x["pot"], reverse=True)

        vote = bflip.breadth_vote(bar_trends, 1.3, 12) if cfg.breadth == "flip" else None
        entries: list[dict] = []
        for cand in raw_cands:
            if cfg.breadth != "flip" or vote is None or cand["side"] == vote:
                entries.append(cand)
                continue
            fc = bflip.flipped_candidate(cand, cand["up"], cand["dn"], cand["px"])
            if fc:
                entries.append(fc)
        entries.sort(key=lambda x: x["pot"], reverse=True)

        if not recording:
            for sym in symbols:
                if stack(sym) > 0:
                    state[sym]["waiting"] = False
            continue

        for cand in entries:
            if len(opens) >= 20 or stack(cand["sym"]) > 0:
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

        if recording:
            eq = equity(mark)
            if eq > peak:
                peak = eq
            maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0.0)

    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    if recording:
        for t in list(opens):
            close_pos(t, last_mark[t["sym"]], "EOD")

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    n = len(trades)
    net = sum(t["pnl"] for t in trades)
    return {
        "name": cfg.name,
        "n": n,
        "wr": len(wins) / n * 100 if n else 0.0,
        "pf": gw / gl if gl > 0 else float("inf"),
        "maxdd": maxdd * 100,
        "t/d": n / days,
        "net": net,
        "ret_pct": net / CAPITAL * 100 if CAPITAL else 0.0,
        "pct_day": net / CAPITAL * 100 / days if days else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest S/R + volume filters on 20-major cache")
    parser.add_argument("--from-date", help="eval window start YYYY-MM-DD (VN), warmup before")
    parser.add_argument("--to-date", help="eval window end YYYY-MM-DD (VN), default cache end")
    parser.add_argument("--extend-cache", action="store_true", help="fetch missing bars before run")
    parser.add_argument("--quick", action="store_true", help="only LIVE vs S/R pullback+vol")
    args = parser.parse_args()

    hunt = _load("hunt_srv", "scripts/backtest_hunt_pct_per_day.py")
    bflip = _load("bflip_srv", "scripts/backtest_donchian_20coin_breadth_flip.py")

    from_ts = parse_date(args.from_date) if args.from_date else None
    to_ts = parse_date(args.to_date) + 86400 * 1000 - 1 if args.to_date else None

    if args.extend_cache:
        print("Extend cache to now...", flush=True)
        extend_cache(hunt, bflip.SYMBOLS_20)

    min_bars = WARMUP_BARS + 100 if from_ts else MIN_BARS
    label = f"from {args.from_date}" if from_ts else "3y"
    print(f"Load cache + volume ({label})...", flush=True)
    dfs = load_cache(hunt, bflip.SYMBOLS_20, from_ts=from_ts, to_ts=to_ts, min_bars=min_bars)
    if len(dfs) < 10:
        print("too few symbols", flush=True)
        return 1

    if args.quick or from_ts:
        cfgs = [
            Cfg("LIVE breadth_flip (ref)", mode="live"),
            Cfg("LIVE + vol>=1.2", mode="vol_filter", min_vol=1.2),
            Cfg("LIVE + S/R pullback + vol>=1.2", mode="sr_bounce_live", min_vol=1.2, sr_atr_tol=0.35),
        ]
    else:
        cfgs = [
            Cfg("LIVE breadth_flip (ref)", mode="live"),
            Cfg("LIVE + vol>=1.2", mode="vol_filter", min_vol=1.2),
            Cfg("LIVE + vol>=1.5", mode="vol_filter", min_vol=1.5),
            Cfg("LIVE + S/R pullback + vol>=1.2", mode="sr_bounce_live", min_vol=1.2, sr_atr_tol=0.35),
            Cfg("LIVE + S/R pullback + vol>=1.0", mode="sr_bounce_live", min_vol=1.0, sr_atr_tol=0.5),
            Cfg("S/R bounce + vol (standalone)", mode="sr_bounce", min_vol=1.2, sr_atr_tol=0.35, breadth="none"),
            Cfg("S/R bounce vol>=1.5 tol=0.25", mode="sr_bounce", min_vol=1.5, sr_atr_tol=0.25, breadth="none"),
            Cfg("S/R breakout + vol>=1.3", mode="sr_breakout", breakout_vol=1.3, breadth="none"),
            Cfg("S/R breakout + vol>=1.5", mode="sr_breakout", breakout_vol=1.5, breadth="none"),
        ]

    print(f"\n=== S/R + volume · {label} · {len(dfs)} coins ===", flush=True)
    rows = []
    for c in cfgs:
        print(f"run {c.name}...", flush=True)
        r = run(c, hunt, bflip, dfs, from_ts=from_ts)
        rows.append(r)
        print(
            f"  ret={r['ret_pct']:+.2f}% ({r['pct_day']:+.3f}%/d) WR={r['wr']:.1f}% PF={r['pf']:.2f} "
            f"MaxDD={r['maxdd']:.1f}% n={r['n']} t/d={r['t/d']:.1f}",
            flush=True,
        )

    base = rows[0]
    print("\n=== vs LIVE ===", flush=True)
    for r in rows:
        print(
            f"{r['name'][:44]:44s} ret={r['ret_pct']:+6.2f}% ({r['pct_day']:+.3f}%/d) "
            f"WR={r['wr']:5.1f}% ({r['wr']-base['wr']:+.1f}) PF={r['pf']:.2f} "
            f"MaxDD={r['maxdd']:5.1f}% t/d={r['t/d']:.1f}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
