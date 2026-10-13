#!/usr/bin/env python3
"""Backtest the live window: market Config A (old) vs dual post-only limit (new) vs actual live.

Signals reuse the bot's rules (src/donchian/signals.check_signal), vectorised and checked
against the original function. Klines come from Binance public /fapi/v1/klines (no API key),
cached under data/bt_klines_15m/live_window/.

Usage:
  .venv/bin/python scripts/backtest_live_window_dual.py --live-json /tmp/livebt/live.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.donchian.signals import DonchianBar, SignalState, check_signal  # noqa: E402

CACHE = ROOT / "data" / "bt_klines_15m" / "live_window"
BAR_MS = 15 * 60 * 1000
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "TRXUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "LINKUSDT", "LTCUSDT", "BCHUSDT", "XLMUSDT", "ATOMUSDT", "NEARUSDT", "APTUSDT",
    "SUIUSDT", "ARBUSDT", "OPUSDT", "UNIUSDT",
]
PERIOD, SLOPE_LB, TOL = 20, 5, 0.015
ATR_P, BODY_MIN, BODY_MAX, RR_MIN = 14, 0.3, 1.2, 0.5
VOL_MIN, VOL_P = 1.2, 20
LEVERAGE = 10
MARGIN_PCT, MARGIN_CAP = 0.01, 0.15
MAX_OPEN, TOP_K = 10, 5
STALE_H, ADD_ATR = 6.0, 0.4
MAX_COINS, TTL_BARS = 10, 1
TAKER, MAKER = 0.0005, 0.0002


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_{start_ms}_{end_ms}.csv"
    if path.is_file():
        return pd.read_csv(path)
    rows: list[list] = []
    cur = start_ms
    while cur < end_ms:
        url = (
            "https://fapi.binance.com/fapi/v1/klines"
            f"?symbol={symbol}&interval=15m&startTime={cur}&endTime={end_ms}&limit=1500"
        )
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "bt/1"}), timeout=30) as r:
            chunk = json.loads(r.read())
        if not chunk:
            break
        rows.extend(chunk)
        cur = int(chunk[-1][0]) + BAR_MS
        time.sleep(0.4)
    df = pd.DataFrame(
        [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[7])] for k in rows],
        columns=["ts", "open", "high", "low", "close", "volume", "quote_volume"],
    ).drop_duplicates("ts")
    df = df[df["ts"] + BAR_MS <= end_ms].reset_index(drop=True)  # closed bars only
    df.to_csv(path, index=False)
    return df


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    h, l, c, o = df["high"], df["low"], df["close"], df["open"]
    up = h.rolling(PERIOD).max()
    dn = l.rolling(PERIOD).min()
    su = (up - up.shift(SLOPE_LB)) / SLOPE_LB / c * 100.0
    sl = (dn - dn.shift(SLOPE_LB)) / SLOPE_LB / c * 100.0
    par = ((su - sl).abs() <= TOL) | su.isna() | sl.isna()
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    tr.iloc[0] = np.nan
    atr = tr.rolling(ATR_P).mean()
    qv = df["quote_volume"].where(df["quote_volume"] > 0, df["volume"] * c)
    vr = qv / qv.rolling(VOL_P).mean()
    vr = vr.where(qv.rolling(VOL_P).min() > 0)
    out = df.copy()
    out["up"], out["dn"], out["mid"], out["par"], out["atr"], out["vr"] = up, dn, (up + dn) / 2, par, atr, vr
    # TP band seen by the live watcher during bar j: prior 19 highs/lows + the forming bar.
    out["tp_up"] = h.shift(1).rolling(PERIOD - 1).max()
    out["tp_dn"] = l.shift(1).rolling(PERIOD - 1).min()
    return out


@dataclass
class Sig:
    side: str
    px: float
    pot: float
    mult: float
    atr: float
    up: float
    dn: float


@dataclass
class SymState:
    trend: str | None = None
    waiting: bool = False
    prev_par: bool = False


def step_signal(st: SymState, b) -> Sig | None:
    """Vectorised twin of check_signal(allow_entry=True) for one closed bar."""
    if np.isnan(b.up):
        return None
    par = bool(b.par)
    if st.prev_par and not par:
        st.trend = "up" if b.close > b.mid else "down"
        st.waiting = True
    sig = None
    if st.waiting and st.trend is not None:
        counter = (st.trend == "up" and b.close < b.open) or (st.trend == "down" and b.close > b.open)
        if counter and not par:
            side = "long" if st.trend == "up" else "short"
            tp = b.up if side == "long" else b.dn
            opp = b.dn if side == "long" else b.up
            dsl = abs(b.close - opp)
            pot = abs(tp - b.close) / dsl if dsl > 1e-12 else 0.0
            if np.isnan(b.atr) or b.atr <= 1e-12:
                st.prev_par = par
                return None
            body = abs(b.close - b.open) / b.atr
            if body < BODY_MIN or body > BODY_MAX or pot < RR_MIN:
                st.prev_par = par
                return None
            if np.isnan(b.vr) or b.vr < VOL_MIN:
                st.prev_par = par
                return None
            sig = Sig(side, float(b.close), pot, float(min(2.0, max(0.0, pot))), float(b.atr), float(b.up), float(b.dn))
            st.waiting = False
    st.prev_par = par
    return sig


def verify_against_bot(df: pd.DataFrame, n: int = 1200) -> int:
    """Run the bot's check_signal on a sliding window and compare signal bars + sides."""
    e = enrich(df).iloc[: n].reset_index(drop=True)
    bars = [DonchianBar(int(r.ts), r.open, r.high, r.low, r.close, r.volume, r.quote_volume) for r in e.itertuples()]
    st_bot, st_vec = SignalState(), SymState()
    mismatches = 0
    win = PERIOD + SLOPE_LB + ATR_P + VOL_P + 5
    for i in range(win, len(bars)):
        a = check_signal(
            bars[i - win + 1 : i + 1], st_bot, period=PERIOD, slope_lookback=SLOPE_LB, tol=TOL,
            atr_period=ATR_P, min_body_atr=BODY_MIN, max_body_atr=BODY_MAX, min_pot_rr=RR_MIN,
            min_vol_ratio=VOL_MIN, vol_ratio_period=VOL_P,
        )
        b = step_signal(st_vec, e.iloc[i])
        if (a is None) != (b is None) or (a and b and (a.side != b.side or abs(a.pot_rr - b.pot) > 1e-9)):
            mismatches += 1
    return mismatches


