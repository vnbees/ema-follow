"""Backtest the live OP return-to-signal loop on 15m bars.

Mirrors src/smc/revert.py with a 30-day signal window instead of 7 days:
1 USDT margin × 10x, ≥1% away, green short / red long, one order per side
per candle on the nearest level, ATR(14) limit TP with fee floor, no new
entry while the open book is in profit, cancel/flatten when the signal ages
out. Does not touch the production database.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from src.donchian.config import LEVERAGE
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
TAIL = Path("/tmp/smc_years/k15/OPUSDT.json")
YEAR_START = int(datetime(2025, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
YEAR_END = int(datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
PRICE_PLACE = 5
SYMBOL = "OPUSDT"
WINDOW_BARS = 30 * 24 * 4
# "atr" = live 1×ATR(14)+fee floor. "fee" = old double-fee target (3× round-trip maker).
TP_MODE = "atr"
TP_FEE_FRAC = 6 * MAKER_FEE


@dataclass
class Signal:
    level: float
    signal_ts: int
    deadline_ts: int


@dataclass
class Order:
    signal_level: float
    signal_ts: int
    side: str
    bar_ts: int
    entry_px: float
    tp_px: float
    qty: float
    status: str = "pending"  # pending|open|closed|cancelled
    fill_px: float = 0.0
    fee_open: float = 0.0
    fee_close: float = 0.0
    close_px: float = 0.0
    pnl: float = 0.0
    reason: str = ""
    opened_ts: int = 0
    closed_ts: int = 0


@dataclass
class Book:
    signals: dict[float, Signal] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    closed: list[Order] = field(default_factory=list)
    used: set[tuple[float, int]] = field(default_factory=set)  # level, bar_ts
    side_used: set[tuple[str, int]] = field(default_factory=set)


def _round_px(price: float) -> float:
    return round(float(price), PRICE_PLACE)


def _load() -> tuple[list[int], list[float], list[float], list[float], list[float]]:
    rows: list[tuple[int, float, float, float, float]] = []
    for path in sorted(CACHE.glob(f"{SYMBOL}_15m_*.csv")):
        with path.open() as handle:
            for row in csv.DictReader(handle):
                ts = int(float(row.get("ts") or row.get("timestamp") or row["open_time"]))
                if ts < 10_000_000_000:
                    ts *= 1000
                rows.append((ts, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])))
    if TAIL.exists():
        for item in json.loads(TAIL.read_text()):
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


def _pnl(side: str, entry: float, close: float, qty: float, fee_open: float, fee_close: float) -> float:
    gross = (close - entry) * qty if side == "long" else (entry - close) * qty
    return gross - fee_open - fee_close


def _finish(order: Order, *, close_px: float, fee_close: float, reason: str, ts: int) -> None:
    order.status = "closed"
    order.close_px = close_px
    order.fee_close = fee_close
    order.reason = reason
    order.closed_ts = ts
    order.pnl = _pnl(order.side, order.fill_px, close_px, order.qty, order.fee_open, fee_close)


def _expire_signals(book: Book, now_ts: int, open_: float) -> None:
    dead = [level for level, sig in book.signals.items() if now_ts >= sig.deadline_ts]
    for level in dead:
        del book.signals[level]
    for order in list(book.orders):
        if order.status not in ("pending", "open"):
            continue
        if now_ts < order.signal_ts + WINDOW_BARS * BAR_MS:
            continue
        if order.status == "pending":
            order.status = "cancelled"
            order.reason = "expire"
            order.closed_ts = now_ts
            book.closed.append(order)
            book.orders.remove(order)
            continue
        # Market flatten at this bar's open (first chance after deadline).
        px = open_
        fee = abs(px * order.qty) * TAKER_FEE
        _finish(order, close_px=px, fee_close=fee, reason="expire", ts=now_ts)
        book.closed.append(order)
        book.orders.remove(order)


def _poll_fills(book: Book, ts: int, high: float, low: float) -> None:
    for order in list(book.orders):
        if order.status == "pending":
            hit = low <= order.entry_px if order.side == "long" else high >= order.entry_px
            if not hit:
                continue
            order.status = "open"
            order.fill_px = order.entry_px
            order.fee_open = abs(order.fill_px * order.qty) * MAKER_FEE
            order.opened_ts = ts
            # same-bar TP allowed if range reaches it after the fill
        if order.status != "open":
            continue
        hit_tp = high >= order.tp_px if order.side == "long" else low <= order.tp_px
        if not hit_tp:
            continue
        fee = abs(order.tp_px * order.qty) * MAKER_FEE
        _finish(order, close_px=order.tp_px, fee_close=fee, reason="tp", ts=ts)
        book.closed.append(order)
        book.orders.remove(order)


def _register(book: Book, i: int, ts: list[int], opens: list[float], highs: list[float], lows: list[float], closes: list[float]) -> None:
    if ts[i] < YEAR_START or ts[i] > YEAR_END:
        return
    lo = max(0, i - 399)
    setup = setup_on_last_bar(ts[lo : i + 1], opens[lo : i + 1], highs[lo : i + 1], lows[lo : i + 1], closes[lo : i + 1])
    if setup is None:
        return
    order = flipped(setup)
    level = _round_px(order.entry)
    if level <= 0:
        return
    deadline = int(order.signal_ts) + WINDOW_BARS * BAR_MS
    existing = book.signals.get(level)
    if existing is None:
        book.signals[level] = Signal(level, int(order.signal_ts), deadline)
    elif int(order.signal_ts) < existing.signal_ts:
        book.signals[level] = Signal(level, int(order.signal_ts), deadline)


def _enter(book: Book, i: int, ts: list[int], opens: list[float], highs: list[float], lows: list[float], closes: list[float]) -> None:
    if ts[i] < YEAR_START or ts[i] > YEAR_END:
        return
    if not book.signals:
        return
    color = candle_color(opens[i], closes[i])
    close = closes[i]
    opens_rows = [
        {
            "status": "open",
            "side": o.side,
            "fill_px": o.fill_px,
            "qty": o.qty,
            "fee_open": o.fee_open,
        }
        for o in book.orders
        if o.status == "open"
    ]
    if book_in_profit(opens_rows, close):
        return
    atr = None
    if TP_MODE == "atr":
        lo_atr = max(0, i - 20)
        atr = last_atr(highs[lo_atr : i + 1], lows[lo_atr : i + 1], closes[lo_atr : i + 1])
        if atr is None or atr <= 0:
            return
    bar_ts = ts[i]
    candidates: list[tuple[dict, str, float]] = []
    for sig in book.signals.values():
        if bar_ts >= sig.deadline_ts:
            continue
        side = entry_side(sig.level, close, color)
        if side is None:
            continue
        if (sig.level, bar_ts) in book.used or (side, bar_ts) in book.side_used:
            continue
        entry = _round_px(close)
        if entry_side(sig.level, entry, color) is None:
            continue
        candidates.append(({"level": sig.level, "signal_ts": sig.signal_ts}, side, abs(close - sig.level) / sig.level))
    if not candidates:
        return
    grouped: dict[str, list[float]] = {}
    for item, side, _dist in candidates:
        grouped.setdefault(side, []).append(float(item["level"]))
    for item, side in nearest_per_side(candidates):
        entry = _round_px(close)
        if TP_MODE == "fee":
            raw = entry * (1.0 + TP_FEE_FRAC) if side == "long" else entry * (1.0 - TP_FEE_FRAC)
            tp = _round_px(raw)
        else:
            tp = _round_px(tp_price(side, entry, float(atr)))
        if side == "long" and tp <= entry:
            continue
        if side == "short" and tp >= entry:
            continue
        qty = (MARGIN_USDT * LEVERAGE) / entry
        order = Order(
            signal_level=float(item["level"]),
            signal_ts=int(item["signal_ts"]),
            side=side,
            bar_ts=bar_ts,
            entry_px=entry,
            tp_px=tp,
            qty=qty,
        )
        book.orders.append(order)
        book.used.add((order.signal_level, bar_ts))
        book.side_used.add((side, bar_ts))
        for other in grouped[side]:
            book.used.add((other, bar_ts))


def run(window_days: int, tp_mode: str) -> None:
    global WINDOW_BARS, TP_MODE
    WINDOW_BARS = int(window_days) * 24 * 4
    TP_MODE = tp_mode
    ts, opens, highs, lows, closes = _load()
    n = len(ts)
    print(f"loaded {n} bars {SYMBOL} last={datetime.fromtimestamp(ts[-1]/1000, timezone.utc)}")
    book = Book()
    start_i = next(i for i in range(n) if ts[i] >= YEAR_START)
    start_i = max(400, start_i)
    for i in range(start_i, n):
        _expire_signals(book, ts[i], opens[i])
        _poll_fills(book, ts[i], highs[i], lows[i])
        _register(book, i, ts, opens, highs, lows, closes)
        _enter(book, i, ts, opens, highs, lows, closes)

    last_ts, last_close = ts[-1], closes[-1]
    for order in list(book.orders):
        if order.status == "pending":
            order.status = "cancelled"
            order.reason = "eod"
            order.closed_ts = last_ts
            book.closed.append(order)
        elif order.status == "open":
            fee = abs(last_close * order.qty) * TAKER_FEE
            _finish(order, close_px=last_close, fee_close=fee, reason="eod", ts=last_ts)
            book.closed.append(order)
    book.orders.clear()

    trades = [o for o in book.closed if o.status == "closed"]
    cancels = [o for o in book.closed if o.status == "cancelled"]
    pnls = [o.pnl for o in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    by_reason: dict[str, list[float]] = {}
    for o in trades:
        by_reason.setdefault(o.reason, []).append(o.pnl)

    tp_label = "1×ATR(14) fee-floor" if tp_mode == "atr" else f"double-fee ({TP_FEE_FRAC*100:.2f}% move)"
    print()
    print("=== OP revert backtest ===")
    print(
        f"window={window_days}d from signal | margin=1 USDT | leverage=10 | "
        f"TP={tp_label}"
    )
    print("period=2025-10-01 → 2026-09-30 | fees: maker entry/TP, taker expire")
    print(f"signals still active at end: {len(book.signals)}")
    print(f"orders placed: {len(book.closed)}  filled: {len(trades)}  cancelled unfilled: {len(cancels)}")
    if not trades:
        print("no filled trades")
        return
    total = sum(pnls)
    print(f"PnL total: {total:+.4f} USDT")
    print(f"winrate: {100*len(wins)/len(trades):.1f}% ({len(wins)}W / {len(losses)}L)")
    print(
        f"avg trade: {total/len(trades):+.4f} | avg win: {sum(wins)/len(wins):+.4f} | "
        f"avg loss: {sum(losses)/len(losses):+.4f}"
    )
    print(f"best: {max(pnls):+.4f} | worst: {min(pnls):+.4f}")
    hold_h = sorted((o.closed_ts - o.opened_ts) / 3_600_000 for o in trades if o.opened_ts)
    if hold_h:
        print(f"hold hours median={median(hold_h):.2f} mean={sum(hold_h)/len(hold_h):.2f}")
    for reason, rows in sorted(by_reason.items()):
        print(f"  {reason}: n={len(rows)} pnl={sum(rows):+.4f} avg={sum(rows)/len(rows):+.4f}")
    events: list[tuple[int, int]] = []
    for o in trades:
        events.append((o.opened_ts, 1))
        events.append((o.closed_ts, -1))
    events.sort()
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    print(f"max concurrent open positions: {peak}")
    print(f"margin locked at peak ≈ {peak * MARGIN_USDT:.1f} USDT")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--tp", choices=("atr", "fee"), default="atr")
    args = parser.parse_args()
    run(args.days, args.tp)
