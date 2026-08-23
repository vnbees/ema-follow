#!/usr/bin/env python3
"""D20 breadth_flip + counter-trend open management (paper only).

Base: breadth_flip (live-like entry flip). Each 15m bar after breadth vote:
  - neutral vote → no action on open book
  - vote LONG/SHORT → lots with side != vote are "counter-trend"

Cases (on top of breadth_flip entries):
  - breadth_flip          baseline (no open-book action)
  - flip_close_ct         case 1: close all counter-trend opens @ bar close
  - flip_close_reopen     case 2: close CT opens + reopen same margin/qty aligned to vote

Does not modify live bot or existing backtest scripts/docs.
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
MAX_OPEN = 20


def _load_hunt():
    path = ROOT / "scripts" / "backtest_hunt_pct_per_day.py"
    spec = importlib.util.spec_from_file_location("hunt_pct_bflip_ct", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_pct_bflip_ct"] = mod
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
    ct_mode: str  # none | close | close_reopen
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
    flip_drop: int = 0
    gated_bars: int = 0
    n_natural: int = 0
    n_flip_ok: int = 0
    pnl_natural: float = 0.0
    pnl_flip: float = 0.0
    ct_closed: int = 0
    ct_reopened: int = 0
    pnl_ct_close: float = 0.0
    pnl_after_reopen: float = 0.0  # subset of trades tagged ct_reopen exit


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


def _close_lot(hunt, book: Book, lot: dict, exit_px: float, ts: int, *, tag: str) -> None:
    pnl = hunt._pnl(lot["side"], lot["entry"], exit_px, lot["qty"])
    book.cash += lot["margin"] + pnl
    role = lot.get("role", "natural")
    book.trades.append(
        {
            "pnl": pnl,
            "sym": lot["sym"],
            "side": lot["side"],
            "ts": ts,
            "role": role,
            "tag": tag,
        }
    )
    if role == "flip":
        book.pnl_flip += pnl
    else:
        book.pnl_natural += pnl
    if tag == "ct_close":
        book.pnl_ct_close += pnl


def summarize(book: Book, common: list[int]) -> dict:
    days = max((common[-1] - common[0]) / 86400000.0, 1e-9)
    wins = [t for t in book.trades if t["pnl"] > 0]
    losses = [t for t in book.trades if t["pnl"] < 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    n_long = sum(1 for t in book.trades if t["side"] == "long")
    n_short = len(book.trades) - n_long
    return {
        "name": book.cfg.name,
        "note": book.cfg.note,
        "n": len(book.trades),
        "n_long": n_long,
        "n_short": n_short,
        "skipped": book.skipped,
        "flipped": book.flipped,
        "flip_drop": book.flip_drop,
        "n_flip_ok": book.n_flip_ok,
        "pnl_natural": book.pnl_natural,
        "pnl_flip": book.pnl_flip,
        "ct_closed": book.ct_closed,
        "ct_reopened": book.ct_reopened,
        "pnl_ct_close": book.pnl_ct_close,
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

        vote = breadth_vote(bar_trends, cfgs[0].ratio, cfgs[0].min_n)

        for book in books:
            # --- normal TP exits ---
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
                    _close_lot(hunt, book, t, tp, ts, tag="tp")
                else:
                    still.append(t)
            book.opens = still

            # --- counter-trend open management (cases 1 & 2) ---
            if vote is not None and book.cfg.ct_mode in ("close", "close_reopen"):
                ct_lots = [t for t in book.opens if t["side"] != vote]
                closed_specs: list[dict] = []
                for t in ct_lots:
                    px = mark[t["sym"]]
                    closed_specs.append(
                        {
                            "sym": t["sym"],
                            "margin": t["margin"],
                            "qty": t["qty"],
                            "old_role": t.get("role", "natural"),
                        }
                    )
                    _close_lot(hunt, book, t, px, ts, tag="ct_close")
                    book.ct_closed += 1
                book.opens = [t for t in book.opens if t["side"] == vote]

                if book.cfg.ct_mode == "close_reopen" and closed_specs:
                    for spec in closed_specs:
                        if len(book.opens) >= MAX_OPEN:
                            break
                        if stack(book.opens, spec["sym"]) > 0:
                            continue
                        sym = spec["sym"]
                        px = mark[sym]
                        margin = spec["margin"]
                        qty = spec["qty"]
                        if book.cash < margin - 1e-12:
                            continue
                        book.cash -= margin
                        book.opens.append(
                            {
                                "sym": sym,
                                "side": vote,
                                "entry": px,
                                "qty": qty,
                                "margin": margin,
                                "role": "ct_reopen",
                            }
                        )
                        book.ct_reopened += 1

            # --- Donchian entry signals (breadth_flip) ---
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
                            raw_cands.append(
                                {
                                    "sym": sym,
                                    "side": side,
                                    "px": px,
                                    "pot": pot,
                                    "body": body,
                                    "up": up,
                                    "dn": dn,
                                    "flipped": False,
                                }
                            )
            raw_cands.sort(key=lambda x: x["pot"], reverse=True)

            entries: list[dict] = []
            for cand in raw_cands:
                if vote is None:
                    entries.append({**cand, "role": "natural"})
                    continue
                if cand["side"] == vote:
                    entries.append({**cand, "role": "natural"})
                    continue
                book.flipped += 1
                fc = flipped_candidate(cand, cand["up"], cand["dn"], cand["px"])
                if fc is None:
                    book.flip_drop += 1
                    continue
                entries.append({**fc, "role": "flip"})

            entries.sort(key=lambda x: x["pot"], reverse=True)

            for cand in entries:
                if len(book.opens) >= MAX_OPEN:
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
            _close_lot(hunt, book, t, last_mark[t["sym"]], common[-1], tag="eod")
        book.opens = []

    return [summarize(b, common) for b in books]


def main() -> int:
    hunt = _load_hunt()
    print("Load cache 20 majors...", flush=True)
    dfs = load_from_cache(hunt, SYMBOLS_20)
    if len(dfs) < 10:
        return 1

    cfgs = [
        Cfg("breadth_flip", "baseline flip (live) — không đụng open book", "none"),
        Cfg("flip_close_ct", "case 1: mỗi 15m đóng hết open ngược breadth vote", "close"),
        Cfg(
            "flip_close_reopen",
            "case 2: đóng CT + mở lại cùng margin/qty theo vote",
            "close_reopen",
        ),
    ]

    print(f"run {len(cfgs)} configs...", flush=True)
    rows = run_all(hunt, dfs, cfgs)
    for st in rows:
        if "error" in st:
            print(f"  ERROR {st}", flush=True)
            continue
        print(
            f"  {st['name']:20s} %/d={st['pct_day']:+7.3f}% DD={st['maxdd']:5.1f}% "
            f"PF={st['pf']:.2f} WR={st['wr']:.1f}% n={st['n']:5d} t/d={st['trades_per_day']:.1f} "
            f"ct_close={st['ct_closed']} reopen={st['ct_reopened']} pnl_ct={st['pnl_ct_close']:+.0f}",
            flush=True,
        )

    ok = [r for r in rows if "error" not in r]
    base = next(r for r in ok if r["name"] == "breadth_flip")

    lines = [
        "# D20 breadth_flip — đóng / reopen lệnh open ngược trend (breadth vote)",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_breadth_flip_ct_reopen.py` (cache-only)",
        "- Base: **breadth_flip** (entry flip như live). Breadth vote mỗi 15m (`ratio=1.3`, `min_n=12`).",
        "- **Case 1 (`flip_close_ct`)**: vote LONG/SHORT → **đóng mọi lot open ngược vote** @ bar close.",
        "- **Case 2 (`flip_close_reopen`)**: như case 1 + **mở lại cùng margin/qty** theo chiều vote.",
        "- Neutral vote → không đụng open book.",
        "",
        "| Config | %/ngày | MaxDD | PF | WR | n | t/d | ct_close | reopen | pnl_ct | flip_ok | Note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for st in ok:
        lines.append(
            f"| `{st['name']}` | **{st['pct_day']:+.3f}%** | **{st['maxdd']:.1f}%** | {st['pf']:.2f} | "
            f"{st['wr']:.1f}% | {st['n']} | {st['trades_per_day']:.1f} | {st['ct_closed']} | "
            f"{st['ct_reopened']} | {st['pnl_ct_close']:+.0f} | {st['n_flip_ok']} | {st['note']} |"
        )
    lines += [
        "",
        "## So với baseline flip",
        "",
        f"- Baseline `breadth_flip`: **{base['pct_day']:+.3f}%**/ngày, MaxDD **{base['maxdd']:.1f}%**, "
        f"PF {base['pf']:.2f}, {base['trades_per_day']:.1f} lệnh/ngày",
    ]
    for st in ok:
        if st["name"] == "breadth_flip":
            continue
        d_pct = st["pct_day"] - base["pct_day"]
        d_dd = st["maxdd"] - base["maxdd"]
        lines.append(
            f"- `{st['name']}`: Δ%/ngày **{d_pct:+.3f}**, ΔMaxDD **{d_dd:+.1f}pp**, "
            f"ct_close={st['ct_closed']}, reopen={st['ct_reopened']}, pnl từ CT close={st['pnl_ct_close']:+.0f}"
        )
    lines += ["", "Paper only — không ảnh hưởng bot live.", ""]
    out = DOCS / "backtest_MULTI_donchian_20major_breadth_flip_ct_reopen_15m_365d.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