@dataclass
class Lot:
    sym: str
    side: str
    entry: float
    qty: float
    margin: float
    entry_i: int
    rule: str
    atr: float = 0.0
    fee_open: float = 0.0
    added: bool = False
    opened_ms: int = 0


@dataclass
class Result:
    name: str
    closed: list = field(default_factory=list)
    eq_curve: list = field(default_factory=list)
    max_lots: int = 0
    max_margin_pct: float = 0.0
    min_avail_pct: float = 100.0
    placed_pairs: int = 0
    filled_legs: int = 0
    open_lots: list = field(default_factory=list)
    final_unreal: float = 0.0


def pnl(side: str, entry: float, px: float, qty: float) -> float:
    return (px - entry) * qty if side == "long" else (entry - px) * qty


def simulate(mode: str, data: dict[str, pd.DataFrame], ts: list[int], start_ms: int, capital: float) -> Result:
    res = Result(mode)
    cash = capital
    lots: list[Lot] = []
    pending: list[dict] = []  # dual: {sym, side, px, qty, margin, sig_i}
    states = {s: SymState() for s in data}
    rows = {s: data[s].to_dict("records") for s in data}

    def mark_eq(i: int) -> float:
        return cash + sum(l.margin + pnl(l.side, l.entry, rows[l.sym][i]["close"], l.qty) for l in lots) + sum(
            p["margin"] for p in pending
        )

    def close_lot(l: Lot, px: float, i: int) -> None:
        nonlocal cash
        fee = px * l.qty * MAKER
        pl = pnl(l.side, l.entry, px, l.qty) - fee - l.fee_open
        cash += l.margin + pnl(l.side, l.entry, px, l.qty) - fee
        res.closed.append({"sym": l.sym, "side": l.side, "pnl": pl, "rule": l.rule, "bars": i - l.entry_i, "ts": ts[i]})

    for i, t in enumerate(ts):
        bar = {s: rows[s][i] for s in data}
        live = t >= start_ms

        # 1) exits on this bar (TP limit at live band; not on the entry bar)
        still = []
        for l in lots:
            b = bar[l.sym]
            if i <= l.entry_i or np.isnan(b["tp_up"]):
                still.append(l)
                continue
            if l.side == "long" and b["high"] >= b["tp_up"]:
                close_lot(l, max(b["tp_up"], b["open"]), i)
            elif l.side == "short" and b["low"] <= b["tp_dn"]:
                close_lot(l, min(b["tp_dn"], b["open"]), i)
            else:
                still.append(l)
        lots = still

        # 2) dual: pending limits fill during the bar after the signal, then expire
        if mode in ("dual", "with"):
            keep = []
            for p in pending:
                b = bar[p["sym"]]
                if i <= p["sig_i"]:
                    keep.append(p)
                    continue
                hit = (p["side"] == "long" and b["low"] < p["px"]) or (p["side"] == "short" and b["high"] > p["px"])
                if hit:
                    fee = p["px"] * p["qty"] * MAKER
                    cash -= fee
                    lots.append(Lot(p["sym"], p["side"], p["px"], p["qty"], p["margin"], i, p["rule"], fee_open=fee, opened_ms=t))
                    res.filled_legs += 1
                elif i - p["sig_i"] < TTL_BARS:
                    keep.append(p)
                    continue
                else:
                    cash += p["margin"]
            pending = keep

        eq = mark_eq(i)
        # 3) signals on the closed bar
        cands: list[tuple[str, Sig]] = []
        for s in data:
            sig = step_signal(states[s], type("B", (), bar[s])())
            if sig is None or not live:
                continue
            if mode == "old":
                held = [l for l in lots if l.sym == s]
                if held:
                    if any(l.side == sig.side for l in held):
                        states[s].waiting = True
                        continue
                    mark = bar[s]["close"]
                    stale = any(
                        (t - l.opened_ms) / 3.6e6 >= STALE_H and pnl(l.side, l.entry, mark, l.qty) <= 0 for l in held
                    )
                    if not stale:
                        states[s].waiting = True
                        continue
                states[s].waiting = True
            cands.append((s, sig))

        if live and mode == "old":
            # favorable add before top-k
            for parent in [l for l in lots if l.rule == "standard" and not l.added]:
                if len(lots) >= MAX_OPEN:
                    break
                if i <= parent.entry_i:
                    continue
                px = bar[parent.sym]["close"]
                move = px - parent.entry if parent.side == "long" else parent.entry - px
                if move < ADD_ATR * parent.atr or cash < parent.margin:
                    continue
                fee = px * parent.margin * LEVERAGE / px * TAKER
                cash -= parent.margin + fee
                lots.append(Lot(parent.sym, parent.side, px, parent.margin * LEVERAGE / px, parent.margin, i, "add", fee_open=fee, opened_ms=t))
                parent.added = True
            cands.sort(key=lambda c: c[1].pot, reverse=True)
            for s, sig in cands[:TOP_K]:
                if len(lots) >= MAX_OPEN:
                    break
                margin = min(eq * MARGIN_PCT * sig.mult, eq * MARGIN_CAP)
                if margin <= 0 or cash < margin:
                    states[s].waiting = False
                    continue
                qty = margin * LEVERAGE / sig.px
                fee = sig.px * qty * TAKER
                cash -= margin + fee
                rule = "stale_opp" if any(l.sym == s for l in lots) else "standard"
                lots.append(Lot(s, sig.side, sig.px, qty, margin, i, rule, atr=sig.atr, fee_open=fee, opened_ms=t))
                states[s].waiting = False
        elif live and mode in ("dual", "with"):
            cands.sort(key=lambda c: c[1].pot, reverse=True)
            sides = ("long", "short") if mode == "dual" else None
            for s, sig in cands:
                active = {l.sym for l in lots} | {p["sym"] for p in pending}
                if s not in active and len(active) >= MAX_COINS:
                    continue
                margin = min(eq * MARGIN_PCT * sig.mult, eq * MARGIN_CAP)
                legs = sides or (sig.side,)
                if margin <= 0 or cash < len(legs) * margin:
                    continue
                qty = margin * LEVERAGE / sig.px
                for side in legs:
                    cash -= margin
                    pending.append({
                        "sym": s, "side": side, "px": sig.px, "qty": qty, "margin": margin, "sig_i": i,
                        "rule": "dual_with" if side == sig.side else "dual_counter",
                    })
                res.placed_pairs += 1

        if live:
            eq = mark_eq(i)
            used = sum(l.margin for l in lots) + sum(p["margin"] for p in pending)
            res.eq_curve.append((t, eq))
            res.max_lots = max(res.max_lots, len(lots))
            if eq > 0:
                res.max_margin_pct = max(res.max_margin_pct, used / eq * 100)
                res.min_avail_pct = min(res.min_avail_pct, cash / eq * 100)

    last = len(ts) - 1
    res.open_lots = lots
    res.final_unreal = sum(pnl(l.side, l.entry, rows[l.sym][last]["close"], l.qty) - l.fee_open for l in lots)
    return res


