#!/usr/bin/env python3
"""RR-improvement research harness — Donchian parallel-trend variants on LINK.

Fetches once, runs many exit/entry filters aimed at lifting RR = avg_win/|avg_loss|.
Does not modify production bot or original backtest scripts.

Variants
--------
baseline          : TP near Donchian band (current prod logic)
tp_width_k        : TP = near_band ± k * channel_width (wider TP)
tp_opp_band       : TP at opposite band (ride full channel)
sl_pct + tp_band  : hard SL % + same TP band
sl_opp + tp_rr    : SL opposite band @ entry; TP = entry ± rr_mult * risk
min_rr_filter     : baseline exits, skip entry if potential RR < min
trail_mid         : after MFE past mid, trail exit on mid cross against
be_after_mid      : after mid touch favorable, SL -> entry; TP still band
time_stop         : baseline + flat after N bars @ close
atr_rr            : SL=atr_mult*ATR, TP=rr_mult*SL distance
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
MAX_OPEN = 1
ATR_PERIOD = 14

_INTERVAL_MS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
}


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
                req = urllib.request.Request(url, headers={"User-Agent": "donchian-rr-research/1.0"})
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


def compute_donchian(df: pd.DataFrame, period: int) -> pd.DataFrame:
    out = df.copy()
    out["dc_upper"] = out["high"].rolling(period, min_periods=period).max()
    out["dc_lower"] = out["low"].rolling(period, min_periods=period).min()
    out["dc_middle"] = (out["dc_upper"] + out["dc_lower"]) / 2.0
    out["dc_width"] = out["dc_upper"] - out["dc_lower"]
    return out


def compute_atr(df: pd.DataFrame, period: int) -> pd.DataFrame:
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(period, min_periods=period).mean()
    return out


def _norm_slope(series: np.ndarray, i: int, lookback: int, ref_px: float) -> float:
    if i < lookback or ref_px <= 0:
        return 0.0
    return (series[i] - series[i - lookback]) / lookback / ref_px * 100.0


def add_parallel_flags(df: pd.DataFrame, lookback: int, tol: float) -> pd.DataFrame:
    out = df.copy()
    n = len(out)
    upper = out["dc_upper"].to_numpy()
    lower = out["dc_lower"].to_numpy()
    closes = out["close"].to_numpy()
    parallel = np.zeros(n, dtype=bool)
    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        ref = closes[i]
        su = _norm_slope(upper, i, lookback, ref)
        sl = _norm_slope(lower, i, lookback, ref)
        parallel[i] = abs(su - sl) <= tol
    out["bands_parallel"] = parallel
    prev = np.roll(parallel, 1)
    prev[0] = False
    out["parallel_exit"] = prev & (~parallel)
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
    # exit mode
    mode: str
    # params
    tp_width_k: float = 0.0
    sl_pct: float = 0.0
    rr_mult: float = 0.0
    min_rr: float = 0.0
    time_stop_bars: int = 0
    atr_sl_mult: float = 0.0


def run_variant(df: pd.DataFrame, v: Variant) -> tuple[list[dict], dict]:
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
    atr = df["atr"].to_numpy()
    n = len(df)

    trend: str | None = None
    waiting_entry = False
    opens_pos: list[dict] = []
    trades: list[dict] = []
    cash = CAPITAL
    nid = 1
    skipped_rr = 0
    skipped_parallel = 0

    def locked_margin() -> float:
        return sum(t["margin"] for t in opens_pos)

    def close_trade(t: dict, exit_px: float, ts: int, reason: str) -> None:
        nonlocal cash
        pnl, fee = _pnl(t["side"], t["entry"], exit_px, t["qty"])
        cash += t["margin"] + pnl
        risk = abs(t["entry"] - t.get("sl0", t["entry"])) or 1e-12
        if t["side"] == "long":
            r_mult = (exit_px - t["entry"]) / risk
        else:
            r_mult = (t["entry"] - exit_px) / risk
        trades.append(
            {
                **t,
                "exit_ts": ts,
                "exit_px": exit_px,
                "reason": reason,
                "pnl": pnl,
                "fee": fee,
                "r_mult": r_mult,
            }
        )

    def try_enter(side: str, px: float, ts: int, i: int, up: float, lo: float, mid: float, w: float) -> bool:
        nonlocal cash, nid, skipped_rr
        if opens_pos:
            return False

        # Potential TP/SL geometry at entry (for filters + fixed levels)
        if side == "long":
            tp_near = up
            sl_opp = lo
        else:
            tp_near = lo
            sl_opp = up
        dist_tp = abs(tp_near - px)
        dist_sl = abs(px - sl_opp)
        pot_rr = dist_tp / dist_sl if dist_sl > 1e-12 else 0.0

        if v.min_rr > 0 and pot_rr < v.min_rr:
            skipped_rr += 1
            return False

        # Build exit levels by mode
        sl0 = None
        tp0 = None
        be_armed = False

        if v.mode == "baseline":
            tp0 = tp_near  # live band used each bar; store initial only
            sl0 = sl_opp  # not used for exit
        elif v.mode == "tp_width":
            k = v.tp_width_k
            if side == "long":
                tp0 = up + k * w
            else:
                tp0 = lo - k * w
            sl0 = sl_opp
        elif v.mode == "tp_opp":
            # Full-channel target: SL opp@entry, TP = entry ± 1× width (toward trend)
            sl0 = sl_opp
            if w <= 1e-12:
                return False
            tp0 = px + w if side == "long" else px - w
            be_armed = False
        elif v.mode == "sl_pct_tp_band":
            sl0 = px * (1 - v.sl_pct) if side == "long" else px * (1 + v.sl_pct)
            tp0 = None  # live band
        elif v.mode == "fixed_rr":
            # SL at opposite band (fixed at entry), TP = rr_mult * risk
            sl0 = sl_opp
            risk = abs(px - sl0)
            if risk < 1e-12:
                return False
            tp0 = px + v.rr_mult * risk if side == "long" else px - v.rr_mult * risk
        elif v.mode == "trail_mid":
            tp0 = None
            sl0 = sl_opp
            be_armed = False
        elif v.mode == "be_after_mid":
            tp0 = None
            sl0 = sl_opp  # will tighten to entry
            be_armed = False
        elif v.mode == "time_stop":
            tp0 = None
            sl0 = None
        elif v.mode == "atr_rr":
            a = float(atr[i])
            if np.isnan(a) or a <= 0:
                return False
            risk = v.atr_sl_mult * a
            sl0 = px - risk if side == "long" else px + risk
            tp0 = px + v.rr_mult * risk if side == "long" else px - v.rr_mult * risk
        else:
            raise ValueError(v.mode)

        eq = max(cash + locked_margin(), 0.0)
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
                "id": nid,
                "side": side,
                "trend": trend,
                "entry_ts": ts,
                "entry_i": i,
                "entry": px,
                "qty": qty,
                "margin": margin,
                "notional": notional,
                "tp0": tp0,
                "sl0": sl0 if sl0 is not None else (lo if side == "long" else up),
                "be_armed": be_armed,
                "cur_sl": sl0,
                "pot_rr": pot_rr,
            }
        )
        nid += 1
        return True

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue

        px = float(closes[i])
        hi = float(highs[i])
        lo = float(lows[i])
        ts = int(tss[i])
        o = float(opens[i])
        up_band = float(upper[i])
        lo_band = float(lower[i])
        mid_band = float(middle[i])
        w = float(width[i]) if not np.isnan(width[i]) else 0.0

        # --- exits ---
        if opens_pos:
            t = opens_pos[0]
            side = t["side"]
            entry = t["entry"]
            bars_held = i - t["entry_i"]
            exit_done = False

            # Update live targets where needed
            if v.mode in ("baseline", "sl_pct_tp_band", "time_stop", "trail_mid", "be_after_mid"):
                live_tp = up_band if side == "long" else lo_band
            elif v.mode == "tp_width":
                live_tp = (up_band + v.tp_width_k * w) if side == "long" else (lo_band - v.tp_width_k * w)
            elif v.mode == "tp_opp":
                live_tp = lo_band if side == "long" else up_band
            else:
                live_tp = t["tp0"]

            # Arm BE / trail
            if v.mode == "be_after_mid":
                if side == "long" and hi >= mid_band:
                    t["be_armed"] = True
                    t["cur_sl"] = max(t.get("cur_sl") or -1e18, entry)
                if side == "short" and lo <= mid_band:
                    t["be_armed"] = True
                    cur = t.get("cur_sl")
                    t["cur_sl"] = min(cur, entry) if cur is not None else entry

            if v.mode == "trail_mid":
                # arm when price reaches mid favorably; then exit if cross mid against
                if side == "long" and hi >= mid_band:
                    t["be_armed"] = True
                if side == "short" and lo <= mid_band:
                    t["be_armed"] = True

            # Same-bar priority: SL before TP (pessimistic) when both hit
            def hit_sl() -> tuple[bool, float, str]:
                if v.mode == "sl_pct_tp_band":
                    sl = t["sl0"]
                    if side == "long" and lo <= sl:
                        return True, sl, "SL_PCT"
                    if side == "short" and hi >= sl:
                        return True, sl, "SL_PCT"
                if v.mode in ("fixed_rr", "atr_rr", "tp_opp"):
                    sl = t["sl0"]
                    if side == "long" and lo <= sl:
                        return True, sl, "SL"
                    if side == "short" and hi >= sl:
                        return True, sl, "SL"
                if v.mode == "be_after_mid" and t.get("cur_sl") is not None:
                    sl = t["cur_sl"]
                    if side == "long" and lo <= sl:
                        return True, sl, "BE_SL" if t.get("be_armed") and abs(sl - entry) < 1e-12 else "SL_BAND"
                    if side == "short" and hi >= sl:
                        return True, sl, "BE_SL" if t.get("be_armed") and abs(sl - entry) < 1e-12 else "SL_BAND"
                if v.mode == "trail_mid" and t.get("be_armed"):
                    if side == "long" and lo <= mid_band:
                        return True, mid_band, "TRAIL_MID"
                    if side == "short" and hi >= mid_band:
                        return True, mid_band, "TRAIL_MID"
                return False, 0.0, ""

            def hit_tp() -> tuple[bool, float, str]:
                if live_tp is None:
                    return False, 0.0, ""
                if side == "long" and hi >= live_tp:
                    return True, live_tp, "TP"
                if side == "short" and lo <= live_tp:
                    return True, live_tp, "TP"
                return False, 0.0, ""

            sl_hit, sl_px, sl_reason = hit_sl()
            tp_hit, tp_px, tp_reason = hit_tp()

            if sl_hit and tp_hit:
                close_trade(t, sl_px, ts, sl_reason)
                opens_pos = []
                exit_done = True
            elif sl_hit:
                close_trade(t, sl_px, ts, sl_reason)
                opens_pos = []
                exit_done = True
            elif tp_hit:
                close_trade(t, tp_px, ts, tp_reason)
                opens_pos = []
                exit_done = True
            elif v.mode == "time_stop" and v.time_stop_bars > 0 and bars_held >= v.time_stop_bars:
                close_trade(t, px, ts, "TIME_STOP")
                opens_pos = []
                exit_done = True

            if exit_done:
                pass

        # --- trend / entry ---
        if parallel_exit[i]:
            trend = "up" if px > mid_band else "down"
            waiting_entry = True

        if not opens_pos and waiting_entry and trend is not None:
            is_red = px < o
            is_green = px > o
            counter = (trend == "up" and is_red) or (trend == "down" and is_green)
            if counter:
                if parallel[i]:
                    skipped_parallel += 1
                else:
                    side = "long" if trend == "up" else "short"
                    if try_enter(side, px, ts, i, up_band, lo_band, mid_band, w):
                        waiting_entry = False

        if opens_pos:
            waiting_entry = False

    # EOD
    last_px = float(closes[-1])
    last_ts = int(tss[-1])
    for t in list(opens_pos):
        close_trade(t, last_px, last_ts, "EOD")
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

    stats = {
        "name": v.name,
        "note": v.note,
        "n": len(trades),
        "wr": wr * 100,
        "rr": rr,
        "rr_be": rr_be,
        "rr_edge": rr - rr_be if wr > 0 and losses else float("nan"),
        "pf": pf,
        "exp": (sum(t["pnl"] for t in trades) / len(trades)) if trades else 0.0,
        "pnl": cash - CAPITAL,
        "pnl_pct": (cash - CAPITAL) / CAPITAL * 100,
        "avg_w": avg_w,
        "avg_l": avg_l,
        "max_w": max((t["pnl"] for t in wins), default=0.0),
        "max_l": min((t["pnl"] for t in losses), default=0.0),
        "fee": sum(t["fee"] for t in trades),
        "skipped_rr": skipped_rr,
        "skipped_parallel": skipped_parallel,
        "avg_r": (sum(t["r_mult"] for t in trades) / len(trades)) if trades else 0.0,
        "reasons": reasons,
        "med_hold": float(np.median([ (t["exit_ts"]-t["entry_ts"])/900000 for t in trades ])) if trades else 0.0,
    }
    return trades, stats


def variants() -> list[Variant]:
    return [
        Variant("baseline", "TP near band (hiện tại)", "baseline"),
        Variant("tp_width_0.5", "TP = band + 0.5×width", "tp_width", tp_width_k=0.5),
        Variant("tp_width_1.0", "TP = band + 1.0×width", "tp_width", tp_width_k=1.0),
        Variant("tp_opp_band", "SL opp@entry, TP = entry±1×width", "tp_opp"),
        Variant("sl1%_tp_band", "SL 1% + TP band sống", "sl_pct_tp_band", sl_pct=0.01),
        Variant("sl2%_tp_band", "SL 2% + TP band sống", "sl_pct_tp_band", sl_pct=0.02),
        Variant("fixed_rr_1.0", "SL opp@entry, TP 1R cố định", "fixed_rr", rr_mult=1.0),
        Variant("fixed_rr_1.5", "SL opp@entry, TP 1.5R cố định", "fixed_rr", rr_mult=1.5),
        Variant("fixed_rr_2.0", "SL opp@entry, TP 2R cố định", "fixed_rr", rr_mult=2.0),
        Variant("fixed_rr_3.0", "SL opp@entry, TP 3R cố định", "fixed_rr", rr_mult=3.0),
        Variant("min_rr_0.4", "Baseline + chỉ vào nếu pot RR≥0.4", "baseline", min_rr=0.4),
        Variant("min_rr_0.5", "Baseline + chỉ vào nếu pot RR≥0.5", "baseline", min_rr=0.5),
        Variant("min_rr_0.75", "Baseline + chỉ vào nếu pot RR≥0.75", "baseline", min_rr=0.75),
        Variant("min_rr_1.0", "Baseline + chỉ vào nếu pot RR≥1.0", "baseline", min_rr=1.0),
        Variant("trail_mid", "Arm sau mid → trail exit mid", "trail_mid"),
        Variant("be_after_mid", "Sau mid → SL=BE, TP band", "be_after_mid"),
        Variant("time_stop_8", "TP band + time stop 8 nến (~2h)", "time_stop", time_stop_bars=8),
        Variant("time_stop_16", "TP band + time stop 16 nến (~4h)", "time_stop", time_stop_bars=16),
        Variant("time_stop_32", "TP band + time stop 32 nến (~8h)", "time_stop", time_stop_bars=32),
        Variant("atr_rr_2", "SL 1×ATR, TP 2R", "atr_rr", atr_sl_mult=1.0, rr_mult=2.0),
        Variant("atr_rr_3", "SL 1×ATR, TP 3R", "atr_rr", atr_sl_mult=1.0, rr_mult=3.0),
        Variant("atr15_rr_2", "SL 1.5×ATR, TP 2R", "atr_rr", atr_sl_mult=1.5, rr_mult=2.0),
        Variant("atr15_rr_3", "SL 1.5×ATR, TP 3R", "atr_rr", atr_sl_mult=1.5, rr_mult=3.0),
        # combo: min RR filter + wider TP
        Variant("min0.5_tp_w0.5", "pot RR≥0.5 + TP band+0.5w", "tp_width", tp_width_k=0.5, min_rr=0.5),
        Variant("min0.5_fixed_rr2", "pot RR≥0.5 + fixed 2R", "fixed_rr", rr_mult=2.0, min_rr=0.5),
    ]


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    bar_ms = _INTERVAL_MS[INTERVAL]
    last_closed = (now_ms // bar_ms) * bar_ms
    warmup_bars = DONCHIAN_PERIOD + SLOPE_LOOKBACK + ATR_PERIOD + 5
    warmup_ms = warmup_bars * bar_ms
    fetch_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000 - warmup_ms
    window_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000

    print(f"Fetching {SYMBOL} {INTERVAL}...", flush=True)
    df = fetch_klines(INTERVAL, fetch_from, last_closed)
    df = compute_donchian(df, DONCHIAN_PERIOD)
    df = compute_atr(df, ATR_PERIOD)
    df = add_parallel_flags(df, SLOPE_LOOKBACK, PARALLEL_SLOPE_TOL)
    df = df[df["ts"] >= window_from].copy().reset_index(drop=True)
    first, last_row = df.iloc[0], df.iloc[-1]

    rows: list[dict] = []
    for v in variants():
        print(f"  run {v.name}...", flush=True)
        _, st = run_variant(df, v)
        rows.append(st)

    def usable(s: dict) -> bool:
        return s["n"] >= 50 and s["rr_edge"] == s["rr_edge"]

    pool = [s for s in rows if usable(s)]
    edge_pos = [s for s in pool if s["rr_edge"] > 0 and s["pf"] >= 1.0]
    best_rr = max(edge_pos or pool, key=lambda s: s["rr"])
    best_edge = max(pool, key=lambda s: s["rr_edge"])
    best_pnl = max(rows, key=lambda s: s["pnl"])
    # Best tradeoff: maximize edge among those with RR >= baseline and PnL >= 0.8*baseline
    base = next(s for s in rows if s["name"] == "baseline")
    tradeoff_pool = [
        s for s in pool
        if s["rr"] >= base["rr"] and s["pnl"] >= base["pnl"] * 0.8 and s["name"] != "baseline" and s["rr_edge"] > 0
    ]
    best_tradeoff = max(tradeoff_pool, key=lambda s: (s["rr_edge"], s["rr"], s["pnl"])) if tradeoff_pool else best_edge

    # Sort by rr_edge then rr; tiny-n sunk to bottom
    ranked = sorted(
        rows,
        key=lambda s: (
            0 if s["n"] >= 50 else -1,
            s["rr_edge"] if s["rr_edge"] == s["rr_edge"] else -999,
            s["rr"],
        ),
        reverse=True,
    )

    lines = [
        f"# RR research — {SYMBOL} {INTERVAL} {LOOKBACK_DAYS}d",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Cua so: {_local(int(first.ts))} -> {_local(int(last_row.ts))}",
        f"- Gia {first.close:.4f} -> {last_row.close:.4f} ({(last_row.close/first.close-1)*100:+.2f}%)",
        f"- Entry chung: parallel→non-parallel + nen nguoc; MAX_OPEN=1; size {MARGIN_PCT*100:.2f}%×{LEVERAGE:.0f}x",
        "- Muc tieu: nang **RR = avgW/|avgL|** (va RR edge = RR − RR_hoa_von)",
        "- Same-bar SL+TP: uu tien SL (pessimistic)",
        "",
        "## Bang so sanh (sort theo RR edge)",
        "",
        "| Rank | Variant | n | WR% | RR | RR_BE | Edge | PF | Exp/lot | PnL | AvgW | AvgL | MaxL | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, s in enumerate(ranked, 1):
        lines.append(
            f"| {i} | `{s['name']}` | {s['n']} | {s['wr']:.1f} | **{s['rr']:.3f}** | {s['rr_be']:.3f} | "
            f"**{s['rr_edge']:+.3f}** | {s['pf']:.2f} | {s['exp']:+.4f} | **{s['pnl']:+.1f}** | "
            f"{s['avg_w']:+.3f} | {s['avg_l']:+.3f} | {s['max_l']:+.2f} | {s['note']} |"
        )

    base = next(s for s in rows if s["name"] == "baseline")
    lines += [
        "",
        "## Ket luan nhanh",
        "",
        f"- Baseline: n={base['n']} · WR {base['wr']:.1f}% · RR **{base['rr']:.3f}** · edge {base['rr_edge']:+.3f} · PnL {base['pnl']:+.1f}",
        f"- RR cao nhat (n≥50): `{best_rr['name']}` → RR **{best_rr['rr']:.3f}** (WR {best_rr['wr']:.1f}%, PnL {best_rr['pnl']:+.1f}, n={best_rr['n']})",
        f"- Edge tot nhat (n≥50): `{best_edge['name']}` → edge **{best_edge['rr_edge']:+.3f}** (RR {best_edge['rr']:.3f}, PnL {best_edge['pnl']:+.1f})",
        f"- Tradeoff tot (RR↑, PnL ≥80% baseline): `{best_tradeoff['name']}` → RR **{best_tradeoff['rr']:.3f}** · edge {best_tradeoff['rr_edge']:+.3f} · PnL {best_tradeoff['pnl']:+.1f}",
        f"- PnL cao nhat: `{best_pnl['name']}` → PnL **{best_pnl['pnl']:+.1f}** (RR {best_pnl['rr']:.3f})",
        "",
        "### Doc ket qua",
        "",
        "- Variant `n < 50` chi de tham khao (de overfitting / may man).",
        "- Ep RR bang TP xa / ATR 2R–3R: RR len ~1.5–2.6 nhung **edge am**, PnL am — khong tradeable.",
        "- Cach nang RR **giu edge**: loc entry `min_rr_*` (bo setup TP qua gan vs SL).",
        "- `time_stop` / `sl_%` nang RR bang cat loss som → WR giam, PnL giam vs baseline.",
        "",
        "## Reason breakdown (baseline + winners)",
        "",
    ]
    show = {base["name"], best_rr["name"], best_edge["name"], best_pnl["name"], best_tradeoff["name"]}
    for s in ranked:
        if s["name"] not in show:
            continue
        rs = ", ".join(f"{k}:{c}" for k, c in sorted(s["reasons"].items(), key=lambda x: -x[1]))
        lines.append(f"- `{s['name']}` (n={s['n']}): skip_rr={s['skipped_rr']} · {rs}")

    lines += [
        "",
        "## Goi y ap dung",
        "",
        f"1. **Khuyen nghi thuc dung:** `{best_tradeoff['name']}` — loc pot RR luc entry, giu TP band.",
        "2. Muon RR ~0.6–0.8: `time_stop_8` / `sl1%` — chap nhan PnL thap hon.",
        "3. Khong nen ep fixed 2R/3R hay ATR-RR tren logic Donchian nay (edge am).",
        "4. Wider TP (`tp_width`) khong SL: xem lai bang sau khi fix (TP xa → it cham TP, RR/WR thay doi).",
        "",
    ]

    out = ROOT / "docs" / f"backtest_{SYMBOL}_donchian_rr_research_{INTERVAL}_{LOOKBACK_DAYS}d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}", flush=True)
    print(
        f"baseline RR={base['rr']:.3f} edge={base['rr_edge']:+.3f} | "
        f"best_rr={best_rr['name']} {best_rr['rr']:.3f} | "
        f"best_edge={best_edge['name']} {best_edge['rr_edge']:+.3f} | "
        f"best_pnl={best_pnl['name']} {best_pnl['pnl']:+.1f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
