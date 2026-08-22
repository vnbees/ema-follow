#!/usr/bin/env python3
"""D20 hunt: detect book trend → same-side full size, counter-trend reduced size.

Cache-only. Does not modify live bot.

Idea (from live read): closed PnL / open MTM say "up" → long enters normal,
short still enters but small (probe for next flip). Neutral → both full size.
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
    spec = importlib.util.spec_from_file_location("hunt_pct_tsz", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_pct_tsz"] = mod
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
    detect: str  # none | pnl | mtm | combo | breadth | green
    window_bars: int = 32
    ratio: float = 1.0
    min_each: int = 0
    min_n: int = 4
    counter_scale: float = 0.25  # size multiplier when against trend


@dataclass
class Book:
    cfg: Cfg
    cash: float = CAPITAL
    opens: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    closed: list = field(default_factory=list)
    state: dict = field(default_factory=dict)
    peak: float = CAPITAL
    maxdd: float = 0.0
    n_align: int = 0
    n_counter: int = 0
    n_neutral: int = 0
    pnl_align: float = 0.0
    pnl_counter: float = 0.0
    gated_bars: int = 0


def window_slice(rows: list[dict], bar_ts: int, bars: int) -> list[dict]:
    if not rows:
        return []
    cutoff = bar_ts - bars * BAR_MS
    out = []
    for t in reversed(rows):
        if t["ts"] < cutoff:
            break
        out.append(t)
    out.reverse()
    return out


def detect_trend(book: Book, hunt, mark: dict[str, float], bar_ts: int, bar_trends: dict) -> str | None:
    """Return 'long' / 'short' bias, or None = neutral (both full size)."""
    cfg = book.cfg
    if cfg.detect == "none":
        return None

    if cfg.detect == "breadth":
        ups = sum(1 for t in bar_trends.values() if t == "up")
        dns = sum(1 for t in bar_trends.values() if t == "down")
        tot = ups + dns
        if tot < cfg.min_n or ups == dns:
            return None
        lead, other = (ups, dns) if ups > dns else (dns, ups)
        if cfg.ratio > 1.01 and lead < other * cfg.ratio:
            return None
        return "long" if ups > dns else "short"

    def from_pnl(pl: float, ps: float, nl: int, ns: int) -> str | None:
        if nl + ns < cfg.min_n:
            return None
        if cfg.min_each and (nl < cfg.min_each or ns < cfg.min_each):
            return None
        if abs(pl - ps) < 1e-12:
            return None
        lead, other = (pl, ps) if pl > ps else (ps, pl)
        if cfg.ratio > 1.01:
            if other > 0 and lead < other * cfg.ratio:
                return None
            if other <= 0 and lead <= 0:
                return None
        return "long" if pl > ps else "short"

    def closed_vote() -> str | None:
        ev = window_slice(book.closed, bar_ts, cfg.window_bars)
        pl = sum(t["pnl"] for t in ev if t["side"] == "long")
        ps = sum(t["pnl"] for t in ev if t["side"] == "short")
        nl = sum(1 for t in ev if t["side"] == "long")
        ns = len(ev) - nl
        return from_pnl(pl, ps, nl, ns)

    def mtm_vote() -> str | None:
        pl = ps = 0.0
        nl = ns = 0
        for t in book.opens:
            u = hunt._pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
            if t["side"] == "long":
                pl += u
                nl += 1
            else:
                ps += u
                ns += 1
        if cfg.detect == "green":
            if nl + ns < cfg.min_n:
                return None
            lg, sg = pl > 0, ps > 0
            if lg == sg:
                return None
            return "long" if lg else "short"
        if cfg.min_each and (nl < cfg.min_each or ns < cfg.min_each):
            return None
        if nl + ns < cfg.min_n:
            return None
        return from_pnl(pl, ps, nl, ns)

    if cfg.detect == "pnl":
        return closed_vote()
    if cfg.detect in {"mtm", "green"}:
        return mtm_vote()
    if cfg.detect == "combo":
        # Agree only; else neutral (safer than fighting)
        a, b = closed_vote(), mtm_vote()
        if a is None or b is None:
            return None
        return a if a == b else None
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
        "n_align": book.n_align,
        "n_counter": book.n_counter,
        "n_neutral": book.n_neutral,
        "pnl_align": book.pnl_align,
        "pnl_counter": book.pnl_counter,
        "gated_bars": book.gated_bars,
        "wr": (len(wins) / len(book.trades) * 100) if book.trades else 0.0,
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "net": book.cash - CAPITAL,
        "pct_day": ((book.cash - CAPITAL) / CAPITAL * 100) / days,
        "maxdd": book.maxdd * 100,
        "trades_per_day": len(book.trades) / days,
        "counter_scale": cfg.counter_scale if cfg.detect != "none" else 1.0,
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
                    rec = {
                        "pnl": pnl,
                        "sym": t["sym"],
                        "side": t["side"],
                        "ts": ts,
                        "role": t.get("role", "neutral"),
                    }
                    book.trades.append(rec)
                    book.closed.append(rec)
                    if rec["role"] == "align":
                        book.pnl_align += pnl
                    elif rec["role"] == "counter":
                        book.pnl_counter += pnl
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
                            cands.append({"sym": sym, "side": side, "px": px, "pot": pot})
            cands.sort(key=lambda x: x["pot"], reverse=True)

            vote = detect_trend(book, hunt, mark, ts, bar_trends)
            if vote is not None:
                book.gated_bars += 1

            for cand in cands:
                if len(book.opens) >= 20:
                    break
                if stack(book.opens, cand["sym"]) > 0:
                    continue
                sm = float(np.clip(0.5 + cand["pot"], 0.5, 2.0))
                role = "neutral"
                if vote is None:
                    book.n_neutral += 1
                elif cand["side"] == vote:
                    role = "align"
                    book.n_align += 1
                else:
                    role = "counter"
                    book.n_counter += 1
                    sm *= book.cfg.counter_scale
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
            role = t.get("role", "neutral")
            book.trades.append({"pnl": pnl, "sym": t["sym"], "side": t["side"], "ts": common[-1], "role": role})
            if role == "align":
                book.pnl_align += pnl
            elif role == "counter":
                book.pnl_counter += pnl
        book.opens = []

    return [summarize(b, common) for b in books]


def main() -> int:
    hunt = _load_hunt()
    print("Load cache 20 majors...", flush=True)
    dfs = load_from_cache(hunt, SYMBOLS_20)
    if len(dfs) < 10:
        print("not enough symbols", flush=True)
        return 1

    # Detect variants × counter scales. Neutral → full size; against → scale.
    cfgs = [
        Cfg("D20_base", "không lọc (baseline)", "none", counter_scale=1.0),
        # closed PnL 2h (best gate from prior hunt) — soft size
        Cfg("pnl2h_x0.50", "PnL đóng 2h; ngược ×0.50", "pnl", 8, 1.0, 0, 3, 0.50),
        Cfg("pnl2h_x0.25", "PnL đóng 2h; ngược ×0.25", "pnl", 8, 1.0, 0, 3, 0.25),
        Cfg("pnl2h_x0.15", "PnL đóng 2h; ngược ×0.15", "pnl", 8, 1.0, 0, 3, 0.15),
        Cfg("pnl8h_x0.25", "PnL đóng 8h; ngược ×0.25", "pnl", 32, 1.0, 0, 6, 0.25),
        Cfg("pnl8h_both_x0.25", "PnL 8h cần 2 phía; ngược ×0.25", "pnl", 32, 1.0, 3, 8, 0.25),
        Cfg("pnl8h_1.5x_x0.25", "PnL 8h lead≥1.5×; ngược ×0.25", "pnl", 32, 1.5, 3, 8, 0.25),
        # open MTM
        Cfg("mtm_both_x0.25", "MTM mở cần 2 phía; ngược ×0.25", "mtm", 0, 1.0, 1, 2, 0.25),
        Cfg("mtm_1.5x_x0.25", "MTM mở lead≥1.5× 2 phía; ngược ×0.25", "mtm", 0, 1.5, 1, 2, 0.25),
        Cfg("mtm_green_x0.25", "Đúng 1 phía MTM>0; ngược ×0.25", "green", 0, 1.0, 0, 2, 0.25),
        Cfg("mtm_green_x0.50", "Đúng 1 phía MTM>0; ngược ×0.50", "green", 0, 1.0, 0, 2, 0.50),
        # closed + MTM must agree
        Cfg("combo_2h_x0.25", "PnL2h ∩ MTM cùng chiều; ngược ×0.25", "combo", 8, 1.0, 0, 3, 0.25),
        Cfg("combo_8h_x0.25", "PnL8h ∩ MTM cùng chiều; ngược ×0.25", "combo", 32, 1.0, 0, 4, 0.25),
        Cfg("combo_8h_x0.50", "PnL8h ∩ MTM; ngược ×0.50", "combo", 32, 1.0, 0, 4, 0.50),
        # market breadth (not book)
        Cfg("breadth_x0.25", "close>mid vs <mid; ngược ×0.25", "breadth", 0, 1.3, 0, 12, 0.25),
        Cfg("breadth_x0.50", "close>mid vs <mid; ngược ×0.50", "breadth", 0, 1.3, 0, 12, 0.50),
    ]

    print(f"run {len(cfgs)} configs in one pass...", flush=True)
    rows = run_all(hunt, dfs, cfgs)
    for st in rows:
        if "error" in st:
            print(f"  ERROR {st}", flush=True)
            continue
        print(
            f"  {st['name']:20s} %/d={st['pct_day']:+7.3f}% DD={st['maxdd']:5.1f}% "
            f"PF={st['pf']:.2f} WR={st['wr']:.1f}% n={st['n']:5d} "
            f"L/S={st['n_long']}/{st['n_short']} bal={st['ls_bal']:.2f} "
            f"A/C/N={st['n_align']}/{st['n_counter']}/{st['n_neutral']} "
            f"pnlA/C={st['pnl_align']:+.0f}/{st['pnl_counter']:+.0f} gate={st['gated_bars']}",
            flush=True,
        )

    ok = [r for r in rows if "error" not in r]
    base = next(r for r in ok if r["name"] == "D20_base")
    ranked = sorted(ok, key=lambda r: (-r["pct_day"], r["maxdd"]))
    # Prefer: DD down without killing %/d too hard, keep L/S balanced
    better_dd = [
        r for r in ok
        if r["name"] != "D20_base"
        and r["maxdd"] <= base["maxdd"] - 2
        and r["pct_day"] >= base["pct_day"] * 0.7
        and r["ls_bal"] >= 0.7
    ]
    best_tradeoff = None
    if better_dd:
        best_tradeoff = max(better_dd, key=lambda r: r["pct_day"] / max(r["maxdd"], 1e-6))
    else:
        # softer: any with DD down and pct >= 50% of base
        soft = [
            r for r in ok
            if r["name"] != "D20_base"
            and r["maxdd"] < base["maxdd"]
            and r["pct_day"] >= base["pct_day"] * 0.5
            and r["ls_bal"] >= 0.7
        ]
        if soft:
            best_tradeoff = max(soft, key=lambda r: r["pct_day"] / max(r["maxdd"], 1e-6))

    lines = [
        "# D20 — detect trend sách → cùng chiều full size, ngược ×scale",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_trend_size.py` (cache-only)",
        "- D20_like_bot 15m 365d 1000$ · **không chặn** lệnh ngược — chỉ giảm size.",
        "- Neutral (mẫu yếu / không đồng thuận) → cả hai phía size đầy đủ.",
        "- `A/C/N` = số lệnh align / counter / lúc neutral; `pnlA/C` = PnL đóng theo role.",
        "",
        "| Config | %/ngày | MaxDD | PF | WR | n | L/S | bal | A/C/N | pnlA/C | Note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- | --- | --- |",
    ]
    for st in ranked:
        lines.append(
            f"| `{st['name']}` | **{st['pct_day']:+.3f}%** | **{st['maxdd']:.1f}%** | {st['pf']:.2f} | "
            f"{st['wr']:.1f}% | {st['n']} | {st['n_long']}/{st['n_short']} | {st['ls_bal']:.2f} | "
            f"{st['n_align']}/{st['n_counter']}/{st['n_neutral']} | "
            f"{st['pnl_align']:+.0f}/{st['pnl_counter']:+.0f} | {st['note']} |"
        )
    lines += [
        "",
        "## Đọc",
        "",
        f"- Baseline: %/ngày **{base['pct_day']:+.3f}%**, MaxDD **{base['maxdd']:.1f}%**",
    ]
    if best_tradeoff:
        lines.append(
            f"- Best tradeoff (hạ DD, giữ ≥50–70% lãi ngày, L/S cân): `{best_tradeoff['name']}` → "
            f"{best_tradeoff['pct_day']:+.3f}%/ngày, MaxDD {best_tradeoff['maxdd']:.1f}%"
        )
    else:
        lines.append("- Không có bản nào vừa hạ DD rõ vừa giữ lãi ngày gần baseline.")
    lines += [
        "",
        "Paper only — không ảnh hưởng bot live.",
        "",
    ]
    out = DOCS / "backtest_MULTI_donchian_20major_trend_size_15m_365d.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
