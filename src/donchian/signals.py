"""Donchian parallel-trend signal detection.

Logic mirrors backtest_link_donchian_parallel_trend.py exactly:
1. Compute upper/middle/lower bands (rolling max/min over DONCHIAN_PERIOD bars).
2. Normalize slope of upper and lower over SLOPE_LOOKBACK bars (%/bar relative to close).
3. Bands are "parallel" when |slope_upper - slope_lower| <= PARALLEL_TOL.
4. On parallel->non-parallel transition: set trend = up if close > middle else down.
5. After trend set (waiting_entry=True): on first counter-trend candle where bands are
   still non-parallel -> signal long (up) or short (down).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class DonchianBar:
    ts: int
    open: float
    high: float
    low: float
    close: float


@dataclass
class SignalState:
    """Per-symbol mutable state carried across cycles."""
    trend: str | None = None          # "up" | "down" | None
    trend_ts: int | None = None
    waiting_entry: bool = False
    prev_parallel: bool = False       # parallel flag of last processed bar
    last_processed_ts: int | None = None  # last closed bar already applied to state


@dataclass
class DonchianBands:
    upper: float
    middle: float
    lower: float
    parallel: bool


def compute_bands(bars: Sequence[DonchianBar], period: int, slope_lookback: int, tol: float) -> list[DonchianBands | None]:
    """Return per-bar Donchian band values. None when not enough bars for period."""
    n = len(bars)
    result: list[DonchianBands | None] = [None] * n
    for i in range(n):
        if i < period - 1:
            continue
        upper = max(b.high for b in bars[i - period + 1: i + 1])
        lower = min(b.low for b in bars[i - period + 1: i + 1])
        middle = (upper + lower) / 2.0
        ref = bars[i].close
        prev_start = i - slope_lookback - period + 1
        if prev_start < 0 or ref <= 0:
            parallel = True
        else:
            prev_upper = max(b.high for b in bars[prev_start: i - slope_lookback + 1])
            prev_lower = min(b.low for b in bars[prev_start: i - slope_lookback + 1])
            su = (upper - prev_upper) / slope_lookback / ref * 100.0
            sl = (lower - prev_lower) / slope_lookback / ref * 100.0
            parallel = abs(su - sl) <= tol
        result[i] = DonchianBands(upper=upper, middle=middle, lower=lower, parallel=parallel)
    return result


def rolling_channel(highs: Sequence[float], lows: Sequence[float], period: int) -> tuple[float, float] | None:
    """Donchian upper/lower from the last `period` bars (includes forming bar)."""
    if period <= 0 or len(highs) < period or len(lows) < period:
        return None
    window_h = highs[-period:]
    window_l = lows[-period:]
    return max(window_h), min(window_l)


def check_signal(
    bars: Sequence[DonchianBar],
    state: SignalState,
    *,
    period: int,
    slope_lookback: int,
    tol: float,
    allow_entry: bool = True,
) -> tuple[str | None, float, float]:
    """Process the last closed bar against current state.

    Returns (signal, tp_band, entry_px) where:
      signal = "long" | "short" | None
      tp_band = Donchian band price target (upper for long, lower for short)
      entry_px = close of the signal bar

    Mutates `state` in place.
    """
    if not bars:
        return None, 0.0, 0.0

    min_bars = period + slope_lookback
    if len(bars) < min_bars:
        return None, 0.0, 0.0

    bands_list = compute_bands(bars, period, slope_lookback, tol)
    last_idx = len(bars) - 1
    bands = bands_list[last_idx]
    if bands is None:
        return None, 0.0, 0.0

    bar = bars[last_idx]
    currently_parallel = bands.parallel

    parallel_exit = state.prev_parallel and not currently_parallel
    if parallel_exit:
        state.trend = "up" if bar.close > bands.middle else "down"
        state.trend_ts = bar.ts
        state.waiting_entry = True

    signal: str | None = None
    tp_band = 0.0
    entry_px = 0.0

    if allow_entry and state.waiting_entry and state.trend is not None:
        is_counter = (
            (state.trend == "up" and bar.close < bar.open) or
            (state.trend == "down" and bar.close > bar.open)
        )
        if is_counter and not currently_parallel:
            signal = "long" if state.trend == "up" else "short"
            tp_band = bands.upper if signal == "long" else bands.lower
            entry_px = bar.close
            state.waiting_entry = False

    state.prev_parallel = currently_parallel
    # Backtest: when max open reached, discard pending entry (no queue while holding).
    if not allow_entry:
        state.waiting_entry = False
    return signal, tp_band, entry_px


def process_closed_bars(
    bars: Sequence[DonchianBar],
    state: SignalState,
    *,
    period: int,
    slope_lookback: int,
    tol: float,
    allow_entry: bool = True,
) -> tuple[str | None, float, float]:
    """Apply every new closed bar to state.

    First run (no last_processed_ts): only the latest bar — never replay history
    into live orders. After a skipped cycle, intermediate bars update trend
    state, but a signal is returned only if it is on the latest closed bar.
    """
    if not bars:
        return None, 0.0, 0.0

    latest_ts = bars[-1].ts
    if state.last_processed_ts is None:
        signal, tp_band, entry_px = check_signal(
            bars, state, period=period, slope_lookback=slope_lookback, tol=tol, allow_entry=allow_entry
        )
        state.last_processed_ts = latest_ts
        return signal, tp_band, entry_px

    out: tuple[str | None, float, float] = (None, 0.0, 0.0)
    for i, bar in enumerate(bars):
        if bar.ts <= state.last_processed_ts:
            continue
        prefix = bars[: i + 1]
        signal, tp_band, entry_px = check_signal(
            prefix, state, period=period, slope_lookback=slope_lookback, tol=tol, allow_entry=allow_entry
        )
        state.last_processed_ts = bar.ts
        if signal and bar.ts == latest_ts:
            out = (signal, tp_band, entry_px)
    return out
