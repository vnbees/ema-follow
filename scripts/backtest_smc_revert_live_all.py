"""Live revert rules on all FIXED_SCAN coins: fade entry + ATR TP, 7d window."""

from __future__ import annotations

import csv
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
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
TAIL_DIR = Path("/tmp/smc_years/k15")
YEAR_START = int(datetime(2025, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
YEAR_END = int(datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
WINDOW = 7 * 24 * 4


def _round_px(price: float) -> float:
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


def _load(symbol: str) -> tuple[list[int], list[float], list[float], list[float], list[float]]:
    rows: list[tuple[int, float, float, float, float]] = []
    for path in sorted(CACHE.glob(f"{symbol}_15m_*.csv")):
        with path.open() as handle:
            for row in csv.DictReader(handle):
                ts = int(float(row.get("ts") or row.get("timestamp") or row["open_time"]))
                if ts < 10_000_000_000:
                    ts *= 1000
                rows.append((ts, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])))
    tail = TAIL_DIR / f"{symbol}.json"
    if tail.exists():
        for item in json.loads(tail.read_text()):
            if isinstance(item, dict):
                ts = int(item.get("ts") or item["timestamp"])
                rows.append((ts, float(item["open"]), float(item["high"]), float(item["low"]), float(item["close"])))
            else:
                rows.append((int(item[0]), float(item[1]), float(item[2]), float(item[3]), float(item[4])))
    by = {r[0]: r for r in rows}
    series = [by[t] for t in sorted(by)]
    if not series:
        raise FileNotFoundError(symbol)
    return (
        [r[0] for r in series],
        [r[1] for r in series],
        [r[2] for r in series],
        [r[3] for r in series],
        [r[4] for r in series],
    )


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
    signals: dict = field(default_factory=dict)
    orders: list = field(default_factory=list)
    closed: list = field(default_factory=list)
    used: set = field(default_factory=set)
    side_used: set = field(default_factory=set)


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


def run_symbol(symbol: str) -> dict:
    ts, opens, highs, lows, closes = _load(symbol)
    n = len(ts)
    book = Book()
    start = next((i for i in range(n) if ts[i] >= YEAR_START), None)
    if start is None:
        return {"symbol": symbol, "error": "no bars in year"}
    start = max(400, start)
    for i in range(start, n):
        now = ts[i]
        for lv in [lv for lv, s in book.signals.items() if now >= s.deadline_ts]:
            del book.signals[lv]
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
            else:
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
            hit = highs[i] >= order.tp_px if order.side == "long" else lows[i] <= order.tp_px
            if hit:
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
                o = flipped(setup)
                level = _round_px(o.entry)
                if level > 0:
                    dl = int(o.signal_ts) + WINDOW * BAR_MS
                    ex = book.signals.get(level)
                    if ex is None or int(o.signal_ts) < ex.signal_ts:
                        book.signals[level] = Signal(level, int(o.signal_ts), dl)

        if not (YEAR_START <= now <= YEAR_END) or not book.signals:
            continue
        color = candle_color(opens[i], closes[i])
        close = closes[i]
        opens_rows = [
            {"status": "open", "side": o.side, "fill_px": o.fill_px, "qty": o.qty, "fee_open": o.fee_open}
            for o in book.orders
            if o.status == "open"
        ]
        if book_in_profit(opens_rows, close):
            continue
        atr = last_atr(highs[max(0, i - 20) : i + 1], lows[max(0, i - 20) : i + 1], closes[max(0, i - 20) : i + 1])
        if atr is None or atr <= 0:
            continue
        cands = []
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
            dist = abs(close - sig.level) / sig.level
            cands.append(({"level": sig.level, "signal_ts": sig.signal_ts}, side, dist))
        if not cands:
            continue
        grouped: dict[str, list[float]] = {}
        for item, side, _d in cands:
            grouped.setdefault(side, []).append(float(item["level"]))
        for item, side in nearest_per_side(cands):
            entry = _round_px(close)
            tp = _round_px(tp_price(side, entry, atr))
            if side == "long" and tp <= entry:
                continue
            if side == "short" and tp >= entry:
                continue
            qty = (MARGIN_USDT * LEVERAGE) / entry
            od = Order(
                signal_level=float(item["level"]),
                signal_ts=int(item["signal_ts"]),
                side=side,
                bar_ts=now,
                entry_px=entry,
                tp_px=tp,
                qty=qty,
            )
            book.orders.append(od)
            book.used.add((od.signal_level, now))
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

    trades = [o for o in book.closed if o.status == "closed"]
    if not trades:
        return {"symbol": symbol, "n": 0, "pnl": 0.0, "wr": 0.0, "peak": 0, "tp": 0.0, "expire": 0.0}
    pnls = [o.pnl for o in trades]
    wins = sum(1 for p in pnls if p > 0)
    by: dict[str, float] = {}
    for o in trades:
        by[o.reason] = by.get(o.reason, 0.0) + o.pnl
    events = sorted([(o.opened_ts, 1) for o in trades] + [(o.closed_ts, -1) for o in trades])
    cur = peak = 0
    for _, d in events:
        cur += d
        peak = max(peak, cur)
    return {
        "symbol": symbol,
        "n": len(trades),
        "pnl": sum(pnls),
        "wr": 100.0 * wins / len(trades),
        "peak": peak,
        "tp": by.get("tp", 0.0),
        "expire": by.get("expire", 0.0),
        "eod": by.get("eod", 0.0),
    }


def main() -> None:
    symbols = list(BACKTEST_SYMBOLS_20)
    print(
        "LIVE rule: fade (xanh+cao→short, đỏ+thấp→long) + TP 1×ATR(14) | "
        "window=7d | margin=1 USDT x10 | 2025-10-01..2026-09-30 | coins=",
        len(symbols),
    )
    rows = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(run_symbol, s): s for s in symbols}
        for fut in as_completed(futs):
            row = fut.result()
            rows.append(row)
            if "error" in row:
                print(f"{row['symbol']:10s} ERROR {row['error']}")
            else:
                print(
                    f"{row['symbol']:10s} n={row['n']:5d} pnl={row['pnl']:+8.2f} "
                    f"wr={row['wr']:5.1f}% peak={row['peak']:3d} "
                    f"tp={row['tp']:+8.2f} exp={row['expire']:+8.2f}"
                )
    rows = [r for r in rows if "error" not in r]
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    print()
    print(f"{'SYMBOL':10s} {'N':>6s} {'PnL':>10s} {'WR%':>6s} {'PEAK':>5s} {'TP':>10s} {'EXPIRE':>10s}")
    for r in rows:
        print(
            f"{r['symbol']:10s} {r['n']:6d} {r['pnl']:+10.2f} {r['wr']:6.1f} "
            f"{r['peak']:5d} {r['tp']:+10.2f} {r['expire']:+10.2f}"
        )
    total_pnl = sum(r["pnl"] for r in rows)
    total_n = sum(r["n"] for r in rows)
    winners = sum(1 for r in rows if r["pnl"] > 0)
    print()
    print(f"coins profitable: {winners}/{len(rows)}")
    print(f"filled trades: {total_n}")
    print(f"sum PnL (each coin alone): {total_pnl:+.2f} USDT")
    print(f"avg PnL/coin: {total_pnl/len(rows):+.2f} USDT")
    print(f"max peak among coins: {max(r['peak'] for r in rows)}")


if __name__ == "__main__":
    main()
