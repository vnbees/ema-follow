"""Scale into one valid flipped SMC order, at most ten slices.

A signal is taken only when the live fee check passes and the signal close
has not already gone through the stop. The first slice fills at the swing
entry. Each later opposite 1m candle adds one equal slice, up to 10. SL and
TP prices stay put; their size is the filled total. The position closes only
when price hits SL or TP. Does not place orders.
"""

from __future__ import annotations

import json
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from src.config import LEVERAGE
from src.donchian.config import FIXED_SCAN_SYMBOLS
from src.smc.broker import MAKER_FEE_RATE, MARGIN_PCT, TAKER_FEE_RATE, order_notional
from src.smc.signals import BAR_MS, TTL_BARS, flipped, setups, stop_hit

VN = timezone(timedelta(hours=7))
LIVE = datetime(2026, 10, 14, 0, 0, tzinfo=VN)
MINUTE = 60_000
SLICES = 10
START_EQUITY = 1000.0


def fetch(symbol: str, interval: str, start: int, end: int) -> list[tuple[int, float, float, float, float]]:
    rows: list = []
    cursor = start
    step = MINUTE if interval == "1m" else BAR_MS
    while cursor < end:
        url = (
            "https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=%s&startTime=%s&endTime=%s&limit=1500"
            % (symbol, interval, cursor, end)
        )
        with urllib.request.urlopen(url, timeout=30) as resp:
            batch = json.load(resp)
        if not batch:
            break
        rows.extend(batch)
        nxt = int(batch[-1][0]) + step
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < 1500:
            break
    out: list[tuple[int, float, float, float, float]] = []
    last = -1
    for row in rows:
        ts = int(row[0])
        if ts == last:
            continue
        last = ts
        out.append((ts, float(row[1]), float(row[2]), float(row[3]), float(row[4])))
    return out


def signals_of(bars: list[tuple[int, float, float, float, float]], live_ms: int) -> list:
    found = []
    for i, bar in enumerate(bars):
        if bar[0] + BAR_MS < live_ms:
            continue
        window = bars[max(0, i - 399) : i + 1]
        if len(window) < 80:
            continue
        rows = setups(*[[item[k] for item in window] for k in range(5)])
        if rows and rows[-1].index == len(window) - 1:
            found.append(flipped(rows[-1]))
    return found


def level_hit(side: str, stop: float, target: float, high: float, low: float) -> tuple[float, str] | None:
    if side == "long":
        if low <= stop:
            return stop, "stop"
        if high >= target:
            return target, "tp"
    elif high >= stop:
        return stop, "stop"
    elif low <= target:
        return target, "tp"
    return None


