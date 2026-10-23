"""OP live fade + ATR TP, plus a same-size opposite that only closes at expire."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from scripts.backtest_smc_revert import YEAR_END, YEAR_START, _load, _round_px

WINDOW = 7 * 24 * 4


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
    kind: str  # main | hedge
    status: str = "pending"
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
    used: set[tuple[float, int]] = field(default_factory=set)
    side_used: set[tuple[str, int]] = field(default_factory=set)


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


def _summarize(title: str, trades: list[Order]) -> None:
    print(title)
    if not trades:
        print("  no filled trades")
        return
    pnls = [o.pnl for o in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    by: dict[str, list[float]] = {}
    for o in trades:
        by.setdefault(o.reason, []).append(o.pnl)
    holds = [(o.closed_ts - o.opened_ts) / 3_600_000 for o in trades if o.opened_ts]
    events = sorted([(o.opened_ts, 1) for o in trades] + [(o.closed_ts, -1) for o in trades])
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    print(
        f"  filled={len(trades)} pnl={sum(pnls):+.2f} wr={100 * len(wins) / len(trades):.1f}% "
        f"({len(wins)}W/{len(losses)}L) peak={peak}"
    )
    print(
        f"  avg={sum(pnls) / len(trades):+.4f} avgW={sum(wins) / len(wins):+.4f} "
        f"avgL={sum(losses) / len(losses):+.4f}"
        if wins and losses
        else f"  avg={sum(pnls) / len(trades):+.4f}"
    )
    if holds:
        print(f"  hold hours median={median(holds):.2f} mean={sum(holds) / len(holds):.2f}")
    for reason, rows in sorted(by.items()):
        print(f"  {reason}: n={len(rows)} pnl={sum(rows):+.2f} avg={sum(rows) / len(rows):+.4f}")
    print(f"  side {dict(Counter(o.side for o in trades))}")


def run() -> None:
    ts, opens, highs, lows, closes = _load()
    n = len(ts)
    book = Book()
    start = max(400, next(i for i in range(n) if ts[i] >= YEAR_START))
    for i in range(start, n):
        now = ts[i]
        for level in [lv for lv, sig in book.signals.items() if now >= sig.deadline_ts]:
            del book.signals[level]
        for order in list(book.orders):
            if order.status not in ("pending", "open"):
                continue
            if now < order.signal_ts + WINDOW * BAR_MS:
                continue
            if order.status == "pending":
                order.status = "cancelled"
                order.reason = "expire"
                order.closed_ts = now
                book.closed.append(order)
                book.orders.remove(order)
                continue
            fee = abs(opens[i] * order.qty) * TAKER_FEE
            _finish(order, close_px=opens[i], fee_close=fee, reason="expire", ts=now)
            book.closed.append(order)
            book.orders.remove(order)

        for order in list(book.orders):
            if order.status == "pending":
                hit = lows[i] <= order.entry_px if order.side == "long" else highs[i] >= order.entry_px
                if hit:
                    order.status = "open"
                    order.fill_px = order.entry_px
                    order.fee_open = abs(order.fill_px * order.qty) * MAKER_FEE
                    order.opened_ts = now
            if order.status != "open":
                continue
            if order.kind != "main":
                continue
            hit = highs[i] >= order.tp_px if order.side == "long" else lows[i] <= order.tp_px
            if not hit:
                continue
            fee = abs(order.tp_px * order.qty) * MAKER_FEE
            _finish(order, close_px=order.tp_px, fee_close=fee, reason="tp", ts=now)
            book.closed.append(order)
            book.orders.remove(order)

        if YEAR_START <= now <= YEAR_END:
            lo = max(0, i - 399)
            setup = setup_on_last_bar(
                ts[lo : i + 1], opens[lo : i + 1], highs[lo : i + 1], lows[lo : i + 1], closes[lo : i + 1]
            )
            if setup is not None:
                flipped_setup = flipped(setup)
                level = _round_px(flipped_setup.entry)
                if level > 0:
                    deadline = int(flipped_setup.signal_ts) + WINDOW * BAR_MS
                    existing = book.signals.get(level)
                    if existing is None or int(flipped_setup.signal_ts) < existing.signal_ts:
                        book.signals[level] = Signal(level, int(flipped_setup.signal_ts), deadline)

        if not (YEAR_START <= now <= YEAR_END) or not book.signals:
            continue
        color = candle_color(opens[i], closes[i])
        close = closes[i]
        mains = [
            {
                "status": "open",
                "side": order.side,
                "fill_px": order.fill_px,
                "qty": order.qty,
                "fee_open": order.fee_open,
            }
            for order in book.orders
            if order.status == "open" and order.kind == "main"
        ]
        if book_in_profit(mains, close):
            continue
        atr = last_atr(highs[max(0, i - 20) : i + 1], lows[max(0, i - 20) : i + 1], closes[max(0, i - 20) : i + 1])
        if atr is None or atr <= 0:
            continue
        candidates: list[tuple[dict, str, float]] = []
        for sig in book.signals.values():
            if now >= sig.deadline_ts:
                continue
            side = entry_side(sig.level, close, color)
            if side is None:
                continue
            if (sig.level, now) in book.used or (side, now) in book.side_used:
                continue
            entry = _round_px(close)
            if entry_side(sig.level, entry, color) is None:
                continue
            candidates.append(
                ({"level": sig.level, "signal_ts": sig.signal_ts}, side, abs(close - sig.level) / sig.level)
            )
        if not candidates:
            continue
        grouped: dict[str, list[float]] = {}
        for item, side, _dist in candidates:
            grouped.setdefault(side, []).append(float(item["level"]))
        for item, side in nearest_per_side(candidates):
            entry = _round_px(close)
            tp = _round_px(tp_price(side, entry, atr))
            if side == "long" and tp <= entry:
                continue
            if side == "short" and tp >= entry:
                continue
            qty = (MARGIN_USDT * LEVERAGE) / entry
            hedge_side = "short" if side == "long" else "long"
            main = Order(
                signal_level=float(item["level"]),
                signal_ts=int(item["signal_ts"]),
                side=side,
                bar_ts=now,
                entry_px=entry,
                tp_px=tp,
                qty=qty,
                kind="main",
            )
            hedge = Order(
                signal_level=float(item["level"]),
                signal_ts=int(item["signal_ts"]),
                side=hedge_side,
                bar_ts=now,
                entry_px=entry,
                tp_px=0.0,
                qty=qty,
                kind="hedge",
            )
            book.orders.extend((main, hedge))
            book.used.add((main.signal_level, now))
            book.side_used.add((side, now))
            for other in grouped[side]:
                book.used.add((other, now))

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

    all_trades = [o for o in book.closed if o.status == "closed"]
    mains = [o for o in all_trades if o.kind == "main"]
    hedges = [o for o in all_trades if o.kind == "hedge"]
    print("OP | 2025-10-01→2026-09-30 | 1 USDT × 10 | window 7d")
    print("Main: fade + TP 1×ATR(14). Hedge: opposite, same size/price, close only at expire.")
    print(f"cancelled unfilled: main={sum(1 for o in book.closed if o.kind=='main' and o.status=='cancelled')} "
          f"hedge={sum(1 for o in book.closed if o.kind=='hedge' and o.status=='cancelled')}")
    print()
    _summarize("MAIN (logic live)", mains)
    print()
    _summarize("HEDGE (chỉ đóng hết hạn)", hedges)
    print()
    _summarize("CẢ HAI", all_trades)
    print()
    print(f"pair margin at a main+hedge fill ≈ {2 * MARGIN_USDT:.0f} USDT")
    print(f"combined PnL vs live main-only ({sum(o.pnl for o in mains):+.2f}): "
          f"{sum(o.pnl for o in all_trades):+.2f}")


if __name__ == "__main__":
    run()
