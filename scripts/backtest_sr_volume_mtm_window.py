#!/usr/bin/env python3
"""MTM backtest for a date window — carry positions from warmup, no EOD force-close."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_sr_volume_3y import (  # noqa: E402
    CAPITAL,
    Cfg,
    _load,
    enrich,
    load_cache,
    parse_date,
    WARMUP_BARS,
)


def run_mtm(cfg: Cfg, hunt, bflip, dfs: dict, from_ts: int) -> dict:
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed)
    days = max((common[-1] - from_ts) / 86400000.0, 1e-9)

    cash = CAPITAL
    opens: list[dict] = []
    closed_since = 0.0
    state = {sym: {"trend": None, "waiting": False} for sym in symbols}
    eq_at_from: float | None = None

    def stack(sym: str) -> int:
        return sum(1 for t in opens if t["sym"] == sym)

    def equity(mark: dict[str, float]) -> float:
        eq = cash
        for t in opens:
            eq += t["margin"] + hunt._pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def close_pos(t: dict, px: float) -> None:
        nonlocal cash, closed_since
        pnl = hunt._pnl(t["side"], t["entry"], px, t["qty"])
        cash += t["margin"] + pnl
        if ts >= from_ts:
            closed_since += pnl

    def vol_ok(b, min_vol: float) -> bool:
        vr = b.get("vol_ratio", np.nan)
        return not np.isnan(vr) and float(vr) >= min_vol

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

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}
        if ts == from_ts:
            eq_at_from = equity(mark)

        still = []
        for t in opens:
            b = bar[t["sym"]]
            hi, lo = float(b["high"]), float(b["low"])
            up, dn = float(b["dc_upper"]), float(b["dc_lower"])
            side = t["side"]
            tp = up if side == "long" else dn
            if (side == "long" and hi >= tp) or (side == "short" and lo <= tp):
                close_pos(t, tp)
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
            if np.isnan(b["dc_upper"]) or np.isnan(b["atr"]) or np.isnan(b.get("vol_ratio", np.nan)):
                continue
            up, dn = float(b["dc_upper"]), float(b["dc_lower"])
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
                    if not (0.3 <= body <= 1.2 and pot >= 0.5):
                        continue
                    if cfg.mode == "vol_filter" and not vol_ok(b, cfg.min_vol):
                        continue
                    if cfg.mode == "sr_bounce_live":
                        if side == "long" and not (near_support(b, cfg.sr_atr_tol) and vol_ok(b, cfg.min_vol)):
                            continue
                        if side == "short" and not (near_resistance(b, cfg.sr_atr_tol) and vol_ok(b, cfg.min_vol)):
                            continue
                    raw_cands.append({"sym": sym, "side": side, "px": px, "pot": pot, "body": body, "up": up, "dn": dn})

        raw_cands.sort(key=lambda x: x["pot"], reverse=True)
        vote = bflip.breadth_vote(bar_trends, 1.3, 12)
        entries: list[dict] = []
        for cand in raw_cands:
            if vote is None or cand["side"] == vote:
                entries.append(cand)
            else:
                fc = bflip.flipped_candidate(cand, cand["up"], cand["dn"], cand["px"])
                if fc:
                    entries.append(fc)
        entries.sort(key=lambda x: x["pot"], reverse=True)

        for cand in entries:
            if len(opens) >= 20 or stack(cand["sym"]) > 0:
                continue
            sm = float(np.clip(0.5 + cand["pot"], 0.5, 2.0))
            eq = max(cash + sum(t["margin"] for t in opens), 0.0)
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

    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    eq_end = equity(last_mark)
    open_unreal = sum(hunt._pnl(t["side"], t["entry"], last_mark[t["sym"]], t["qty"]) for t in opens)
    assert eq_at_from is not None
    ret = (eq_end - eq_at_from) / eq_at_from * 100
    return {
        "name": cfg.name,
        "eq_start": eq_at_from,
        "eq_end": eq_end,
        "ret_pct": ret,
        "pct_day": ret / days,
        "closed_since": closed_since,
        "open_unreal": open_unreal,
        "n_open": len(opens),
        "days": days,
    }


def main() -> int:
    from_ts = parse_date("2026-08-24")
    hunt = _load("hunt_mtm", "scripts/backtest_hunt_pct_per_day.py")
    bflip = _load("bflip_mtm", "scripts/backtest_donchian_20coin_breadth_flip.py")
    dfs = load_cache(hunt, bflip.SYMBOLS_20, from_ts=from_ts, min_bars=WARMUP_BARS + 100)

    cfgs = [
        Cfg("LIVE", mode="live"),
        Cfg("S/R+vol1.2", mode="sr_bounce_live", min_vol=1.2, sr_atr_tol=0.35),
    ]
    print("=== MTM window 24/8→now (carry positions, không đóng hết cuối kỳ) ===", flush=True)
    for c in cfgs:
        r = run_mtm(c, hunt, bflip, dfs, from_ts)
        print(
            f"{r['name']:12s} eq ${r['eq_start']:.0f}→${r['eq_end']:.0f} "
            f"ret={r['ret_pct']:+.2f}% ({r['pct_day']:+.3f}%/d) "
            f"closed={r['closed_since']:+.1f} open_unreal={r['open_unreal']:+.1f} ({r['n_open']} lệnh)",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
