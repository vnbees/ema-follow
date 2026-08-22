#!/usr/bin/env python3
"""D20 hunt: gate entries by recently *closed* book trend.

Cache-only. Does not modify live bot or other BT scripts.

Idea
----
Donchian already has a per-symbol trend. The extra filter asks: *what has
the shared book actually been paying?* Recent **winning** closes (TP-band)
are a cleaner vote than losers, because WR is high — a cluster of long TPs
means the tape is paying longs; fading that with a fresh short is the
common D20 pain (one-sided tape, both sides still fire).

If not enough closed history yet → allow (warmup), unless noted.
Fail filter → skip this bar, keep waiting_entry (retry later).
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
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
    spec = importlib.util.spec_from_file_location("hunt_pct_ct", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_pct_ct"] = mod
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
        print(f"  {sym} bars={len(df)}", flush=True)
    return dfs


@dataclass
class Gate:
    name: str
    note: str
    kind: str  # none | last_win | last_any | win_maj | any_maj | win_all | pnl | window_win | sym_last_win | sym_last_same
    n: int = 5
    # window in bars for window_win
    window_bars: int = 16  # 4h on 15m
    deny_if_short: bool = False  # if True, skip when history too short


def closed_allows(
    cand_side: str, cand_sym: str, closed: list[dict], gate: Gate, *, bar_ts: int
) -> bool:
    if gate.kind == "none":
        return True

    if gate.kind in {"sym_last_win", "sym_last_same"}:
        same = [t for t in closed if t["sym"] == cand_sym]
        if not same:
            return not gate.deny_if_short
        last = same[-1]
        if gate.kind == "sym_last_win":
            return last["pnl"] > 0
        return last["side"] == cand_side

    if gate.kind == "window_win":
        if not closed:
            return not gate.deny_if_short
        cutoff = bar_ts - gate.window_bars * BAR_MS
        wins = [t for t in closed if t["pnl"] > 0 and t["ts"] >= cutoff]
        if len(wins) < 2:
            return not gate.deny_if_short
        n_long = sum(1 for t in wins if t["side"] == "long")
        n_short = len(wins) - n_long
        maj = "long" if n_long > n_short else "short" if n_short > n_long else None
        return maj is None or maj == cand_side

    wins = [t for t in closed if t["pnl"] > 0]
    if gate.kind in {"last_win", "win_maj", "win_all"}:
        pool = wins
    elif gate.kind in {"last_any", "any_maj", "pnl"}:
        pool = closed
    else:
        return True

    if gate.kind == "last_win":
        if not pool:
            return not gate.deny_if_short
        return pool[-1]["side"] == cand_side
    if gate.kind == "last_any":
        if not pool:
            return not gate.deny_if_short
        return pool[-1]["side"] == cand_side

    tail = pool[-gate.n :] if pool else []
    if len(tail) < gate.n:
        return not gate.deny_if_short

    if gate.kind == "pnl":
        pl = sum(t["pnl"] for t in tail if t["side"] == "long")
        ps = sum(t["pnl"] for t in tail if t["side"] == "short")
        if abs(pl - ps) < 1e-9:
            return True
        maj = "long" if pl > ps else "short"
        return maj == cand_side

    n_long = sum(1 for t in tail if t["side"] == "long")
    n_short = len(tail) - n_long
    if gate.kind == "win_all":
        return all(t["side"] == cand_side for t in tail)
    # majority; tie → allow
    if n_long == n_short:
        return True
    maj = "long" if n_long > n_short else "short"
    return maj == cand_side


def run(hunt, dfs: dict[str, pd.DataFrame], gate: Gate) -> dict:
    ts_sets = [set(df["ts"].tolist()) for df in dfs.values()]
    common = sorted(set.intersection(*ts_sets)) if ts_sets else []
    if len(common) < 500:
        return {"name": gate.name, "error": "no common ts"}

    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed.keys())
    cash = CAPITAL
    opens: list[dict] = []
    trades: list[dict] = []
    closed: list[dict] = []
    state = {sym: {"trend": None, "waiting": False} for sym in symbols}
    peak = CAPITAL
    maxdd = 0.0
    skipped = 0

    def equity(mark: dict[str, float]) -> float:
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

        still: list[dict] = []
        for t in opens:
            b = bar[t["sym"]]
            if np.isnan(b["dc_upper"]) or np.isnan(b["dc_lower"]):
                still.append(t)
                continue
            hi, lo = float(b["high"]), float(b["low"])
            up, dn = float(b["dc_upper"]), float(b["dc_lower"])
            tp = up if t["side"] == "long" else dn
            if (t["side"] == "long" and hi >= tp) or (t["side"] == "short" and lo <= tp):
                pnl = hunt._pnl(t["side"], t["entry"], tp, t["qty"])
                cash += t["margin"] + pnl
                rec = {"pnl": pnl, "sym": t["sym"], "side": t["side"], "reason": "TP_BAND", "ts": ts}
                trades.append(rec)
                closed.append(rec)
            else:
                still.append(t)
        opens = still

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
                        candidates.append({"sym": sym, "side": side, "px": px, "pot": pot, "sl0": sl_opp})
        candidates.sort(key=lambda x: x["pot"], reverse=True)

        for cand in candidates:
            if len(opens) >= 20:
                break
            if stack(cand["sym"]) > 0:
                continue
            if not closed_allows(cand["side"], cand["sym"], closed, gate, bar_ts=ts):
                skipped += 1
                continue
            sm = float(np.clip(0.5 + cand["pot"], 0.5, 2.0))
            eq = max(cash + locked(), 0.0)
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
                    "sl0": cand["sl0"],
                }
            )
            state[cand["sym"]]["waiting"] = False

        for sym in symbols:
            if stack(sym) > 0:
                state[sym]["waiting"] = False

        eq = equity(mark)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0.0)

    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    for t in list(opens):
        pnl = hunt._pnl(t["side"], t["entry"], last_mark[t["sym"]], t["qty"])
        cash += t["margin"] + pnl
        trades.append({"pnl": pnl, "sym": t["sym"], "side": t["side"], "reason": "EOD", "ts": common[-1]})
    opens = []

    days = max((common[-1] - common[0]) / 86400000.0, 1e-9)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = cash - CAPITAL
    n_long = sum(1 for t in trades if t["side"] == "long")
    n_short = len(trades) - n_long
    return {
        "name": gate.name,
        "note": gate.note,
        "n": len(trades),
        "n_long": n_long,
        "n_short": n_short,
        "skipped": skipped,
        "wr": (len(wins) / len(trades) * 100) if trades else 0.0,
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "wipe": (gl / gw) if gw > 0 else float("inf"),
        "net": net,
        "end": cash,
        "pct_day": (net / CAPITAL * 100) / days,
        "maxdd": maxdd * 100,
        "trades_per_day": len(trades) / days,
        "days": days,
        "start": fmt_ts(common[0]),
        "end_when": fmt_ts(common[-1]),
    }


def main() -> int:
    hunt = _load_hunt()
    print("Load cache 20 majors (no REST)...", flush=True)
    dfs = load_from_cache(hunt, SYMBOLS_20)
    if len(dfs) < 10:
        print("not enough symbols", flush=True)
        return 1

    gates = [
        Gate("D20_base", "no closed-trend gate", "none"),
        Gate("last1_win", "chỉ vào cùng side lệnh *thắng* gần nhất", "last_win"),
        Gate("last1_any", "chỉ vào cùng side lệnh đóng gần nhất (kể cả lỗ)", "last_any"),
        Gate("win3_maj", "majority 3 lệnh thắng gần nhất (tie=cho)", "win_maj", n=3),
        Gate("win5_maj", "majority 5 lệnh thắng gần nhất (tie=cho)", "win_maj", n=5),
        Gate("win8_maj", "majority 8 lệnh thắng gần nhất (tie=cho)", "win_maj", n=8),
        Gate("any5_maj", "majority 5 lệnh đóng gần nhất", "any_maj", n=5),
        Gate("win3_all", "3 lệnh thắng gần nhất phải cùng side", "win_all", n=3),
        Gate("pnl5", "side có tổng PnL cao hơn trong 5 lệnh đóng gần nhất", "pnl", n=5),
        Gate("pnl10", "side có tổng PnL cao hơn trong 10 lệnh đóng gần nhất", "pnl", n=10),
        Gate("win4h", "majority thắng đóng trong ~4h (16 nến)", "window_win", window_bars=16),
        Gate("win8h", "majority thắng đóng trong ~8h (32 nến)", "window_win", window_bars=32),
        Gate("sym_last_win", "chỉ vào symbol nếu lần đóng *trước đó* của symbol là lãi", "sym_last_win"),
        Gate("sym_last_same", "chỉ vào symbol cùng side lần đóng trước của symbol đó", "sym_last_same"),
    ]

    rows = []
    for g in gates:
        print(f"run {g.name}...", flush=True)
        st = run(hunt, dfs, g)
        rows.append(st)
        if "error" in st:
            print(f"  ERROR {st['error']}", flush=True)
            continue
        print(
            f"  %/d={st['pct_day']:+.3f}% net={st['net']:+.0f} maxDD={st['maxdd']:.1f}% "
            f"PF={st['pf']:.2f} WR={st['wr']:.1f}% n={st['n']} t/d={st['trades_per_day']:.1f} "
            f"skip={st['skipped']} L/S={st['n_long']}/{st['n_short']}",
            flush=True,
        )

    ok = [r for r in rows if "error" not in r]
    base = next(r for r in ok if r["name"] == "D20_base")
    ranked = sorted(ok, key=lambda r: (-r["pct_day"], r["maxdd"]))
    safer = [r for r in ok if r["maxdd"] < base["maxdd"] - 0.5]
    best_safe = max(safer, key=lambda r: r["pct_day"]) if safer else None

    lines = [
        "# D20 — gate entry theo xu hướng lệnh đã đóng",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_closed_trend.py` (cache-only, **không** đụng bot)",
        "- Pool/rule: D20_like_bot (1%, max20, body 0.3–1.2, pot_rr≥0.5), 15m ~365d, 1000$",
        "- Ý: sau khi có tín hiệu Donchian, hỏi **sách lệnh đã đóng** đang trả hướng nào rồi mới vào.",
        "- Warmup: chưa đủ lịch sử đóng → **vẫn cho vào** (trừ khi ghi chú).",
        "- Fail gate → bỏ nến này, **giữ waiting** (thử nến sau).",
        "",
        "## Kết quả",
        "",
        "| Config | %/ngày | Net | MaxDD | PF | WR | n | lệnh/ngày | skip | L/S | Note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for st in ranked:
        lines.append(
            f"| `{st['name']}` | **{st['pct_day']:+.3f}%** | {st['net']:+.0f} | **{st['maxdd']:.1f}%** | "
            f"{st['pf']:.2f} | {st['wr']:.1f}% | {st['n']} | {st['trades_per_day']:.1f} | {st['skipped']} | "
            f"{st['n_long']}/{st['n_short']} | {st['note']} |"
        )
    lines += [
        "",
        "## Đọc",
        "",
        f"- Baseline D20: %/ngày **{base['pct_day']:+.3f}%**, MaxDD **{base['maxdd']:.1f}%**, n={base['n']}",
        f"- Best %/ngày: `{ranked[0]['name']}` → **{ranked[0]['pct_day']:+.3f}%**, MaxDD {ranked[0]['maxdd']:.1f}%",
    ]
    if best_safe:
        lines.append(
            f"- Best vẫn hạ MaxDD: `{best_safe['name']}` → {best_safe['pct_day']:+.3f}%/ngày, "
            f"MaxDD {best_safe['maxdd']:.1f}% (vs {base['maxdd']:.1f}%)"
        )
    else:
        lines.append("- Không có biến thể nào hạ MaxDD rõ mà vẫn giữ lãi gần baseline.")
    lines += [
        "",
        "Paper only — không ảnh hưởng bot live.",
        "",
    ]
    out = DOCS / "backtest_MULTI_donchian_20major_closed_trend_15m_365d.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
