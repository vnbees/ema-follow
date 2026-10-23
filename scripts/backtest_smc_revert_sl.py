"""Live fade + ATR TP, with optional 1×ATR stop. OP or all coins, 7-day window."""

from __future__ import annotations

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
    tp_distance,
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


def _load(symbol: str):
    import csv
    import json

    rows = []
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
    sl_px: float
    qty: float
    status: str = "pending"
    fill_px: float = 0.0
    fee_open: float = 0.0
    pnl: float = 0.0
    reason: str = ""
    opened_ts: int = 0
    closed_ts: int = 0


def _finish(order: Order, close_px: float, fee_close: float, reason: str, ts: int) -> None:
    order.status = "closed"
    order.reason = reason
    order.closed_ts = ts
    gross = (close_px - order.fill_px) * order.qty if order.side == "long" else (order.fill_px - close_px) * order.qty
    order.pnl = gross - order.fee_open - fee_close


def sl_price(side: str, entry: float, atr: float) -> float:
    dist = tp_distance(entry, atr)
    if side == "long":
        return entry - dist
    return entry + dist


def run_symbol(symbol: str) -> dict:
    ts, opens, highs, lows, closes = _load(symbol)
    n = len(ts)
    signals: dict[float, Signal] = {}
    orders: list[Order] = []
    closed: list[Order] = []
    used: set = set()
    side_used: set = set()
    start = next((i for i in range(n) if ts[i] >= YEAR_START), None)
    if start is None:
        return {"symbol": symbol, "error": "no bars"}
    start = max(400, start)
    for i in range(start, n):
        now = ts[i]
        for lv in [lv for lv, s in signals.items() if now >= s.deadline_ts]:
            del signals[lv]
        for order in list(orders):
            if order.status not in ("pending", "open"):
                continue
            if now < order.signal_ts + WINDOW * BAR_MS:
                continue
            if order.status == "pending":
                order.status = "cancelled"
                order.reason = "expire"
                order.closed_ts = now
                closed.append(order)
                orders.remove(order)
            else:
                fee = abs(opens[i] * order.qty) * TAKER_FEE
                _finish(order, opens[i], fee, "expire", now)
                closed.append(order)
                orders.remove(order)
        for order in list(orders):
            if order.status == "pending":
                hit = lows[i] <= order.entry_px if order.side == "long" else highs[i] >= order.entry_px
                if hit:
                    order.status = "open"
                    order.fill_px = order.entry_px
                    order.fee_open = abs(order.fill_px * order.qty) * MAKER_FEE
                    order.opened_ts = now
            if order.status != "open":
                continue
            hit_tp = highs[i] >= order.tp_px if order.side == "long" else lows[i] <= order.tp_px
            hit_sl = lows[i] <= order.sl_px if order.side == "long" else highs[i] >= order.sl_px
            # Same bar hits both: take SL (stop fills as price trades through).
            if hit_sl:
                fee = abs(order.sl_px * order.qty) * TAKER_FEE
                _finish(order, order.sl_px, fee, "sl", now)
                closed.append(order)
                orders.remove(order)
                continue
            if hit_tp:
                fee = abs(order.tp_px * order.qty) * MAKER_FEE
                _finish(order, order.tp_px, fee, "tp", now)
                closed.append(order)
                orders.remove(order)

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
                    existing = signals.get(level)
                    if existing is None or int(flipped_setup.signal_ts) < existing.signal_ts:
                        signals[level] = Signal(level, int(flipped_setup.signal_ts), deadline)

        if not (YEAR_START <= now <= YEAR_END) or not signals:
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
        atr = last_atr(highs[max(0, i - 20) : i + 1], lows[max(0, i - 20) : i + 1], closes[max(0, i - 20) : i + 1])
        if atr is None or atr <= 0:
            continue
        candidates = []
        for sig in signals.values():
            if now >= sig.deadline_ts:
                continue
            side = entry_side(sig.level, close, color)
            if side is None:
                continue
            if (sig.level, now) in used or (side, now) in side_used:
                continue
            entry = _round_px(close)
            if entry_side(sig.level, entry, color) is None:
                continue
            candidates.append(({"level": sig.level, "signal_ts": sig.signal_ts}, side, abs(close - sig.level) / sig.level))
        if not candidates:
            continue
        grouped: dict[str, list[float]] = {}
        for item, side, _d in candidates:
            grouped.setdefault(side, []).append(float(item["level"]))
        for item, side in nearest_per_side(candidates):
            entry = _round_px(close)
            tp = _round_px(tp_price(side, entry, atr))
            sl = _round_px(sl_price(side, entry, atr))
            if side == "long" and (tp <= entry or sl >= entry):
                continue
            if side == "short" and (tp >= entry or sl <= entry):
                continue
            qty = (MARGIN_USDT * LEVERAGE) / entry
            orders.append(
                Order(
                    signal_level=float(item["level"]),
                    signal_ts=int(item["signal_ts"]),
                    side=side,
                    bar_ts=now,
                    entry_px=entry,
                    tp_px=tp,
                    sl_px=sl,
                    qty=qty,
                )
            )
            used.add((float(item["level"]), now))
            side_used.add((side, now))
            for other in grouped[side]:
                used.add((other, now))

    for order in list(orders):
        if order.status == "pending":
            order.status = "cancelled"
            order.reason = "eod"
            order.closed_ts = ts[-1]
            closed.append(order)
        elif order.status == "open":
            fee = abs(closes[-1] * order.qty) * TAKER_FEE
            _finish(order, closes[-1], fee, "eod", ts[-1])
            closed.append(order)

    trades = [o for o in closed if o.status == "closed"]
    if not trades:
        return {"symbol": symbol, "n": 0, "pnl": 0.0, "wr": 0.0, "peak": 0, "tp": 0.0, "sl": 0.0, "expire": 0.0, "ntp": 0, "nsl": 0, "nexp": 0}
    pnls = [o.pnl for o in trades]
    wins = sum(1 for p in pnls if p > 0)
    by: dict[str, list[float]] = {}
    for o in trades:
        by.setdefault(o.reason, []).append(o.pnl)
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
        "tp": sum(by.get("tp", [])),
        "sl": sum(by.get("sl", [])),
        "expire": sum(by.get("expire", [])),
        "eod": sum(by.get("eod", [])),
        "ntp": len(by.get("tp", [])),
        "nsl": len(by.get("sl", [])),
        "nexp": len(by.get("expire", [])),
        "avg_tp": (sum(by["tp"]) / len(by["tp"])) if by.get("tp") else 0.0,
        "avg_sl": (sum(by["sl"]) / len(by["sl"])) if by.get("sl") else 0.0,
        "avg_exp": (sum(by["expire"]) / len(by["expire"])) if by.get("expire") else 0.0,
    }