def run() -> None:
    live_ms = int(LIVE.timestamp() * 1000)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    m15, m1 = {}, {}
    for symbol in FIXED_SCAN_SYMBOLS:
        m15[symbol] = fetch(symbol, "15m", live_ms - 420 * BAR_MS, end_ms)
        m1[symbol] = fetch(symbol, "1m", live_ms, end_ms)
    signals = {symbol: signals_of(m15[symbol], live_ms) for symbol in FIXED_SCAN_SYMBOLS}
    m15_at = {symbol: {bar[0]: bar for bar in m15[symbol]} for symbol in FIXED_SCAN_SYMBOLS}
    idx1 = {symbol: {bar[0]: bar for bar in m1[symbol]} for symbol in FIXED_SCAN_SYMBOLS}
    sig_i = {symbol: 0 for symbol in FIXED_SCAN_SYMBOLS}
    zones: dict[str, dict] = {}
    positions: dict[str, dict] = {}
    cash = START_EQUITY
    last: dict[str, float] = {}
    trades: list[dict] = []
    skipped: dict[str, int] = defaultdict(int)
    peak = START_EQUITY
    max_dd = 0.0

    def equity() -> float:
        upnl = 0.0
        locked = 0.0
        for symbol, pos in positions.items():
            if not pos["slices"]:
                continue
            mark = last.get(symbol, pos["slices"][-1]["entry"])
            locked += sum(part["margin"] for part in pos["slices"])
            for part in pos["slices"]:
                if pos["side"] == "long":
                    upnl += (mark - part["entry"]) * part["qty"]
                else:
                    upnl += (part["entry"] - mark) * part["qty"]
        return cash + locked + upnl

    def note() -> None:
        nonlocal peak, max_dd
        eq = equity()
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    def close_pos(symbol: str, price: float, ts: int, reason: str) -> None:
        nonlocal cash
        pos = positions.pop(symbol)
        maker = reason == "tp"
        fee_rate = MAKER_FEE_RATE if maker else TAKER_FEE_RATE
        gross = 0.0
        fee_open = 0.0
        qty = 0.0
        margin = 0.0
        for part in pos["slices"]:
            qty += part["qty"]
            margin += part["margin"]
            fee_open += part["fee_open"]
            gross += (price - part["entry"]) * part["qty"] if pos["side"] == "long" else (part["entry"] - price) * part["qty"]
        fee_close = qty * price * fee_rate
        cash += margin + gross - fee_close
        trades.append(
            {
                "symbol": symbol,
                "side": pos["side"],
                "pnl": gross - fee_open - fee_close,
                "reason": reason,
                "exit_ts": ts,
                "parts": len(pos["slices"]),
                "signal_ts": pos["signal_ts"],
            }
        )

    def add_slice(symbol: str, price: float, ts: int) -> None:
        nonlocal cash
        pos = positions[symbol]
        if len(pos["slices"]) >= SLICES or price <= 0 or cash < pos["slice_margin"]:
            return
        margin = pos["slice_margin"]
        notional = margin * LEVERAGE
        qty = notional / price
        fee_open = notional * TAKER_FEE_RATE
        cash -= margin + fee_open
        pos["slices"].append({"entry": price, "qty": qty, "margin": margin, "fee_open": fee_open, "ts": ts})

    def entry_touched(side: str, entry: float, high: float, low: float) -> bool:
        return high >= entry if side == "long" else low <= entry

    t = live_ms - (live_ms % MINUTE)
    while t <= end_ms:
        for symbol in FIXED_SCAN_SYMBOLS:
            rows = signals[symbol]
            i = sig_i[symbol]
            while i < len(rows) and rows[i].signal_ts + BAR_MS < t:
                i += 1
            sig_i[symbol] = i
            busy = symbol in zones or symbol in positions
            if i < len(rows) and rows[i].signal_ts + BAR_MS == t and not busy:
                order = rows[i]
                sig_i[symbol] = i + 1
                candle = m15_at[symbol].get(order.signal_ts)
                if candle is not None and stop_hit(order.side, order.stop, candle[4]):
                    skipped["through"] += 1
                    continue
                eq = equity()
                if order_notional(order.entry, order.stop, order.target, eq) is None:
                    skipped["small"] += 1
                    continue
                zones[symbol] = {
                    "side": order.side,
                    "stop": order.stop,
                    "target": order.target,
                    "signal_ts": order.signal_ts,
                    "entry": order.entry,
                    "slice_margin": eq * MARGIN_PCT / SLICES,
                }
            elif i < len(rows) and rows[i].signal_ts + BAR_MS <= t:
                sig_i[symbol] = i + 1
        for symbol in list(zones):
            bar = idx1[symbol].get(t)
            if bar is None:
                continue
            _ts, opened, high, low, close = bar
            last[symbol] = close
            zone = zones[symbol]
            side = zone["side"]
            if t >= zone["signal_ts"] + (TTL_BARS + 1) * BAR_MS and symbol not in positions:
                zones.pop(symbol, None)
                skipped["expire"] += 1
                continue
            hit = level_hit(side, zone["stop"], zone["target"], high, low)
            if symbol not in positions:
                if t < zone["signal_ts"] + BAR_MS:
                    continue
                touched = entry_touched(side, zone["entry"], high, low)
                if touched and hit is not None and hit[1] == "stop":
                    zones.pop(symbol, None)
                    skipped["both_touch"] += 1
                    continue
                if hit is not None and hit[1] == "stop":
                    zones.pop(symbol, None)
                    skipped["stop_before_fill"] += 1
                    continue
                if not touched:
                    continue
                positions[symbol] = {**zone, "slices": []}
                add_slice(symbol, zone["entry"], t + MINUTE)
                if not positions[symbol]["slices"]:
                    positions.pop(symbol, None)
                    continue
                if hit is not None:
                    close_pos(symbol, hit[0], t + MINUTE, hit[1])
                    zones.pop(symbol, None)
                continue
            if hit is not None:
                close_pos(symbol, hit[0], t + MINUTE, hit[1])
                zones.pop(symbol, None)
                continue
            if len(positions[symbol]["slices"]) >= SLICES:
                continue
            opposite = (side == "long" and close < opened) or (side == "short" and close > opened)
            if opposite:
                add_slice(symbol, close, t + MINUTE)
        if (t + MINUTE) % BAR_MS == 0:
            note()
        t += MINUTE

    for symbol, pos in list(positions.items()):
        price = last.get(symbol, 0.0)
        if price > 0 and pos["slices"]:
            first = pos["slices"][0]
            print(
                "OPEN",
                symbol,
                pos["side"],
                "signal",
                datetime.fromtimestamp(pos["signal_ts"] / 1000, VN).strftime("%d/%m %H:%M"),
                "fill",
                datetime.fromtimestamp(first["ts"] / 1000, VN).strftime("%d/%m %H:%M"),
                "entry",
                round(pos["entry"], 6),
                "stop",
                round(pos["stop"], 6),
                "tp",
                round(pos["target"], 6),
                "mark",
                price,
                "parts",
                len(pos["slices"]),
            )
            close_pos(symbol, price, end_ms, "open")
    note()

    wins = [row for row in trades if row["pnl"] > 0]
    losses = [row for row in trades if row["pnl"] <= 0]
    gross_win = sum(row["pnl"] for row in wins)
    gross_loss = abs(sum(row["pnl"] for row in losses))
    by_reason: dict[str, list] = defaultdict(lambda: [0, 0.0])
    by_day: dict[str, list] = defaultdict(lambda: [0, 0.0])
    by_symbol: dict[str, list] = defaultdict(lambda: [0, 0.0])
    parts = defaultdict(int)
    for row in trades:
        by_reason[row["reason"]][0] += 1
        by_reason[row["reason"]][1] += row["pnl"]
        day = datetime.fromtimestamp(row["exit_ts"] / 1000, VN).strftime("%d/%m")
        by_day[day][0] += 1
        by_day[day][1] += row["pnl"]
        by_symbol[row["symbol"]][0] += 1
        by_symbol[row["symbol"]][1] += row["pnl"]
        parts[row["parts"]] += 1
    end_vn = datetime.fromtimestamp(end_ms / 1000, VN).strftime("%d/%m %H:%M")
    final = equity()
    print(f"window 14/10 00:00 → {end_vn}")
    print(f"signals {sum(len(v) for v in signals.values())}  trades {len(trades)}  skipped {dict(skipped)}")
    print(f"start {START_EQUITY:.0f}  final {final:.2f}  pnl {final - START_EQUITY:+.2f}  maxDD {max_dd * 100:.1f}%")
    wr = len(wins) / len(trades) * 100 if trades else 0
    pf = gross_win / gross_loss if gross_loss else 0
    print(f"winrate {wr:.1f}%  pf {pf:.2f}")
    print("reasons", {k: (v[0], round(v[1], 2)) for k, v in sorted(by_reason.items())})
    print("parts", dict(sorted(parts.items())))
    print("day")
    for day in sorted(by_day):
        n, amount = by_day[day]
        print(f"  {day}  n={n}  pnl={amount:+.2f}")
    print("symbol")
    for symbol, (n, amount) in sorted(by_symbol.items(), key=lambda item: item[1][1]):
        print(f"  {symbol}  n={n}  pnl={amount:+.2f}")


if __name__ == "__main__":
    run()
