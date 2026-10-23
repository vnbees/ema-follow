"""Sweep a few optimisations on the OP revert backtest (7d window)."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
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
from scripts.backtest_smc_revert import (
    TP_FEE_FRAC,
    YEAR_END,
    YEAR_START,
    Order,
    Signal,
    _finish,
    _load,
    _round_px,
)

WINDOW = 7 * 24 * 4


@dataclass
class Book:
    signals: dict[float, Signal] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    closed: list[Order] = field(default_factory=list)
    used: set[tuple[float, int]] = field(default_factory=set)
    side_used: set[tuple[str, int]] = field(default_factory=set)


def entry_side_toward(level: float, close: float, color: str | None) -> str | None:
    """Long when below and candle turns up; short when above and candle turns down."""
    if level <= 0 or color is None:
        return None
    dist = (close - level) / level
    if dist >= 0.01 and color == "red":
        return "short"
    if dist <= -0.01 and color == "green":
        return "long"
    return None


def simulate(
    *,
    tp_mode: str,
    max_open: int | None,
    max_dist: float | None,
    flat_only: bool,
    one_per_level: bool,
    pending_bars: int | None,
    sl_pct: float | None,
    toward: bool,
) -> dict:
    ts, opens, highs, lows, closes = SERIES
    n = len(ts)
    book = Book()
    start_i = next(i for i in range(n) if ts[i] >= YEAR_START)
    start_i = max(400, start_i)
    side_fn = entry_side_toward if toward else entry_side

    for i in range(start_i, n):
        now = ts[i]
        # expire signals / orders
        dead = [lv for lv, sig in book.signals.items() if now >= sig.deadline_ts]
        for lv in dead:
            del book.signals[lv]
        for order in list(book.orders):
            if order.status not in ("pending", "open"):
                continue
            # pending TTL
            if (
                pending_bars is not None
                and order.status == "pending"
                and now >= order.bar_ts + pending_bars * BAR_MS
            ):
                order.status = "cancelled"
                order.reason = "ttl"
                order.closed_ts = now
                book.closed.append(order)
                book.orders.remove(order)
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

        # fills + optional SL
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
            if sl_pct is not None:
                if order.side == "long":
                    sl = order.fill_px * (1.0 - sl_pct)
                    if lows[i] <= sl:
                        fee = abs(sl * order.qty) * TAKER_FEE
                        _finish(order, close_px=sl, fee_close=fee, reason="sl", ts=now)
                        book.closed.append(order)
                        book.orders.remove(order)
                        continue
                else:
                    sl = order.fill_px * (1.0 + sl_pct)
                    if highs[i] >= sl:
                        fee = abs(sl * order.qty) * TAKER_FEE
                        _finish(order, close_px=sl, fee_close=fee, reason="sl", ts=now)
                        book.closed.append(order)
                        book.orders.remove(order)
                        continue
            hit_tp = highs[i] >= order.tp_px if order.side == "long" else lows[i] <= order.tp_px
            if hit_tp:
                fee = abs(order.tp_px * order.qty) * MAKER_FEE
                _finish(order, close_px=order.tp_px, fee_close=fee, reason="tp", ts=now)
                book.closed.append(order)
                book.orders.remove(order)

        # register
        if YEAR_START <= now <= YEAR_END:
            lo = max(0, i - 399)
            setup = setup_on_last_bar(ts[lo : i + 1], opens[lo : i + 1], highs[lo : i + 1], lows[lo : i + 1], closes[lo : i + 1])
            if setup is not None:
                order = flipped(setup)
                level = _round_px(order.entry)
                deadline = int(order.signal_ts) + WINDOW * BAR_MS
                if level > 0:
                    existing = book.signals.get(level)
                    if existing is None or int(order.signal_ts) < existing.signal_ts:
                        book.signals[level] = Signal(level, int(order.signal_ts), deadline)

        # enter
        if not (YEAR_START <= now <= YEAR_END) or not book.signals:
            continue
        color = candle_color(opens[i], closes[i])
        close = closes[i]
        working = [o for o in book.orders if o.status in ("pending", "open")]
        opens_rows = [
            {"status": "open", "side": o.side, "fill_px": o.fill_px, "qty": o.qty, "fee_open": o.fee_open}
            for o in book.orders
            if o.status == "open"
        ]
        if flat_only and working:
            continue
        if book_in_profit(opens_rows, close):
            continue
        open_n = sum(1 for o in book.orders if o.status == "open")
        pending_n = sum(1 for o in book.orders if o.status == "pending")
        if max_open is not None and open_n + pending_n >= max_open:
            continue
        atr = None
        if tp_mode == "atr":
            atr = last_atr(highs[max(0, i - 20) : i + 1], lows[max(0, i - 20) : i + 1], closes[max(0, i - 20) : i + 1])
            if atr is None or atr <= 0:
                continue
        candidates = []
        for sig in book.signals.values():
            if now >= sig.deadline_ts:
                continue
            side = side_fn(sig.level, close, color)
            if side is None:
                continue
            if (sig.level, now) in book.used or (side, now) in book.side_used:
                continue
            if one_per_level and any(
                o.signal_level == sig.level and o.status in ("pending", "open") for o in book.orders
            ):
                continue
            dist = abs(close - sig.level) / sig.level
            if dist < 0.01:
                continue
            if max_dist is not None and dist > max_dist:
                continue
            entry = _round_px(close)
            if side_fn(sig.level, entry, color) is None:
                continue
            candidates.append(({"level": sig.level, "signal_ts": sig.signal_ts}, side, dist))
        if not candidates:
            continue
        grouped: dict[str, list[float]] = {}
        for item, side, _d in candidates:
            grouped.setdefault(side, []).append(float(item["level"]))
        for item, side in nearest_per_side(candidates):
            if max_open is not None:
                cur = sum(1 for o in book.orders if o.status in ("pending", "open"))
                if cur >= max_open:
                    break
            entry = _round_px(close)
            if tp_mode == "fee":
                raw = entry * (1.0 + TP_FEE_FRAC) if side == "long" else entry * (1.0 - TP_FEE_FRAC)
                tp = _round_px(raw)
            else:
                tp = _round_px(tp_price(side, entry, float(atr)))
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
    pnls = [o.pnl for o in trades]
    if not trades:
        return {"n": 0, "pnl": 0.0, "wr": 0.0, "peak": 0, "expire_pnl": 0.0, "tp_pnl": 0.0}
    wins = sum(1 for p in pnls if p > 0)
    by = {}
    for o in trades:
        by.setdefault(o.reason, []).append(o.pnl)
    events = []
    for o in trades:
        events.append((o.opened_ts, 1))
        events.append((o.closed_ts, -1))
    events.sort()
    cur = peak = 0
    for _, d in events:
        cur += d
        peak = max(peak, cur)
    return {
        "n": len(trades),
        "pnl": sum(pnls),
        "wr": 100 * wins / len(trades),
        "peak": peak,
        "expire_pnl": sum(by.get("expire", [])),
        "tp_pnl": sum(by.get("tp", [])),
        "sl_pnl": sum(by.get("sl", [])),
        "avg": sum(pnls) / len(trades),
    }


print("loading...")
SERIES = _load()
print("bars", len(SERIES[0]))

variants = [
    ("baseline fade", dict(tp_mode="fee", max_open=None, max_dist=None, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=False)),
    ("baseline atr", dict(tp_mode="atr", max_open=None, max_dist=None, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=False)),
    ("max_open=1 fee", dict(tp_mode="fee", max_open=1, max_dist=None, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=False)),
    ("max_open=3 fee", dict(tp_mode="fee", max_open=3, max_dist=None, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=False)),
    ("flat_only fee", dict(tp_mode="fee", max_open=None, max_dist=None, flat_only=True, one_per_level=False, pending_bars=None, sl_pct=None, toward=False)),
    ("dist≤3% fee", dict(tp_mode="fee", max_open=None, max_dist=0.03, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=False)),
    ("dist≤5% fee", dict(tp_mode="fee", max_open=None, max_dist=0.05, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=False)),
    ("1/level fee", dict(tp_mode="fee", max_open=None, max_dist=None, flat_only=False, one_per_level=True, pending_bars=None, sl_pct=None, toward=False)),
    ("pending 1bar fee", dict(tp_mode="fee", max_open=None, max_dist=None, flat_only=False, one_per_level=False, pending_bars=1, sl_pct=None, toward=False)),
    ("SL 1% fee", dict(tp_mode="fee", max_open=None, max_dist=None, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=0.01, toward=False)),
    ("toward+fee", dict(tp_mode="fee", max_open=None, max_dist=None, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=True)),
    ("toward+atr", dict(tp_mode="atr", max_open=None, max_dist=None, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=True)),
    ("toward+dist3%+max1 fee", dict(tp_mode="fee", max_open=1, max_dist=0.03, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=True)),
    ("toward+dist3%+max3 fee", dict(tp_mode="fee", max_open=3, max_dist=0.03, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=True)),
    ("toward+dist5%+max3 atr", dict(tp_mode="atr", max_open=3, max_dist=0.05, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=True)),
    ("fade+dist3%+max3 fee", dict(tp_mode="fee", max_open=3, max_dist=0.03, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=None, toward=False)),
    ("fade+dist3%+max1+SL1% fee", dict(tp_mode="fee", max_open=1, max_dist=0.03, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=0.01, toward=False)),
    ("toward+dist3%+max1+SL1% fee", dict(tp_mode="fee", max_open=1, max_dist=0.03, flat_only=False, one_per_level=False, pending_bars=None, sl_pct=0.01, toward=True)),
]

rows = []
for name, kwargs in variants:
    r = simulate(**kwargs)
    rows.append((name, r))
    print(
        f"{name:32s} pnl={r['pnl']:+8.2f} n={r['n']:5d} wr={r['wr']:5.1f}% "
        f"peak={r['peak']:3d} tp={r['tp_pnl']:+8.2f} exp={r['expire_pnl']:+8.2f} sl={r.get('sl_pnl', 0):+8.2f}"
    )

best = max(rows, key=lambda x: x[1]["pnl"])
print()
print("BEST:", best[0], f"pnl={best[1]['pnl']:+.2f}")
