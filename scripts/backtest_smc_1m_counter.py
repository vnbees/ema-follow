"""1m counter-candle test inside each SMC stop/target zone.

A flipped SMC setup supplies the side and the zone. After that 15m bar
closes, each opposite 1m candle whose close is still strictly inside the
zone opens a market trade. A same-direction 1m candle closes every open
trade on that side which is net-positive after taker fees. A 1m bar that
touches the stop or the target closes the zone's open trades at that price
(stop wins if the same bar touches both). Each trade uses margin = equity
× 2% / 10 and leverage 10. Does not place orders.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.config import LEVERAGE
from src.donchian.config import FIXED_SCAN_SYMBOLS
from src.smc.broker import MARGIN_PCT, TAKER_FEE_RATE
from src.smc.signals import BAR_MS, flipped, setups

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "bt_klines_15m"
CACHE_1M = ROOT / "data" / "bt_klines_1m"
START_EQUITY = 1000.0
MINUTE = 60_000
SLICES = 10
RATIO = (1.0 + TAKER_FEE_RATE) / (1.0 - TAKER_FEE_RATE)


@dataclass
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float


@dataclass
class Pos:
    entry: float
    qty: float
    margin: float
    fee_open: float
    fill_ts: int


def _load_15m(symbol: str) -> list[Bar]:
    files = list(CACHE.glob(f"{symbol}_15m_*.csv"))
    path = max(files, key=lambda f: int(f.stem.split("_")[-1]) - int(f.stem.split("_")[-2]))
    bars: list[Bar] = []
    with path.open() as handle:
        for row in csv.DictReader(handle):
            bars.append(Bar(int(row["ts"]), float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])))
    bars.sort(key=lambda bar: bar.ts)
    return bars


def _load_1m(symbol: str) -> dict[str, np.ndarray]:
    with np.load(CACHE_1M / f"{symbol}.npz") as pack:
        return {
            "ts": pack["ts"],
            "open": pack["open"],
            "high": pack["high"],
            "low": pack["low"],
            "close": pack["close"],
        }


def _signals(symbol: str) -> list[tuple[int, str, float, float]]:
    bars = _load_15m(symbol)
    found = setups(
        [bar.ts for bar in bars],
        [bar.open for bar in bars],
        [bar.high for bar in bars],
        [bar.low for bar in bars],
        [bar.close for bar in bars],
    )
    out = []
    for setup in found:
        order = flipped(setup)
        out.append((order.signal_ts, order.side, order.stop, order.target))
    return out


def run() -> dict:
    minutes = {symbol: _load_1m(symbol) for symbol in FIXED_SCAN_SYMBOLS}
    signals = {symbol: _signals(symbol) for symbol in FIXED_SCAN_SYMBOLS}
    sig_i = {symbol: 0 for symbol in FIXED_SCAN_SYMBOLS}
    pointer = {symbol: 0 for symbol in FIXED_SCAN_SYMBOLS}
    zones: dict[str, dict] = {}
    longs: dict[str, list[Pos]] = {symbol: [] for symbol in FIXED_SCAN_SYMBOLS}
    shorts: dict[str, list[Pos]] = {symbol: [] for symbol in FIXED_SCAN_SYMBOLS}
    qty_sum = {symbol: [0.0, 0.0] for symbol in FIXED_SCAN_SYMBOLS}
    cost_sum = {symbol: [0.0, 0.0] for symbol in FIXED_SCAN_SYMBOLS}
    margin_sum = {symbol: 0.0 for symbol in FIXED_SCAN_SYMBOLS}
    last_close: dict[str, float] = {}
    brackets: dict[str, tuple[str, float, float]] = {}
    cash = START_EQUITY
    trades: list[dict] = []
    peak = START_EQUITY
    max_dd = 0.0
    max_open = 0
    entries = 0
    no_margin = 0

    def insert(book: list[Pos], pos: Pos) -> None:
        lo, hi = 0, len(book)
        while lo < hi:
            mid = (lo + hi) // 2
            if book[mid].entry < pos.entry:
                lo = mid + 1
            else:
                hi = mid
        book.insert(lo, pos)

    def open_count() -> int:
        return sum(len(longs[s]) + len(shorts[s]) for s in FIXED_SCAN_SYMBOLS)

    def equity_now() -> float:
        upnl = 0.0
        for symbol, mark in last_close.items():
            lqty, sqty = qty_sum[symbol]
            lcost, scost = cost_sum[symbol]
            upnl += mark * lqty - lcost
            upnl += scost - mark * sqty
        locked = sum(margin_sum.values())
        return cash + locked + upnl

    def note_equity() -> None:
        nonlocal peak, max_dd
        eq = equity_now()
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    def add_pos(symbol: str, side: str, pos: Pos) -> None:
        slot = 0 if side == "long" else 1
        qty_sum[symbol][slot] += pos.qty
        cost_sum[symbol][slot] += pos.entry * pos.qty
        margin_sum[symbol] += pos.margin
        insert(longs[symbol] if side == "long" else shorts[symbol], pos)

    def drop_pos(symbol: str, side: str, pos: Pos) -> None:
        slot = 0 if side == "long" else 1
        qty_sum[symbol][slot] -= pos.qty
        cost_sum[symbol][slot] -= pos.entry * pos.qty
        margin_sum[symbol] -= pos.margin

    def close_pos(symbol: str, side: str, pos: Pos, price: float, ts: int, reason: str) -> None:
        nonlocal cash
        fee_close = pos.qty * price * TAKER_FEE_RATE
        gross = (price - pos.entry) * pos.qty if side == "long" else (pos.entry - price) * pos.qty
        pnl = gross - pos.fee_open - fee_close
        cash += pos.margin + gross - fee_close
        drop_pos(symbol, side, pos)
        trades.append({"symbol": symbol, "side": side, "pnl": pnl, "reason": reason, "exit_ts": ts, "hold": ts - pos.fill_ts})

    def close_winners(symbol: str, side: str, price: float, ts: int) -> None:
        book = longs[symbol] if side == "long" else shorts[symbol]
        if side == "long":
            limit = price / RATIO
            cut = 0
            while cut < len(book) and book[cut].entry < limit:
                cut += 1
            chosen = book[:cut]
            del book[:cut]
        else:
            limit = price * RATIO
            cut = len(book)
            while cut > 0 and book[cut - 1].entry > limit:
                cut -= 1
            chosen = book[cut:]
            del book[cut:]
        for pos in chosen:
            close_pos(symbol, side, pos, price, ts, "profit")

    def close_book(symbol: str, side: str, price: float, ts: int, reason: str) -> None:
        book = longs[symbol] if side == "long" else shorts[symbol]
        chosen = list(book)
        book.clear()
        for pos in chosen:
            close_pos(symbol, side, pos, price, ts, reason)
        if not longs[symbol] and not shorts[symbol]:
            brackets.pop(symbol, None)

    def level_hit(side: str, stop: float, target: float, high: float, low: float) -> tuple[float, str] | None:
        if side == "long":
            if low <= stop:
                return stop, "stop"
            if high >= target:
                return target, "tp"
            return None
        if high >= stop:
            return stop, "stop"
        if low <= target:
            return target, "tp"
        return None

    def minute_of(symbol: str, ts: int) -> tuple[float, float, float, float] | None:
        book = minutes[symbol]
        stamps = book["ts"]
        i = pointer[symbol]
        if i < len(stamps) and int(stamps[i]) < ts:
            i = int(np.searchsorted(stamps, ts))
        if i >= len(stamps) or int(stamps[i]) != ts:
            pointer[symbol] = i
            return None
        pointer[symbol] = i + 1
        return float(book["open"][i]), float(book["high"][i]), float(book["low"][i]), float(book["close"][i])

    def try_enter(symbol: str, side: str, price: float, ts: int) -> None:
        nonlocal cash, entries, no_margin
        if price <= 0:
            return
        equity = equity_now()
        margin = equity * MARGIN_PCT / SLICES
        if margin <= 0 or cash < margin:
            no_margin += 1
            return
        notional = margin * LEVERAGE
        qty = notional / price
        fee_open = notional * TAKER_FEE_RATE
        cash -= margin + fee_open
        add_pos(symbol, side, Pos(price, qty, margin, fee_open, ts))
        last_close[symbol] = price
        entries += 1

    start = int(minutes["BTCUSDT"]["ts"][0])
    start -= start % MINUTE
    end_t = max(int(book["ts"][-1]) for book in minutes.values())
    t = start
    while t <= end_t:
        if not zones and open_count() == 0:
            nxt = None
            for symbol in FIXED_SCAN_SYMBOLS:
                rows = signals[symbol]
                i = sig_i[symbol]
                while i < len(rows) and rows[i][0] + BAR_MS < t:
                    i += 1
                sig_i[symbol] = i
                if i < len(rows):
                    act = rows[i][0] + BAR_MS
                    if nxt is None or act < nxt:
                        nxt = act
            if nxt is None:
                break
            t = nxt
        active = set(zones) | {s for s in FIXED_SCAN_SYMBOLS if longs[s] or shorts[s]}
        for symbol in FIXED_SCAN_SYMBOLS:
            rows = signals[symbol]
            i = sig_i[symbol]
            while i < len(rows) and rows[i][0] + BAR_MS < t:
                i += 1
            sig_i[symbol] = i
            if i < len(rows) and rows[i][0] + BAR_MS == t and symbol not in zones:
                _sig_ts, side, stop, target = signals[symbol][i]
                sig_i[symbol] = i + 1
                zones[symbol] = {"side": side, "stop": stop, "target": target}
                active.add(symbol)
            elif i < len(signals[symbol]) and signals[symbol][i][0] + BAR_MS <= t and symbol in zones:
                sig_i[symbol] = i + 1
        for symbol in active:
            quote = minute_of(symbol, t)
            if quote is None:
                continue
            opened, high, low, close = quote
            last_close[symbol] = close
            bracket = brackets.get(symbol)
            if bracket is not None:
                hit = level_hit(bracket[0], bracket[1], bracket[2], high, low)
                if hit is not None:
                    close_book(symbol, bracket[0], hit[0], t + MINUTE, hit[1])
                    zone_now = zones.get(symbol)
                    if zone_now is not None and zone_now["stop"] == bracket[1] and zone_now["target"] == bracket[2]:
                        zones.pop(symbol, None)
                    continue
            if opened < close:
                close_winners(symbol, "long", close, t + MINUTE)
            elif opened > close:
                close_winners(symbol, "short", close, t + MINUTE)
            zone = zones.get(symbol)
            if zone is None:
                continue
            if level_hit(zone["side"], zone["stop"], zone["target"], high, low) is not None or not (
                min(zone["stop"], zone["target"]) < close < max(zone["stop"], zone["target"])
            ):
                zones.pop(symbol, None)
                continue
            opposite = (zone["side"] == "long" and close < opened) or (zone["side"] == "short" and close > opened)
            if opposite:
                brackets[symbol] = (zone["side"], zone["stop"], zone["target"])
                try_enter(symbol, zone["side"], close, t + MINUTE)
        max_open = max(max_open, open_count())
        if (t + MINUTE) % BAR_MS == 0:
            note_equity()
        t += MINUTE

    last_ts = end_t + MINUTE
    for symbol in FIXED_SCAN_SYMBOLS:
        price = last_close.get(symbol, 0.0)
        if price <= 0:
            continue
        for pos in longs[symbol]:
            close_pos(symbol, "long", pos, price, last_ts, "eod")
        for pos in shorts[symbol]:
            close_pos(symbol, "short", pos, price, last_ts, "eod")
        longs[symbol].clear()
        shorts[symbol].clear()
    note_equity()

    wins = [row for row in trades if row["pnl"] > 0]
    losses = [row for row in trades if row["pnl"] <= 0]
    gross_win = sum(row["pnl"] for row in wins)
    gross_loss = abs(sum(row["pnl"] for row in losses))
    by_reason: dict[str, list] = defaultdict(lambda: [0, 0.0])
    by_year: dict[int, list] = defaultdict(lambda: [0, 0.0])
    by_symbol: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for row in trades:
        by_reason[row["reason"]][0] += 1
        by_reason[row["reason"]][1] += row["pnl"]
        year = datetime.fromtimestamp(row["exit_ts"] / 1000, timezone.utc).year
        by_year[year][0] += 1
        by_year[year][1] += row["pnl"]
        by_symbol[row["symbol"]][0] += 1
        by_symbol[row["symbol"]][1] += row["pnl"]
    holds = [row["hold"] for row in trades if row["reason"] != "eod"]
    return {
        "start": start,
        "end": end_t,
        "final": cash,
        "n": len(trades),
        "wins": len(wins),
        "pf": (gross_win / gross_loss) if gross_loss else None,
        "max_dd": max_dd,
        "by_reason": dict(by_reason),
        "by_year": dict(by_year),
        "by_symbol": dict(by_symbol),
        "entries": entries,
        "no_margin": no_margin,
        "max_open": max_open,
        "median_hold_min": (sorted(holds)[len(holds) // 2] / 60_000) if holds else 0,
        "span_days": (end_t - start) / 86_400_000,
        "signals": sum(len(rows) for rows in signals.values()),
    }


def main() -> None:
    result = run()
    start = datetime.fromtimestamp(result["start"] / 1000, timezone.utc).date()
    end = datetime.fromtimestamp(result["end"] / 1000, timezone.utc).date()
    pnl = result["final"] - START_EQUITY
    print(f"window {start} → {end}  ({result['span_days']:.0f} days)")
    print(f"signals {result['signals']}  entries {result['entries']}  no_margin {result['no_margin']}  max_open {result['max_open']}")
    print(f"start {START_EQUITY:.0f}  final {result['final']:.2f}  pnl {pnl:+.2f}  ({pnl / START_EQUITY * 100:+.1f}%)")
    wr = result["wins"] / result["n"] * 100 if result["n"] else 0
    pf = "—" if result["pf"] is None else f"{result['pf']:.2f}"
    print(
        f"trades {result['n']}  winrate {wr:.1f}%  pf {pf}  maxDD {result['max_dd'] * 100:.1f}%  "
        f"median hold {result['median_hold_min']:.0f}m"
    )
    print("reasons", {k: (v[0], round(v[1], 2)) for k, v in sorted(result["by_reason"].items())})
    print("year")
    for year in sorted(result["by_year"]):
        n, amount = result["by_year"][year]
        print(f"  {year}  n={n}  pnl={amount:+.2f}")
    print("symbol")
    for symbol, (n, amount) in sorted(result["by_symbol"].items(), key=lambda item: item[1][1]):
        print(f"  {symbol}  n={n}  pnl={amount:+.2f}")


if __name__ == "__main__":
    main()
