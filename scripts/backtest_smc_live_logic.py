"""Backtest the live SMC bot.

Signals stay on the cached 15m bars. Fills, the stop-before-entry cancel, and
the 41-minute exit walk the 1m cache. A pending order is cancelled when one
minute touches both the entry and the stop. Does not place orders and does
not read or write the production database.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.config import LEVERAGE
from src.donchian.config import FIXED_SCAN_SYMBOLS
from src.smc.broker import (
    MAX_HOLD_SEC,
    MAKER_FEE_RATE,
    TAKER_FEE_RATE,
    entry_block_reason,
    order_notional,
)
from src.smc.signals import BAR_MS, TTL_BARS, flipped, setups, stop_hit

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "bt_klines_15m"
CACHE_1M = ROOT / "data" / "bt_klines_1m"
START_EQUITY = 1000.0
MINUTE = 60_000


@dataclass
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float


def _load(symbol: str) -> list[Bar]:
    files = list(CACHE.glob(f"{symbol}_15m_*.csv"))
    if not files:
        raise FileNotFoundError(symbol)
    path = max(files, key=lambda f: int(f.stem.split("_")[-1]) - int(f.stem.split("_")[-2]))
    bars: list[Bar] = []
    with path.open() as handle:
        for row in csv.DictReader(handle):
            bars.append(
                Bar(
                    int(row["ts"]),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                )
            )
    bars.sort(key=lambda bar: bar.ts)
    return bars


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def _load_1m(symbol: str) -> dict[str, np.ndarray]:
    path = CACHE_1M / f"{symbol}.npz"
    if not path.exists():
        raise FileNotFoundError(path)
    with np.load(path) as pack:
        return {"ts": pack["ts"], "high": pack["high"], "low": pack["low"], "close": pack["close"]}


def _hits(side: str, entry: float, stop: float, target: float, high: float, low: float) -> tuple[bool, bool, bool]:
    if side == "long":
        return high >= entry, low <= stop, high >= target
    return low <= entry, high >= stop, low <= target


def run(half_at_hold: bool = False) -> dict:
    books = {symbol: _load(symbol) for symbol in FIXED_SCAN_SYMBOLS}
    index = {symbol: {bar.ts: i for i, bar in enumerate(bars)} for symbol, bars in books.items()}
    signals: dict[str, dict[int, object]] = {}
    for symbol, bars in books.items():
        found = setups(
            [bar.ts for bar in bars],
            [bar.open for bar in bars],
            [bar.high for bar in bars],
            [bar.low for bar in bars],
            [bar.close for bar in bars],
        )
        signals[symbol] = {setup.signal_ts: flipped(setup) for setup in found}
    minutes = {symbol: _load_1m(symbol) for symbol in FIXED_SCAN_SYMBOLS}
    clock = sorted({bar.ts for bars in books.values() for bar in bars})
    pointer = {symbol: 0 for symbol in FIXED_SCAN_SYMBOLS}

    cash = START_EQUITY
    pending: dict[str, dict] = {}
    open_pos: dict[str, dict] = {}
    rows: dict[str, list[dict]] = defaultdict(list)
    trades: list[dict] = []
    equity_curve: list[tuple[int, float]] = []
    last_close: dict[str, float] = {}
    skips = defaultdict(int)
    half_cuts = {"n": 0, "pnl": 0.0}

    def mark_of(symbol: str) -> float:
        if symbol in open_pos:
            return last_close.get(symbol, open_pos[symbol]["entry"])
        return last_close.get(symbol, 0.0)

    def equity_now() -> float:
        upnl = 0.0
        for symbol, pos in open_pos.items():
            mark = mark_of(symbol)
            qty = pos["qty"]
            upnl += (mark - pos["entry"]) * qty if pos["side"] == "long" else (pos["entry"] - mark) * qty
        locked = sum(item["margin"] for item in pending.values()) + sum(pos["margin"] for pos in open_pos.values())
        return cash + locked + upnl

    def release(symbol: str, margin: float) -> None:
        nonlocal cash
        cash += margin
        pending.pop(symbol, None)

    def close_pos(symbol: str, price: float, ts: int, reason: str, maker: bool) -> None:
        nonlocal cash
        pos = open_pos.pop(symbol)
        fee_close = pos["qty"] * price * (MAKER_FEE_RATE if maker else TAKER_FEE_RATE)
        gross = (price - pos["entry"]) * pos["qty"] if pos["side"] == "long" else (pos["entry"] - price) * pos["qty"]
        pnl = gross - pos["fee_open"] - fee_close
        cash += pos["margin"] + gross - fee_close
        pos["row"]["closed_at"] = _iso(ts)
        pos["row"]["close_reason"] = reason
        half_pnl = float(pos.get("half_pnl") or 0.0)
        trades.append(
            {
                "symbol": symbol,
                "side": pos["side"],
                "pnl": pnl + half_pnl,
                "reason": reason,
                "entry_ts": pos["fill_ts"],
                "exit_ts": ts,
                "half_pnl": half_pnl,
                "rest_pnl": pnl,
            }
        )

    def cut_half(symbol: str, price: float, ts: int) -> None:
        nonlocal cash
        pos = open_pos[symbol]
        qty = pos["qty"] * 0.5
        fee_close = qty * price * TAKER_FEE_RATE
        gross = (price - pos["entry"]) * qty if pos["side"] == "long" else (pos["entry"] - price) * qty
        fee_open_part = pos["fee_open"] * 0.5
        pnl = gross - fee_open_part - fee_close
        cash += pos["margin"] * 0.5 + gross - fee_close
        pos["qty"] = qty
        pos["margin"] *= 0.5
        pos["fee_open"] = fee_open_part
        pos["halved"] = True
        pos["half_pnl"] = pnl
        half_cuts["n"] += 1
        half_cuts["pnl"] += pnl

    def minute_at(symbol: str, ts: int) -> tuple[float, float, float] | None:
        book = minutes[symbol]
        stamps = book["ts"]
        i = pointer[symbol]
        if i < len(stamps) and int(stamps[i]) < ts:
            i = int(np.searchsorted(stamps, ts))
            pointer[symbol] = i
        if i >= len(stamps) or int(stamps[i]) != ts:
            return None
        pointer[symbol] = i + 1
        return float(book["high"][i]), float(book["low"][i]), float(book["close"][i])

    t = int(minutes["BTCUSDT"]["ts"][0])
    t -= t % MINUTE
    end_t = max(int(book["ts"][-1]) for book in minutes.values())
    while t <= end_t:
        if not pending and not open_pos:
            bar_open = t - (t % BAR_MS)
            last_minute = bar_open + 14 * MINUTE
            if last_minute < t:
                last_minute += BAR_MS
            t = last_minute
            if t > end_t:
                break
        for symbol in list(pending):
            quote = minute_at(symbol, t)
            if quote is None:
                continue
            high, low, close = quote
            last_close[symbol] = close
            order = pending[symbol]
            if t < order["signal_ts"] + BAR_MS:
                continue
            if t >= order["signal_ts"] + (TTL_BARS + 1) * BAR_MS:
                order["row"]["closed_at"] = _iso(t)
                order["row"]["close_reason"] = "expire"
                release(symbol, order["margin"])
                skips["expire"] += 1
                continue
            hit_entry, hit_stop, hit_tp = _hits(order["side"], order["entry"], order["stop"], order["target"], high, low)
            if hit_entry and hit_stop:
                order["row"]["closed_at"] = _iso(t + MINUTE)
                order["row"]["close_reason"] = "stop"
                release(symbol, order["margin"])
                skips["both_touch"] += 1
                continue
            if hit_stop:
                order["row"]["closed_at"] = _iso(t + MINUTE)
                order["row"]["close_reason"] = "stop"
                release(symbol, order["margin"])
                skips["stop_before_fill"] += 1
                continue
            if not hit_entry:
                continue
            pending.pop(symbol)
            fee_open = order["qty"] * order["entry"] * TAKER_FEE_RATE
            cash -= fee_open
            order["row"]["opened_at"] = _iso(t)
            open_pos[symbol] = {**order, "fill_ts": t, "fee_open": fee_open}
            if hit_tp:
                close_pos(symbol, order["target"], t + MINUTE, "tp", True)
        for symbol in list(open_pos):
            pos = open_pos.get(symbol)
            if pos is None or pos["fill_ts"] == t:
                continue
            quote = minute_at(symbol, t)
            if quote is None:
                continue
            high, low, close = quote
            last_close[symbol] = close
            _hit_entry, hit_stop, hit_tp = _hits(pos["side"], pos["entry"], pos["stop"], pos["target"], high, low)
            if hit_stop:
                close_pos(symbol, pos["stop"], t + MINUTE, "stop", False)
            elif hit_tp:
                close_pos(symbol, pos["target"], t + MINUTE, "tp", True)
            elif not pos.get("halved") and t + MINUTE >= pos["fill_ts"] + MAX_HOLD_SEC * 1000:
                if half_at_hold:
                    cut_half(symbol, close, t + MINUTE)
                else:
                    close_pos(symbol, close, t + MINUTE, "time", False)
        if (t + MINUTE) % BAR_MS != 0:
            t += MINUTE
            continue
        ts = t + MINUTE - BAR_MS
        equity = equity_now()
        equity_curve.append((ts, equity))
        free = cash
        for symbol in FIXED_SCAN_SYMBOLS:
            slot = index[symbol].get(ts)
            if slot is None:
                continue
            order = signals[symbol].get(ts)
            if order is None:
                continue
            bar = books[symbol][slot]
            row = {
                "close_reason": None,
                "created_at": _iso(ts + BAR_MS),
                "closed_at": None,
                "opened_at": None,
            }
            rows[symbol].append(row)
            if stop_hit(order.side, order.stop, bar.close):
                row["close_reason"] = "through"
                row["closed_at"] = _iso(ts + BAR_MS)
                skips["through"] += 1
                continue
            block = entry_block_reason(ts, rows[symbol])
            if block is None and (symbol in pending or symbol in open_pos):
                block = "busy"
            if block is not None:
                row["close_reason"] = block
                row["closed_at"] = _iso(ts + BAR_MS)
                skips[block] += 1
                continue
            notional = order_notional(order.entry, order.stop, order.target, equity)
            if notional is None:
                row["close_reason"] = "small"
                row["closed_at"] = _iso(ts + BAR_MS)
                skips["small"] += 1
                continue
            margin = notional / LEVERAGE
            if free < margin:
                skips["no_margin"] += 1
                rows[symbol].pop()
                continue
            free -= margin
            cash -= margin
            row["close_reason"] = None
            pending[symbol] = {
                "signal_ts": ts,
                "side": order.side,
                "entry": order.entry,
                "stop": order.stop,
                "target": order.target,
                "margin": margin,
                "qty": notional / order.entry,
                "row": row,
            }
        t += MINUTE

    last_ts = end_t
    for symbol, pos in list(open_pos.items()):
        close_pos(symbol, last_close[symbol], last_ts + BAR_MS, "eod", False)
    for symbol, order in list(pending.items()):
        order["row"]["closed_at"] = _iso(last_ts)
        order["row"]["close_reason"] = "eod"
        release(symbol, order["margin"])

    peak = START_EQUITY
    max_dd = 0.0
    for _ts, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    by_reason = defaultdict(lambda: [0, 0.0])
    by_year = defaultdict(lambda: [0, 0.0])
    by_symbol = defaultdict(lambda: [0, 0.0])
    for trade in trades:
        by_reason[trade["reason"]][0] += 1
        by_reason[trade["reason"]][1] += trade["pnl"]
        year = datetime.fromtimestamp(trade["exit_ts"] / 1000, timezone.utc).year
        by_year[year][0] += 1
        by_year[year][1] += trade["pnl"]
        by_symbol[trade["symbol"]][0] += 1
        by_symbol[trade["symbol"]][1] += trade["pnl"]
    return {
        "start": int(minutes["BTCUSDT"]["ts"][0]),
        "end": end_t,
        "final": cash,
        "n": len(trades),
        "wins": len(wins),
        "pf": (gross_win / gross_loss) if gross_loss else None,
        "max_dd": max_dd,
        "by_reason": dict(by_reason),
        "by_year": dict(by_year),
        "by_symbol": dict(by_symbol),
        "skips": dict(skips),
        "span_days": (end_t - int(minutes["BTCUSDT"]["ts"][0])) / 86_400_000,
        "half_cuts": dict(half_cuts),
        "rest_by_reason": _bucket([t for t in trades if t["half_pnl"]], "rest_pnl"),
    }


def _bucket(rows: list[dict], field: str) -> dict:
    out: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for trade in rows:
        out[trade["reason"]][0] += 1
        out[trade["reason"]][1] += trade[field]
    return dict(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--half-at-41", action="store_true")
    args = parser.parse_args()
    result = run(half_at_hold=args.half_at_41)
    start = datetime.fromtimestamp(result["start"] / 1000, timezone.utc).date()
    end = datetime.fromtimestamp(result["end"] / 1000, timezone.utc).date()
    pnl = result["final"] - START_EQUITY
    print(f"window {start} → {end}  ({result['span_days']:.0f} days)")
    print(f"start {START_EQUITY:.0f}  final {result['final']:.2f}  pnl {pnl:+.2f}  ({pnl / START_EQUITY * 100:+.1f}%)")
    wr = result["wins"] / result["n"] * 100 if result["n"] else 0
    pf = "—" if result["pf"] is None else f"{result['pf']:.2f}"
    print(f"trades {result['n']}  winrate {wr:.1f}%  pf {pf}  maxDD {result['max_dd'] * 100:.1f}%")
    print("reasons", {k: (v[0], round(v[1], 2)) for k, v in sorted(result["by_reason"].items())})
    if args.half_at_41:
        cuts = result["half_cuts"]
        print(f"half cuts n={cuts['n']} pnl={cuts['pnl']:+.2f}")
        print("kept half", {k: (v[0], round(v[1], 2)) for k, v in sorted(result["rest_by_reason"].items())})
    print("skips", result["skips"])
    print("year")
    for year in sorted(result["by_year"]):
        n, amount = result["by_year"][year]
        print(f"  {year}  n={n}  pnl={amount:+.2f}")
    print("symbol")
    for symbol, (n, amount) in sorted(result["by_symbol"].items(), key=lambda item: item[1][1]):
        print(f"  {symbol}  n={n}  pnl={amount:+.2f}")


if __name__ == "__main__":
    main()
