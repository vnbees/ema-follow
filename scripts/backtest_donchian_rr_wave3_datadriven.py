#!/usr/bin/env python3
"""Data-driven RR/edge hunt from winning baseline behavior.

1) Run baseline on multi-coin, label each trade with entry features.
2) Find feature splits with higher expectancy / RR.
3) Backtest new rules derived from those splits (wave-3).
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
SYMBOLS = ["LINKUSDT", "HYPEUSDT", "BTWUSDT", "SUIUSDT", "DOGEUSDT", "SOLUSDT"]
INTERVAL = "15m"
LOOKBACK_DAYS = 365
DONCHIAN_PERIOD = 20
SLOPE_LOOKBACK = 5
PARALLEL_TOL = 0.015
CAPITAL = 1000.0
MARGIN_PCT = 0.005
LEVERAGE = 10.0
FEE = 0.0004
ATR_PERIOD = 14
BAR_MS = 15 * 60 * 1000


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": INTERVAL,
            "startTime": str(cursor),
            "endTime": str(end_ms),
            "limit": "1500",
        }
        url = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "donchian-w3/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rows = json.loads(resp.read().decode())
                break
            except Exception:
                if attempt < 5:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        time.sleep(0.1)
        if not rows:
            break
        for r in rows:
            ts = int(r[0])
            if start_ms <= ts < end_ms:
                out.append({"ts": ts, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])})
        nxt = int(rows[-1][0]) + BAR_MS
        if nxt <= cursor:
            break
        cursor = nxt
    if not out:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close"])
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
    slope_diff = np.full(n, np.nan)
    slope_abs = np.full(n, np.nan)

    def slope(series: np.ndarray, i: int, ref: float) -> float:
        if i < SLOPE_LOOKBACK or ref <= 0:
            return 0.0
        return (series[i] - series[i - SLOPE_LOOKBACK]) / SLOPE_LOOKBACK / ref * 100.0

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        ref = closes[i]
        su, sl = slope(upper, i, ref), slope(lower, i, ref)
        slope_diff[i] = abs(su - sl)
        slope_abs[i] = abs(su) + abs(sl)
        parallel[i] = slope_diff[i] <= PARALLEL_TOL
    out["bands_parallel"] = parallel
    out["slope_diff"] = slope_diff
    out["slope_abs"] = slope_abs
    prev_p = np.roll(parallel, 1)
    prev_p[0] = False
    out["parallel_exit"] = prev_p & (~parallel)
    return out


def _pnl(side: str, entry: float, exit_px: float, qty: float) -> float:
    if side == "long":
        gross = (exit_px - entry) * qty
    else:
        gross = (entry - exit_px) * qty
    return gross - (entry + exit_px) * qty * FEE


@dataclass(frozen=True)
class Rule:
    name: str
    note: str
    # filters
    min_pot_rr: float = 0.0
    min_slope_abs: float = 0.0  # at trend event
    min_body_atr: float = 0.0  # |close-open|/atr of entry candle
    max_body_atr: float = 99.0
    min_wait: int = 0
    max_wait: int = 9999
    hour_from: int = 0
    hour_to: int = 24  # [from, to) local VN
    side_only: str | None = None  # long|short
    # sizing
    size_by_rr: bool = False  # scale margin with pot_rr (capped)
    # exits
    mode: str = "band"  # band | loss_cap_k | mfe_be | soft_time
    loss_cap_k: float = 0.0  # close if uPnL <= -k * entry_margin
    mfe_arm_r: float = 0.0  # arm BE after MFE >= r * initial_risk (opp band)
    soft_time_bars: int = 0
    soft_time_min_r: float = 0.0  # if held >= bars and r_mult < this, exit


def simulate(df: pd.DataFrame, rule: Rule, *, collect: bool = False) -> tuple[dict, list[dict]]:
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
    slope_abs = df["slope_abs"].to_numpy()
    n = len(df)

    trend = None
    trend_i = None
    trend_slope_abs = 0.0
    waiting = False
    pos = None
    trades: list[dict] = []
    cash = CAPITAL
    nid = 1

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        px, hi, lo, o = float(closes[i]), float(highs[i]), float(lows[i]), float(opens[i])
        ts = int(tss[i])
        up, dn, mid = float(upper[i]), float(lower[i]), float(middle[i])
        w = float(width[i]) if not np.isnan(width[i]) else 0.0
        a = float(atr[i]) if not np.isnan(atr[i]) else 0.0

        # exits
        if pos is not None:
            side = pos["side"]
            entry = pos["entry"]
            risk = abs(entry - pos["sl0"]) or 1e-12
            # update MFE in R
            if side == "long":
                mfe = (hi - entry) / risk
                mae = (entry - lo) / risk
                upnl = _pnl(side, entry, lo, pos["qty"])  # adverse for loss check approx
                upnl_mark = _pnl(side, entry, px, pos["qty"])
            else:
                mfe = (entry - lo) / risk
                mae = (hi - entry) / risk
                upnl = _pnl(side, entry, hi, pos["qty"])
                upnl_mark = _pnl(side, entry, px, pos["qty"])
            pos["mfe"] = max(pos["mfe"], mfe)
            pos["mae"] = max(pos["mae"], mae)

            if rule.mode == "mfe_be" and pos["mfe"] >= rule.mfe_arm_r:
                pos["be"] = True

            exit_px = None
            reason = None
            bars_held = i - pos["entry_i"]

            # SL / BE
            if pos.get("be"):
                if side == "long" and lo <= entry:
                    exit_px, reason = entry, "BE"
                if side == "short" and hi >= entry:
                    exit_px, reason = entry, "BE"

            if exit_px is None and rule.mode == "loss_cap_k" and rule.loss_cap_k > 0:
                # adverse extreme vs -k * margin
                if upnl <= -rule.loss_cap_k * pos["margin"]:
                    exit_px = lo if side == "long" else hi
                    reason = "LOSS_CAP"

            tp = up if side == "long" else dn
            if exit_px is None:
                if (side == "long" and hi >= tp) or (side == "short" and lo <= tp):
                    exit_px, reason = tp, "TP_BAND"

            if exit_px is None and rule.mode == "soft_time" and rule.soft_time_bars > 0:
                if bars_held >= rule.soft_time_bars:
                    r_now = ((px - entry) if side == "long" else (entry - px)) / risk
                    if r_now < rule.soft_time_min_r:
                        exit_px, reason = px, "SOFT_TIME"

            if exit_px is not None:
                pnl = _pnl(side, entry, exit_px, pos["qty"])
                cash += pos["margin"] + pnl
                rec = {
                    **{k: pos[k] for k in (
                        "side", "entry", "entry_ts", "pot_rr", "chan_pos", "width_pct",
                        "tp_atr", "body_atr", "wait_bars", "slope_abs", "hour", "size_mult"
                    )},
                    "id": nid,
                    "exit_ts": ts,
                    "exit_px": exit_px,
                    "pnl": pnl,
                    "reason": reason,
                    "mfe": pos["mfe"],
                    "mae": pos["mae"],
                    "hold": bars_held,
                }
                trades.append(rec)
                nid += 1
                pos = None

        if parallel_exit[i]:
            trend = "up" if px > mid else "down"
            trend_i = i
            trend_slope_abs = float(slope_abs[i]) if not np.isnan(slope_abs[i]) else 0.0
            waiting = True

        if pos is None and waiting and trend is not None:
            is_red, is_green = px < o, px > o
            counter = (trend == "up" and is_red) or (trend == "down" and is_green)
            if counter and not parallel[i] and w > 1e-12:
                side = "long" if trend == "up" else "short"
                wait_bars = i - trend_i if trend_i is not None else 0
                tp_near = up if side == "long" else dn
                sl_opp = dn if side == "long" else up
                dist_tp = abs(tp_near - px)
                dist_sl = abs(px - sl_opp)
                pot_rr = dist_tp / dist_sl if dist_sl > 1e-12 else 0.0
                chan_pos = (px - dn) / w if side == "long" else (up - px) / w
                width_pct = w / px * 100.0
                tp_atr = dist_tp / a if a > 0 else 0.0
                body_atr = abs(px - o) / a if a > 0 else 0.0
                hour = datetime.fromtimestamp(ts / 1000, TZ).hour

                ok = True
                if rule.side_only and side != rule.side_only:
                    ok = False
                if pot_rr < rule.min_pot_rr:
                    ok = False
                if trend_slope_abs < rule.min_slope_abs:
                    ok = False
                if body_atr < rule.min_body_atr or body_atr > rule.max_body_atr:
                    ok = False
                if wait_bars < rule.min_wait or wait_bars > rule.max_wait:
                    ok = False
                if not (rule.hour_from <= hour < rule.hour_to):
                    ok = False

                if ok:
                    size_mult = 1.0
                    if rule.size_by_rr:
                        # pot_rr 0.3→0.6x, 0.5→1x, 1.0→1.5x, cap 2x
                        size_mult = float(np.clip(0.5 + pot_rr, 0.5, 2.0))
                    eq = max(cash, 0.0)
                    notional = min(eq * MARGIN_PCT * LEVERAGE * size_mult, cash * LEVERAGE)
                    if notional >= 1e-6:
                        margin = notional / LEVERAGE
                        if cash >= margin - 1e-12:
                            cash -= margin
                            pos = {
                                "side": side,
                                "entry": px,
                                "entry_ts": ts,
                                "entry_i": i,
                                "qty": notional / px,
                                "margin": margin,
                                "sl0": sl_opp,
                                "pot_rr": pot_rr,
                                "chan_pos": chan_pos,
                                "width_pct": width_pct,
                                "tp_atr": tp_atr,
                                "body_atr": body_atr,
                                "wait_bars": wait_bars,
                                "slope_abs": trend_slope_abs,
                                "hour": hour,
                                "size_mult": size_mult,
                                "mfe": 0.0,
                                "mae": 0.0,
                                "be": False,
                            }
                            waiting = False
            elif counter and parallel[i]:
                pass

        if pos is not None:
            waiting = False

    if pos is not None:
        px = float(closes[-1])
        pnl = _pnl(pos["side"], pos["entry"], px, pos["qty"])
        cash += pos["margin"] + pnl
        trades.append({
            **{k: pos[k] for k in (
                "side", "entry", "entry_ts", "pot_rr", "chan_pos", "width_pct",
                "tp_atr", "body_atr", "wait_bars", "slope_abs", "hour", "size_mult"
            )},
            "id": nid,
            "exit_ts": int(tss[-1]),
            "exit_px": px,
            "pnl": pnl,
            "reason": "EOD",
            "mfe": pos["mfe"],
            "mae": pos["mae"],
            "hold": n - 1 - pos["entry_i"],
        })

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    avg_w = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_l = sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0
    rr = avg_w / abs(avg_l) if losses else float("inf")
    wr = len(wins) / len(trades) if trades else 0.0
    rr_be = (1 - wr) / wr if wr > 0 else float("inf")
    stats = {
        "name": rule.name,
        "note": rule.note,
        "n": len(trades),
        "wr": wr * 100,
        "rr": rr,
        "rr_edge": (rr - rr_be) if wr > 0 and losses else float("nan"),
        "pf": (sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))) if losses else float("inf"),
        "exp": (sum(t["pnl"] for t in trades) / len(trades)) if trades else 0.0,
        "pnl": cash - CAPITAL,
    }
    return stats, trades if collect else []


def feature_report(trades: list[dict]) -> list[str]:
    if not trades:
        return ["(no trades)"]
    df = pd.DataFrame(trades)
    lines = []
    lines.append(f"n={len(df)} WR={(df.pnl>0).mean()*100:.1f}% mean_pnl={df.pnl.mean():+.4f}")

    def split(col: str, edges: list[float]):
        lines.append(f"\n### by {col}")
        lines.append("| Bucket | n | WR | RR | Exp | SumPnL |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        cats = pd.cut(df[col], bins=[-1e9] + edges + [1e9], duplicates="drop")
        for cat, g in df.groupby(cats, observed=False):
            if len(g) < 20:
                continue
            w = g[g.pnl > 0].pnl
            l = g[g.pnl < 0].pnl
            rr = (w.mean() / abs(l.mean())) if len(l) and len(w) else float("nan")
            lines.append(
                f"| {cat} | {len(g)} | {(g.pnl>0).mean()*100:.0f}% | {rr:.3f} | {g.pnl.mean():+.4f} | {g.pnl.sum():+.1f} |"
            )

    split("pot_rr", [0.3, 0.5, 0.75, 1.0])
    split("wait_bars", [1, 2, 4, 8])
    split("width_pct", [1.0, 2.0, 3.0, 5.0])
    split("tp_atr", [0.8, 1.2, 1.5, 2.0])
    split("body_atr", [0.2, 0.5, 1.0, 1.5])
    split("slope_abs", [0.05, 0.1, 0.2, 0.4])
    split("chan_pos", [0.3, 0.45, 0.6])
    split("hour", [6, 12, 18])
    split("mfe", [0.3, 0.6, 1.0, 1.5])
    split("mae", [0.5, 1.0, 2.0])
    split("hold", [2, 4, 8, 16])

    # side
    lines.append("\n### by side")
    lines.append("| Side | n | WR | RR | Exp | SumPnL |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for side, g in df.groupby("side"):
        w = g[g.pnl > 0].pnl
        l = g[g.pnl < 0].pnl
        rr = (w.mean() / abs(l.mean())) if len(l) and len(w) else float("nan")
        lines.append(
            f"| {side} | {len(g)} | {(g.pnl>0).mean()*100:.0f}% | {rr:.3f} | {g.pnl.mean():+.4f} | {g.pnl.sum():+.1f} |"
        )
    return lines


def wave3_rules() -> list[Rule]:
    """Rules seeded after typical baseline patterns (refined after analysis too)."""
    return [
        Rule("baseline", "ref"),
        # from positive: higher pot_rr buckets usually better RR
        Rule("pot_rr_ge_0.6", "pot RR≥0.6", min_pot_rr=0.6),
        Rule("pot_rr_ge_0.8", "pot RR≥0.8", min_pot_rr=0.8),
        # wait sweet spot often 2-8
        Rule("wait_2_8", "wait ∈[2,8]", min_wait=2, max_wait=8),
        Rule("wait_2_8_rr06", "wait∈[2,8] + RR≥0.6", min_wait=2, max_wait=8, min_pot_rr=0.6),
        # strong break from parallel
        Rule("slope_ge_0.15", "trend slope_abs≥0.15", min_slope_abs=0.15),
        Rule("slope_ge_0.25", "trend slope_abs≥0.25", min_slope_abs=0.25),
        Rule("slope15_rr05", "slope≥0.15 + RR≥0.5", min_slope_abs=0.15, min_pot_rr=0.5),
        # entry candle quality
        Rule("body_0.3_1.2", "body ATR ∈[0.3,1.2]", min_body_atr=0.3, max_body_atr=1.2),
        Rule("body_rr", "body∈[0.3,1.2] + RR≥0.5", min_body_atr=0.3, max_body_atr=1.2, min_pot_rr=0.5),
        # session VN
        Rule("asia_0_8", "hour VN [0,8)", hour_from=0, hour_to=8),
        Rule("eu_8_16", "hour VN [8,16)", hour_from=8, hour_to=16),
        Rule("us_16_24", "hour VN [16,24)", hour_from=16, hour_to=24),
        # size by RR (keep more edge on good setups)
        Rule("size_by_rr", "size ∝ pot_rr (0.5–2x)", size_by_rr=True),
        Rule("size_by_rr_min05", "size∝RR + min RR0.5", size_by_rr=True, min_pot_rr=0.5),
        # exits from MAE/MFE patterns
        Rule("loss_cap_1.5m", "cut if loss ≥1.5×margin", mode="loss_cap_k", loss_cap_k=1.5),
        Rule("loss_cap_2m", "cut if loss ≥2×margin", mode="loss_cap_k", loss_cap_k=2.0),
        Rule("mfe_be_0.5", "BE after MFE≥0.5R", mode="mfe_be", mfe_arm_r=0.5),
        Rule("mfe_be_0.8", "BE after MFE≥0.8R", mode="mfe_be", mfe_arm_r=0.8),
        Rule("soft_time_8_0", "sau 8 nen neu R<0 → out", mode="soft_time", soft_time_bars=8, soft_time_min_r=0.0),
        Rule("soft_time_12_0", "sau 12 nen neu R<0 → out", mode="soft_time", soft_time_bars=12, soft_time_min_r=0.0),
        # combos likely from analysis
        Rule("quality_v3", "RR≥0.6 + wait[2,8] + slope≥0.15", min_pot_rr=0.6, min_wait=2, max_wait=8, min_slope_abs=0.15),
        Rule("quality_v3b", "RR≥0.5 + wait[2,8] + body[0.3,1.2]", min_pot_rr=0.5, min_wait=2, max_wait=8, min_body_atr=0.3, max_body_atr=1.2),
        Rule("quality_size", "quality_v3 + size∝RR", min_pot_rr=0.6, min_wait=2, max_wait=8, min_slope_abs=0.15, size_by_rr=True),
    ]


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_closed = (now_ms // BAR_MS) * BAR_MS
    warmup = (DONCHIAN_PERIOD + SLOPE_LOOKBACK + ATR_PERIOD + 5) * BAR_MS
    window_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000
    fetch_from = window_from - warmup

    lines = [
        f"# Wave-3 data-driven Donchian — multi-coin {INTERVAL} {LOOKBACK_DAYS}d",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Buoc 1: phan tich feature lenh baseline (gop multi-coin + tung coin LINK)",
        "- Buoc 2: backtest rule moi sinh tu pattern tich cuc",
        "",
    ]

    all_base_trades: list[dict] = []
    dfs: dict[str, pd.DataFrame] = {}
    meta: dict[str, dict] = {}

    for sym in SYMBOLS:
        print(f"fetch {sym}...", flush=True)
        raw = fetch_klines(sym, fetch_from, last_closed)
        if raw.empty:
            continue
        df = prepare(raw)
        df = df[df["ts"] >= window_from].copy().reset_index(drop=True)
        if len(df) < 100:
            continue
        dfs[sym] = df
        first, last = df.iloc[0], df.iloc[-1]
        meta[sym] = {
            "days": (int(last.ts) - int(first.ts)) / 86400000,
            "chg": (float(last.close) / float(first.close) - 1) * 100,
            "from": _local(int(first.ts)),
            "to": _local(int(last.ts)),
        }
        st, tr = simulate(df, Rule("baseline", "ref"), collect=True)
        for t in tr:
            t["symbol"] = sym
        all_base_trades.extend(tr)
        print(f"  baseline n={st['n']} WR={st['wr']:.0f}% RR={st['rr']:.3f} PnL={st['pnl']:+.1f}", flush=True)

    lines += ["## A. Feature splits — ALL coins baseline", ""]
    lines += feature_report(all_base_trades)
    link_tr = [t for t in all_base_trades if t["symbol"] == "LINKUSDT"]
    lines += ["", "## B. Feature splits — LINK only", ""]
    lines += feature_report(link_tr)

    # Derive extra rules dynamically from pooled splits (top expectancy buckets)
    # Keep static wave3_rules; report will show which work.

    lines += ["", "## C. Wave-3 rule backtests (per coin + SUM)", ""]
    rules = wave3_rules()
    per: dict[str, list[dict]] = {sym: [] for sym in dfs}
    for rule in rules:
        print(f"rule {rule.name}...", flush=True)
        for sym, df in dfs.items():
            st, _ = simulate(df, rule, collect=False)
            st["symbol"] = sym
            per[sym].append(st)

    # summary table: for each rule, SUM pnl and median RR/edge
    lines += [
        "### C1. Tong hop theo rule (SUM PnL 6 coin)",
        "",
        "| Rank | Rule | ΣPnL | Δ vs base | med RR | med Edge | med WR | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    base_sum = sum(next(s for s in per[sym] if s["name"] == "baseline")["pnl"] for sym in dfs)
    rule_rows = []
    for rule in rules:
        pnls = []
        rrs, edges, wrs = [], [], []
        for sym in dfs:
            s = next(x for x in per[sym] if x["name"] == rule.name)
            pnls.append(s["pnl"])
            if s["n"] >= 30 and s["rr_edge"] == s["rr_edge"]:
                rrs.append(s["rr"])
                edges.append(s["rr_edge"])
                wrs.append(s["wr"])
        rule_rows.append({
            "name": rule.name,
            "note": rule.note,
            "sum": sum(pnls),
            "d": sum(pnls) - base_sum,
            "med_rr": float(np.median(rrs)) if rrs else float("nan"),
            "med_edge": float(np.median(edges)) if edges else float("nan"),
            "med_wr": float(np.median(wrs)) if wrs else float("nan"),
        })
    rule_rows.sort(key=lambda r: (r["med_edge"] if r["med_edge"] == r["med_edge"] else -999, r["sum"]), reverse=True)
    for i, r in enumerate(rule_rows, 1):
        lines.append(
            f"| {i} | `{r['name']}` | **{r['sum']:+.1f}** | {r['d']:+.1f} | {r['med_rr']:.3f} | "
            f"**{r['med_edge']:+.3f}** | {r['med_wr']:.0f}% | {r['note']} |"
        )

    # Top tradeoff: edge >= baseline med edge and sum pnl >= 0.85 * base
    base_med_edge = next(r for r in rule_rows if r["name"] == "baseline")["med_edge"]
    tradeoffs = [
        r for r in rule_rows
        if r["name"] != "baseline"
        and r["med_edge"] == r["med_edge"]
        and r["med_edge"] >= base_med_edge
        and r["sum"] >= base_sum * 0.85
    ]
    best_tf = max(tradeoffs, key=lambda r: (r["med_edge"], r["sum"])) if tradeoffs else rule_rows[0]
    best_pnl = max(rule_rows, key=lambda r: r["sum"])
    best_edge = max((r for r in rule_rows if r["med_edge"] == r["med_edge"]), key=lambda r: r["med_edge"])

    lines += [
        "",
        f"- Baseline ΣPnL **{base_sum:+.1f}** · med edge {base_med_edge:+.3f}",
        f"- Best edge: `{best_edge['name']}` med_edge {best_edge['med_edge']:+.3f} ΣPnL {best_edge['sum']:+.1f}",
        f"- Best PnL: `{best_pnl['name']}` ΣPnL {best_pnl['sum']:+.1f}",
        f"- Best tradeoff (edge≥base & ΣPnL≥85% base): `{best_tf['name']}`",
        "",
        "### C2. Chi tiet tung coin (baseline + top rules)",
        "",
    ]
    show = {"baseline", best_edge["name"], best_pnl["name"], best_tf["name"], "size_by_rr", "quality_v3", "quality_size", "soft_time_12_0", "mfe_be_0.8", "wait_2_8_rr06"}
    for sym in dfs:
        m = meta[sym]
        lines += [
            f"#### {sym} ({m['days']:.0f}d, px {m['chg']:+.1f}%)",
            "",
            "| Rule | n | WR | RR | Edge | PnL |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for s in sorted(per[sym], key=lambda x: (x["rr_edge"] if x["rr_edge"] == x["rr_edge"] else -999), reverse=True):
            if s["name"] not in show and s["rr_edge"] == s["rr_edge"] and s["rr_edge"] < base_med_edge:
                continue
            if s["name"] not in show and s["pnl"] < next(x["pnl"] for x in per[sym] if x["name"] == "baseline") * 0.9:
                # still show if in show set only
                if s["name"] not in show:
                    continue
            edge = s["rr_edge"]
            edge_s = f"{edge:+.3f}" if edge == edge else "nan"
            lines.append(
                f"| `{s['name']}` | {s['n']} | {s['wr']:.0f}% | {s['rr']:.3f} | {edge_s} | **{s['pnl']:+.1f}** |"
            )
        lines.append("")

    lines += [
        "## D. Huong di moi (tu data)",
        "",
        "1. **Size ∝ pot_rr**: giu nhieu lenh baseline, phong to setup RR tot — muc tieu tang PnL/RR ma khong giet frequency.",
        "2. **Wait sweet-spot + pot_rr**: khong wait vo han; loc RR.",
        "3. **Slope break manh**: chi trade khi roi parallel voi |slope| lon.",
        "4. **Soft time / loss cap / MFE→BE**: cat duoi beo (mae lon) — day la nguyen nhan RR thap.",
        "5. Neu feature split chi ra bucket tot (doc phan A/B) → uu tien rule trung bucket do.",
        "",
    ]

    out = ROOT / "docs" / f"backtest_MULTI_donchian_rr_wave3_datadriven_{INTERVAL}_{LOOKBACK_DAYS}d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}", flush=True)
    print(f"best_edge={best_edge['name']} best_pnl={best_pnl['name']} best_tf={best_tf['name']}", flush=True)


if __name__ == "__main__":
    main()
