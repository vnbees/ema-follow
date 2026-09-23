#!/usr/bin/env python3
"""Research: find strategies that BEAT Config A on same yardstick.

Rules (per user):
  - Different from live Donchian counter-candle Config A logic
  - Careful backtest on cache (no live bot changes)
  - Same pool / shared wallet / fees as Config A for fair compare
  - Rank until something beats baseline

Baseline Config A (docs/backtest_channel_vol_1y_3y_5y_max.md):
  365d: PF 1.54 · WR 71.5% · MaxDD 38.7% · ret +2545.7%
  1095d: PF 1.53 · MaxDD 52.3%

Beat rule (strict):
  PF >= baseline_PF + 0.03 AND MaxDD <= baseline_MaxDD + 2.0
  on BOTH 365d and 1095d windows.
Soft beat (report): PF higher OR (MaxDD much lower with PF>=1.45).

Usage:
  .venv/bin/python scripts/research_beat_config_a.py
  .venv/bin/python scripts/research_beat_config_a.py --windows 365,1095
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")

spec = importlib.util.spec_from_file_location("pvc", ROOT / "scripts/backtest_price_vol_channel.py")
pvc = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["pvc"] = pvc
spec.loader.exec_module(pvc)

load_pool = pvc.load_pool
channel_enrich = pvc.channel_enrich
Cfg = pvc.Cfg
run_cfg = pvc.run
CAPITAL = pvc.CAPITAL
LEVERAGE = pvc.LEVERAGE
FEE = pvc.FEE
pnl = pvc.pnl

BASE_365 = {"pf": 1.54, "maxdd": 38.7, "ret": 2545.7, "wr": 71.5}
BASE_1095 = {"pf": 1.53, "maxdd": 52.3, "ret": 7258521.6, "wr": 71.8}


def enrich_extra(df: pd.DataFrame) -> pd.DataFrame:
    """Indicators for alternative strategies (on top of channel_enrich)."""
    out = df.copy()
    c, h, l, o = out["close"], out["high"], out["low"], out["open"]
    # Bollinger
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    out["bb_mid"] = mid
    out["bb_up"] = mid + 2 * std
    out["bb_dn"] = mid - 2 * std
    # Keltner
    atr = out["atr"]
    out["kel_mid"] = mid
    out["kel_up"] = mid + 1.5 * atr
    out["kel_dn"] = mid - 1.5 * atr
    out["squeeze"] = (out["bb_up"] < out["kel_up"]) & (out["bb_dn"] > out["kel_dn"])
    # RSI
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    out["rsi"] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    # ROC / momentum
    out["roc20"] = c.pct_change(20) * 100
    out["sma50"] = c.rolling(50).mean()
    out["sma200"] = c.rolling(200).mean()
    # Supertrend-ish ATR trail
    hl2 = (h + l) / 2
    out["st_up"] = hl2 - 2.5 * atr
    out["st_dn"] = hl2 + 2.5 * atr
    # Donchian break levels (period 20 already as ch_upper/lower)
    out["prev_up"] = out["ch_upper"].shift(1)
    out["prev_dn"] = out["ch_lower"].shift(1)
    # NR7
    rng = h - l
    out["nr7"] = rng == rng.rolling(7).min()
    # Z-score
    out["z20"] = (c - mid) / std.replace(0, np.nan)
    # ADX proxy: DI
    up_move = h.diff()
    dn_move = -l.diff()
    plus_dm = np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0)
    tr = atr  # approx already smoothed
    out["di_plus"] = 100 * pd.Series(plus_dm, index=out.index).ewm(alpha=1 / 14, adjust=False).mean() / atr
    out["di_minus"] = 100 * pd.Series(minus_dm, index=out.index).ewm(alpha=1 / 14, adjust=False).mean() / atr
    out["di_diff"] = out["di_plus"] - out["di_minus"]
    # Heikin Ashi
    ha_c = (o + h + l + c) / 4
    ha_o = (o.shift(1) + c.shift(1)) / 2
    out["ha_bull"] = ha_c > ha_o
    out["ha_bull_prev"] = out["ha_bull"].shift(1)
    # Body / vol already from channel
    out["body_atr"] = (c - o).abs() / atr.replace(0, np.nan)
    out["is_green"] = c > o
    out["is_red"] = c < o
    # Range position
    out["clv"] = (c - l) / (h - l).replace(0, np.nan)
    return out


@dataclass
class PortCfg:
    name: str
    strategy: str
    # exits
    exit_mode: str = "band"  # band | atr_rr | bb_mid
    atr_sl_mult: float = 1.5
    rr: float = 2.0
    # portfolio
    margin_pct: float = 0.01
    max_open: int = 10
    top_k: int = 5
    size_by_rr: bool = True
    size_mult_cap: float = 2.0
    min_vol: float = 1.2
    # strategy knobs
    min_pot_rr: float = 0.5
    rsi_lo: float = 30.0
    rsi_hi: float = 70.0
    z_th: float = 2.0


def _size(eq: float, cash: float, entry: float, pot_rr: float, cfg: PortCfg) -> tuple[float, float]:
    base = eq * cfg.margin_pct
    mult = min(pot_rr, cfg.size_mult_cap) if cfg.size_by_rr else 1.0
    margin = min(base * mult, eq * 0.15, cash)
    if margin < 1.0:
        return 0.0, 0.0
    qty = margin * LEVERAGE / entry
    return margin, qty


def run_strategy(cfg: PortCfg, raw_dfs: dict[str, pd.DataFrame], eval_start: int) -> dict:
    """Shared-wallet simulator with pluggable entry logic."""
    dfs = {
        sym: enrich_extra(channel_enrich(df, 20, 5, 0.015))
        for sym, df in raw_dfs.items()
    }
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed)
    first_eval = next((t for t in common if t >= eval_start), common[-1])
    days = max((common[-1] - first_eval) / 86400000.0, 1e-9)

    cash = CAPITAL
    opens: list[dict] = []
    trades: list[dict] = []
    peak = CAPITAL
    maxdd = 0.0
    # per-sym state for expand-based strategies
    state = {sym: {"trend": None, "waiting": False, "sq": False} for sym in symbols}

    def equity(mark: dict[str, float]) -> float:
        eq = cash
        for t in opens:
            eq += t["margin"] + pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def close_trade(t: dict, px: float, reason: str, ts: int) -> None:
        nonlocal cash
        pl = pnl(t["side"], t["entry"], px, t["qty"])
        # approx fee already inside pnl helper? check pvc.pnl
        cash += t["margin"] + pl
        if ts >= eval_start:
            trades.append({"pnl": pl, "reason": reason, "side": t["side"], "sym": t["sym"]})

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}

        # --- exits ---
        still = []
        for t in opens:
            b = bar[t["sym"]]
            hi, lo, px = float(b["high"]), float(b["low"]), float(b["close"])
            side = t["side"]
            sl, tp = t["sl"], t["tp"]
            hit_sl = (side == "long" and lo <= sl) or (side == "short" and hi >= sl)
            hit_tp = (side == "long" and hi >= tp) or (side == "short" and lo <= tp)
            # band exit: TP = dynamic channel band each bar
            if cfg.exit_mode == "band":
                # Match Config A: TP = current near band only (no hard SL)
                up, dn = float(b["ch_upper"]), float(b["ch_lower"])
                tp_dyn = up if side == "long" else dn
                if (side == "long" and hi >= tp_dyn) or (side == "short" and lo <= tp_dyn):
                    close_trade(t, tp_dyn, "TP", ts)
                    continue
                still.append(t)
                continue
            if cfg.exit_mode == "bb_mid":
                mid = float(b["bb_mid"])
                if (side == "long" and hi >= mid) or (side == "short" and lo <= mid):
                    close_trade(t, mid, "TP", ts)
                    continue
                if hit_sl:
                    close_trade(t, sl, "SL", ts)
                    continue
                still.append(t)
                continue
            # atr_rr fixed
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
            for sym in symbols:
                b = bar[sym]
                if bool(b.get("channel_expand", False)):
                    px = float(b["close"])
                    mid = float(b["ch_mid"])
                    state[sym]["trend"] = "up" if px > mid else "down"
                    state[sym]["waiting"] = True
                if bool(b.get("squeeze", False)):
                    state[sym]["sq"] = True
            continue

        cands: list[dict] = []
        for sym in symbols:
            b = bar[sym]
            if any(t["sym"] == sym for t in opens):
                continue
            if np.isnan(b.get("atr", np.nan)) or float(b["atr"]) <= 0:
                continue
            px = float(b["close"])
            o = float(b["open"])
            hi, lo = float(b["high"]), float(b["low"])
            atr = float(b["atr"])
            vol_ok = (not np.isnan(b.get("vol_ratio", np.nan))) and float(b["vol_ratio"]) >= cfg.min_vol

            sig = _signal(cfg, sym, b, state, vol_ok, px, o, hi, lo, atr)
            if sig is None:
                continue
            side, pot_rr, sl, tp = sig
            cands.append({"sym": sym, "side": side, "entry": px, "tp": tp, "sl": sl, "pot_rr": pot_rr})

        cands.sort(key=lambda x: x["pot_rr"], reverse=True)
        for cand in cands[: cfg.top_k]:
            if len(opens) >= cfg.max_open:
                break
            if any(t["sym"] == cand["sym"] for t in opens):
                continue
            eq = max(cash + sum(t["margin"] for t in opens), 1.0)
            margin, qty = _size(eq, cash, cand["entry"], cand["pot_rr"], cfg)
            if margin < 1.0:
                continue
            cash -= margin
            opens.append({**cand, "qty": qty, "margin": margin, "entry_ts": ts})
            if cfg.strategy in ("same_dir_expand", "counter_baseline_ref"):
                state[cand["sym"]]["waiting"] = False
            if cfg.strategy == "keltner_squeeze":
                state[cand["sym"]]["sq"] = False

        eq = equity(mark)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)

    # EOD flatten
    if opens:
        last_ts = common[-1]
        bar = {sym: indexed[sym].iloc[-1] for sym in symbols}
        for t in opens:
            close_trade(t, float(bar[t["sym"]]["close"]), "EOD", last_ts)

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in trades)
    return {
        "name": cfg.name,
        "strategy": cfg.strategy,
        "days": days,
        "n_syms": len(symbols),
        "n": len(trades),
        "wr": 100 * len(wins) / len(trades) if trades else 0.0,
        "pf": (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "ret_pct": net / CAPITAL * 100,
        "pct_day": net / CAPITAL * 100 / days,
        "maxdd": maxdd * 100,
        "tpd": len(trades) / days,
        "final_eq": cash,
    }


def _signal(cfg, sym, b, state, vol_ok, px, o, hi, lo, atr):
    """Return (side, pot_rr, sl, tp) or None. Never uses Config A counter rule except ref."""
    st = state[sym]
    up, dn, mid = float(b["ch_upper"]), float(b["ch_lower"]), float(b["ch_mid"])
    width = max(up - dn, 1e-12)

    # update expand / squeeze state
    if bool(b.get("channel_expand", False)):
        st["trend"] = "up" if px > mid else "down"
        st["waiting"] = True
    if bool(b.get("squeeze", False)):
        st["sq"] = True

    strat = cfg.strategy

    # --- SAME-DIR after expand (opposite of Config A counter) ---
    if strat == "same_dir_expand":
        if not st["waiting"] or not st["trend"]:
            return None
        if bool(b.get("bands_parallel", False)):
            return None
        if not vol_ok:
            return None
        body = float(b.get("body_atr", 0) or 0)
        if not (0.3 <= body <= 1.2):
            return None
        follow = (st["trend"] == "up" and px > o) or (st["trend"] == "down" and px < o)
        if not follow:
            return None
        side = "long" if st["trend"] == "up" else "short"
        tp = up if side == "long" else dn
        sl = dn if side == "long" else up
        pot_rr = abs(tp - px) / max(abs(px - sl), 1e-12)
        if pot_rr < cfg.min_pot_rr:
            return None
        if cfg.exit_mode == "band":
            return side, pot_rr, sl, tp
        # atr
        sl2 = px - cfg.atr_sl_mult * atr if side == "long" else px + cfg.atr_sl_mult * atr
        tp2 = px + cfg.rr * abs(px - sl2) if side == "long" else px - cfg.rr * abs(px - sl2)
        return side, cfg.rr, sl2, tp2

    # --- Turtle breakout: close beyond prior Donchian ---
    if strat == "turtle_break":
        if not vol_ok:
            return None
        prev_up, prev_dn = float(b["prev_up"]), float(b["prev_dn"])
        if np.isnan(prev_up) or np.isnan(prev_dn):
            return None
        if px > prev_up and px > o:
            side = "long"
            sl = px - cfg.atr_sl_mult * atr
            tp = px + cfg.rr * (px - sl)
            return side, cfg.rr, sl, tp
        if px < prev_dn and px < o:
            side = "short"
            sl = px + cfg.atr_sl_mult * atr
            tp = px - cfg.rr * (sl - px)
            return side, cfg.rr, sl, tp
        return None

    # --- Bollinger fade ---
    if strat == "boll_fade":
        if not vol_ok:
            return None
        bb_up, bb_dn, bb_mid = float(b["bb_up"]), float(b["bb_dn"]), float(b["bb_mid"])
        if np.isnan(bb_up):
            return None
        if lo <= bb_dn and px > o:  # wick below, close reclaim
            side = "long"
            sl = lo - 0.2 * atr
            tp = bb_mid
            risk = px - sl
            if risk <= 0:
                return None
            pot_rr = abs(tp - px) / risk
            if pot_rr < 1.0:
                return None
            return side, pot_rr, sl, tp
        if hi >= bb_up and px < o:
            side = "short"
            sl = hi + 0.2 * atr
            tp = bb_mid
            risk = sl - px
            if risk <= 0:
                return None
            pot_rr = abs(px - tp) / risk
            if pot_rr < 1.0:
                return None
            return side, pot_rr, sl, tp
        return None

    # --- Keltner squeeze breakout ---
    if strat == "keltner_squeeze":
        if not st["sq"] or not vol_ok:
            return None
        # breakout after squeeze
        kel_up, kel_dn = float(b["kel_up"]), float(b["kel_dn"])
        if px > kel_up and px > o:
            side = "long"
            sl = px - cfg.atr_sl_mult * atr
            tp = px + cfg.rr * (px - sl)
            return side, cfg.rr, sl, tp
        if px < kel_dn and px < o:
            side = "short"
            sl = px + cfg.atr_sl_mult * atr
            tp = px - cfg.rr * (sl - px)
            return side, cfg.rr, sl, tp
        return None

    # --- RSI extreme fade ---
    if strat == "rsi_fade":
        rsi = float(b["rsi"])
        if np.isnan(rsi) or not vol_ok:
            return None
        if rsi < cfg.rsi_lo and px > o:
            side = "long"
            sl = px - cfg.atr_sl_mult * atr
            tp = px + cfg.rr * (px - sl)
            return side, cfg.rr, sl, tp
        if rsi > cfg.rsi_hi and px < o:
            side = "short"
            sl = px + cfg.atr_sl_mult * atr
            tp = px - cfg.rr * (sl - px)
            return side, cfg.rr, sl, tp
        return None

    # --- Z-score mean reversion ---
    if strat == "zscore_fade":
        z = float(b["z20"])
        if np.isnan(z) or not vol_ok:
            return None
        if z <= -cfg.z_th and px > o:
            side = "long"
            sl = px - cfg.atr_sl_mult * atr
            tp = float(b["bb_mid"])
            risk = px - sl
            if risk <= 0:
                return None
            return side, abs(tp - px) / risk, sl, tp
        if z >= cfg.z_th and px < o:
            side = "short"
            sl = px + cfg.atr_sl_mult * atr
            tp = float(b["bb_mid"])
            risk = sl - px
            if risk <= 0:
                return None
            return side, abs(px - tp) / risk, sl, tp
        return None

    # --- ROC momentum ---
    if strat == "roc_mom":
        roc = float(b["roc20"])
        prev_roc = float(b["roc20"])  # need shift — use di as proxy if missing
        if np.isnan(roc) or not vol_ok:
            return None
        # cross approx: roc strong + price vs sma50
        sma50 = float(b["sma50"])
        if np.isnan(sma50):
            return None
        if roc > 2 and px > sma50 and px > o:
            side = "long"
            sl = px - cfg.atr_sl_mult * atr
            tp = px + cfg.rr * (px - sl)
            return side, cfg.rr, sl, tp
        if roc < -2 and px < sma50 and px < o:
            side = "short"
            sl = px + cfg.atr_sl_mult * atr
            tp = px - cfg.rr * (sl - px)
            return side, cfg.rr, sl, tp
        return None

    # --- DI cross momentum ---
    if strat == "di_cross":
        if not vol_ok:
            return None
        di = float(b["di_diff"])
        # use sign of di_diff with body confirm
        if di > 5 and px > o and float(b.get("body_atr", 0) or 0) >= 0.3:
            side = "long"
            sl = px - cfg.atr_sl_mult * atr
            tp = px + cfg.rr * (px - sl)
            return side, cfg.rr, sl, tp
        if di < -5 and px < o and float(b.get("body_atr", 0) or 0) >= 0.3:
            side = "short"
            sl = px + cfg.atr_sl_mult * atr
            tp = px - cfg.rr * (sl - px)
            return side, cfg.rr, sl, tp
        return None

    # --- HA flip ---
    if strat == "ha_flip":
        if not vol_ok:
            return None
        bull = bool(b["ha_bull"])
        prev = b["ha_bull_prev"]
        if pd.isna(prev):
            return None
        if bull and not bool(prev):
            side = "long"
            sl = px - cfg.atr_sl_mult * atr
            tp = px + cfg.rr * (px - sl)
            return side, cfg.rr, sl, tp
        if (not bull) and bool(prev):
            side = "short"
            sl = px + cfg.atr_sl_mult * atr
            tp = px - cfg.rr * (sl - px)
            return side, cfg.rr, sl, tp
        return None

    # --- NR7 breakout ---
    if strat == "nr7_break":
        if not vol_ok:
            return None
        # previous bar NR7 → this bar breaks range of that bar — approximate: current nr7 false and wide
        if bool(b["nr7"]):
            return None
        # break using channel
        if px > up and px > o:
            side = "long"
            sl = px - cfg.atr_sl_mult * atr
            tp = px + cfg.rr * (px - sl)
            return side, cfg.rr, sl, tp
        if px < dn and px < o:
            side = "short"
            sl = px + cfg.atr_sl_mult * atr
            tp = px - cfg.rr * (sl - px)
            return side, cfg.rr, sl, tp
        return None

    # --- Vol climax fade (long lower wick / short upper wick on huge vol) ---
    if strat == "vol_climax_fade":
        vr = float(b.get("vol_ratio", 0) or 0)
        if vr < 2.0:
            return None
        clv = float(b.get("clv", 0.5) or 0.5)
        rng = hi - lo
        if rng <= 0:
            return None
        # long: big sell climax — close in top of range
        if clv >= 0.7 and px > o:
            side = "long"
            sl = lo - 0.1 * atr
            tp = px + cfg.rr * (px - sl)
            return side, cfg.rr, sl, tp
        if clv <= 0.3 and px < o:
            side = "short"
            sl = hi + 0.1 * atr
            tp = px - cfg.rr * (sl - px)
            return side, cfg.rr, sl, tp
        return None

    # --- Trend SMA pullback (price touch sma50 in uptrend sma50>sma200) ---
    if strat == "sma_pullback":
        sma50, sma200 = float(b["sma50"]), float(b["sma200"])
        if np.isnan(sma50) or np.isnan(sma200) or not vol_ok:
            return None
        if sma50 > sma200 and lo <= sma50 <= hi and px >= sma50 and px > o:
            side = "long"
            sl = min(sma200, lo) - 0.25 * atr
            if sl >= px:
                return None
            tp = px + cfg.rr * (px - sl)
            return side, cfg.rr, sl, tp
        if sma50 < sma200 and lo <= sma50 <= hi and px <= sma50 and px < o:
            side = "short"
            sl = max(sma200, hi) + 0.25 * atr
            if sl <= px:
                return None
            tp = px - cfg.rr * (sl - px)
            return side, cfg.rr, sl, tp
        return None

    return None


def candidate_cfgs() -> list[PortCfg]:
    """Diverse strategies — none is Config A counter-candle."""
    out: list[PortCfg] = []
    # same-dir expand — structurally opposite entry to live bot
    out.append(PortCfg("same_dir_expand+band", "same_dir_expand", exit_mode="band", min_vol=1.2))
    out.append(PortCfg("same_dir_expand+band vol1.0", "same_dir_expand", exit_mode="band", min_vol=1.0))
    out.append(PortCfg("same_dir_expand+atrRR2", "same_dir_expand", exit_mode="atr_rr", rr=2.0, atr_sl_mult=1.5))
    out.append(PortCfg("same_dir_expand+atrRR3", "same_dir_expand", exit_mode="atr_rr", rr=3.0, atr_sl_mult=1.2))

    for rr in (1.5, 2.0, 2.5, 3.0):
        out.append(PortCfg(f"turtle_break RR{rr:g}", "turtle_break", exit_mode="atr_rr", rr=rr, atr_sl_mult=1.5))
    out.append(PortCfg("turtle_break RR2 sl2", "turtle_break", exit_mode="atr_rr", rr=2.0, atr_sl_mult=2.0))

    out.append(PortCfg("boll_fade→mid", "boll_fade", exit_mode="bb_mid", min_vol=1.2))
    out.append(PortCfg("boll_fade→mid vol1.5", "boll_fade", exit_mode="bb_mid", min_vol=1.5))

    for rr in (2.0, 3.0):
        out.append(PortCfg(f"keltner_sq RR{rr:g}", "keltner_squeeze", exit_mode="atr_rr", rr=rr))

    for rr in (1.5, 2.0, 3.0):
        out.append(PortCfg(f"rsi_fade RR{rr:g}", "rsi_fade", exit_mode="atr_rr", rr=rr, atr_sl_mult=1.2))
        out.append(PortCfg(f"zscore_fade RR{rr:g}", "zscore_fade", exit_mode="atr_rr", rr=rr))
        out.append(PortCfg(f"roc_mom RR{rr:g}", "roc_mom", exit_mode="atr_rr", rr=rr))
        out.append(PortCfg(f"di_cross RR{rr:g}", "di_cross", exit_mode="atr_rr", rr=rr))
        out.append(PortCfg(f"ha_flip RR{rr:g}", "ha_flip", exit_mode="atr_rr", rr=rr))
        out.append(PortCfg(f"nr7_break RR{rr:g}", "nr7_break", exit_mode="atr_rr", rr=rr))
        out.append(PortCfg(f"vol_climax RR{rr:g}", "vol_climax_fade", exit_mode="atr_rr", rr=rr, atr_sl_mult=1.0))
        out.append(PortCfg(f"sma_pullback RR{rr:g}", "sma_pullback", exit_mode="atr_rr", rr=rr))

    return out


def beats(r: dict, base: dict) -> bool:
    return r["pf"] >= base["pf"] + 0.03 and r["maxdd"] <= base["maxdd"] + 2.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", default="365,1095")
    parser.add_argument("--quick", action="store_true", help="fewer candidates")
    args = parser.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]

    cfgs = candidate_cfgs()
    if args.quick:
        cfgs = [c for c in cfgs if c.strategy in (
            "same_dir_expand", "turtle_break", "boll_fade", "keltner_squeeze",
            "rsi_fade", "sma_pullback", "vol_climax_fade",
        )][:18]

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Research: beat Config A (non-Donchian-counter strategies)",
        "",
        f"- Generated: **{now}**",
        "- Script: `scripts/research_beat_config_a.py` (**does not modify live bot**)",
        "- Yardstick: same 20-coin shared wallet, cache `data/bt_klines_15m/`",
        f"- Baseline A 365d: PF **{BASE_365['pf']}** · MaxDD **{BASE_365['maxdd']}%** · WR **{BASE_365['wr']}%**",
        f"- Baseline A 1095d: PF **{BASE_1095['pf']}** · MaxDD **{BASE_1095['maxdd']}%**",
        "- **Strict beat**: PF ≥ baseline+0.03 AND MaxDD ≤ baseline+2pp on **both** windows",
        "- All candidates below use **different entry** from live counter-candle Config A",
        "",
    ]

    by_window: dict[int, list[dict]] = {}
    for days in windows:
        print(f"\n{'='*70}\nWINDOW {days}d · {len(cfgs)} candidates\n{'='*70}", flush=True)
        raw, eval_start, eval_end = load_pool(days)
        # verify baseline A still matches
        base_run = run_cfg(
            Cfg("A baseline verify", period=20, min_vol=1.2, min_pot_rr=0.5,
                margin_pct=0.01, max_open=10, size_mult_cap=2.0),
            raw, eval_start,
        )
        print(
            f"  VERIFY A: PF={base_run['pf']:.2f} WR={base_run['wr']:.1f}% "
            f"ret={base_run['ret_pct']:+.1f}% MaxDD={base_run['maxdd']:.1f}% n={base_run['n']}",
            flush=True,
        )
        rows = []
        base_ref = BASE_365 if days <= 400 else BASE_1095
        for c in cfgs:
            print(f"  run {c.name}...", flush=True)
            try:
                r = run_strategy(c, raw, eval_start)
            except Exception as e:
                print(f"    FAIL {e}", flush=True)
                continue
            r["window_days"] = days
            r["beat"] = beats(r, base_ref)
            rows.append(r)
            mark = " ★BEAT" if r["beat"] else ""
            print(
                f"    PF={r['pf']:.2f} WR={r['wr']:.1f}% ret={r['ret_pct']:+.1f}% "
                f"MaxDD={r['maxdd']:.1f}% n={r['n']} t/d={r['tpd']:.1f}{mark}",
                flush=True,
            )
        by_window[days] = rows

        lines += [
            f"## {days}d · verify A PF={base_run['pf']:.2f} MaxDD={base_run['maxdd']:.1f}%",
            "",
            "| Rank | Strategy | PF | WR | Return | MaxDD | n | t/d | vs A |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        ranked = sorted(rows, key=lambda x: (x["pf"], -x["maxdd"], x["ret_pct"]), reverse=True)
        for i, r in enumerate(ranked, 1):
            vs = []
            if r["pf"] > base_ref["pf"]:
                vs.append(f"PF+{r['pf']-base_ref['pf']:.2f}")
            if r["maxdd"] < base_ref["maxdd"]:
                vs.append(f"DD{r['maxdd']-base_ref['maxdd']:+.1f}")
            if r["beat"]:
                vs.append("**STRICT BEAT**")
            lines.append(
                f"| {i} | {r['name']} | {r['pf']:.2f} | {r['wr']:.1f}% | {r['ret_pct']:+.1f}% | "
                f"{r['maxdd']:.1f}% | {r['n']} | {r['tpd']:.1f} | {', '.join(vs) or '—'} |"
            )
        lines.append("")

    # Cross-window winners
    if 365 in by_window and 1095 in by_window:
        m365 = {r["name"]: r for r in by_window[365]}
        m1095 = {r["name"]: r for r in by_window[1095]}
        dual = []
        for name, a in m365.items():
            b = m1095.get(name)
            if not b:
                continue
            if beats(a, BASE_365) and beats(b, BASE_1095):
                dual.append((name, a, b))
        lines += ["## Strict dual-window beats", ""]
        if dual:
            for name, a, b in dual:
                lines.append(
                    f"- **{name}**: 365d PF {a['pf']:.2f} DD {a['maxdd']:.1f}% · "
                    f"1095d PF {b['pf']:.2f} DD {b['maxdd']:.1f}%"
                )
        else:
            lines.append("- **None** yet — continuing research required.")
        lines.append("")

        # Best PF on 365 even if not strict beat
        best = max(by_window[365], key=lambda x: x["pf"])
        lines += [
            "## Best PF on 365d (may not beat A)",
            "",
            f"- **{best['name']}**: PF **{best['pf']:.2f}** · WR {best['wr']:.1f}% · "
            f"ret {best['ret_pct']:+.1f}% · MaxDD {best['maxdd']:.1f}%",
            "",
        ]

    out = DOCS / "research_beat_config_a.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
