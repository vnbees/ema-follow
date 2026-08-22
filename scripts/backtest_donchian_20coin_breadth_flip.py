#!/usr/bin/env python3
"""D20 hunt: breadth mid — hard skip vs FLIP side when conflict.

Cache-only. Does not modify live bot.

Live today: signal opposite breadth → skip.
This hunt: signal opposite breadth → open the *other* side (align to vote),
optionally re-check pot_rr after flip.
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
    spec = importlib.util.spec_from_file_location("hunt_pct_bflip", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_pct_bflip"] = mod
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
    mode: str  # none | hard | flip | flip_rr
    ratio: float = 1.3
    min_n: int = 12


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
    flipped: int = 0
    flip_drop: int = 0  # flip attempted but pot_rr fail
    gated_bars: int = 0
    n_natural: int = 0
    n_flip_ok: int = 0
    pnl_natural: float = 0.0
    pnl_flip: float = 0.0


def breadth_vote(bar_trends: dict[str, str | None], ratio: float, min_n: int) -> str | None:
    ups = sum(1 for t in bar_trends.values() if t == "up")
    dns = sum(1 for t in bar_trends.values() if t == "down")
    tot = ups + dns
    if tot < min_n or ups == dns:
        return None
    lead, other = (ups, dns) if ups > dns else (dns, ups)
    if ratio > 1.01 and lead < other * ratio:
        return None
    return "long" if ups > dns else "short"


def flipped_candidate(cand: dict, up: float, dn: float, px: float) -> dict | None:
    """Flip long↔short; recompute pot_rr for new TP/opp bands."""
    side = "short" if cand["side"] == "long" else "long"
    tp = up if side == "long" else dn
    opp = dn if side == "long" else up
    pot = abs(tp - px) / max(abs(px - opp), 1e-12)
    return {
        "sym": cand["sym"],
        "side": side,
        "px": px,
        "pot": pot,
        "body": cand["body"],
        "flipped": True,
    }


def summarize(book: Book, common: list[int]) -> dict:
    cfg = book.cfg
    days = max((common[-1] - common[0]) / 86400000.0, 1e-9)
    wins = [t for t in book.trades if t["pnl"] > 0]
    losses = [t for t in book.trades if t["pnl"] < 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    n_long = sum(1 for t in book.trades if t["side"] == "long")
    n_short = len(book.trades) - n_long
    ls = (min(n_long, n_short) / max(n_long, n_short)) if max(n_long, n_short) else 1.0
    return {
        "name": cfg.name,
        "note": cfg.note,
        "n": len(book.trades),
        "n_long": n_long,
        "n_short": n_short,
        "ls_bal": ls,
        "skipped": book.skipped,
        "flipped": book.flipped,
        "flip_drop": book.flip_drop,
        "n_natural": book.n_natural,
        "n_flip_ok": book.n_flip_ok,
        "pnl_natural": book.pnl_natural,
        "pnl_flip": book.pnl_flip,
        "gated_bars": book.gated_bars,
        "wr": (len(wins) / len(book.trades) * 100) if book.trades else 0.0,
        "pf": (gw / gl) if gl > 0 else float("inf"),
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
        bar_trends: dict[str, str | None] = {}
        for sym in symbols:
            b = bar[sym]
            if np.isnan(b["dc_middle"]):
                bar_trends[sym] = None
            else:
                bar_trends[sym] = "up" if float(b["close"]) > float(b["dc_middle"]) else "down"

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
                    role = t.get("role", "natural")
                    book.trades.append({"pnl": pnl, "sym": t["sym"], "side": t["side"], "ts": ts, "role": role})
                    if role == "flip":
                        book.pnl_flip += pnl
                    else:
                        book.pnl_natural += pnl
                else:
                    still.append(t)
            book.opens = still

            # Raw Donchian candidates (same as live before breadth)
            raw_cands = []
            for sym in symbols:
                b = bar[sym]
                if np.isnan(b["dc_upper"]) or np.isnan(b["dc_lower"]) or np.isnan(b["atr"]):
                    continue
                px, o = float(b["close"]), float(b["open"])
                up, dn, mid = float(b["dc_upper"]), float(b["dc_lower"]), float(b["dc_middle"])
                w, a = float(b["dc_width"]), float(b["atr"])
                pe, par = bool(b["parallel_exit"]), bool(b["bands_parallel"])
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
                            raw_cands.append({
                                "sym": sym, "side": side, "px": px, "pot": pot,
                                "body": body, "up": up, "dn": dn, "flipped": False,
                            })
            raw_cands.sort(key=lambda x: x["pot"], reverse=True)

            vote = None
            if book.cfg.mode != "none":
                vote = breadth_vote(bar_trends, book.cfg.ratio, book.cfg.min_n)
                if vote is not None:
                    book.gated_bars += 1

            # Apply mode → final entry list
            entries: list[dict] = []
            for cand in raw_cands:
                if vote is None or book.cfg.mode == "none":
                    entries.append({**cand, "role": "natural"})
                    continue
                if cand["side"] == vote:
                    entries.append({**cand, "role": "natural"})
                    continue
                # Conflict with breadth
                if book.cfg.mode == "hard":
                    book.skipped += 1
                    continue
                # flip / flip_rr
                book.flipped += 1
                fc = flipped_candidate(cand, cand["up"], cand["dn"], cand["px"])
                if fc is None:
                    book.flip_drop += 1
                    continue
                if book.cfg.mode == "flip_rr" and fc["pot"] < 0.5:
                    book.flip_drop += 1
                    continue
                entries.append({**fc, "role": "flip"})

            entries.sort(key=lambda x: x["pot"], reverse=True)

            for cand in entries:
                if len(book.opens) >= 20:
                    break
                if stack(book.opens, cand["sym"]) > 0:
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
                role = cand.get("role", "natural")
                if role == "flip":
                    book.n_flip_ok += 1
                else:
                    book.n_natural += 1
                book.opens.append(
                    {
                        "sym": cand["sym"],
                        "side": cand["side"],
                        "entry": cand["px"],
                        "qty": notional / cand["px"],
                        "margin": margin,
                        "role": role,
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
            role = t.get("role", "natural")
            book.trades.append({"pnl": pnl, "sym": t["sym"], "side": t["side"], "ts": common[-1], "role": role})
            if role == "flip":
                book.pnl_flip += pnl
            else:
                book.pnl_natural += pnl
        book.opens = []

    return [summarize(b, common) for b in books]


def main() -> int:
    hunt = _load_hunt()
    print("Load cache 20 majors...", flush=True)
    dfs = load_from_cache(hunt, SYMBOLS_20)
    if len(dfs) < 10:
        return 1

    cfgs = [
        Cfg("D20_base", "không breadth (baseline live cũ)", "none"),
        Cfg("breadth_hard", "ngược breadth → SKIP (live hiện tại)", "hard"),
        Cfg("breadth_flip", "ngược breadth → LẬT side vào (không check pot sau flip)", "flip"),
        Cfg("breadth_flip_rr", "ngược breadth → lật; pot_rr flip ≥0.5 mới vào", "flip_rr"),
        # Softer breadth ratio
        Cfg("flip_ratio1.5", "lật; vote cần lead ≥1.5×", "flip", 1.5, 12),
        Cfg("hard_ratio1.5", "skip; vote lead ≥1.5×", "hard", 1.5, 12),
    ]

    print(f"run {len(cfgs)} configs...", flush=True)
    rows = run_all(hunt, dfs, cfgs)
    for st in rows:
        if "error" in st:
            print(f"  ERROR {st}", flush=True)
            continue
        print(
            f"  {st['name']:16s} %/d={st['pct_day']:+7.3f}% DD={st['maxdd']:5.1f}% "
            f"PF={st['pf']:.2f} WR={st['wr']:.1f}% n={st['n']:5d} t/d={st['trades_per_day']:.1f} "
            f"L/S={st['n_long']}/{st['n_short']} skip={st['skipped']} flip={st['flipped']} "
            f"flip_ok={st['n_flip_ok']} drop={st['flip_drop']} "
            f"pnlN/F={st['pnl_natural']:+.0f}/{st['pnl_flip']:+.0f}",
            flush=True,
        )

    ok = [r for r in rows if "error" not in r]
    base = next(r for r in ok if r["name"] == "D20_base")
    hard = next(r for r in ok if r["name"] == "breadth_hard")
    ranked = sorted(ok, key=lambda r: (-r["pct_day"], r["maxdd"]))

    lines = [
        "# D20 — breadth mid: hard SKIP vs FLIP side",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_breadth_flip.py` (cache-only)",
        "- D20_like_bot 15m 365d 1000$ · vote mid như live (`ratio=1.3`, `min_n=12`).",
        "- **hard**: tín hiệu ngược vote → bỏ (live hiện tại).",
        "- **flip**: tín hiệu ngược vote → **lật long↔short**, TP/opp band theo side mới, vào lệnh.",
        "- **flip_rr**: như flip nhưng `pot_rr` sau lật phải ≥ 0.5.",
        "",
        "| Config | %/ngày | MaxDD | PF | WR | n | t/d | L/S | skip | flip_ok | drop | pnlN/F | Note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for st in ranked:
        lines.append(
            f"| `{st['name']}` | **{st['pct_day']:+.3f}%** | **{st['maxdd']:.1f}%** | {st['pf']:.2f} | "
            f"{st['wr']:.1f}% | {st['n']} | {st['trades_per_day']:.1f} | {st['n_long']}/{st['n_short']} | "
            f"{st['skipped']} | {st['n_flip_ok']} | {st['flip_drop']} | "
            f"{st['pnl_natural']:+.0f}/{st['pnl_flip']:+.0f} | {st['note']} |"
        )
    lines += [
        "",
        "## Đọc",
        "",
        f"- Baseline D20: **{base['pct_day']:+.3f}%**/ngày, MaxDD **{base['maxdd']:.1f}%**, {base['trades_per_day']:.1f} lệnh/ngày",
        f"- Live hard: **{hard['pct_day']:+.3f}%**/ngày, MaxDD **{hard['maxdd']:.1f}%**, {hard['trades_per_day']:.1f} lệnh/ngày",
    ]
    flips = [r for r in ok if "flip" in r["name"]]
    if flips:
        best = max(flips, key=lambda r: r["pct_day"])
        lines.append(
            f"- Best flip: `{best['name']}` → {best['pct_day']:+.3f}%/ngày, MaxDD {best['maxdd']:.1f}%, "
            f"flip_ok={best['n_flip_ok']}, pnl flip={best['pnl_flip']:+.0f}"
        )
    lines += ["", "Paper only — không ảnh hưởng bot live.", ""]
    out = DOCS / "backtest_MULTI_donchian_20major_breadth_flip_15m_365d.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
