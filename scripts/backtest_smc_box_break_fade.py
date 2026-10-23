"""Fade SMC box breaks on OP 15m.

Break (close outside [swing lo, swing hi]) is the signal. While the broken
box is live: every green 15m shorts after an up-break, every red 15m longs
after a down-break. Flat all when close is back inside the broken box or
when the SMC box updates. 1 USDT × 10, taker in/out at close. OP only.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from src.donchian.config import LEVERAGE
from src.smc.revert import MAKER_FEE, MARGIN_USDT, TAKER_FEE
from src.smc.signals import SWING

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "bt_klines_15m"
TAIL = Path("/tmp/smc_years/k15/OPUSDT.json")
YEAR_START = int(datetime(2025, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
YEAR_END = int(datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
SYMBOL = "OPUSDT"
PRICE_PLACE = 5


@dataclass
class Order:
    side: str
    bar_ts: int
    fill_px: float
    qty: float
    fee_open: float
    opened_ts: int
    break_dir: str
    status: str = "open"
    fee_close: float = 0.0
    close_px: float = 0.0
    pnl: float = 0.0
    reason: str = ""
    closed_ts: int = 0


@dataclass
class Book:
    orders: list[Order] = field(default_factory=list)
    closed: list[Order] = field(default_factory=list)
    dir: str | None = None  # "up" | "dn"
    locked_hi: float = 0.0
    locked_lo: float = 0.0


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


def _close_one(book: Book, order: Order, close_px: float, reason: str, ts: int, *, fee_rate: float = TAKER_FEE) -> None:
    order.status = "closed"
    order.close_px = close_px
    order.fee_close = abs(close_px * order.qty) * fee_rate
    order.reason = reason
    order.closed_ts = ts
    order.pnl = _pnl(order.side, order.fill_px, close_px, order.qty, order.fee_open, order.fee_close)
    book.closed.append(order)


def _finish(book: Book, close_px: float, reason: str, ts: int) -> None:
    for order in book.orders:
        _close_one(book, order, close_px, reason, ts)
    book.orders.clear()
    book.dir = None
    book.locked_hi = 0.0
    book.locked_lo = 0.0


def _net_if_close(order: Order, close_px: float) -> float:
    fee_close = abs(close_px * order.qty) * TAKER_FEE
    return _pnl(order.side, order.fill_px, close_px, order.qty, order.fee_open, fee_close)


def basket_be(orders: list[Order]) -> float:
    """Limit TP at VWAP, bumped until the basket nets >= 0 after maker exit."""
    q = sum(o.qty for o in orders)
    if q <= 0:
        return 0.0
    side = orders[0].side
    cost = sum(o.fill_px * o.qty for o in orders)
    fees = sum(o.fee_open for o in orders)
    raw = (cost + fees) / (q * (1.0 - MAKER_FEE)) if side == "long" else (cost - fees) / (q * (1.0 + MAKER_FEE))
    px = round(raw, PRICE_PLACE)
    tick = 10 ** (-PRICE_PLACE)
    for _ in range(20):
        net = sum(_pnl(o.side, o.fill_px, px, o.qty, o.fee_open, abs(px * o.qty) * MAKER_FEE) for o in orders)
        if net >= 0:
            return px
        px = round(px + tick if side == "long" else px - tick, PRICE_PLACE)
        if px <= 0:
            break
    return px


def _tp_hit(side: str, tp: float, high: float, low: float) -> bool:
    if tp <= 0:
        return False
    if side == "long":
        return high >= tp
    return low <= tp


def _toward_box(book: Book, open_: float, close: float) -> bool:
    if book.dir == "up":
        return close < open_
    if book.dir == "dn":
        return close > open_
    return False


def _enter(book: Book, side: str, break_dir: str, close: float, ts: int) -> None:
    if close <= 0:
        return
    qty = (MARGIN_USDT * LEVERAGE) / close
    fee = abs(close * qty) * TAKER_FEE
    book.orders.append(
        Order(
            side=side,
            bar_ts=ts,
            fill_px=close,
            qty=qty,
            fee_open=fee,
            opened_ts=ts,
            break_dir=break_dir,
        )
    )


def _bounds(highs: list[float], lows: list[float]) -> tuple[list[float | None], list[float | None]]:
    n = len(highs)
    his: list[float | None] = [None] * n
    los: list[float | None] = [None] * n
    hi = lo = None
    leg = 0
    for i in range(SWING, n):
        window_hi = max(highs[i - SWING + 1 : i + 1])
        window_lo = min(lows[i - SWING + 1 : i + 1])
        prev = leg
        if highs[i - SWING] > window_hi:
            leg = 0
        elif lows[i - SWING] < window_lo:
            leg = 1
        if leg != prev:
            if leg == 1:
                lo = lows[i - SWING]
            else:
                hi = highs[i - SWING]
        his[i] = hi
        los[i] = lo
    return his, los


def run(*, max_stack: int, exit_mode: str) -> dict:
    ts, opens, highs, lows, closes = _load()
    n = len(ts)
    his, los = _bounds(highs, lows)
    book = Book()
    start_i = next(i for i in range(n) if ts[i] >= YEAR_START)
    end_i = next(i for i in range(n - 1, -1, -1) if ts[i] <= YEAR_END)
    start_i = max(SWING, start_i)
    peak = 0

    for i in range(start_i, end_i + 1):
        hi, lo = his[i], los[i]
        close = closes[i]
        now = ts[i]
        if hi is None or lo is None or not hi > lo or close <= 0:
            continue

        closed_this_bar = False
        if book.dir is not None:
            if exit_mode == "stack_avg":
                if len(book.orders) > 1:
                    tp = basket_be(book.orders)
                    if _tp_hit(book.orders[0].side, tp, highs[i], lows[i]):
                        for order in list(book.orders):
                            _close_one(book, order, tp, "avg_tp", now, fee_rate=MAKER_FEE)
                        book.orders.clear()
                        closed_this_bar = True
                elif len(book.orders) == 1 and _toward_box(book, opens[i], close):
                    order = book.orders[0]
                    if _net_if_close(order, close) > 0:
                        _close_one(book, order, close, "toward_tp", now)
                        book.orders.clear()
            elif exit_mode == "toward_profit":
                if _toward_box(book, opens[i], close):
                    keep: list[Order] = []
                    for order in book.orders:
                        if _net_if_close(order, close) > 0:
                            _close_one(book, order, close, "toward_tp", now)
                        else:
                            keep.append(order)
                    book.orders = keep
            else:
                new_box = hi != book.locked_hi or lo != book.locked_lo
                inside = book.locked_lo <= close <= book.locked_hi
                if inside:
                    _finish(book, close, "reenter", now)
                    closed_this_bar = True
                elif exit_mode == "box" and new_box:
                    _finish(book, close, "new_box", now)
                    closed_this_bar = True

        if book.dir is not None and not book.orders and book.locked_lo <= close <= book.locked_hi:
            book.dir = None
            book.locked_hi = 0.0
            book.locked_lo = 0.0

        if book.dir is None and not book.orders:
            if close > hi:
                book.dir = "up"
                book.locked_hi = hi
                book.locked_lo = lo
            elif close < lo:
                book.dir = "dn"
                book.locked_hi = hi
                book.locked_lo = lo

        if book.dir is None or closed_this_bar:
            peak = max(peak, len(book.orders))
            continue

        if len(book.orders) >= max_stack:
            peak = max(peak, len(book.orders))
            continue

        color_green = close > opens[i]
        color_red = close < opens[i]
        if book.dir == "up" and color_green and close > book.locked_hi:
            _enter(book, "short", "up", close, now)
        elif book.dir == "dn" and color_red and close < book.locked_lo:
            _enter(book, "long", "dn", close, now)
        peak = max(peak, len(book.orders))

    if book.orders:
        _finish(book, closes[end_i], "eod", ts[end_i])

    trades = [o for o in book.closed if o.status == "closed"]
    stack_label = "chồng nến" if max_stack > 1 else "max 1"
    exit_label = {
        "box": "vào hộp hoặc hộp mới",
        "reenter": "chỉ vào hộp cũ",
        "toward_profit": "nến về hộp + lãi sau phí",
        "stack_avg": "n>1 TP giá TB hòa vốn; n=1 nến về hộp + lãi",
    }[exit_mode]
    print(f"\n=== OP | {stack_label} | đóng: {exit_label} ===")
    if not trades:
        print("no trades")
        return {"n": 0, "pnl": 0.0, "wr": 0.0, "peak": 0, "dd": 0.0, "label": f"{stack_label} | {exit_label}"}
    pnls = [o.pnl for o in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total = sum(pnls)
    wr = 100 * len(wins) / len(trades)
    print(f"n={len(trades)} pnl={total:+.2f} wr={wr:.1f}%  peak={peak} (~{peak * MARGIN_USDT:.0f} USDT)")
    print(
        f"avg {total/len(trades):+.4f} | win {sum(wins)/len(wins):+.4f} n={len(wins)} | "
        f"loss {sum(losses)/len(losses):+.4f} n={len(losses)}"
    )
    hold_h = sorted((o.closed_ts - o.opened_ts) / 3_600_000 for o in trades if o.opened_ts)
    if hold_h:
        print(f"hold median {median(hold_h):.2f}h mean {sum(hold_h)/len(hold_h):.2f}h")
    by_reason: dict[str, list[float]] = {}
    for o in trades:
        by_reason.setdefault(o.reason, []).append(o.pnl)
    for key, rows in sorted(by_reason.items()):
        w = sum(1 for p in rows if p > 0)
        print(f"  {key}: n={len(rows)} pnl={sum(rows):+.2f} wr={100*w/len(rows):.1f}%")
    equity = 0.0
    peak_eq = 0.0
    dd = 0.0
    for o in sorted(trades, key=lambda x: x.closed_ts):
        equity += o.pnl
        peak_eq = max(peak_eq, equity)
        dd = min(dd, equity - peak_eq)
    print(f"equity peak {peak_eq:+.2f} | max DD {dd:.2f}")
    return {
        "n": len(trades),
        "pnl": total,
        "wr": wr,
        "peak": peak,
        "dd": dd,
        "peak_eq": peak_eq,
        "label": f"{stack_label} | {exit_label}",
    }


if __name__ == "__main__":
    print("1 USDT × 10 | taker close 15m | OP 2025-10-01 → 2026-09-30")
    print("phá lên + xanh → short | phá xuống + đỏ → long")
    rows = [
        run(max_stack=1, exit_mode="toward_profit"),
        run(max_stack=10_000, exit_mode="toward_profit"),
        run(max_stack=10_000, exit_mode="stack_avg"),
    ]
    print("\n=== so sánh ===")
    print(f"{'biến thể':<44} {'n':>6} {'PnL':>9} {'WR%':>7} {'peak':>6} {'DD':>9}")
    for row in rows:
        print(
            f"{row['label']:<44} {row['n']:6d} {row['pnl']:+9.2f} {row['wr']:6.1f}% "
            f"{row['peak']:6d} {row['dd']:9.2f}"
        )
