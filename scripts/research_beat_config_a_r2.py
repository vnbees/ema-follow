#!/usr/bin/env python3
"""Round 2 research: soft-exit (Donchian band TP, no hard SL) + non-A entries.

Config A's edge appears driven by soft exit (high WR). Round 1 showed ATR hard-SL
strategies fail. This round keeps Config-A-style exits but DIFFERENT entries.

Also deep-tunes same_dir_expand (opposite of live counter-candle).

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
channel_enrich = pvc.channel_enrich
Cfg = pvc.Cfg
run_cfg = pvc.run
CAPITAL = pvc.CAPITAL
LEVERAGE = pvc.LEVERAGE
pnl = pvc.pnl

BASE = {365: {"pf": 1.54, "maxdd": 38.7}, 1095: {"pf": 1.53, "maxdd": 52.3}}


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = channel_enrich(df, 20, 5, 0.015)
    c, h, l, o = out["close"], out["high"], out["low"], out["open"]
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    out["bb_up"] = mid + 2 * std
    out["bb_dn"] = mid - 2 * std
    out["bb_mid"] = mid
    atr = out["atr"]
    out["kel_up"] = mid + 1.5 * atr
    out["kel_dn"] = mid - 1.5 * atr
    out["squeeze"] = (out["bb_up"] < out["kel_up"]) & (out["bb_dn"] > out["kel_dn"])
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    out["rsi"] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    out["sma50"] = c.rolling(50).mean()
    out["sma200"] = c.rolling(200).mean()
    out["ema21"] = c.ewm(span=21, adjust=False).mean()
    out["ema89"] = c.ewm(span=89, adjust=False).mean()
    out["ema200"] = c.ewm(span=200, adjust=False).mean()
    out["body_atr"] = (c - o).abs() / atr.replace(0, np.nan)
    out["clv"] = (c - l) / (h - l).replace(0, np.nan)
    out["z20"] = (c - mid) / std.replace(0, np.nan)
    out["prev_up"] = out["ch_upper"].shift(1)
    out["prev_dn"] = out["ch_lower"].shift(1)
    # pin bar / engulf
    out["pin_bull"] = ((c - l) > 2 * (h - c).abs()) & (c >= o)
    out["pin_bear"] = ((h - c) > 2 * (c - l).abs()) & (c <= o)
    rng = h - l
    out["inside"] = (h <= h.shift(1)) & (l >= l.shift(1))
    out["wide"] = rng > rng.rolling(20).mean()
    return out


@dataclass
class C:
    name: str
    kind: str
    min_vol: float = 1.2
    body_lo: float = 0.3
    body_hi: float = 1.2
    min_pot_rr: float = 0.5
    margin_pct: float = 0.01
    max_open: int = 10
    top_k: int = 5
    size_mult_cap: float = 2.0
    ema_mode: str | None = None  # stack_soft | ema200 | None
    rsi_lo: float = 35.0
    rsi_hi: float = 65.0
    period: int = 20  # channel period override via re-enrich? use fixed 20 for now


def run(cfg: C, raw: dict[str, pd.DataFrame], eval_start: int) -> dict:
    dfs = {sym: enrich(df) for sym, df in raw.items()}
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed)
    first = next((t for t in common if t >= eval_start), common[-1])
    days = max((common[-1] - first) / 86400000.0, 1e-9)

    cash = CAPITAL
    opens: list[dict] = []
    trades: list[dict] = []
    peak = CAPITAL
    maxdd = 0.0
    state = {s: {"trend": None, "waiting": False, "sq": False, "inside": False} for s in symbols}

    def equity(mark):
        eq = cash
        for t in opens:
            eq += t["margin"] + pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def close_trade(t, px, reason, ts):
        nonlocal cash
        cash += t["margin"] + pnl(t["side"], t["entry"], px, t["qty"])
        if ts >= eval_start:
            trades.append({"pnl": pnl(t["side"], t["entry"], px, t["qty"]), "side": t["side"]})

    for i, ts in enumerate(common):
        bar = {s: indexed[s].iloc[i] for s in symbols}
        mark = {s: float(bar[s]["close"]) for s in symbols}

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
            for s in symbols:
                b = bar[s]
                if bool(b.get("channel_expand", False)):
                    px = float(b["close"])
                    state[s]["trend"] = "up" if px > float(b["ch_mid"]) else "down"
                    state[s]["waiting"] = True
                if bool(b.get("squeeze", False)):
                    state[s]["sq"] = True
                if bool(b.get("inside", False)):
                    state[s]["inside"] = True
            continue

        cands = []
        for s in symbols:
            if any(t["sym"] == s for t in opens):
                continue
            b = bar[s]
            if np.isnan(b.get("atr", np.nan)) or float(b["atr"]) <= 0:
                continue
            if np.isnan(b.get("vol_ratio", np.nan)):
                continue
            px, o = float(b["close"]), float(b["open"])
            hi, lo = float(b["high"]), float(b["low"])
            up, dn, mid = float(b["ch_upper"]), float(b["ch_lower"]), float(b["ch_mid"])
            atr = float(b["atr"])
            st = state[s]
            if bool(b.get("channel_expand", False)):
                st["trend"] = "up" if px > mid else "down"
                st["waiting"] = True
            if bool(b.get("squeeze", False)):
                st["sq"] = True
            if bool(b.get("inside", False)):
                st["inside"] = True

            side = None
            kind = cfg.kind

            if kind.startswith("same_dir"):
                if not st["waiting"] or not st["trend"]:
                    continue
                if bool(b.get("bands_parallel", False)):
                    continue
                if float(b["vol_ratio"]) < cfg.min_vol:
                    continue
                body = float(b["body_atr"]) if not np.isnan(b["body_atr"]) else 0
                if not (cfg.body_lo <= body <= cfg.body_hi):
                    continue
                follow = (st["trend"] == "up" and px > o) or (st["trend"] == "down" and px < o)
                if not follow:
                    continue
                if "pin" in kind:
                    ok = (st["trend"] == "up" and bool(b["pin_bull"])) or (st["trend"] == "down" and bool(b["pin_bear"]))
                    if not ok:
                        continue
                if "wide" in kind and not bool(b["wide"]):
                    continue
                side = "long" if st["trend"] == "up" else "short"

            elif kind == "rsi_soft":
                rsi = float(b["rsi"])
                if np.isnan(rsi) or float(b["vol_ratio"]) < cfg.min_vol:
                    continue
                body = float(b["body_atr"]) if not np.isnan(b["body_atr"]) else 0
                if not (cfg.body_lo <= body <= cfg.body_hi):
                    continue
                if rsi < cfg.rsi_lo and px > o:
                    side = "long"
                elif rsi > cfg.rsi_hi and px < o:
                    side = "short"
                else:
                    continue

            elif kind == "bb_soft":
                if float(b["vol_ratio"]) < cfg.min_vol:
                    continue
                bb_up, bb_dn = float(b["bb_up"]), float(b["bb_dn"])
                if np.isnan(bb_up):
                    continue
                if lo <= bb_dn and px > o:
                    side = "long"
                elif hi >= bb_up and px < o:
                    side = "short"
                else:
                    continue

            elif kind == "z_soft":
                z = float(b["z20"])
                if np.isnan(z) or float(b["vol_ratio"]) < cfg.min_vol:
                    continue
                if z <= -2 and px > o:
                    side = "long"
                elif z >= 2 and px < o:
                    side = "short"
                else:
                    continue

            elif kind == "sq_break_soft":
                if not st["sq"] or float(b["vol_ratio"]) < cfg.min_vol:
                    continue
                body = float(b["body_atr"]) if not np.isnan(b["body_atr"]) else 0
                if not (cfg.body_lo <= body <= cfg.body_hi):
                    continue
                if px > float(b["kel_up"]) and px > o:
                    side = "long"
                    st["sq"] = False
                elif px < float(b["kel_dn"]) and px < o:
                    side = "short"
                    st["sq"] = False
                else:
                    continue

            elif kind == "turtle_soft":
                if float(b["vol_ratio"]) < cfg.min_vol:
                    continue
                pu, pdn = float(b["prev_up"]), float(b["prev_dn"])
                if np.isnan(pu):
                    continue
                body = float(b["body_atr"]) if not np.isnan(b["body_atr"]) else 0
                if not (cfg.body_lo <= body <= cfg.body_hi):
                    continue
                if px > pu and px > o:
                    side = "long"
                elif px < pdn and px < o:
                    side = "short"
                else:
                    continue

            elif kind == "inside_break_soft":
                if not st["inside"] or float(b["vol_ratio"]) < cfg.min_vol:
                    continue
                # break prior inside — use break of channel mid direction
                body = float(b["body_atr"]) if not np.isnan(b["body_atr"]) else 0
                if not (cfg.body_lo <= body <= cfg.body_hi):
                    continue
                if not bool(b["inside"]) and px > mid and px > o:
                    side = "long"
                    st["inside"] = False
                elif not bool(b["inside"]) and px < mid and px < o:
                    side = "short"
                    st["inside"] = False
                else:
                    continue

            elif kind == "sma_pb_soft":
                sma50, sma200 = float(b["sma50"]), float(b["sma200"])
                if np.isnan(sma50) or np.isnan(sma200) or float(b["vol_ratio"]) < cfg.min_vol:
                    continue
                if sma50 > sma200 and lo <= sma50 <= hi and px >= sma50 and px > o:
                    side = "long"
                elif sma50 < sma200 and lo <= sma50 <= hi and px <= sma50 and px < o:
                    side = "short"
                else:
                    continue

            elif kind == "climax_soft":
                if float(b["vol_ratio"]) < 2.0:
                    continue
                clv = float(b["clv"]) if not np.isnan(b["clv"]) else 0.5
                if clv >= 0.7 and px > o:
                    side = "long"
                elif clv <= 0.3 and px < o:
                    side = "short"
                else:
                    continue

            elif kind == "ema_reclaim_soft":
                # reclaim ema89 with stack
                e21, e89, e200 = float(b["ema21"]), float(b["ema89"]), float(b["ema200"])
                if not (np.isfinite(e21) and np.isfinite(e89) and np.isfinite(e200)):
                    continue
                if float(b["vol_ratio"]) < cfg.min_vol:
                    continue
                body = float(b["body_atr"]) if not np.isnan(b["body_atr"]) else 0
                if not (cfg.body_lo <= body <= cfg.body_hi):
                    continue
                if e21 > e89 > e200 and lo <= e89 <= hi and px >= e89 and px > o:
                    side = "long"
                elif e21 < e89 < e200 and lo <= e89 <= hi and px <= e89 and px < o:
                    side = "short"
                else:
                    continue

            else:
                continue

            if side is None:
                continue

            # optional EMA filter
            if cfg.ema_mode:
                e21, e89, e200 = float(b["ema21"]), float(b["ema89"]), float(b["ema200"])
                if cfg.ema_mode == "stack_soft":
                    ok = (e21 > e89) if side == "long" else (e21 < e89)
                    if not ok:
                        continue
                elif cfg.ema_mode == "ema200":
                    ok = (px > e200) if side == "long" else (px < e200)
                    if not ok:
                        continue
                elif cfg.ema_mode == "stack":
                    ok = (e21 > e89 > e200) if side == "long" else (e21 < e89 < e200)
                    if not ok:
                        continue

            tp = up if side == "long" else dn
            sl0 = dn if side == "long" else up
            pot_rr = abs(tp - px) / max(abs(px - sl0), 1e-12)
            if pot_rr < cfg.min_pot_rr:
                continue
            cands.append({"sym": s, "side": side, "entry": px, "tp": tp, "sl0": sl0, "pot_rr": pot_rr})

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
            if kind.startswith("same_dir"):
                state[cand["sym"]]["waiting"] = False

        eq = equity(mark)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)

    if opens:
        last = {s: indexed[s].iloc[-1] for s in symbols}
        for t in opens:
            close_trade(t, float(last[t["sym"]]["close"]), "EOD", common[-1])

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
        "days": days,
    }


def cfgs() -> list[C]:
    xs: list[C] = []
    # same_dir deep tune
    for vol in (1.0, 1.2, 1.5, 1.8):
        for rr in (0.5, 0.8, 1.0):
            xs.append(C(f"same_dir v{vol} rr{rr}", "same_dir", min_vol=vol, min_pot_rr=rr))
    xs.append(C("same_dir pin v1.2", "same_dir_pin", min_vol=1.2))
    xs.append(C("same_dir wide v1.2", "same_dir_wide", min_vol=1.2))
    xs.append(C("same_dir body0.5-1.0", "same_dir", body_lo=0.5, body_hi=1.0))
    xs.append(C("same_dir+ema_soft", "same_dir", ema_mode="stack_soft"))
    xs.append(C("same_dir+ema200", "same_dir", ema_mode="ema200"))
    xs.append(C("same_dir+stack", "same_dir", ema_mode="stack"))
    xs.append(C("same_dir v1.5+ema_soft", "same_dir", min_vol=1.5, ema_mode="stack_soft", min_pot_rr=0.8))

    # soft-exit other entries
    for vol in (1.2, 1.5):
        xs.append(C(f"rsi_soft v{vol}", "rsi_soft", min_vol=vol))
        xs.append(C(f"bb_soft v{vol}", "bb_soft", min_vol=vol))
        xs.append(C(f"z_soft v{vol}", "z_soft", min_vol=vol))
        xs.append(C(f"sq_break_soft v{vol}", "sq_break_soft", min_vol=vol))
        xs.append(C(f"turtle_soft v{vol}", "turtle_soft", min_vol=vol))
        xs.append(C(f"sma_pb_soft v{vol}", "sma_pb_soft", min_vol=vol))
        xs.append(C(f"climax_soft", "climax_soft", min_vol=vol))
        xs.append(C(f"ema_reclaim_soft v{vol}", "ema_reclaim_soft", min_vol=vol))
        xs.append(C(f"inside_break_soft v{vol}", "inside_break_soft", min_vol=vol))

    xs.append(C("rsi_soft 30/70", "rsi_soft", rsi_lo=30, rsi_hi=70))
    xs.append(C("rsi_soft 40/60", "rsi_soft", rsi_lo=40, rsi_hi=60))
    xs.append(C("ema_reclaim+stack", "ema_reclaim_soft", ema_mode="stack", min_vol=1.2))
    xs.append(C("turtle_soft+ema_soft", "turtle_soft", ema_mode="stack_soft"))
    xs.append(C("bb_soft+ema200", "bb_soft", ema_mode="ema200"))
    return xs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", default="365,1095")
    args = parser.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]
    candidates = cfgs()
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Research R2: soft-exit + non-Config-A entries",
        "",
        f"- Generated: **{now}**",
        "- Exit = Config-A style (TP Donchian band, **no hard SL**)",
        "- Entry ≠ live counter-candle",
        f"- Strict beat: PF≥A+0.03 and MaxDD≤A+2 on both windows",
        "",
    ]
    store: dict[int, list] = {}
    for days in windows:
        print(f"\n=== {days}d · {len(candidates)} cfgs ===", flush=True)
        raw, es, ee = load_pool(days)
        a = run_cfg(Cfg("A", period=20, min_vol=1.2, min_pot_rr=0.5, margin_pct=0.01, max_open=10, size_mult_cap=2.0), raw, es)
        print(f"  A verify PF={a['pf']:.2f} DD={a['maxdd']:.1f}%", flush=True)
        rows = []
        base = BASE[days] if days in BASE else BASE[365]
        for c in candidates:
            r = run(c, raw, es)
            r["name"] = c.name
            r["beat"] = r["pf"] >= base["pf"] + 0.03 and r["maxdd"] <= base["maxdd"] + 2
            rows.append(r)
            flag = " ★" if r["beat"] else ""
            print(f"  {c.name:32} PF={r['pf']:.2f} WR={r['wr']:.1f}% ret={r['ret_pct']:+8.1f}% DD={r['maxdd']:5.1f}% n={r['n']}{flag}", flush=True)
        store[days] = rows
        lines += [
            f"## {days}d · A PF={a['pf']:.2f} DD={a['maxdd']:.1f}%",
            "",
            "| Rank | Name | PF | WR | Return | MaxDD | n | t/d |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for i, r in enumerate(sorted(rows, key=lambda x: (x["pf"], -x["maxdd"]), reverse=True), 1):
            star = " ★" if r["beat"] else ""
            lines.append(
                f"| {i} | {r['name']}{star} | {r['pf']:.2f} | {r['wr']:.1f}% | {r['ret_pct']:+.1f}% | "
                f"{r['maxdd']:.1f}% | {r['n']} | {r['tpd']:.1f} |"
            )
        lines.append("")

    if 365 in store and 1095 in store:
        m1 = {r["name"]: r for r in store[365]}
        m2 = {r["name"]: r for r in store[1095]}
        dual = [n for n in m1 if m1[n]["beat"] and m2.get(n, {}).get("beat")]
        lines += ["## Dual-window strict beats", ""]
        if dual:
            for n in dual:
                lines.append(f"- **{n}**")
        else:
            top = sorted(store[365], key=lambda x: x["pf"], reverse=True)[:5]
            lines.append("- None strict. Top-5 PF on 365d:")
            for r in top:
                r2 = m2.get(r["name"])
                extra = f" · 3y PF {r2['pf']:.2f} DD {r2['maxdd']:.1f}%" if r2 else ""
                lines.append(f"  - {r['name']}: PF {r['pf']:.2f} DD {r['maxdd']:.1f}%{extra}")
        lines.append("")

    path = DOCS / "research_beat_config_a_r2.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
