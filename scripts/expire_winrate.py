"""Expire win/loss % for live fade+ATR and toward+TP@signal."""

from __future__ import annotations

import csv
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.donchian.config import BACKTEST_SYMBOLS_20, LEVERAGE
from src.smc.revert import (
    MAKER_FEE,
    MARGIN_USDT,
    TAKER_FEE,
    book_in_profit,
    candle_color,
    entry_side,
    nearest_per_side,
    tp_price,
)
from src.smc.signals import BAR_MS, flipped, last_atr, setup_on_last_bar

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "bt_klines_15m"
TAIL = Path("/tmp/smc_years/k15")
YEAR_START = int(datetime(2025, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
YEAR_END = int(datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
WINDOW = 7 * 24 * 4


def round_px(price: float) -> float:
    p = float(price)
    if p >= 1000:
        return round(p, 1)
    if p >= 100:
        return round(p, 2)
    if p >= 10:
        return round(p, 3)
    if p >= 1:
        return round(p, 4)
    return round(p, 5)


def load(symbol: str):
    rows = []
    for path in sorted(CACHE.glob(f"{symbol}_15m_*.csv")):
        with path.open() as handle:
            for row in csv.DictReader(handle):
                ts = int(float(row.get("ts") or row.get("timestamp") or row["open_time"]))
                if ts < 10_000_000_000:
                    ts *= 1000
                rows.append((ts, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])))
    tail = TAIL / f"{symbol}.json"
    if tail.exists():
        for item in json.loads(tail.read_text()):
            if isinstance(item, dict):
                ts = int(item.get("ts") or item["timestamp"])
                rows.append((ts, float(item["open"]), float(item["high"]), float(item["low"]), float(item["close"])))
            else:
                rows.append((int(item[0]), float(item[1]), float(item[2]), float(item[3]), float(item[4])))
    by = {r[0]: r for r in rows}
    series = [by[t] for t in sorted(by)]
    return (
        [r[0] for r in series],
        [r[1] for r in series],
        [r[2] for r in series],
        [r[3] for r in series],
        [r[4] for r in series],
    )


def toward_side(level, close, color):
    if level <= 0 or color is None:
        return None
    dist = (close - level) / level
    if dist >= 0.01 and color == "red":
        return "short"
    if dist <= -0.01 and color == "green":
        return "long"
    return None


@dataclass
class Sig:
    level: float
    signal_ts: int
    deadline_ts: int


@dataclass
class Ord:
    signal_level: float
    signal_ts: int
    side: str
    bar_ts: int
    entry_px: float
    tp_px: float
    qty: float
    status: str = "pending"
    fill_px: float = 0.0
    fee_open: float = 0.0
    pnl: float = 0.0
    reason: str = ""
    opened_ts: int = 0


def finish(o: Ord, close_px: float, fee_close: float, reason: str) -> None:
    o.status = "closed"
    o.reason = reason
    gross = (close_px - o.fill_px) * o.qty if o.side == "long" else (o.fill_px - close_px) * o.qty
    o.pnl = gross - o.fee_open - fee_close


def run(symbol: str, mode: str) -> tuple:
    ts, opens, highs, lows, closes = load(symbol)
    n = len(ts)
    sigs: dict = {}
    orders: list[Ord] = []
    closed: list[Ord] = []
    used: set = set()
    side_used: set = set()
    start = max(400, next(i for i in range(n) if ts[i] >= YEAR_START))
    side_fn = entry_side if mode == "live" else toward_side
    for i in range(start, n):
        now = ts[i]
        for lv in [lv for lv, s in sigs.items() if now >= s.deadline_ts]:
            del sigs[lv]
        for o in list(orders):
            if o.status not in ("pending", "open"):
                continue
            if now < o.signal_ts + WINDOW * BAR_MS:
                continue
            if o.status == "pending":
                o.status = "cancelled"
                o.reason = "expire"
                closed.append(o)
                orders.remove(o)
            else:
                fee = abs(opens[i] * o.qty) * TAKER_FEE
                finish(o, opens[i], fee, "expire")
                closed.append(o)
                orders.remove(o)
        for o in list(orders):
            if o.status == "pending":
                hit = lows[i] <= o.entry_px if o.side == "long" else highs[i] >= o.entry_px
                if hit:
                    o.status = "open"
                    o.fill_px = o.entry_px
                    o.fee_open = abs(o.fill_px * o.qty) * MAKER_FEE
                    o.opened_ts = now
            if o.status != "open":
                continue
            hit = highs[i] >= o.tp_px if o.side == "long" else lows[i] <= o.tp_px
            if hit:
                fee = abs(o.tp_px * o.qty) * MAKER_FEE
                finish(o, o.tp_px, fee, "tp")
                closed.append(o)
                orders.remove(o)
        if YEAR_START <= now <= YEAR_END:
            lo = max(0, i - 399)
            setup = setup_on_last_bar(
                ts[lo : i + 1], opens[lo : i + 1], highs[lo : i + 1], lows[lo : i + 1], closes[lo : i + 1]
            )
            if setup:
                fo = flipped(setup)
                level = round_px(fo.entry)
                if level > 0:
                    dl = int(fo.signal_ts) + WINDOW * BAR_MS
                    ex = sigs.get(level)
                    if ex is None or int(fo.signal_ts) < ex.signal_ts:
                        sigs[level] = Sig(level, int(fo.signal_ts), dl)
        if not (YEAR_START <= now <= YEAR_END) or not sigs:
            continue
        color = candle_color(opens[i], closes[i])
        close = closes[i]
        opens_rows = [
            {"status": "open", "side": o.side, "fill_px": o.fill_px, "qty": o.qty, "fee_open": o.fee_open}
            for o in orders
            if o.status == "open"
        ]
        if book_in_profit(opens_rows, close):
            continue
        atr = None
        if mode == "live":
            atr = last_atr(highs[max(0, i - 20) : i + 1], lows[max(0, i - 20) : i + 1], closes[max(0, i - 20) : i + 1])
            if atr is None or atr <= 0:
                continue
        cands = []
        for s in sigs.values():
            if now >= s.deadline_ts:
                continue
            side = side_fn(s.level, close, color)
            if side is None:
                continue
            if (s.level, now) in used or (side, now) in side_used:
                continue
            entry = round_px(close)
            if side_fn(s.level, entry, color) is None:
                continue
            cands.append(({"level": s.level, "signal_ts": s.signal_ts}, side, abs(close - s.level) / s.level))
        if not cands:
            continue
        grouped: dict[str, list[float]] = {}
        for item, side, _d in cands:
            grouped.setdefault(side, []).append(float(item["level"]))
        for item, side in nearest_per_side(cands):
            entry = round_px(close)
            if mode == "live":
                tp = round_px(tp_price(side, entry, float(atr)))
            else:
                tp = round_px(float(item["level"]))
            if side == "long" and tp <= entry:
                continue
            if side == "short" and tp >= entry:
                continue
            if mode != "live" and abs(tp - entry) / entry < 2 * MAKER_FEE:
                continue
            qty = (MARGIN_USDT * LEVERAGE) / entry
            od = Ord(float(item["level"]), int(item["signal_ts"]), side, now, entry, tp, qty)
            orders.append(od)
            used.add((od.signal_level, now))
            side_used.add((side, now))
            for other in grouped[side]:
                used.add((other, now))
    for o in list(orders):
        if o.status == "open":
            fee = abs(closes[-1] * o.qty) * TAKER_FEE
            finish(o, closes[-1], fee, "eod")
            closed.append(o)
    exp = [o for o in closed if o.status == "closed" and o.reason == "expire"]
    if not exp:
        return symbol, 0, 0, 0, 0.0, 0.0
    w = sum(1 for o in exp if o.pnl > 0)
    l = sum(1 for o in exp if o.pnl <= 0)
    return symbol, len(exp), w, l, sum(o.pnl for o in exp if o.pnl > 0), sum(o.pnl for o in exp if o.pnl <= 0)


def summarize(mode: str, label: str) -> None:
    print(f"=== {label} | đóng market vì hết 7 ngày ===")
    rows = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(run, s, mode): s for s in BACKTEST_SYMBOLS_20}
        for fut in as_completed(futs):
            rows.append(fut.result())
    rows.sort(key=lambda r: r[0])
    tw = tl = tn = 0
    pw = pl = 0.0
    print(f"{'SYMBOL':10s} {'N':>6s} {'LÃI%':>7s} {'LỖ%':>7s} {'W':>5s} {'L':>5s}")
    for sym, n, w, l, pwin, ploss in rows:
        if n == 0:
            continue
        print(f"{sym:10s} {n:6d} {100 * w / n:6.1f}% {100 * l / n:6.1f}% {w:5d} {l:5d}")
        tw += w
        tl += l
        tn += n
        pw += pwin
        pl += ploss
    print(f"{'ALL':10s} {tn:6d} {100 * tw / tn:6.1f}% {100 * tl / tn:6.1f}% {tw:5d} {tl:5d}")
    print(f"PnL expire thắng={pw:+.2f} | thua={pl:+.2f} | net={pw + pl:+.2f}")
    print()


if __name__ == "__main__":
    summarize("live", "Rule live: fade + TP ATR")
    summarize("toward", "Toward + TP tại mức tín hiệu")