def curve_stats(curve: list[tuple[int, float]]) -> dict:
    eqs = np.array([e for _, e in curve])
    peak = np.maximum.accumulate(eqs)
    dd = (peak - eqs) / peak
    j = int(dd.argmax())
    return {
        "start": float(eqs[0]),
        "end": float(eqs[-1]),
        "min": float(eqs.min()),
        "maxdd": float(dd.max() * 100),
        "maxdd_at": datetime.fromtimestamp(curve[j][0] / 1000, tz=timezone.utc),
    }


def summarise(r: Result, capital: float) -> dict:
    cl = r.closed
    wins = [c["pnl"] for c in cl if c["pnl"] > 0]
    losses = [c["pnl"] for c in cl if c["pnl"] <= 0]
    gl = abs(sum(losses))
    st = curve_stats(r.eq_curve)
    by_rule: dict[str, dict] = {}
    for c in cl:
        g = by_rule.setdefault(c["rule"], {"n": 0, "w": 0, "pnl": 0.0, "bars": 0})
        g["n"] += 1
        g["w"] += c["pnl"] > 0
        g["pnl"] += c["pnl"]
        g["bars"] += c["bars"]
    for l in r.open_lots:
        by_rule.setdefault(l.rule, {"n": 0, "w": 0, "pnl": 0.0, "bars": 0}).setdefault("open", 0)
        by_rule[l.rule]["open"] = by_rule[l.rule].get("open", 0) + 1
    return {
        "by_rule": by_rule,
        "name": r.name,
        "n": len(cl),
        "wr": len(wins) / len(cl) * 100 if cl else 0.0,
        "pf": sum(wins) / gl if gl > 0 else float("inf"),
        "realized": sum(c["pnl"] for c in cl),
        "unreal": r.final_unreal,
        "total": sum(c["pnl"] for c in cl) + r.final_unreal,
        "open_n": len(r.open_lots),
        "worst_trade": min((c["pnl"] for c in cl), default=0.0),
        "max_lots": r.max_lots,
        "max_margin_pct": r.max_margin_pct,
        "min_avail_pct": r.min_avail_pct,
        "pairs": r.placed_pairs,
        "legs": r.filled_legs,
        **st,
        "ret_pct": (st["end"] / capital - 1) * 100,
    }


