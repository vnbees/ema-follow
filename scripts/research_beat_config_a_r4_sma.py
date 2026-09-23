#!/usr/bin/env python3
"""Research R4: deep-tune SMA pullback + soft Donchian-band TP (non-Config-A entry).

Candidate from R2: sma_pb_soft v1.2 → 365d PF 1.68 DD 17.5%; 1095d PF ~1.56 DD 18%.
Goal: clear dual-window beat vs Config A (PF≥A+0.03, MaxDD≤A+2).

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


@dataclass
class P:
    name: str
    fast: int = 50
    slow: int = 200
    min_vol: float = 1.2
    body_lo: float = 0.0
    body_hi: float = 99.0
    min_pot_rr: float = 0.5
    touch_mode: str = "wick"  # wick | close_cross
    require_bounce: bool = True  # close back on trend side of sma
    ema_mode: str | None = None
    margin_pct: float = 0.01
    max_open: int = 10
    top_k: int = 5
    size_mult_cap: float = 2.0
    tp_mode: str = "donchian"  # donchian | sma_ext | atr_rr
    rr: float = 2.0
    atr_sl: float = 1.5
    ch_period: int = 20


def enrich(df: pd.DataFrame, fast: int, slow: int, ch_period: int) -> pd.DataFrame:
    out = channel_enrich(df, ch_period, 5, 0.015)
    c = out["close"]
    out["sma_f"] = c.rolling(fast).mean()
    out["sma_s"] = c.rolling(slow).mean()
    out["ema21"] = c.ewm(span=21, adjust=False).mean()
    out["ema89"] = c.ewm(span=89, adjust=False).mean()
    out["ema200"] = c.ewm(span=200, adjust=False).mean()
    out["body_atr"] = (c - out["open"]).abs() / out["atr"].replace(0, np.nan)
    return out


def run(cfg: P, raw: dict, eval_start: int) -> dict:
    dfs = {s: enrich(df, cfg.fast, cfg.slow, cfg.ch_period) for s, df in raw.items()}
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
            hi, lo, px = float(b["high"]), float(b["low"]), float(b["close"])
            if t.get("tp_mode") == "atr_rr":
                sl, tp = t["sl"], t["tp"]
                hit_sl = (t["side"] == "long" and lo <= sl) or (t["side"] == "short" and hi >= sl)
                hit_tp = (t["side"] == "long" and hi >= tp) or (t["side"] == "short" and lo <= tp)
                if hit_sl and hit_tp:
                    close_trade(t, sl, ts)
                elif hit_sl:
                    close_trade(t, sl, ts)
                elif hit_tp:
                    close_trade(t, tp, ts)
                else:
                    still.append(t)
                continue
            # soft band / sma extension TP
            tp = t["tp"]
            if t.get("tp_mode") == "donchian":
                up, dn = float(b["ch_upper"]), float(b["ch_lower"])
                tp = up if t["side"] == "long" else dn
            if (t["side"] == "long" and hi >= tp) or (t["side"] == "short" and lo <= tp):
                close_trade(t, tp, ts)
            else:
                still.append(t)
        opens = still

        if ts < eval_start:
            continue

        cands = []
        for s in symbols:
            if any(t["sym"] == s for t in opens):
                continue
            b = bar[s]
            if np.isnan(b.get("atr", np.nan)) or np.isnan(b.get("vol_ratio", np.nan)):
                continue
            if np.isnan(b.get("sma_f", np.nan)) or np.isnan(b.get("sma_s", np.nan)):
                continue
            if float(b["vol_ratio"]) < cfg.min_vol:
                continue
            px, o = float(b["close"]), float(b["open"])
            hi, lo = float(b["high"]), float(b["low"])
            sma_f, sma_s = float(b["sma_f"]), float(b["sma_s"])
            atr = float(b["atr"])
            up, dn = float(b["ch_upper"]), float(b["ch_lower"])
            body = float(b["body_atr"]) if not np.isnan(b["body_atr"]) else 0
            if not (cfg.body_lo <= body <= cfg.body_hi):
                continue

            side = None
            # uptrend pullback to fast SMA
            if sma_f > sma_s:
                touched = (lo <= sma_f <= hi) if cfg.touch_mode == "wick" else (
                    (o > sma_f and px <= sma_f) or (o < sma_f and px >= sma_f)
                )
                bounce = px >= sma_f and px > o if cfg.require_bounce else px >= sma_f
                if touched and bounce:
                    side = "long"
            elif sma_f < sma_s:
                touched = (lo <= sma_f <= hi) if cfg.touch_mode == "wick" else (
                    (o < sma_f and px >= sma_f) or (o > sma_f and px <= sma_f)
                )
                bounce = px <= sma_f and px < o if cfg.require_bounce else px <= sma_f
                if touched and bounce:
                    side = "short"
            if side is None:
                continue

            if cfg.ema_mode:
                e21, e89, e200 = float(b["ema21"]), float(b["ema89"]), float(b["ema200"])
                if cfg.ema_mode == "stack_soft":
                    if not ((e21 > e89) if side == "long" else (e21 < e89)):
                        continue
                elif cfg.ema_mode == "ema200":
                    if not ((px > e200) if side == "long" else (px < e200)):
                        continue
                elif cfg.ema_mode == "stack":
                    if not ((e21 > e89 > e200) if side == "long" else (e21 < e89 < e200)):
                        continue

            if cfg.tp_mode == "donchian":
                tp = up if side == "long" else dn
                sl0 = dn if side == "long" else up
                pot_rr = abs(tp - px) / max(abs(px - sl0), 1e-12)
                sl = sl0
            elif cfg.tp_mode == "sma_ext":
                # TP = distance from entry to SMA mirrored / or slow SMA
                if side == "long":
                    tp = px + max(px - sma_f, atr) * cfg.rr
                    sl = min(sma_s, lo) - 0.2 * atr
                else:
                    tp = px - max(sma_f - px, atr) * cfg.rr
                    sl = max(sma_s, hi) + 0.2 * atr
                pot_rr = abs(tp - px) / max(abs(px - sl), 1e-12)
            else:  # atr_rr
                if side == "long":
                    sl = px - cfg.atr_sl * atr
                    tp = px + cfg.rr * (px - sl)
                else:
                    sl = px + cfg.atr_sl * atr
                    tp = px - cfg.rr * (sl - px)
                pot_rr = cfg.rr

            if pot_rr < cfg.min_pot_rr:
                continue
            cands.append({
                "sym": s, "side": side, "entry": px, "tp": tp, "sl": sl,
                "pot_rr": pot_rr, "tp_mode": cfg.tp_mode,
            })

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
        "final_eq": cash,
    }


def cfgs() -> list[P]:
    xs: list[P] = []
    # baseline recreate
    xs.append(P("sma50/200 v1.2 donchTP", fast=50, slow=200, min_vol=1.2, tp_mode="donchian"))
    xs.append(P("sma50/200 v1.5 donchTP", fast=50, slow=200, min_vol=1.5, tp_mode="donchian"))
    xs.append(P("sma50/200 v1.0 donchTP", fast=50, slow=200, min_vol=1.0, tp_mode="donchian"))

    for fast, slow in ((20, 100), (34, 144), (50, 200), (21, 89), (50, 150)):
        for vol in (1.2, 1.5):
            xs.append(P(f"sma{fast}/{slow} v{vol}", fast=fast, slow=slow, min_vol=vol))

    # filters on best family 50/200
    xs.append(P("sma50/200 body0.3-1.2", body_lo=0.3, body_hi=1.2))
    xs.append(P("sma50/200 body0.4-1.0", body_lo=0.4, body_hi=1.0))
    xs.append(P("sma50/200 rr0.8", min_pot_rr=0.8))
    xs.append(P("sma50/200 rr1.0", min_pot_rr=1.0))
    xs.append(P("sma50/200 +ema_soft", ema_mode="stack_soft"))
    xs.append(P("sma50/200 +ema200", ema_mode="ema200"))
    xs.append(P("sma50/200 +stack", ema_mode="stack"))
    xs.append(P("sma50/200 v1.5 +ema_soft", min_vol=1.5, ema_mode="stack_soft"))
    xs.append(P("sma50/200 v1.5 rr0.8", min_vol=1.5, min_pot_rr=0.8))
    xs.append(P("sma50/200 no_bounce", require_bounce=False))
    xs.append(P("sma50/200 close_cross", touch_mode="close_cross"))
    xs.append(P("sma50/200 ch10", ch_period=10))
    xs.append(P("sma50/200 ch30", ch_period=30))
    xs.append(P("sma50/200 top3", top_k=3))
    xs.append(P("sma50/200 max6", max_open=6))
    xs.append(P("sma50/200 cap1.5", size_mult_cap=1.5))
    xs.append(P("sma50/200 cap3", size_mult_cap=3.0))

    # alt TP
    xs.append(P("sma50/200 sma_ext RR2", tp_mode="sma_ext", rr=2.0))
    xs.append(P("sma50/200 sma_ext RR3", tp_mode="sma_ext", rr=3.0))
    xs.append(P("sma50/200 atrRR2", tp_mode="atr_rr", rr=2.0))
    xs.append(P("sma50/200 atrRR3", tp_mode="atr_rr", rr=3.0))

    # combos aiming PF↑
    xs.append(P("BEST try: v1.2 rr0.8 body0.3-1.2", min_vol=1.2, min_pot_rr=0.8, body_lo=0.3, body_hi=1.2))
    xs.append(P("BEST try: v1.2 +ema_soft rr0.8", min_vol=1.2, ema_mode="stack_soft", min_pot_rr=0.8))
    xs.append(P("BEST try: v1.5 body0.3-1.2", min_vol=1.5, body_lo=0.3, body_hi=1.2))
    xs.append(P("BEST try: sma34/144 v1.2", fast=34, slow=144, min_vol=1.2))
    xs.append(P("BEST try: sma34/144 v1.2 +ema_soft", fast=34, slow=144, min_vol=1.2, ema_mode="stack_soft"))
    xs.append(P("BEST try: sma21/89 v1.2", fast=21, slow=89, min_vol=1.2))
    xs.append(P("BEST try: sma50/200 v1.2 top5 cap2 ch20", ))  # alias baseline
    return xs


def beats(r, base):
    return r["pf"] >= base["pf"] + 0.03 and r["maxdd"] <= base["maxdd"] + 2.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", default="365,1095")
    args = parser.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]
    candidates = cfgs()
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# Research R4: SMA pullback deep-tune vs Config A",
        "",
        f"- Generated: **{now}**",
        "- Entry: pullback to SMA_fast while SMA_fast vs SMA_slow trend (≠ Donchian counter)",
        "- Exit default: soft TP at Donchian band (Config-A-style exit only)",
        "- Strict beat: PF≥A+0.03 & MaxDD≤A+2 on both 365d and 1095d",
        "",
    ]
    store = {}
    for days in windows:
        print(f"\n=== {days}d · {len(candidates)} ===", flush=True)
        raw, es, _ = load_pool(days)
        a = run_cfg(Cfg("A", period=20, min_vol=1.2, min_pot_rr=0.5, margin_pct=0.01, max_open=10, size_mult_cap=2.0), raw, es)
        print(f"  A PF={a['pf']:.4f} DD={a['maxdd']:.2f}%", flush=True)
        rows = []
        base = BASE[days]
        for c in candidates:
            r = run(c, raw, es)
            r["name"] = c.name
            r["beat"] = beats(r, base)
            rows.append(r)
            mark = " ★" if r["beat"] else ""
            print(
                f"  {c.name[:42]:42} PF={r['pf']:.4f} WR={r['wr']:.1f}% "
                f"ret={r['ret_pct']:+8.1f}% DD={r['maxdd']:5.1f}% n={r['n']}{mark}",
                flush=True,
            )
        store[days] = rows
        lines += [
            f"## {days}d · A PF={a['pf']:.4f} DD={a['maxdd']:.2f}%",
            "",
            "| Rank | Name | PF | WR | Return | MaxDD | n | beat |",
            "| ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for i, r in enumerate(sorted(rows, key=lambda x: (x["pf"], -x["maxdd"], x["ret_pct"]), reverse=True), 1):
            lines.append(
                f"| {i} | {r['name']} | {r['pf']:.4f} | {r['wr']:.1f}% | {r['ret_pct']:+.1f}% | "
                f"{r['maxdd']:.1f}% | {r['n']} | {'YES' if r['beat'] else ''} |"
            )
        lines.append("")

    if 365 in store and 1095 in store:
        m1 = {r["name"]: r for r in store[365]}
        m2 = {r["name"]: r for r in store[1095]}
        dual = [n for n in m1 if m1[n]["beat"] and m2.get(n, {}).get("beat")]
        lines += ["## Dual-window STRICT BEATS", ""]
        if dual:
            for n in dual:
                lines.append(
                    f"- **{n}**\n"
                    f"  - 365d: PF {m1[n]['pf']:.4f} · WR {m1[n]['wr']:.1f}% · ret {m1[n]['ret_pct']:+.1f}% · MaxDD {m1[n]['maxdd']:.1f}%\n"
                    f"  - 1095d: PF {m2[n]['pf']:.4f} · WR {m2[n]['wr']:.1f}% · ret {m2[n]['ret_pct']:+.1f}% · MaxDD {m2[n]['maxdd']:.1f}%"
                )
            print("\n*** DUAL BEATS FOUND ***", flush=True)
            for n in dual:
                print(f"  WINNER: {n}", flush=True)
        else:
            lines.append("- None with strict dual beat.")
            # best by min(pf_365, pf_1095)
            scored = []
            for n, a in m1.items():
                b = m2.get(n)
                if not b:
                    continue
                scored.append((min(a["pf"], b["pf"]), -max(a["maxdd"], b["maxdd"]), n, a, b))
            scored.sort(reverse=True)
            lines.append("- Top by min(PF_1y, PF_3y):")
            for _, __, n, a, b in scored[:8]:
                lines.append(
                    f"  - {n}: 1y PF {a['pf']:.4f} DD {a['maxdd']:.1f}% · "
                    f"3y PF {b['pf']:.4f} DD {b['maxdd']:.1f}%"
                )
        lines.append("")

    path = DOCS / "research_beat_config_a_r4_sma.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
