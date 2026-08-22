#!/usr/bin/env python3
"""D20 hunt: time-window *book* vote (all coins, PnL / wins) → only long or short.

Cache-only. Does not modify live bot.

Why not last-N closes
---------------------
A global last-winner vote locks the book to one side. A **time window** on
*all* coins, plus **neutral unless the sample is two-sided / strong**,
lets the opposite side re-enter when the tape is mixed.

Windows (15m): 2h=8, 4h=16, 8h=32, 24h=96 bars.
Hold time in this strategy is often ~1–3h, so 8h ≈ a few cycles; 24h ≈ 1 day.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
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
    spec = importlib.util.spec_from_file_location("hunt_pct_tw", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_pct_tw"] = mod
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
    kind: str  # none | pnl | wins | setups | pnl_open | breadth
    window_bars: int = 32
    # Neutral unless vote is strong enough
    ratio: float = 1.0  # pnl_long >= ratio * |pnl_short| (and vice versa)
    min_each: int = 0  # need at least this many closes/setups on EACH side, else allow both
    min_n: int = 4  # min total events in window
    wr_lead: float = 0.0  # if >0, require wr_side - wr_other >= this (e.g. 0.10)


@dataclass
class Book:
    cfg: Cfg
    cash: float = CAPITAL
    opens: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    closed: list = field(default_factory=list)
    setups: list = field(default_factory=list)  # all quality signals, even skipped
    state: dict = field(default_factory=dict)
    peak: float = CAPITAL
    maxdd: float = 0.0
    skipped: int = 0
    gated_bars: int = 0  # bars where vote was directional


def window_slice(rows: list[dict], bar_ts: int, bars: int) -> list[dict]:
    if not rows:
        return []
    cutoff = bar_ts - bars * BAR_MS
    # rows append in time order
    out = []
    for t in reversed(rows):
        if t["ts"] < cutoff:
            break
        out.append(t)
    out.reverse()
    return out


def vote_side(book: Book, hunt, mark: dict[str, float], bar_ts: int, bar_trends: dict[str, str | None]) -> str | None:
    """Return 'long' / 'short' to restrict, or None = allow both."""
    cfg = book.cfg
    if cfg.kind == "none":
        return None

    if cfg.kind == "breadth":
        ups = sum(1 for t in bar_trends.values() if t == "up")
        dns = sum(1 for t in bar_trends.values() if t == "down")
        tot = ups + dns
        if tot < cfg.min_n:
            return None
        if ups == dns:
            return None
        lead, other = (ups, dns) if ups > dns else (dns, ups)
        if lead / tot < 0.5 * (1 + (cfg.ratio - 1) / max(cfg.ratio, 1)):  # mild
            if cfg.ratio > 1.01 and lead < other * cfg.ratio:
                return None
        if cfg.ratio > 1.01 and lead < other * cfg.ratio:
            return None
        return "long" if ups > dns else "short"

    if cfg.kind == "setups":
        ev = window_slice(book.setups, bar_ts, cfg.window_bars)
        n_l = sum(1 for t in ev if t["side"] == "long")
        n_s = len(ev) - n_l
        if len(ev) < cfg.min_n:
            return None
        if cfg.min_each and (n_l < cfg.min_each or n_s < cfg.min_each):
            return None
        if n_l == n_s:
            return None
        lead, other = (n_l, n_s) if n_l > n_s else (n_s, n_l)
        if cfg.ratio > 1.01 and lead < other * cfg.ratio:
            return None
        return "long" if n_l > n_s else "short"

    ev = window_slice(book.closed, bar_ts, cfg.window_bars)
    if cfg.kind in {"pnl", "pnl_open"}:
        pl = sum(t["pnl"] for t in ev if t["side"] == "long")
        ps = sum(t["pnl"] for t in ev if t["side"] == "short")
        nl = sum(1 for t in ev if t["side"] == "long")
        ns = sum(1 for t in ev if t["side"] == "short")
        if cfg.kind == "pnl_open":
            for t in book.opens:
                u = hunt._pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
                if t["side"] == "long":
                    pl += u
                    nl += 1
                else:
                    ps += u
                    ns += 1
        if nl + ns < cfg.min_n and cfg.kind != "pnl_open":
            return None
        if cfg.min_each and (nl < cfg.min_each or ns < cfg.min_each):
            return None
        if abs(pl - ps) < 1e-9:
            return None
        lead, other = (pl, ps) if pl > ps else (ps, pl)
        # other can be negative; compare magnitudes of edge
        if cfg.ratio > 1.01:
            # require lead pnl exceed other by ratio if other > 0, or lead > 0 if other <= 0
            if other > 0 and lead < other * cfg.ratio:
                return None
            if other <= 0 and lead <= 0:
                return None
        return "long" if pl > ps else "short"

    if cfg.kind == "wins":
        wins = [t for t in ev if t["pnl"] > 0]
        losses = [t for t in ev if t["pnl"] <= 0]
        nl_w = sum(1 for t in wins if t["side"] == "long")
        ns_w = len(wins) - nl_w
        nl = sum(1 for t in ev if t["side"] == "long")
        ns = len(ev) - nl
        if len(ev) < cfg.min_n:
            return None
        if cfg.min_each and (nl < cfg.min_each or ns < cfg.min_each):
            return None
        if cfg.wr_lead > 0:
            wr_l = (nl_w / nl) if nl else 0.0
            wr_s = (ns_w / ns) if ns else 0.0
            if abs(wr_l - wr_s) < cfg.wr_lead:
                return None
            return "long" if wr_l > wr_s else "short"
        if nl_w == ns_w:
            return None
        lead, other = (nl_w, ns_w) if nl_w > ns_w else (ns_w, nl_w)
        if cfg.ratio > 1.01 and lead < other * cfg.ratio:
            return None
        return "long" if nl_w > ns_w else "short"

    return None


def summarize(book: Book, common: list[int], hunt) -> dict:
    cfg = book.cfg
    days = max((common[-1] - common[0]) / 86400000.0, 1e-9)
    wins = [t for t in book.trades if t["pnl"] > 0]
    losses = [t for t in book.trades if t["pnl"] < 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = book.cash - CAPITAL
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
        "net": net,
        "pct_day": (net / CAPITAL * 100) / days,
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
                    rec = {"pnl": pnl, "sym": t["sym"], "side": t["side"], "ts": ts}
                    book.trades.append(rec)
                    book.closed.append(rec)
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
            for c in cands:
                book.setups.append({"side": c["side"], "ts": ts, "sym": c["sym"]})

            vote = vote_side(book, hunt, mark, ts, bar_trends)
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
                        "sl0": cand["sl0"],
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

    return [summarize(b, common, hunt) for b in books]


def main() -> int:
    hunt = _load_hunt()
    print("Load cache 20 majors...", flush=True)
    dfs = load_from_cache(hunt, SYMBOLS_20)
    if len(dfs) < 10:
        print("not enough symbols", flush=True)
        return 1

    cfgs = [
        Cfg("D20_base", "không lọc (baseline)", "none"),
        # PnL of ALL closed lots in window (all coins)
        Cfg("pnl_2h", "PnL long vs short — đóng trong 2h; lệch là chọn", "pnl", 8, 1.0, 0, 3),
        Cfg("pnl_4h", "PnL đóng trong 4h", "pnl", 16, 1.0, 0, 4),
        Cfg("pnl_8h", "PnL đóng trong 8h (vài vòng hold)", "pnl", 32, 1.0, 0, 6),
        Cfg("pnl_24h", "PnL đóng trong 24h", "pnl", 96, 1.0, 0, 10),
        # Anti-lock: only vote when BOTH sides appear in the window
        Cfg("pnl_8h_both", "PnL 8h — chỉ chặn khi cửa sổ có ≥3 long VÀ ≥3 short", "pnl", 32, 1.0, 3, 8),
        Cfg("pnl_24h_both", "PnL 24h — ≥5 mỗi phía mới vote", "pnl", 96, 1.0, 5, 15),
        Cfg("pnl_8h_1.5x", "PnL 8h + cả 2 phía; chỉ chặn nếu lead ≥1.5×", "pnl", 32, 1.5, 3, 8),
        Cfg("pnl_24h_1.5x", "PnL 24h + 2 phía; lead ≥1.5×", "pnl", 96, 1.5, 5, 15),
        # Win counts
        Cfg("wins_8h_both", "Số lệnh thắng 8h; ≥3 mỗi phía", "wins", 32, 1.0, 3, 8),
        Cfg("wr_8h", "WR long vs short 8h; lệch ≥10pp; ≥4 mỗi phía", "wins", 32, 1.0, 4, 10, 0.10),
        Cfg("wr_24h", "WR 24h; lệch ≥10pp; ≥6 mỗi phía", "wins", 96, 1.0, 6, 16, 0.10),
        # Closed PnL + current open MTM
        Cfg("pnl+mtm_8h", "PnL đã đóng 8h + MTM lệnh đang mở", "pnl_open", 32, 1.0, 0, 4),
        Cfg("pnl+mtm_8h_both", "PnL+MTM 8h; cần 2 phía", "pnl_open", 32, 1.0, 3, 8),
        # Setups (tín hiệu pass filter, kể cả bỏ) — không phụ thuộc lệnh đã vào
        Cfg("setup_8h", "Số tín hiệu L/S pass filter trong 8h", "setups", 32, 1.0, 3, 8),
        Cfg("setup_24h", "Số tín hiệu L/S 24h", "setups", 96, 1.2, 5, 15),
        # Independent of our book: how many coins close above/below Donchian mid
        Cfg("breadth_mid", "Số coin close>mid vs <mid (trend thị trường, không phải sách)", "breadth", 0, 1.3, 0, 12),
    ]

    print(f"run {len(cfgs)} configs in one pass...", flush=True)
    rows = run_all(hunt, dfs, cfgs)
    for st in rows:
        if "error" in st:
            print(f"  ERROR {st}", flush=True)
            continue
        print(
            f"  {st['name']:18s} %/d={st['pct_day']:+7.3f}% DD={st['maxdd']:5.1f}% "
            f"PF={st['pf']:.2f} WR={st['wr']:.1f}% n={st['n']:5d} t/d={st['trades_per_day']:.1f} "
            f"L/S={st['n_long']}/{st['n_short']} bal={st['ls_bal']:.2f} skip={st['skipped']} gate_bars={st['gated_bars']}",
            flush=True,
        )

    ok = [r for r in rows if "error" not in r]
    base = next(r for r in ok if r["name"] == "D20_base")
    ranked = sorted(ok, key=lambda r: (-r["pct_day"], r["maxdd"]))
    safer = [r for r in ok if r["maxdd"] <= base["maxdd"] - 1 and r["ls_bal"] >= 0.25]
    best_safe = max(safer, key=lambda r: r["pct_day"]) if safer else None

    lines = [
        "# D20 — vote xu hướng theo cửa sổ thời gian (cả sách, mọi coin)",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_timewindow_trend.py` (cache-only)",
        "- D20_like_bot 15m 365d 1000$ · sau tín hiệu Donchian, nhìn **toàn bộ lệnh 20 coin** trong cửa sổ.",
        "- Cửa sổ: **2h / 4h / 8h / 24h**. Hold thường ~1–3h → 8h ≈ vài vòng; 24h ≈ 1 ngày.",
        "- **Neutral:** chưa đủ mẫu, hoặc (bản `both`) cửa sổ thiếu một phía → **cho cả long lẫn short** (tránh khóa 1 hướng).",
        "- `ls_bal` = min(L,S)/max(L,S) — gần 1 = không lệch sách; gần 0 = khóa 1 phía.",
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
    out = DOCS / "backtest_MULTI_donchian_20major_timewindow_trend_15m_365d.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