def live_summary(live: dict, start_ms: int, last_close: dict[str, float]) -> dict:
    start_iso = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat()
    lots = live["lots"]
    closed = [l for l in lots if l["status"] == "closed" and (l["opened_at"] or "") >= start_iso]
    open_ = [l for l in lots if l["status"] == "open"]
    pn = [float(l["pnl_usdt"] or 0) for l in closed]
    wins = [p for p in pn if p > 0]
    gl = abs(sum(p for p in pn if p <= 0))
    unreal = sum(
        pnl(l["side"], float(l["entry_px"]), last_close.get(l["symbol"], float(l["entry_px"])), float(l["size"]))
        for l in open_
    )
    snaps = [(int(datetime.fromisoformat(s["recorded_at"]).timestamp() * 1000), float(s["equity"])) for s in live["snaps"]]
    snaps = [s for s in snaps if s[0] >= start_ms]
    st = curve_stats(snaps) if snaps else {}
    return {
        "n": len(closed), "wr": len(wins) / len(pn) * 100 if pn else 0.0,
        "pf": sum(wins) / gl if gl > 0 else float("inf"), "realized": sum(pn), "unreal": unreal,
        "total": sum(pn) + unreal, "open_n": len(open_), "worst_trade": min(pn, default=0.0), **st,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-json", default="/tmp/livebt/live.json")
    ap.add_argument("--start", default="2026-08-28T09:00:00+00:00")
    ap.add_argument("--out", default=str(ROOT / "docs" / "backtest_live_window_dual.md"))
    args = ap.parse_args()

    live = json.loads(Path(args.live_json).read_text())
    start_ms = int(datetime.fromisoformat(args.start).timestamp() * 1000)
    end_ms = (int(time.time() * 1000) // BAR_MS) * BAR_MS
    warm_ms = start_ms - 4 * 86400 * 1000

    data: dict[str, pd.DataFrame] = {}
    for s in SYMBOLS:
        df = fetch_klines(s, warm_ms, end_ms)
        data[s] = df
        print(f"  {s}: {len(df)} bars", flush=True)

    bad = verify_against_bot(data["SOLUSDT"]) + verify_against_bot(data["DOTUSDT"])
    print(f"signal check vs src.donchian.signals.check_signal: {bad} mismatching bars", flush=True)

    common = sorted(set.intersection(*[set(d["ts"]) for d in data.values()]))
    data = {s: enrich(d.set_index("ts").loc[common].reset_index()) for s, d in data.items()}
    capital = next(float(s["equity"]) for s in live["snaps"] if s["recorded_at"] >= args.start)

    old = summarise(simulate("old", data, common, start_ms, capital), capital)
    dual = summarise(simulate("dual", data, common, start_ms, capital), capital)
    with_only = summarise(simulate("with", data, common, start_ms, capital), capital)
    last_close = {s: float(d["close"].iloc[-1]) for s, d in data.items()}
    lv = live_summary(live, start_ms, last_close)

    end_dt = datetime.fromtimestamp(common[-1] / 1000, tz=timezone.utc)
    lines = [
        "# Backtest giai đoạn live: market Config A (cũ) vs limit 2 chiều (mới)",
        "",
        f"- Cửa sổ: **{args.start[:16]} → {end_dt:%Y-%m-%d %H:%M} UTC** ({(common[-1]-start_ms)/86400000:.1f} ngày), 20 coin, nến 15m",
        f"- Vốn đầu = equity live lúc bắt đầu: **{capital:.2f} USDT** · 10x · margin 1% × size_mult (cap 15%)",
        "- Phí: market taker 0.05%, limit maker 0.02%. Không tính funding.",
        "- Cũ: top 5/cycle, max 10 lot, quy tắc ngược 6h, lệnh 2 (0,4 ATR). Mới: mọi tín hiệu → LONG + SHORT limit @ close, khớp nếu nến sau xuyên giá, hủy sau 1 nến, max 10 coin, không giới hạn lot. Limit 1 chiều: như Mới nhưng chỉ đặt chiều của tín hiệu.",
        "- TP: band Donchian live (19 nến trước + nến đang chạy), không SL, không chốt trên nến vào.",
        f"- Tín hiệu đối chiếu với `check_signal` của bot: {bad} nến lệch.",
        "",
        "| | Live thật | BT cũ (market) | BT mới (2 chiều) | BT limit 1 chiều |",
        "|---|---|---|---|---|",
    ]
    cols = (old, dual, with_only)

    def row(label: str, live_val: str, fmt) -> str:
        return f"| {label} | {live_val} | " + " | ".join(fmt(c) for c in cols) + " |"

    lines += [
        row("Lệnh đã đóng", str(lv["n"]), lambda c: str(c["n"])),
        row("Winrate", f"{lv['wr']:.1f}%", lambda c: f"{c['wr']:.1f}%"),
        row("Profit factor", f"{lv['pf']:.2f}", lambda c: f"{c['pf']:.2f}"),
        row("Lãi đã chốt (USDT)", f"{lv['realized']:+.2f}", lambda c: f"{c['realized']:+.2f}"),
        row("Lãi/lỗ tạm tính cuối kỳ", f"{lv['unreal']:+.2f} ({lv['open_n']} lot)", lambda c: f"{c['unreal']:+.2f} ({c['open_n']} lot)"),
        row("**Tổng**", f"**{lv['total']:+.2f}**", lambda c: f"**{c['total']:+.2f}**"),
        row("Lệnh lỗ nặng nhất", f"{lv['worst_trade']:+.2f}", lambda c: f"{c['worst_trade']:+.2f}"),
        row("Equity thấp nhất", f"{lv.get('min', 0):.2f}", lambda c: f"{c['min']:.2f}"),
        row("**Max drawdown equity**", f"{lv.get('maxdd', 0):.1f}%", lambda c: f"**{c['maxdd']:.1f}%**"),
        row("Lúc drawdown sâu nhất (UTC)", f"{lv['maxdd_at']:%m-%d %H:%M}", lambda c: f"{c['maxdd_at']:%m-%d %H:%M}"),
        row("Số lot mở cùng lúc tối đa", "—", lambda c: str(c["max_lots"])),
        row("Margin bị khóa tối đa (% equity)", "—", lambda c: f"{c['max_margin_pct']:.1f}%"),
        row("Available thấp nhất (% equity)", "—", lambda c: f"{c['min_avail_pct']:.1f}%"),
        row("Lệnh limit đặt / khớp", "—", lambda c: f"{c['pairs']} / {c['legs']}" if c["pairs"] else "—"),
        "",
        "## Tách theo loại lệnh (đã đóng)",
        "",
        "| Kịch bản | Loại | Số lệnh | Winrate | Lãi đã chốt | TB giữ (giờ) | Còn mở cuối kỳ |",
        "|---|---|---|---|---|---|---|",
    ]
    labels = {
        "standard": "lệnh thường", "add": "lệnh 2", "stale_opp": "quy tắc ngược",
        "dual_with": "chân cùng chiều tín hiệu", "dual_counter": "chân ngược chiều tín hiệu",
    }
    for res_name, summ in (("BT cũ", old), ("BT mới", dual), ("BT limit 1 chiều", with_only)):
        for rule, g in sorted(summ["by_rule"].items()):
            wr = g["w"] / g["n"] * 100 if g["n"] else 0.0
            hold = g["bars"] / g["n"] / 4 if g["n"] else 0.0
            lines.append(
                f"| {res_name} | {labels.get(rule, rule)} | {g['n']} | {wr:.1f}% | {g['pnl']:+.2f} | {hold:.1f} | {g.get('open', 0)} |"
            )
    lines += [
        "",
        "Live thật: equity snapshot gồm cả phần đã rút sang spot (nếu có) và nhiều lần đổi config trong kỳ, nên chỉ để tham chiếu.",
    ]
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
