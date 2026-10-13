"""Failed liquidity sweep back to the swing equilibrium.

Matches the UNI 15m backtest: swing length 50, wick through the latest swing
extreme that closes back inside, wick no longer than 0.8 ATR(14), limit at the
extreme, stop at the wick, target at the midpoint. Reward must be at least the
risk, and risk at most 1.5% of price.
"""

from __future__ import annotations

from dataclasses import dataclass

SWING = 50
ATR_PERIOD = 14
WICK_ATR = 0.8
MAX_RISK_PCT = 0.015
BAR_MS = 15 * 60 * 1000
TTL_BARS = 16


@dataclass(frozen=True)
class Setup:
    side: str
    index: int
    signal_ts: int
    entry: float
    stop: float
    target: float


def _atr(highs: list[float], lows: list[float], closes: list[float]) -> list[float | None]:
    n = len(closes)
    trs = [0.0] * n
    for i in range(n):
        if i == 0:
            trs[i] = highs[i] - lows[i]
        else:
            trs[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
    out: list[float | None] = [None] * n
    if n < ATR_PERIOD:
        return out
    acc = sum(trs[:ATR_PERIOD])
    out[ATR_PERIOD - 1] = acc / ATR_PERIOD
    for i in range(ATR_PERIOD, n):
        acc += trs[i] - trs[i - ATR_PERIOD]
        out[i] = acc / ATR_PERIOD
    return out


def setups(
    ts: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[Setup]:
    """Every bar that is a valid sweep. `opens` is unused; kept so callers pass OHLC as stored."""
    del opens
    n = len(closes)
    if n != len(ts) or n != len(highs) or n != len(lows):
        raise ValueError("ohlc length mismatch")
    atr = _atr(highs, lows, closes)
    found: list[Setup] = []
    leg = 0
    hi: float | None = None
    lo: float | None = None
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
        prev_atr = atr[i - 1] if i else None
        if not prev_atr or hi is None or lo is None or not hi > lo:
            continue
        mid = (hi + lo) / 2
        if lows[i] < lo and closes[i] > lo and (lo - lows[i]) <= WICK_ATR * prev_atr:
            entry, stop = lo, lows[i]
            risk, reward = entry - stop, mid - entry
            if risk > 0 and reward >= risk and risk / closes[i] <= MAX_RISK_PCT:
                found.append(Setup("long", i, ts[i], entry, stop, mid))
        elif highs[i] > hi and closes[i] < hi and (highs[i] - hi) <= WICK_ATR * prev_atr:
            entry, stop = hi, highs[i]
            risk, reward = stop - entry, entry - mid
            if risk > 0 and reward >= risk and risk / closes[i] <= MAX_RISK_PCT:
                found.append(Setup("short", i, ts[i], entry, stop, mid))
    return found


def setup_on_last_bar(
    ts: list[int],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> Setup | None:
    rows = setups(ts, opens, highs, lows, closes)
    if not rows or rows[-1].index != len(closes) - 1:
        return None
    return rows[-1]


def expired(signal_ts: int, now_ms: int, ttl_bars: int = TTL_BARS) -> bool:
    """True once the bar after the fill window has opened.

    The signal bar is index i. Fills are allowed on i+1 .. i+ttl. The next bar
    opens at signal_ts + (ttl + 1) bars and cancels the order.
    """
    return now_ms >= int(signal_ts) + (ttl_bars + 1) * BAR_MS


def stop_hit(side: str, stop: float, price: float) -> bool:
    if side == "long":
        return price <= stop
    return price >= stop
