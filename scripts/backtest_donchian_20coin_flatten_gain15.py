#!/usr/bin/env python3
"""D20 shared backtest clone: flatten-all when equity +1.5% from last anchor.

Cache-only. Does not modify other BT scripts or the live bot.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "bt_klines_15m"
DOCS = ROOT / "docs"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")

SYMBOLS_20 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "TRXUSDT", "ADAUSDT",
    "AVAXUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT", "BCHUSDT", "XLMUSDT", "ATOMUSDT",
    "NEARUSDT", "APTUSDT", "SUIUSDT", "ARBUSDT", "OPUSDT", "UNIUSDT",
]
BAR_MS = 15 * 60 * 1000
LOOKBACK_DAYS = 365
CAPITAL = 1000.0
MIN_BARS = 8000
GAIN_TRIGGER = 0.015  # +1.5% from anchor


def _load_hunt():
    path = ROOT / "scripts" / "backtest_hunt_pct_per_day.py"
    spec = importlib.util.spec_from_file_location("hunt_pct_flat", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_pct_flat"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_from_cache(hunt, symbols: list[str]) -> dict[str, pd.DataFrame]:
    dfs: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        files = sorted(CACHE_DIR.glob(f"{sym}_15m_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            print(f"  missing {sym}", flush=True)
            continue
        raw = pd.read_csv(files[0])
        df = hunt.prepare(raw)
        last = (int(df["ts"].max()) // BAR_MS) * BAR_MS
        wf = last - LOOKBACK_DAYS * 86400 * 1000
        df = df[df["ts"] >= wf].copy().reset_index(drop=True)
        if len(df) < MIN_BARS:
            continue
        dfs[sym] = df
    return dfs


def run(
    hunt,
    dfs: dict[str, pd.DataFrame],
    *,
    name: str,
    note: str,
    margin_pct: float,
    max_open: int,
    flatten_gain: float | None,
) -> dict:
    ts_sets = [set(df["ts"].tolist()) for df in dfs.values()]
    common = sorted(set.intersection(*ts_sets)) if ts_sets else []
    if len(common) < 500:
        return {"name": name, "error": "no common ts"}

    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed.keys())
    cash = CAPITAL
    opens: list[dict] = []
    trades: list[dict] = []
    state = {sym: {"trend": None, "waiting": False} for sym in symbols}

    peak = CAPITAL
    maxdd = 0.0
    dd_peak = CAPITAL
    dd_trough = CAPITAL
    dd_trough_ts = common[0]
    anchor = CAPITAL
    flatten_n = 0
    flatten_pnl_sum = 0.0

    def equity(mark: dict[str, float]) -> float:
        eq = cash
        for t in opens:
            eq += t["margin"] + hunt._pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def locked() -> float:
        return sum(t["margin"] for t in opens)

    def stack(sym: str) -> int:
        return sum(1 for t in opens if t["sym"] == sym)

    def flatten_all(mark: dict[str, float], reason: str) -> float:
        nonlocal cash, opens, flatten_n, flatten_pnl_sum
        realized = 0.0
        for t in opens:
            px = mark[t["sym"]]
            pnl = hunt._pnl(t["side"], t["entry"], px, t["qty"])
            cash += t["margin"] + pnl
            trades.append({"pnl": pnl, "reason": reason})
            realized += pnl
        if opens:
            flatten_n += 1
            flatten_pnl_sum += realized
        opens = []
        return realized

    def fmt_ts(ts: int) -> str:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d %H:%M %Z")

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}

        # band TP exits
        still: list[dict] = []
        for t in opens:
            sym = t["sym"]
            b = bar[sym]
            if np.isnan(b["dc_upper"]) or np.isnan(b["dc_lower"]):
                still.append(t)
                continue
            hi, lo = float(b["high"]), float(b["low"])
            up, dn = float(b["dc_upper"]), float(b["dc_lower"])
            side = t["side"]
            tp = up if side == "long" else dn
            if (side == "long" and hi >= tp) or (side == "short" and lo <= tp):
                pnl = hunt._pnl(side, t["entry"], tp, t["qty"])
                cash += t["margin"] + pnl
                trades.append({"pnl": pnl, "reason": "TP_BAND"})
            else:
                still.append(t)
        opens = still

        # profit target flatten: equity >= anchor * (1 + gain)
        if flatten_gain is not None and opens:
            eq = equity(mark)
            if eq >= anchor * (1.0 + flatten_gain) - 1e-9:
                flatten_all(mark, "FLAT_GAIN")
                anchor = cash  # fully flat → equity == cash
                # after flatten, no new entries this bar (cool-off one bar)
                eq2 = cash
                if eq2 > peak:
                    peak = eq2
                dd = (peak - eq2) / peak if peak > 0 else 0.0
                if dd > maxdd:
                    maxdd = dd
                    dd_peak = peak
                    dd_trough = eq2
                    dd_trough_ts = ts
                continue

        # entries
        candidates: list[dict] = []
        for sym in symbols:
            b = bar[sym]
            if np.isnan(b["dc_upper"]) or np.isnan(b["dc_lower"]) or np.isnan(b["atr"]):
                continue
            px, o = float(b["close"]), float(b["open"])
            up, dn, mid = float(b["dc_upper"]), float(b["dc_lower"]), float(b["dc_middle"])
            w = float(b["dc_width"])
            a = float(b["atr"])
            pe = bool(b["parallel_exit"])
            par = bool(b["bands_parallel"])
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
                    if 0.3 <= body <= 1.2 and pot >= 0.5:
                        candidates.append(
                            {"sym": sym, "side": side, "px": px, "pot": pot, "sl0": sl_opp}
                        )
        candidates.sort(key=lambda x: x["pot"], reverse=True)

        for cand in candidates:
            if len(opens) >= max_open:
                break
            if stack(cand["sym"]) > 0:
                continue
            sm = float(np.clip(0.5 + cand["pot"], 0.5, 2.0))
            eq = max(cash + locked(), 0.0)
            notional = min(eq * margin_pct * hunt.LEVERAGE * sm, cash * hunt.LEVERAGE)
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
                    "sl0": cand["sl0"],
                }
            )
            state[cand["sym"]]["waiting"] = False

        for sym in symbols:
            if stack(sym) > 0:
                state[sym]["waiting"] = False

        eq = equity(mark)
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > maxdd:
            maxdd = dd
            dd_peak = peak
            dd_trough = eq
            dd_trough_ts = ts

    # EOD
    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    for t in list(opens):
        pnl = hunt._pnl(t["side"], t["entry"], last_mark[t["sym"]], t["qty"])
        cash += t["margin"] + pnl
        trades.append({"pnl": pnl, "reason": "EOD"})
    opens = []

    days = max((common[-1] - common[0]) / 86400000.0, 1e-9)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = cash - CAPITAL
    flat_trades = [t for t in trades if t.get("reason") == "FLAT_GAIN"]

    return {
        "name": name,
        "note": note,
        "n": len(trades),
        "n_flat_events": flatten_n,
        "n_flat_legs": len(flat_trades),
        "flat_pnl_sum": flatten_pnl_sum,
        "wr": (len(wins) / len(trades) * 100) if trades else 0.0,
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "wipe": (gl / gw) if gw > 0 else float("inf"),
        "net": net,
        "end": cash,
        "pct_day": (net / CAPITAL * 100) / days,
        "maxdd": maxdd * 100,
        "dd_peak": dd_peak,
        "dd_trough": dd_trough,
        "dd_trough_when": fmt_ts(dd_trough_ts),
        "trades_per_day": len(trades) / days,
        "days": days,
        "start": fmt_ts(common[0]),
        "end_when": fmt_ts(common[-1]),
    }


def main() -> int:
    hunt = _load_hunt()
    print("Load cache 20 majors (no REST)...", flush=True)
    dfs = load_from_cache(hunt, SYMBOLS_20)
    print(f"ready {len(dfs)} symbols", flush=True)
    if len(dfs) < 10:
        return 1

    configs = [
        ("D20_base", "D20 no flatten (baseline)", 0.01, 20, None),
        ("D20_flat15", "D20 flatten-all when eq ≥ anchor×1.015", 0.01, 20, GAIN_TRIGGER),
        ("D20_flat15_max10", "D20 max10 + flatten +1.5%", 0.01, 10, GAIN_TRIGGER),
        ("A20_flat15", "A20 0.5% + flatten +1.5%", 0.005, 20, GAIN_TRIGGER),
    ]

    rows = []
    for name, note, mp, mo, fg in configs:
        print(f"run {name}...", flush=True)
        st = run(hunt, dfs, name=name, note=note, margin_pct=mp, max_open=mo, flatten_gain=fg)
        rows.append(st)
        if "error" in st:
            print(f"  ERROR {st['error']}", flush=True)
            continue
        print(
            f"  net={st['net']:+.1f} %/day={st['pct_day']:+.3f}% maxDD={st['maxdd']:.1f}% "
            f"flat_events={st['n_flat_events']} n={st['n']} t/d={st['trades_per_day']:.1f}",
            flush=True,
        )

    lines = [
        "# Donchian D20 — flatten-all when equity +1.5% from anchor",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_flatten_gain15.py` (clone; khong sua BT goc / bot)",
        "- Cache only: `data/bt_klines_15m/`",
        "- Rule entry giong D20 (body/pot_rr/size_mult). Flatten: moi khi `equity ≥ anchor × 1.015` "
        "→ dong het lot @ close, `anchor = cash` moi, tiep tuc trade (skip entry cung bar).",
        "- Anchor ban dau = 1000$; sau moi flatten gan lai bang equity (cash) luc do.",
        "",
        "| Config | %/ngày | Net | End | MaxDD | DD peak→trough | Flat events | n | lệnh/ngày | PF | WR | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for st in rows:
        if "error" in st:
            lines.append(f"| `{st['name']}` | ERROR | | | | | | | | | | {st.get('error')} |")
            continue
        lines.append(
            f"| `{st['name']}` | **{st['pct_day']:+.3f}%** | **{st['net']:+.1f}** | {st['end']:.1f} | "
            f"**{st['maxdd']:.1f}%** | {st['dd_peak']:.0f}→{st['dd_trough']:.0f} | "
            f"{st['n_flat_events']} | {st['n']} | {st['trades_per_day']:.1f} | "
            f"{st['pf']:.2f} | {st['wr']:.1f}% | {st['note']} |"
        )

    base = next((r for r in rows if r["name"] == "D20_base" and "error" not in r), None)
    flat = next((r for r in rows if r["name"] == "D20_flat15" and "error" not in r), None)
    lines += ["", "## Ket luan", ""]
    if base and flat:
        lines.append(
            f"- Baseline: %/ngày **{base['pct_day']:+.3f}%**, MaxDD **{base['maxdd']:.1f}%**, net **{base['net']:+.1f}**"
        )
        lines.append(
            f"- Flatten +1.5%: %/ngày **{flat['pct_day']:+.3f}%**, MaxDD **{flat['maxdd']:.1f}%**, "
            f"net **{flat['net']:+.1f}**, so lan flatten **{flat['n_flat_events']}** "
            f"(tong pnl luc flatten {flat['flat_pnl_sum']:+.1f})"
        )
        lines.append(
            f"- MaxDD: {base['maxdd']:.1f}% → {flat['maxdd']:.1f}% "
            f"({flat['maxdd'] - base['maxdd']:+.1f} pp); "
            f"%/ngày: {base['pct_day']:+.3f}% → {flat['pct_day']:+.3f}%"
        )
    lines += [
        "",
        "Flatten @ close (khong phai high/low) — paper; live co slip. Khong anh huong bot.",
        "",
    ]
    out = DOCS / "backtest_MULTI_donchian_20major_flatten_gain15_15m_365d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
