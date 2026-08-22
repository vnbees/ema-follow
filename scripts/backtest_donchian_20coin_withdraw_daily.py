#!/usr/bin/env python3
"""Clone of 20-major shared D backtest + optional daily equity withdraw.

Does NOT modify backtest_donchian_20coin_shared_d.py / hunt script.
Cache-only (data/bt_klines_15m) — no REST — safe alongside live bot.
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


def _load_hunt():
    path = ROOT / "scripts" / "backtest_hunt_pct_per_day.py"
    spec = importlib.util.spec_from_file_location("hunt_pct_wd", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_pct_wd"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_from_cache(hunt, symbols: list[str]) -> dict[str, pd.DataFrame]:
    dfs: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        files = sorted(CACHE_DIR.glob(f"{sym}_15m_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            print(f"  missing cache {sym}", flush=True)
            continue
        raw = pd.read_csv(files[0])
        df = hunt.prepare(raw)
        last = (int(df["ts"].max()) // BAR_MS) * BAR_MS
        wf = last - LOOKBACK_DAYS * 86400 * 1000
        df = df[df["ts"] >= wf].copy().reset_index(drop=True)
        if len(df) < MIN_BARS:
            print(f"  skip {sym} bars={len(df)}", flush=True)
            continue
        dfs[sym] = df
        print(f"  {sym} bars={len(df)} ← {files[0].name}", flush=True)
    return dfs


def shared_backtest_withdraw(
    hunt,
    dfs: dict[str, pd.DataFrame],
    *,
    name: str,
    note: str,
    margin_pct: float,
    max_open: int,
    withdraw_pct_per_day: float = 0.0,
) -> dict:
    """Like hunt.shared_backtest, plus optional daily withdraw of pct × equity from cash→spot."""
    ts_sets = [set(df["ts"].tolist()) for df in dfs.values()]
    common = sorted(set.intersection(*ts_sets)) if ts_sets else []
    if len(common) < 500:
        return {"name": name, "error": "no common ts"}

    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed.keys())
    cash = CAPITAL
    spot = 0.0  # withdrawn, held flat (no yield)
    opens: list[dict] = []
    trades: list[dict] = []
    state = {sym: {"trend": None, "waiting": False} for sym in symbols}

    peak_bot = CAPITAL
    maxdd_bot = 0.0
    dd_peak_bot = CAPITAL
    dd_trough_bot = CAPITAL
    dd_peak_ts = common[0]
    dd_trough_ts = common[0]

    peak_total = CAPITAL
    maxdd_total = 0.0

    last_day: int | None = None
    withdraw_events = 0
    withdraw_sum = 0.0

    def equity_bot(mark: dict[str, float]) -> float:
        eq = cash
        for t in opens:
            eq += t["margin"] + hunt._pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def locked() -> float:
        return sum(t["margin"] for t in opens)

    def stack(sym: str) -> int:
        return sum(1 for t in opens if t["sym"] == sym)

    def fmt_ts(ts: int) -> str:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d %H:%M %Z")

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}

        # --- exits ---
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
                trades.append({"pnl": pnl})
            else:
                still.append(t)
        opens = still

        # --- daily withdraw (UTC day) after exits, before new entries ---
        day = int(ts // 86400000)
        if withdraw_pct_per_day > 0 and last_day is not None and day != last_day:
            eq = max(equity_bot(mark), 0.0)
            target = eq * withdraw_pct_per_day
            take = min(target, max(cash, 0.0))
            if take > 1e-9:
                cash -= take
                spot += take
                withdraw_sum += take
                withdraw_events += 1
        last_day = day

        # --- entries (body_size_rr05) ---
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
                        # keep waiting until filled
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

        bot_eq = equity_bot(mark)
        total_eq = bot_eq + spot

        if bot_eq > peak_bot:
            peak_bot = bot_eq
        dd_b = (peak_bot - bot_eq) / peak_bot if peak_bot > 0 else 0.0
        if dd_b > maxdd_bot:
            maxdd_bot = dd_b
            dd_peak_bot = peak_bot
            dd_trough_bot = bot_eq
            # approximate: peak timestamp unknown precisely; store trough ts
            dd_trough_ts = ts

        if total_eq > peak_total:
            peak_total = total_eq
        dd_t = (peak_total - total_eq) / peak_total if peak_total > 0 else 0.0
        if dd_t > maxdd_total:
            maxdd_total = dd_t

    # EOD flat bot
    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    for t in list(opens):
        pnl = hunt._pnl(t["side"], t["entry"], last_mark[t["sym"]], t["qty"])
        cash += t["margin"] + pnl
        trades.append({"pnl": pnl})
    opens = []
    end_bot = cash
    end_total = cash + spot

    days = max((common[-1] - common[0]) / 86400000.0, 1e-9)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    # bot trading net vs start (ignores that withdraw removed cash — report separately)
    bot_net = end_bot - CAPITAL
    total_net = end_total - CAPITAL

    return {
        "name": name,
        "note": note,
        "n_syms": len(dfs),
        "withdraw_pct": withdraw_pct_per_day * 100,
        "n": len(trades),
        "wr": (len(wins) / len(trades) * 100) if trades else 0.0,
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "wipe": (gl / gw) if gw > 0 else float("inf"),
        "trades_per_day": len(trades) / days,
        "days": days,
        "end_bot": end_bot,
        "end_spot": spot,
        "end_total": end_total,
        "bot_net": bot_net,
        "total_net": total_net,
        "pct_day_bot": (bot_net / CAPITAL * 100) / days,
        "pct_day_total": (total_net / CAPITAL * 100) / days,
        "maxdd_bot": maxdd_bot * 100,
        "maxdd_total": maxdd_total * 100,
        "dd_peak_bot": dd_peak_bot,
        "dd_trough_bot": dd_trough_bot,
        "dd_trough_when": fmt_ts(dd_trough_ts),
        "withdraw_events": withdraw_events,
        "withdraw_sum": withdraw_sum,
        "start": fmt_ts(common[0]),
        "end": fmt_ts(common[-1]),
    }


def main() -> int:
    hunt = _load_hunt()
    print("Loading 20 majors from cache only (no REST)...", flush=True)
    dfs = load_from_cache(hunt, SYMBOLS_20)
    if len(dfs) < 10:
        print("Not enough cached symbols", flush=True)
        return 1

    configs = [
        ("D20_no_wd", "D20 like bot — no withdraw", 0.01, 20, 0.0),
        ("D20_wd1pct", "D20 + withdraw 1% equity/day → spot", 0.01, 20, 0.01),
        ("A20_wd1pct", "A20 half margin + withdraw 1%/day", 0.005, 20, 0.01),
        ("D5_wd1pct", "5-coin style size on 20 pool? skip", 0.01, 10, 0.01),
    ]
    # Drop confusing D5 on 20 pool — use D20_max10_wd instead
    configs = [
        ("D20_no_wd", "margin 1%, max20, no withdraw (baseline clone)", 0.01, 20, 0.0),
        ("D20_wd1pct", "margin 1%, max20, withdraw 1% equity/UTC-day", 0.01, 20, 0.01),
        ("D20_max10_wd1pct", "margin 1%, max10, withdraw 1%/day", 0.01, 10, 0.01),
        ("A20_wd1pct", "margin 0.5%, max20, withdraw 1%/day", 0.005, 20, 0.01),
    ]

    rows = []
    for name, note, mp, mo, wd in configs:
        print(f"run {name}...", flush=True)
        st = shared_backtest_withdraw(
            hunt, dfs, name=name, note=note, margin_pct=mp, max_open=mo, withdraw_pct_per_day=wd
        )
        rows.append(st)
        if "error" in st:
            print(f"  ERROR {st['error']}", flush=True)
            continue
        print(
            f"  total_net={st['total_net']:+.1f} bot_end={st['end_bot']:.1f} spot={st['end_spot']:.1f} "
            f"maxDD_bot={st['maxdd_bot']:.1f}% maxDD_total={st['maxdd_total']:.1f}% "
            f"%/day_total={st['pct_day_total']:+.3f}% wd_sum={st['withdraw_sum']:.1f}",
            flush=True,
        )

    lines = [
        "# Donchian D20 — daily withdraw 1% equity (cache-only clone)",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_withdraw_daily.py` (clone; **khong** sua BT goc)",
        "- Data: `data/bt_klines_15m/` cache only — **khong REST** (an toan voi bot live)",
        "- Rule giong D20: body 0.3–1.2, pot_rr≥0.5, size_mult clip(0.5+pot_rr,0.5,2), 15m, 365d, capital 1000$",
        "- Withdraw: moi ngay UTC, rut `min(1% × equity_bot, cash tu do)` sang **spot** (spot flat, khong sinh loi)",
        "- MaxDD_bot: peak→trough tren equity futures/bot; MaxDD_total: bot + spot da rut",
        "",
        "| Config | %/ngày total | %/ngày bot-only | Net total | End bot | End spot | MaxDD bot | MaxDD total | PF | WR | lệnh/ngày | WD sum | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for st in rows:
        if "error" in st:
            lines.append(f"| `{st['name']}` | ERROR | | | | | | | | | | | {st.get('error')} |")
            continue
        lines.append(
            f"| `{st['name']}` | **{st['pct_day_total']:+.3f}%** | {st['pct_day_bot']:+.3f}% | "
            f"**{st['total_net']:+.1f}** | {st['end_bot']:.1f} | {st['end_spot']:.1f} | "
            f"**{st['maxdd_bot']:.1f}%** | **{st['maxdd_total']:.1f}%** | {st['pf']:.2f} | "
            f"{st['wr']:.1f}% | {st['trades_per_day']:.1f} | {st['withdraw_sum']:.1f} | {st['note']} |"
        )

    base = next((r for r in rows if r["name"] == "D20_no_wd" and "error" not in r), None)
    wd = next((r for r in rows if r["name"] == "D20_wd1pct" and "error" not in r), None)
    lines += ["", "## Ket luan", ""]
    if base and wd:
        lines.append(
            f"- Baseline D20 no WD: MaxDD_bot **{base['maxdd_bot']:.1f}%**, "
            f"%/ngày total **{base['pct_day_total']:+.3f}%**, end total **{base['end_total']:.1f}**"
        )
        lines.append(
            f"- D20 + WD 1%/ngày: MaxDD_bot **{wd['maxdd_bot']:.1f}%**, MaxDD_total **{wd['maxdd_total']:.1f}%**, "
            f"%/ngày total **{wd['pct_day_total']:+.3f}%**, "
            f"end bot **{wd['end_bot']:.1f}** + spot **{wd['end_spot']:.1f}** = total **{wd['end_total']:.1f}**"
        )
        lines.append(
            f"- MaxDD_bot: {base['maxdd_bot']:.1f}% → {wd['maxdd_bot']:.1f}% "
            f"({wd['maxdd_bot'] - base['maxdd_bot']:+.1f} pp)"
        )
        lines.append(
            f"- DD episode (bot) wd run: peak≈{wd['dd_peak_bot']:.0f} → trough≈{wd['dd_trough_bot']:.0f} "
            f"(trough ~ {wd['dd_trough_when']})"
        )
    lines += [
        "",
        "Rut chi lay tu **cash** (khong force-close). Neu ky quy dang lock nhieu, co ngay rut < 1% equity.",
        "Paper only; khong anh huong bot live.",
        "",
    ]
    out = DOCS / "backtest_MULTI_donchian_20major_withdraw_1pct_day_15m_365d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
