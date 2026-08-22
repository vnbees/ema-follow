#!/usr/bin/env python3
"""D20 hunt: vote trend from *currently open* MTM only (all 20 coins).

Cache-only. Does not modify live bot.

Snapshot is after same-bar TPs, before new entries = floating PnL of the live book.
No closed-trade window. Neutral (allow both) when sample is thin or one-sided,
except the `_any` variants which vote whenever anything is open (shows lock risk).
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from datetime import datetime
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
    spec = importlib.util.spec_from_file_location("hunt_pct_omt", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_pct_omt"] = mod
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
class Cfg:
    name: str
    note: str
    kind: str  # none | sum | avg | win | cnt | green | pos_sum
    ratio: float = 1.0
    min_each: int = 0
    min_n: int = 1


@dataclass
class Book:
    cfg: Cfg
    cash: float = CAPITAL
    opens: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    state: dict = field(default_factory=dict)
    peak: float = CAPITAL
    maxdd: float = 0.0
    skipped: int = 0
    gated_bars: int = 0


def open_mtm(book: Book, hunt, mark: dict[str, float]) -> dict:
    pl = ps = 0.0
    pl_pos = ps_pos = 0.0
    nl = ns = 0
    wl = ws = 0
    for t in book.opens:
        u = hunt._pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        if t["side"] == "long":
            pl += u
            nl += 1
            if u > 0:
                wl += 1
                pl_pos += u
        else:
            ps += u
            ns += 1
            if u > 0:
                ws += 1
                ps_pos += u
    return {
        "pl": pl, "ps": ps, "nl": nl, "ns": ns,
        "wl": wl, "ws": ws, "pl_pos": pl_pos, "ps_pos": ps_pos,
    }


def pick(long_m: float, short_m: float, ratio: float) -> str | None:
    if abs(long_m - short_m) < 1e-12:
        return None
    lead, other = (long_m, short_m) if long_m > short_m else (short_m, long_m)
    if ratio > 1.01:
        if other > 0 and lead < other * ratio:
            return None
        if other <= 0 and lead <= 0:
            return None
    return "long" if long_m > short_m else "short"


def vote_side(book: Book, hunt, mark: dict[str, float]) -> str | None:
    cfg = book.cfg
    if cfg.kind == "none":
        return None
    m = open_mtm(book, hunt, mark)
    n = m["nl"] + m["ns"]
    if n < cfg.min_n:
        return None
    if cfg.min_each and (m["nl"] < cfg.min_each or m["ns"] < cfg.min_each):
        return None

    if cfg.kind == "sum":
        return pick(m["pl"], m["ps"], cfg.ratio)
    if cfg.kind == "avg":
        if m["nl"] < 1 or m["ns"] < 1:
            return None
        return pick(m["pl"] / m["nl"], m["ps"] / m["ns"], cfg.ratio)
    if cfg.kind == "win":
        return pick(float(m["wl"]), float(m["ws"]), cfg.ratio)
    if cfg.kind == "cnt":
        return pick(float(m["nl"]), float(m["ns"]), cfg.ratio)
    if cfg.kind == "green":
        # Only restrict if exactly one side's *total* MTM is green.
        lg, sg = m["pl"] > 0, m["ps"] > 0
        if lg == sg:
            return None
        return "long" if lg else "short"
    if cfg.kind == "pos_sum":
        if m["pl_pos"] == 0 and m["ps_pos"] == 0:
            return None
        return pick(m["pl_pos"], m["ps_pos"], cfg.ratio)
    return None


def summarize(book: Book, common: list[int]) -> dict:
    cfg = book.cfg
    days = max((common[-1] - common[0]) / 86400000.0, 1e-9)
    wins = [t for t in book.trades if t["pnl"] > 0]
    losses = [t for t in book.trades if t["pnl"] < 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    n_long = sum(1 for t in book.trades if t["side"] == "long")
    n_short = len(book.trades) - n_long
    ls_ratio = (min(n_long, n_short) / max(n_long, n_short)) if max(n_long, n_short) else 1.0
    return {
        "name": cfg.name,
        "note": cfg.note,
        "n": len(book.trades),
        "n_long": n_long,
        "n_short": n_short,
        "ls_bal": ls_ratio,
        "skipped": book.skipped,
        "gated_bars": book.gated_bars,
        "wr": (len(wins) / len(book.trades) * 100) if book.trades else 0.0,
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "net": book.cash - CAPITAL,
        "pct_day": ((book.cash - CAPITAL) / CAPITAL * 100) / days,
        "maxdd": book.maxdd * 100,
        "trades_per_day": len(book.trades) / days,
    }


def run_all(hunt, dfs: dict[str, pd.DataFrame], cfgs: list[Cfg]) -> list[dict]:
    ts_sets = [set(df["ts"].tolist()) for df in dfs.values()]
    common = sorted(set.intersection(*ts_sets)) if ts_sets else []
    if len(common) < 500:
        return [{"name": "err", "error": "no common ts"}]

    indexed = {sym: df.set_index("ts").loc[common] for sym, df in dfs.items()}
    symbols = list(indexed.keys())
    books = [
        Book(cfg=c, state={sym: {"trend": None, "waiting": False} for sym in symbols})
        for c in cfgs
    ]

    def locked(opens):
        return sum(t["margin"] for t in opens)

    def stack(opens, sym):
        return sum(1 for t in opens if t["sym"] == sym)

    def equity(book, mark):
        eq = book.cash
        for t in book.opens:
            eq += t["margin"] + hunt._pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}

        for book in books:
            still = []
            for t in book.opens:
                b = bar[t["sym"]]
                if np.isnan(b["dc_upper"]) or np.isnan(b["dc_lower"]):
                    still.append(t)
                    continue
                hi, lo = float(b["high"]), float(b["low"])
                up, dn = float(b["dc_upper"]), float(b["dc_lower"])
                tp = up if t["side"] == "long" else dn
                if (t["side"] == "long" and hi >= tp) or (t["side"] == "short" and lo <= tp):
                    pnl = hunt._pnl(t["side"], t["entry"], tp, t["qty"])
                    book.cash += t["margin"] + pnl
                    book.trades.append({"pnl": pnl, "sym": t["sym"], "side": t["side"], "ts": ts})
                else:
                    still.append(t)
            book.opens = still

            cands = []
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
                st = book.state[sym]
                if pe:
                    st["trend"] = "up" if px > mid else "down"
                    st["waiting"] = True
                if st["waiting"] and st["trend"] and stack(book.opens, sym) == 0:
                    counter = (st["trend"] == "up" and px < o) or (st["trend"] == "down" and px > o)
                    if counter and not par and w > 1e-12:
                        side = "long" if st["trend"] == "up" else "short"
                        tp_near = up if side == "long" else dn
                        sl_opp = dn if side == "long" else up
                        pot = abs(tp_near - px) / max(abs(px - sl_opp), 1e-12)
                        body = abs(px - o) / a if a > 0 else 0.0
                        if 0.3 <= body <= 1.2 and pot >= 0.5:
                            cands.append({"sym": sym, "side": side, "px": px, "pot": pot, "sl0": sl_opp})
            cands.sort(key=lambda x: x["pot"], reverse=True)

            vote = vote_side(book, hunt, mark)
            if vote is not None:
                book.gated_bars += 1

            for cand in cands:
                if len(book.opens) >= 20:
                    break
                if stack(book.opens, cand["sym"]) > 0:
                    continue
                if vote is not None and cand["side"] != vote:
                    book.skipped += 1
                    continue
                sm = float(np.clip(0.5 + cand["pot"], 0.5, 2.0))
                eq = max(book.cash + locked(book.opens), 0.0)
                notional = min(eq * 0.01 * hunt.LEVERAGE * sm, book.cash * hunt.LEVERAGE)
                if notional < 1e-6:
                    continue
                margin = notional / hunt.LEVERAGE
                if book.cash < margin - 1e-12:
                    continue
                book.cash -= margin
                book.opens.append(
                    {
                        "sym": cand["sym"],
                        "side": cand["side"],
                        "entry": cand["px"],
                        "qty": notional / cand["px"],
                        "margin": margin,
                    }
                )
                book.state[cand["sym"]]["waiting"] = False

            for sym in symbols:
                if stack(book.opens, sym) > 0:
                    book.state[sym]["waiting"] = False

            eq = equity(book, mark)
            if eq > book.peak:
                book.peak = eq
            dd = (book.peak - eq) / book.peak if book.peak > 0 else 0.0
            if dd > book.maxdd:
                book.maxdd = dd

        if i % 5000 == 0 and i:
            print(f"  ... bar {i}/{len(common)}", flush=True)

    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    for book in books:
        for t in list(book.opens):
            pnl = hunt._pnl(t["side"], t["entry"], last_mark[t["sym"]], t["qty"])
            book.cash += t["margin"] + pnl
            book.trades.append({"pnl": pnl, "sym": t["sym"], "side": t["side"], "ts": common[-1]})
        book.opens = []

    return [summarize(b, common) for b in books]


def main() -> int:
    hunt = _load_hunt()
    print("Load cache 20 majors...", flush=True)
    dfs = load_from_cache(hunt, SYMBOLS_20)
    if len(dfs) < 10:
        print("not enough symbols", flush=True)
        return 1

    cfgs = [
        Cfg("D20_base", "không lọc (baseline)", "none"),
        # Pathological: vote whenever anything is open (one-sided book locks)
        Cfg("mtm_sum_any", "Tổng MTM L vs S — vote dù sách 1 phía", "sum", 1.0, 0, 1),
        Cfg("mtm_sum_n4", "Tổng MTM; cần ≥4 lệnh mở", "sum", 1.0, 0, 4),
        Cfg("mtm_sum_both", "Tổng MTM; cần ≥1 long VÀ ≥1 short đang mở", "sum", 1.0, 1, 2),
        Cfg("mtm_sum_both2", "Tổng MTM; ≥2 mỗi phía đang mở", "sum", 1.0, 2, 4),
        Cfg("mtm_sum_1.5x", "Tổng MTM; 2 phía; lead ≥1.5×", "sum", 1.5, 1, 2),
        Cfg("mtm_avg_both", "MTM trung bình / lệnh mỗi phía (cần 2 phía)", "avg", 1.0, 1, 2),
        Cfg("mtm_win_any", "Số lệnh mở đang lãi (MTM>0) L vs S", "win", 1.0, 0, 2),
        Cfg("mtm_win_both", "Số lệnh đang lãi; cần 2 phía đang mở", "win", 1.0, 1, 2),
        Cfg("mtm_cnt_both", "Số lệnh mở L vs S (không nhìn $); cần 2 phía", "cnt", 1.0, 1, 2),
        Cfg("mtm_green", "Chỉ chặn khi đúng 1 phía tổng MTM > 0 (phía kia ≤0)", "green", 1.0, 0, 2),
        Cfg("mtm_pos_both", "Chỉ cộng MTM dương; cần 2 phía đang mở", "pos_sum", 1.0, 1, 2),
    ]

    print(f"run {len(cfgs)} configs in one pass...", flush=True)
    rows = run_all(hunt, dfs, cfgs)
    for st in rows:
        if "error" in st:
            print(f"  ERROR {st}", flush=True)
            continue
        print(
            f"  {st['name']:16s} %/d={st['pct_day']:+7.3f}% DD={st['maxdd']:5.1f}% "
            f"PF={st['pf']:.2f} WR={st['wr']:.1f}% n={st['n']:5d} t/d={st['trades_per_day']:.1f} "
            f"L/S={st['n_long']}/{st['n_short']} bal={st['ls_bal']:.2f} skip={st['skipped']} "
            f"gate_bars={st['gated_bars']}",
            flush=True,
        )

    ok = [r for r in rows if "error" not in r]
    base = next(r for r in ok if r["name"] == "D20_base")
    ranked = sorted(ok, key=lambda r: (-r["pct_day"], r["maxdd"]))
    safer = [r for r in ok if r["maxdd"] <= base["maxdd"] - 1 and r["ls_bal"] >= 0.25]
    best_safe = max(safer, key=lambda r: r["pct_day"]) if safer else None

    lines = [
        "# D20 — vote xu hướng theo MTM lệnh đang mở",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_open_mtm_trend.py` (cache-only)",
        "- D20_like_bot 15m 365d 1000$ · sau TP cùng nến, nhìn **PnL nổi tất cả lệnh còn mở** (20 coin).",
        "- Không dùng lệnh đã đóng. Neutral = cho cả long lẫn short.",
        "- `ls_bal` = min(L,S)/max(L,S). `_any` = vote dù sách 1 phía (dễ khóa).",
        "",
        "| Config | %/ngày | MaxDD | PF | WR | n | t/d | L/S | bal | skip | Note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for st in ranked:
        lines.append(
            f"| `{st['name']}` | **{st['pct_day']:+.3f}%** | **{st['maxdd']:.1f}%** | {st['pf']:.2f} | "
            f"{st['wr']:.1f}% | {st['n']} | {st['trades_per_day']:.1f} | {st['n_long']}/{st['n_short']} | "
            f"{st['ls_bal']:.2f} | {st['skipped']} | {st['note']} |"
        )
    lines += [
        "",
        "## Đọc",
        "",
        f"- Baseline: %/ngày **{base['pct_day']:+.3f}%**, MaxDD **{base['maxdd']:.1f}%**, L/S {base['n_long']}/{base['n_short']}",
    ]
    if best_safe:
        lines.append(
            f"- Best vừa hạ DD vừa không khóa sách (`ls_bal`≥0.25): `{best_safe['name']}` → "
            f"{best_safe['pct_day']:+.3f}%/ngày, MaxDD {best_safe['maxdd']:.1f}%"
        )
    else:
        lines.append("- Không có bản nào vừa hạ MaxDD rõ vừa giữ L/S cân.")
    locked = [r for r in ok if r["ls_bal"] < 0.15 and r["name"] != "D20_base"]
    if locked:
        lines.append(
            "- Bản lệch sách nặng (bal<0.15): "
            + ", ".join(f"`{r['name']}`" for r in locked)
        )
    lines += ["", "Paper only — không ảnh hưởng bot live.", ""]
    out = DOCS / "backtest_MULTI_donchian_20major_open_mtm_trend_15m_365d.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
