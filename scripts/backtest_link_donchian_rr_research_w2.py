#!/usr/bin/env python3
"""RR research wave-2 — more ways beyond min_rr / fixed-R / time-stop.

New ideas
---------
1. Deep pullback filters (channel position / beyond mid)
2. Min TP distance in ATR
3. Width regime filter
4. Wait N bars after trend / require 2nd counter candle
5. SL at mid@entry (tighter risk)
6. Exit when bands turn parallel again
7. Scale-out 50% @ mid, runner @ band
8. ATR chandelier trail after arm
9. Combos with min_rr
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
SYMBOL = "LINKUSDT"
INTERVAL = "15m"
LOOKBACK_DAYS = 365
DONCHIAN_PERIOD = 20
SLOPE_LOOKBACK = 5
PARALLEL_SLOPE_TOL = 0.015
CAPITAL = 1000.0
MARGIN_PCT = 0.005
LEVERAGE = 10.0
FEE = 0.0004
ATR_PERIOD = 14

_INTERVAL_MS = {"5m": 5 * 60 * 1000, "15m": 15 * 60 * 1000}


def fetch_klines(interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    bar_ms = _INTERVAL_MS[interval]
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": interval,
            "startTime": str(cursor),
            "endTime": str(end_ms),
            "limit": "1500",
        }
        url = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "donchian-rr-w2/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rows = json.loads(resp.read().decode())
                break
            except Exception:
                if attempt < 5:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
        time.sleep(0.15)
        if not rows:
            break
        for r in rows:
            ts = int(r[0])
            if start_ms <= ts < end_ms:
                out.append({"ts": ts, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])})
        last_ts = int(rows[-1][0])
        nxt = last_ts + bar_ms
        if nxt <= cursor:
            break
        cursor = nxt
    return pd.DataFrame(out).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["dc_upper"] = out["high"].rolling(DONCHIAN_PERIOD, min_periods=DONCHIAN_PERIOD).max()
    out["dc_lower"] = out["low"].rolling(DONCHIAN_PERIOD, min_periods=DONCHIAN_PERIOD).min()
    out["dc_middle"] = (out["dc_upper"] + out["dc_lower"]) / 2.0
    out["dc_width"] = out["dc_upper"] - out["dc_lower"]
    prev = out["close"].shift(1)
    tr = pd.concat(
        [out["high"] - out["low"], (out["high"] - prev).abs(), (out["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()

    upper = out["dc_upper"].to_numpy()
    lower = out["dc_lower"].to_numpy()
    closes = out["close"].to_numpy()
    n = len(out)
    parallel = np.zeros(n, dtype=bool)

    def slope(series: np.ndarray, i: int, ref: float) -> float:
        if i < SLOPE_LOOKBACK or ref <= 0:
            return 0.0
        return (series[i] - series[i - SLOPE_LOOKBACK]) / SLOPE_LOOKBACK / ref * 100.0

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        ref = closes[i]
        parallel[i] = abs(slope(upper, i, ref) - slope(lower, i, ref)) <= PARALLEL_SLOPE_TOL
    out["bands_parallel"] = parallel
    prev_p = np.roll(parallel, 1)
    prev_p[0] = False
    out["parallel_exit"] = prev_p & (~parallel)
    out["parallel_enter"] = (~prev_p) & parallel  # became parallel this bar
    return out


def _pnl(side: str, entry: float, exit_px: float, qty: float) -> tuple[float, float]:
    if side == "long":
        gross = (exit_px - entry) * qty
    else:
        gross = (entry - exit_px) * qty
    fee = (entry + exit_px) * qty * FEE
    return gross - fee, fee


@dataclass(frozen=True)
class Variant:
    name: str
    note: str
    mode: str = "baseline"  # baseline | scale_mid | exit_parallel | sl_mid | atr_trail
    min_rr: float = 0.0
    max_chan_pos: float = 1.0  # long: (px-lo)/w <= this; 1=off
    require_beyond_mid: bool = False
    min_tp_atr: float = 0.0
    min_width_pct: float = 0.0
    max_width_pct: float = 99.0
    wait_bars: int = 0
    need_second_counter: bool = False
    sl_pct: float = 0.0
    atr_trail_mult: float = 0.0
    arm_atr: float = 0.0  # arm trail after this much favorable ATR move


def run_variant(df: pd.DataFrame, v: Variant) -> dict:
    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    tss = df["ts"].to_numpy()
    upper = df["dc_upper"].to_numpy()
    lower = df["dc_lower"].to_numpy()
    middle = df["dc_middle"].to_numpy()
    width = df["dc_width"].to_numpy()
    parallel = df["bands_parallel"].to_numpy()
    parallel_exit = df["parallel_exit"].to_numpy()
    parallel_enter = df["parallel_enter"].to_numpy()
    atr = df["atr"].to_numpy()
    n = len(df)

    trend: str | None = None
    trend_i: int | None = None
    waiting_entry = False
    counter_streak = 0
    opens_pos: list[dict] = []
    trades: list[dict] = []
    cash = CAPITAL
    nid = 1
    skipped = {"rr": 0, "chan": 0, "mid": 0, "tp_atr": 0, "width": 0, "wait": 0, "counter2": 0, "parallel": 0}

    def locked() -> float:
        return sum(t["margin"] for t in opens_pos)

    def close_leg(t: dict, qty: float, margin: float, exit_px: float, ts: int, reason: str, tag: str) -> None:
        nonlocal cash, nid
        pnl, fee = _pnl(t["side"], t["entry"], exit_px, qty)
        cash += margin + pnl
        risk = abs(t["entry"] - t["sl0"]) or 1e-12
        r_mult = ((exit_px - t["entry"]) if t["side"] == "long" else (t["entry"] - exit_px)) / risk
        trades.append(
            {
                "id": nid,
                "side": t["side"],
                "entry": t["entry"],
                "exit_px": exit_px,
                "exit_ts": ts,
                "entry_ts": t["entry_ts"],
                "pnl": pnl,
                "fee": fee,
                "reason": reason,
                "tag": tag,
                "r_mult": r_mult,
                "qty": qty,
                "margin": margin,
            }
        )
        nid += 1

    def try_enter(side: str, px: float, ts: int, i: int) -> bool:
        nonlocal cash
        if opens_pos:
            return False
        up, lo, mid = float(upper[i]), float(lower[i]), float(middle[i])
        w = float(width[i]) if not np.isnan(width[i]) else 0.0
        a = float(atr[i]) if not np.isnan(atr[i]) else 0.0
        if w <= 1e-12:
            return False

        if v.wait_bars > 0 and trend_i is not None and (i - trend_i) < v.wait_bars:
            skipped["wait"] += 1
            return False

        width_pct = w / px * 100.0
        if width_pct < v.min_width_pct or width_pct > v.max_width_pct:
            skipped["width"] += 1
            return False

        if v.require_beyond_mid:
            if side == "long" and px >= mid:
                skipped["mid"] += 1
                return False
            if side == "short" and px <= mid:
                skipped["mid"] += 1
                return False

        chan_pos = (px - lo) / w if side == "long" else (up - px) / w
        if chan_pos > v.max_chan_pos:
            skipped["chan"] += 1
            return False

        tp_near = up if side == "long" else lo
        sl_opp = lo if side == "long" else up
        dist_tp = abs(tp_near - px)
        dist_sl = abs(px - sl_opp)
        pot_rr = dist_tp / dist_sl if dist_sl > 1e-12 else 0.0
        if v.min_rr > 0 and pot_rr < v.min_rr:
            skipped["rr"] += 1
            return False
        if v.min_tp_atr > 0 and a > 0 and dist_tp < v.min_tp_atr * a:
            skipped["tp_atr"] += 1
            return False

        if v.mode == "sl_mid":
            sl0 = mid
        elif v.sl_pct > 0:
            sl0 = px * (1 - v.sl_pct) if side == "long" else px * (1 + v.sl_pct)
        else:
            sl0 = sl_opp

        eq = max(cash + locked(), 0.0)
        notional = min(eq * MARGIN_PCT * LEVERAGE, cash * LEVERAGE)
        if notional < 1e-6:
            return False
        margin = notional / LEVERAGE
        if cash < margin - 1e-12:
            return False
        qty = notional / px
        cash -= margin
        opens_pos.append(
            {
                "side": side,
                "entry": px,
                "entry_ts": ts,
                "entry_i": i,
                "qty": qty,
                "margin": margin,
                "notional": notional,
                "sl0": sl0,
                "cur_sl": sl0 if (v.mode == "sl_mid" or v.sl_pct > 0) else None,
                "scaled": False,
                "arm_trail": False,
                "ext": px,  # extreme for chandelier
                "pot_rr": pot_rr,
            }
        )
        return True

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        px = float(closes[i])
        hi = float(highs[i])
        lo = float(lows[i])
        o = float(opens[i])
        ts = int(tss[i])
        up_b, lo_b, mid_b = float(upper[i]), float(lower[i]), float(middle[i])
        a = float(atr[i]) if not np.isnan(atr[i]) else 0.0

        if opens_pos:
            t = opens_pos[0]
            side = t["side"]
            live_tp = up_b if side == "long" else lo_b

            # Update chandelier extreme / trail
            if v.mode == "atr_trail" and a > 0:
                if side == "long":
                    t["ext"] = max(t["ext"], hi)
                    move = t["ext"] - t["entry"]
                    if move >= v.arm_atr * a:
                        t["arm_trail"] = True
                    if t["arm_trail"]:
                        trail = t["ext"] - v.atr_trail_mult * a
                        t["cur_sl"] = max(t["cur_sl"] or -1e18, trail)
                else:
                    t["ext"] = min(t["ext"], lo)
                    move = t["entry"] - t["ext"]
                    if move >= v.arm_atr * a:
                        t["arm_trail"] = True
                    if t["arm_trail"]:
                        trail = t["ext"] + v.atr_trail_mult * a
                        cur = t["cur_sl"]
                        t["cur_sl"] = min(cur, trail) if cur is not None else trail

            # Scale-out at mid
            if v.mode == "scale_mid" and not t["scaled"]:
                hit_mid = (side == "long" and hi >= mid_b) or (side == "short" and lo <= mid_b)
                if hit_mid:
                    half_q = t["qty"] * 0.5
                    half_m = t["margin"] * 0.5
                    close_leg(t, half_q, half_m, mid_b, ts, "TP_MID", "scale1")
                    t["qty"] -= half_q
                    t["margin"] -= half_m
                    t["scaled"] = True
                    # move SL to BE for runner
                    t["cur_sl"] = t["entry"]

            sl_hit = False
            sl_px = 0.0
            sl_reason = ""
            if t.get("cur_sl") is not None:
                sl = t["cur_sl"]
                if side == "long" and lo <= sl:
                    sl_hit, sl_px, sl_reason = True, sl, "SL"
                if side == "short" and hi >= sl:
                    sl_hit, sl_px, sl_reason = True, sl, "SL"

            tp_hit = (side == "long" and hi >= live_tp) or (side == "short" and lo <= live_tp)
            tp_px = live_tp

            par_hit = False
            if v.mode == "exit_parallel" and parallel_enter[i]:
                par_hit = True

            if sl_hit and tp_hit:
                close_leg(t, t["qty"], t["margin"], sl_px, ts, sl_reason, "full")
                opens_pos = []
            elif sl_hit:
                close_leg(t, t["qty"], t["margin"], sl_px, ts, sl_reason, "full")
                opens_pos = []
            elif tp_hit:
                close_leg(t, t["qty"], t["margin"], tp_px, ts, "TP_BAND", "full" if not t.get("scaled") else "runner")
                opens_pos = []
            elif par_hit:
                close_leg(t, t["qty"], t["margin"], px, ts, "EXIT_PARALLEL", "full")
                opens_pos = []

        if parallel_exit[i]:
            trend = "up" if px > mid_b else "down"
            trend_i = i
            waiting_entry = True
            counter_streak = 0

        if not opens_pos and waiting_entry and trend is not None:
            is_red = px < o
            is_green = px > o
            counter = (trend == "up" and is_red) or (trend == "down" and is_green)
            if counter:
                counter_streak += 1
                if parallel[i]:
                    skipped["parallel"] += 1
                else:
                    need = 2 if v.need_second_counter else 1
                    if counter_streak < need:
                        skipped["counter2"] += 1
                    else:
                        side = "long" if trend == "up" else "short"
                        if try_enter(side, px, ts, i):
                            waiting_entry = False
                            counter_streak = 0
            else:
                counter_streak = 0

        if opens_pos:
            waiting_entry = False

    last_px = float(closes[-1])
    last_ts = int(tss[-1])
    for t in list(opens_pos):
        close_leg(t, t["qty"], t["margin"], last_px, last_ts, "EOD", "full")
    opens_pos = []

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    avg_w = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_l = sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0
    rr = avg_w / abs(avg_l) if losses else float("inf")
    wr = len(wins) / len(trades) if trades else 0.0
    rr_be = (1 - wr) / wr if wr > 0 else float("inf")
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    pf = gross_w / gross_l if gross_l > 0 else float("inf")
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

    return {
        "name": v.name,
        "note": v.note,
        "n": len(trades),
        "wr": wr * 100,
        "rr": rr,
        "rr_be": rr_be,
        "rr_edge": (rr - rr_be) if wr > 0 and losses else float("nan"),
        "pf": pf,
        "exp": (sum(t["pnl"] for t in trades) / len(trades)) if trades else 0.0,
        "pnl": cash - CAPITAL,
        "avg_w": avg_w,
        "avg_l": avg_l,
        "max_l": min((t["pnl"] for t in losses), default=0.0),
        "skipped": skipped,
        "reasons": reasons,
    }


def variants() -> list[Variant]:
    return [
        Variant("baseline", "TP band (ref)"),
        Variant("min_rr_0.5", "wave1 best PnL", min_rr=0.5),
        Variant("min_rr_0.75", "wave1 best edge", min_rr=0.75),
        # deep pullback
        Variant("beyond_mid", "Chi vao khi px qua mid (pullback sau)", require_beyond_mid=True),
        Variant("chan_pos_0.4", "Long/short chi khi o 40% band phia SL", max_chan_pos=0.4),
        Variant("chan_pos_0.3", "Channel pos ≤0.3", max_chan_pos=0.3),
        Variant("chan_pos_0.5", "Channel pos ≤0.5", max_chan_pos=0.5),
        Variant("beyond_mid_rr0.5", "Beyond mid + min RR 0.5", require_beyond_mid=True, min_rr=0.5),
        Variant("chan0.4_rr0.5", "Chan≤0.4 + min RR 0.5", max_chan_pos=0.4, min_rr=0.5),
        # distance / width
        Variant("min_tp_1atr", "Dist TP ≥ 1×ATR", min_tp_atr=1.0),
        Variant("min_tp_1.5atr", "Dist TP ≥ 1.5×ATR", min_tp_atr=1.5),
        Variant("width_1_4pct", "Width 1–4% price", min_width_pct=1.0, max_width_pct=4.0),
        Variant("width_1_3pct", "Width 1–3% price", min_width_pct=1.0, max_width_pct=3.0),
        Variant("width_gt2_rr0.5", "Width≥2% + min RR 0.5", min_width_pct=2.0, min_rr=0.5),
        # timing entry
        Variant("wait_2", "Doi 2 nen sau trend moi vao", wait_bars=2),
        Variant("wait_4", "Doi 4 nen sau trend", wait_bars=4),
        Variant("counter_2", "Can 2 nen nguoc lien tiep", need_second_counter=True),
        Variant("wait2_rr0.5", "Wait 2 + min RR 0.5", wait_bars=2, min_rr=0.5),
        # risk geometry
        Variant("sl_mid", "SL = mid@entry, TP band", mode="sl_mid"),
        Variant("sl_mid_rr0.5", "SL mid + min RR 0.5 (vs opp)", mode="sl_mid", min_rr=0.5),
        Variant("sl0.8pct", "SL 0.8% + TP band", sl_pct=0.008),
        # exits
        Variant("exit_parallel", "Thoat khi band song song lai", mode="exit_parallel"),
        Variant("scale_mid", "50% @ mid, 50% @ band + BE", mode="scale_mid"),
        Variant("scale_mid_rr0.5", "Scale mid + min RR 0.5", mode="scale_mid", min_rr=0.5),
        Variant("atr_trail_2", "Arm 0.5ATR, trail 2×ATR", mode="atr_trail", atr_trail_mult=2.0, arm_atr=0.5),
        Variant("atr_trail_15", "Arm 0.5ATR, trail 1.5×ATR", mode="atr_trail", atr_trail_mult=1.5, arm_atr=0.5),
        Variant("atr_trail_1", "Arm 1ATR, trail 1×ATR", mode="atr_trail", atr_trail_mult=1.0, arm_atr=1.0),
        # combos
        Variant("deep_combo", "Beyond mid + chan≤0.45 + RR≥0.5", require_beyond_mid=True, max_chan_pos=0.45, min_rr=0.5),
        Variant("quality_combo", "RR≥0.5 + TP≥1ATR + width 1–4%", min_rr=0.5, min_tp_atr=1.0, min_width_pct=1.0, max_width_pct=4.0),
        Variant("patient_combo", "Wait2 + counter2 + RR≥0.5", wait_bars=2, need_second_counter=True, min_rr=0.5),
    ]


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    bar_ms = _INTERVAL_MS[INTERVAL]
    last_closed = (now_ms // bar_ms) * bar_ms
    warmup = (DONCHIAN_PERIOD + SLOPE_LOOKBACK + ATR_PERIOD + 5) * bar_ms
    fetch_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000 - warmup
    window_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000

    print(f"Fetching {SYMBOL}...", flush=True)
    df = fetch_klines(INTERVAL, fetch_from, last_closed)
    df = prepare(df)
    df = df[df["ts"] >= window_from].copy().reset_index(drop=True)
    first, last = df.iloc[0], df.iloc[-1]

    rows = []
    for v in variants():
        print(f"  {v.name}...", flush=True)
        rows.append(run_variant(df, v))

    base = next(s for s in rows if s["name"] == "baseline")
    pool = [s for s in rows if s["n"] >= 50 and s["rr_edge"] == s["rr_edge"]]
    edge_pos = [s for s in pool if s["rr_edge"] > 0 and s["pf"] >= 1]
    best_edge = max(pool, key=lambda s: s["rr_edge"])
    best_rr = max(edge_pos or pool, key=lambda s: s["rr"])
    best_pnl = max(rows, key=lambda s: s["pnl"])
    tradeoff = [
        s for s in pool
        if s["name"] != "baseline" and s["rr"] >= base["rr"] and s["pnl"] >= base["pnl"] * 0.8 and s["rr_edge"] > 0
    ]
    best_tf = max(tradeoff, key=lambda s: (s["rr_edge"], s["rr"], s["pnl"])) if tradeoff else best_edge

    ranked = sorted(
        rows,
        key=lambda s: (0 if s["n"] >= 50 else -1, s["rr_edge"] if s["rr_edge"] == s["rr_edge"] else -999, s["rr"]),
        reverse=True,
    )

    lines = [
        f"# RR research wave-2 — {SYMBOL} {INTERVAL} {LOOKBACK_DAYS}d",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Cua so: {_local(int(first.ts))} -> {_local(int(last.ts))}",
        f"- Gia {first.close:.4f} -> {last.close:.4f} ({(last.close/first.close-1)*100:+.2f}%)",
        "- Wave-2: pullback sau, channel pos, ATR distance, width regime, wait/counter2, SL mid, exit parallel, scale-out, ATR trail",
        "",
        "## Bang so sanh (sort RR edge)",
        "",
        "| Rank | Variant | n | WR% | RR | Edge | PF | Exp | PnL | AvgW | AvgL | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, s in enumerate(ranked, 1):
        edge = s["rr_edge"]
        edge_s = f"{edge:+.3f}" if edge == edge else "nan"
        lines.append(
            f"| {i} | `{s['name']}` | {s['n']} | {s['wr']:.1f} | **{s['rr']:.3f}** | **{edge_s}** | "
            f"{s['pf']:.2f} | {s['exp']:+.4f} | **{s['pnl']:+.1f}** | {s['avg_w']:+.3f} | {s['avg_l']:+.3f} | {s['note']} |"
        )

    lines += [
        "",
        "## Ket luan wave-2",
        "",
        f"- Baseline: RR {base['rr']:.3f} · edge {base['rr_edge']:+.3f} · PnL {base['pnl']:+.1f}",
        f"- Best edge (n≥50): `{best_edge['name']}` RR {best_edge['rr']:.3f} edge {best_edge['rr_edge']:+.3f} PnL {best_edge['pnl']:+.1f}",
        f"- Best RR (edge>0, PF≥1): `{best_rr['name']}` RR {best_rr['rr']:.3f} PnL {best_rr['pnl']:+.1f}",
        f"- Best tradeoff: `{best_tf['name']}` RR {best_tf['rr']:.3f} edge {best_tf['rr_edge']:+.3f} PnL {best_tf['pnl']:+.1f}",
        f"- Best PnL: `{best_pnl['name']}` PnL {best_pnl['pnl']:+.1f} RR {best_pnl['rr']:.3f}",
        "",
        "### Huong di them (so voi wave-1)",
        "",
        "- Pullback sau (`beyond_mid` / `chan_pos_*`): nang pot RR bang cach vao sau hon trong channel.",
        "- `min_tp_*atr` / width regime: bo setup TP qua sat.",
        "- `scale_mid`: chot 1/2 som — RR theo leg co the doi, xem PnL tong.",
        "- `atr_trail` / `exit_parallel` / `sl_mid`: doi hinh hoc risk/reward, de giam WR.",
        "",
        "## Skip / reason (top)",
        "",
    ]
    show = {base["name"], best_edge["name"], best_rr["name"], best_tf["name"], best_pnl["name"], "min_rr_0.5", "min_rr_0.75"}
    for s in ranked:
        if s["name"] not in show and not (s["n"] >= 50 and s["rr_edge"] == s["rr_edge"] and s["rr_edge"] >= base["rr_edge"]):
            continue
        sk = ", ".join(f"{k}:{c}" for k, c in s["skipped"].items() if c)
        rs = ", ".join(f"{k}:{c}" for k, c in sorted(s["reasons"].items(), key=lambda x: -x[1]))
        lines.append(f"- `{s['name']}`: skip[{sk}] · {rs}")

    out = ROOT / "docs" / f"backtest_{SYMBOL}_donchian_rr_research_w2_{INTERVAL}_{LOOKBACK_DAYS}d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}", flush=True)
    print(
        f"baseline RR={base['rr']:.3f} | best_edge={best_edge['name']} {best_edge['rr_edge']:+.3f} | "
        f"best_tf={best_tf['name']} RR={best_tf['rr']:.3f} | best_pnl={best_pnl['name']} {best_pnl['pnl']:+.1f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