def main() -> None:
    print("LIVE fade + TP 1×ATR + SL 1×ATR | window=7d | 1 USDT ×10 | 2025-10-01→2026-09-30")
    print("Same-bar TP+SL → SL. SL fee = taker 0.05%.")
    print()
    rows = []
    with ProcessPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(run_symbol, s): s for s in BACKTEST_SYMBOLS_20}
        for fut in as_completed(futs):
            row = fut.result()
            rows.append(row)
            print(
                f"{row['symbol']:10s} n={row['n']:5d} pnl={row['pnl']:+8.2f} wr={row['wr']:5.1f}% "
                f"tp={row['ntp']:5d}/{row['tp']:+8.2f} sl={row['nsl']:5d}/{row['sl']:+8.2f} "
                f"exp={row['nexp']:4d}/{row['expire']:+7.2f}"
            )
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    print()
    print(f"{'SYMBOL':10s} {'N':>6s} {'PnL':>9s} {'WR%':>6s} {'nTP':>5s} {'nSL':>5s} {'nEXP':>5s}")
    for r in rows:
        print(f"{r['symbol']:10s} {r['n']:6d} {r['pnl']:+9.2f} {r['wr']:6.1f} {r['ntp']:5d} {r['nsl']:5d} {r['nexp']:5d}")
    op = next(r for r in rows if r["symbol"] == "OPUSDT")
    print()
    print("OP detail:")
    print(f"  pnl={op['pnl']:+.2f} (live không SL: -83.08)")
    print(f"  tp n={op['ntp']} pnl={op['tp']:+.2f} avg={op['avg_tp']:+.4f}")
    print(f"  sl n={op['nsl']} pnl={op['sl']:+.2f} avg={op['avg_sl']:+.4f}")
    print(f"  expire n={op['nexp']} pnl={op['expire']:+.2f} avg={op['avg_exp']:+.4f}")
    print()
    print(f"coins profitable: {sum(1 for r in rows if r['pnl']>0)}/{len(rows)}")
    print(f"sum PnL each coin alone: {sum(r['pnl'] for r in rows):+.2f}")


if __name__ == "__main__":
    main()
