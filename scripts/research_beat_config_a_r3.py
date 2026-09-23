#!/usr/bin/env python3
"""Research R3: keep counter+soft-exit edge, swap STRUCTURE to non-Donchian.

Live bot = Donchian parallel-trend + counter candle.
This tests the SAME behavioral pattern on Keltner / Bollinger / HalfTrend-like
structures — different indicator family, careful shared-wallet BT.

Does NOT modify live bot.
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
sys.modules["pvc"] = pvc
assert spec.loader is not None
spec.loader.exec_module(pvc)

load_pool = pvc.load_pool
Cfg = pvc.Cfg
run_cfg = pvc.run
CAPITAL = pvc.CAPITAL
LEVERAGE = pvc.LEVERAGE
pnl = pvc.pnl

BASE = {365: {"pf": 1.54, "maxdd": 38.7}, 1095: {"pf": 1.53, "maxdd": 52.3}}


def enrich_struct(df: pd.DataFrame, kind: str, period: int, slope_lb: int, parallel_tol: float, k_mult: float) -> pd.DataFrame:
    out = df.copy()
    c, h, l = out["close"], out["high"], out["low"]
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(14, min_periods=14).mean()
    out["vol_sma"] = out["quote_volume"].rolling(20, min_periods=20).mean()
    out["vol_ratio"] = out["quote_volume"] / out["vol_sma"].replace(0, np.nan)
    out["body_atr"] = (c - out["open"]).abs() / out["atr"].replace(0, np.nan)

    if kind == "donchian":
        out["ch_upper"] = h.rolling(period, min_periods=period).max()
        out["ch_lower"] = l.rolling(period, min_periods=period).min()
    elif kind == "keltner":
        mid = c.ewm(span=period, adjust=False).mean()
        out["ch_upper"] = mid + k_mult * out["atr"]
        out["ch_lower"] = mid - k_mult * out["atr"]
    elif kind == "bollinger":
        mid = c.rolling(period).mean()
        std = c.rolling(period).std()
        out["ch_upper"] = mid + k_mult * std
        out["ch_lower"] = mid - k_mult * std
    elif kind == "hl_ema":
        # smoothed high/low channel
        out["ch_upper"] = h.ewm(span=period, adjust=False).mean()
        out["ch_lower"] = l.ewm(span=period, adjust=False).mean()
    else:
        raise ValueError(kind)

    out["ch_mid"] = (out["ch_upper"] + out["ch_lower"]) / 2
    out["ch_width"] = out["ch_upper"] - out["ch_lower"]

    upper = out["ch_upper"].to_numpy()
    lower = out["ch_lower"].to_numpy()
    closes = c.to_numpy()
    n = len(out)
    parallel = np.zeros(n, dtype=bool)

    def slope(arr, i, ref):
        if i < slope_lb or ref <= 0:
            return 0.0
        return (arr[i] - arr[i - slope_lb]) / slope_lb / ref * 100.0

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        parallel[i] = abs(slope(upper, i, closes[i]) - slope(lower, i, closes[i])) <= parallel_tol

    out["bands_parallel"] = parallel
    prev_p = np.roll(parallel, 1)
    prev_p[0] = False
    out["channel_expand"] = prev_p & (~parallel)
    return out


@dataclass
class SCfg:
    name: str
    kind: str
    period: int = 20
    k_mult: float = 2.0
    min_vol: float = 1.2
    body_lo: float = 0.3
    body_hi: float = 1.2
    min_pot_rr: float = 0.5
    margin_pct: float = 0.01
    max_open: int = 10
    top_k: int = 5
    size_mult_cap: float = 2.0
    parallel_tol: float = 0.015
    slope_lb: int = 5
    entry_mode: str = "counter"  # counter | same_dir


def run(cfg: SCfg, raw: dict, eval_start: int) -> dict:
    dfs = {
        s: enrich_struct(df, cfg.kind, cfg.period, cfg.slope_lb, cfg.parallel_tol, cfg.k_mult)
        for s, df in raw.items()
    }
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {s: df.set_index("ts").loc[common] for s, df in dfs.items()}
    symbols = list(indexed)
    first = next((t for t in common if t >= eval_start), common[-1])
    days = max((common[-1] - first) / 86400000.0, 1e-9)

    cash = CAPITAL
    opens: list[dict] = []
    trades: list[dict] = []
    peak = CAPITAL
    maxdd = 0.0
    state = {s: {"trend": None, "waiting": False} for s in symbols}

    def equity(mark):
        eq = cash
        for t in opens:
            eq += t["margin"] + pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def close_trade(t, px, ts):
        nonlocal cash
        pl = pnl(t["side"], t["entry"], px, t["qty"])
        cash += t["margin"] + pl
        if ts >= eval_start:
            trades.append({"pnl": pl, "side": t["side"]})

    for i, ts in enumerate(common):
        bar = {s: indexed[s].iloc[i] for s in symbols}
        mark = {s: float(bar[s]["close"]) for s in symbols}
        still = []
        for t in opens:
            b = bar[t["sym"]]
            hi, lo = float(b["high"]), float(b["low"])
            up, dn = float(b["ch_upper"]), float(b["ch_lower"])
            tp = up if t["side"] == "long" else dn
            if (t["side"] == "long" and hi >= tp) or (t["side"] == "short" and lo <= tp):
                close_trade(t, tp, ts)
            else:
                still.append(t)
        opens = still

        if ts < eval_start:
            for s in symbols:
                b = bar[s]
                if bool(b.get("channel_expand", False)):
                    px = float(b["close"])
                    state[s]["trend"] = "up" if px > float(b["ch_mid"]) else "down"
                    state[s]["waiting"] = True
            continue

        cands = []
        for s in symbols:
            if any(t["sym"] == s for t in opens):
                continue
            b = bar[s]
            if np.isnan(b.get("ch_upper", np.nan)) or np.isnan(b.get("atr", np.nan)):
                continue
            if np.isnan(b.get("vol_ratio", np.nan)):
                continue
            px, o = float(b["close"]), float(b["open"])
            up, dn, mid = float(b["ch_upper"]), float(b["ch_lower"]), float(b["ch_mid"])
            atr = float(b["atr"])
            st = state[s]
            if bool(b.get("channel_expand", False)):
                st["trend"] = "up" if px > mid else "down"
                st["waiting"] = True
            if not st["waiting"] or not st["trend"]:
                continue
            if bool(b.get("bands_parallel", False)):
                continue
            if float(b["vol_ratio"]) < cfg.min_vol:
                continue
            body = float(b["body_atr"]) if not np.isnan(b["body_atr"]) else 0
            if not (cfg.body_lo <= body <= cfg.body_hi):
                continue
            is_green, is_red = px > o, px < o
            if cfg.entry_mode == "counter":
                ok = (st["trend"] == "up" and is_red) or (st["trend"] == "down" and is_green)
            else:
                ok = (st["trend"] == "up" and is_green) or (st["trend"] == "down" and is_red)
            if not ok:
                continue
            side = "long" if st["trend"] == "up" else "short"
            tp = up if side == "long" else dn
            sl0 = dn if side == "long" else up
            pot_rr = abs(tp - px) / max(abs(px - sl0), 1e-12)
            if pot_rr < cfg.min_pot_rr:
                continue
            cands.append({"sym": s, "side": side, "entry": px, "tp": tp, "pot_rr": pot_rr})

        cands.sort(key=lambda x: x["pot_rr"], reverse=True)
        for cand in cands[: cfg.top_k]:
            if len(opens) >= cfg.max_open:
                break
            if any(t["sym"] == cand["sym"] for t in opens):
                continue
            eq = max(cash + sum(t["margin"] for t in opens), 1.0)
            margin = min(eq * cfg.margin_pct * min(cand["pot_rr"], cfg.size_mult_cap), eq * 0.15, cash)
            if margin < 1:
                continue
            qty = margin * LEVERAGE / cand["entry"]
            cash -= margin
            opens.append({**cand, "qty": qty, "margin": margin})
            state[cand["sym"]]["waiting"] = False

        eq = equity(mark)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)

    if opens:
        last = {s: indexed[s].iloc[-1] for s in symbols}
        for t in opens:
            close_trade(t, float(last[t["sym"]]["close"]), common[-1])

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in trades)
    return {
        "name": cfg.name,
        "n": len(trades),
        "wr": 100 * len(wins) / len(trades) if trades else 0,
        "pf": gw / gl if gl > 0 else 0,
        "ret_pct": net / CAPITAL * 100,
        "maxdd": maxdd * 100,
        "tpd": len(trades) / days,
    }


def cfgs() -> list[SCfg]:
    xs: list[SCfg] = []
    xs.append(SCfg("REF donchian counter (A-clone)", "donchian", period=20, k_mult=0, entry_mode="counter"))

    # Focused grid (not full cartesian — careful but tractable)
    for kind, ks in (("keltner", (1.5, 2.0, 2.5)), ("bollinger", (1.5, 2.0, 2.5)), ("hl_ema", (0,))):
        for period in (14, 20, 30):
            for k in ks:
                for vol in (1.2, 1.5):
                    xs.append(SCfg(
                        f"{kind} p{period} k{k} v{vol} counter",
                        kind, period=period, k_mult=k, min_vol=vol, entry_mode="counter",
                    ))
                # same_dir only at default vol for contrast
                xs.append(SCfg(
                    f"{kind} p{period} k{k} v1.2 same_dir",
                    kind, period=period, k_mult=k, min_vol=1.2, entry_mode="same_dir",
                ))

    for kind in ("keltner", "bollinger"):
        xs.append(SCfg(f"{kind} p20 k2 v1.2 counter tol0.01", kind, period=20, k_mult=2.0, parallel_tol=0.01))
        xs.append(SCfg(f"{kind} p20 k2 v1.2 counter body0.4-1.0", kind, period=20, k_mult=2.0, body_lo=0.4, body_hi=1.0))
        xs.append(SCfg(f"{kind} p20 k2 v1.2 counter rr0.8", kind, period=20, k_mult=2.0, min_pot_rr=0.8))
        xs.append(SCfg(f"{kind} p20 k2 v1.8 counter", kind, period=20, k_mult=2.0, min_vol=1.8))
    return xs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", default="365,1095")
    args = parser.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]
    candidates = cfgs()
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Research R3: counter/same_dir on Keltner / Bollinger / HL-EMA (not Donchian live)",
        "",
        f"- Generated: **{now}**",
        "- Pattern: parallel→expand → counter (or same_dir) → vol/body → TP at structure band",
        "- Structure ≠ live Donchian",
        "",
    ]
    store: dict[int, list] = {}
    for days in windows:
        print(f"\n=== {days}d · {len(candidates)} ===", flush=True)
        raw, es, _ = load_pool(days)
        a = run_cfg(Cfg("A", period=20, min_vol=1.2, min_pot_rr=0.5, margin_pct=0.01, max_open=10, size_mult_cap=2.0), raw, es)
        print(f"  A PF={a['pf']:.2f} DD={a['maxdd']:.1f}%", flush=True)
        rows = []
        base = BASE.get(days, BASE[365])
        for c in candidates:
            r = run(c, raw, es)
            r["name"] = c.name
            r["beat"] = r["pf"] >= base["pf"] + 0.03 and r["maxdd"] <= base["maxdd"] + 2
            rows.append(r)
            mark = " ★" if r["beat"] else ""
            print(f"  {c.name[:48]:48} PF={r['pf']:.2f} WR={r['wr']:.1f}% DD={r['maxdd']:5.1f}% ret={r['ret_pct']:+8.1f}%{mark}", flush=True)
        store[days] = rows
        lines += [
            f"## {days}d · A PF={a['pf']:.2f} DD={a['maxdd']:.1f}%",
            "",
            "| Rank | Name | PF | WR | Return | MaxDD | n |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for i, r in enumerate(sorted(rows, key=lambda x: (x["pf"], -x["maxdd"]), reverse=True)[:40], 1):
            star = " ★" if r["beat"] else ""
            lines.append(
                f"| {i} | {r['name']}{star} | {r['pf']:.2f} | {r['wr']:.1f}% | {r['ret_pct']:+.1f}% | {r['maxdd']:.1f}% | {r['n']} |"
            )
        lines.append("")

    if 365 in store and 1095 in store:
        m1 = {r["name"]: r for r in store[365]}
        m2 = {r["name"]: r for r in store[1095]}
        dual = [n for n in m1 if m1[n]["beat"] and m2.get(n, {}).get("beat")]
        lines += ["## Dual-window strict beats", ""]
        if dual:
            for n in dual:
                lines.append(
                    f"- **{n}**: 1y PF {m1[n]['pf']:.2f} DD {m1[n]['maxdd']:.1f}% · "
                    f"3y PF {m2[n]['pf']:.2f} DD {m2[n]['maxdd']:.1f}%"
                )
        else:
            lines.append("- None yet.")
            # near-misses: PF>A on 365
            near = [r for r in store[365] if r["pf"] >= BASE[365]["pf"] and "donchian" not in r["name"]]
            near = sorted(near, key=lambda x: x["pf"], reverse=True)[:10]
            lines.append("- Near-misses (PF≥A on 365d, non-Donchian):")
            for r in near:
                r2 = m2.get(r["name"])
                lines.append(
                    f"  - {r['name']}: PF {r['pf']:.2f} DD {r['maxdd']:.1f}%"
                    + (f" · 3y PF {r2['pf']:.2f} DD {r2['maxdd']:.1f}%" if r2 else "")
                )
        lines.append("")

    path = DOCS / "research_beat_config_a_r3.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
