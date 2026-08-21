"""Donchian parallel-trend signal detection.

Logic mirrors backtest_link_donchian_parallel_trend.py + body_size_rr05 filters:
1. Compute upper/middle/lower bands (rolling max/min over DONCHIAN_PERIOD bars).
2. Normalize slope of upper and lower over SLOPE_LOOKBACK bars (%/bar relative to close).
3. Bands are "parallel" when |slope_upper - slope_lower| <= PARALLEL_TOL.
4. On parallel->non-parallel transition: set trend = up if close > middle else down.
5. After trend set (waiting_entry=True): on counter-trend candle where bands are
   still non-parallel, apply body ATR + pot RR filters, then signal long/short.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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

    trend: str | None = None  # "up" | "down" | None
    trend_ts: int | None = None
    waiting_entry: bool = False
    prev_parallel: bool = False  # parallel flag of last processed bar
    last_processed_ts: int | None = None  # last closed bar already applied to state


@dataclass
class DonchianBands:
    upper: float
    middle: float
    lower: float
    parallel: bool


@dataclass(frozen=True)
class EntrySignal:
    """Live entry candidate after quality filters."""

    side: str  # "long" | "short"
    tp_band: float
    entry_px: float
    opp_band: float
    body_atr: float
    pot_rr: float
    size_mult: float
    atr: float
    why: str


def compute_bands(
    bars: Sequence[DonchianBar], period: int, slope_lookback: int, tol: float
) -> list[DonchianBands | None]:
    """Return per-bar Donchian band values. None when not enough bars for period."""
    n = len(bars)
    result: list[DonchianBands | None] = [None] * n
    for i in range(n):
        if i < period - 1:
            continue
        upper = max(b.high for b in bars[i - period + 1 : i + 1])
        lower = min(b.low for b in bars[i - period + 1 : i + 1])
        middle = (upper + lower) / 2.0
        ref = bars[i].close
        prev_start = i - slope_lookback - period + 1
        if prev_start < 0 or ref <= 0:
            parallel = True
        else:
            prev_upper = max(b.high for b in bars[prev_start : i - slope_lookback + 1])
            prev_lower = min(b.low for b in bars[prev_start : i - slope_lookback + 1])
            su = (upper - prev_upper) / slope_lookback / ref * 100.0
            sl = (lower - prev_lower) / slope_lookback / ref * 100.0
            parallel = abs(su - sl) <= tol
        result[i] = DonchianBands(upper=upper, middle=middle, lower=lower, parallel=parallel)
    return result


def compute_atr(bars: Sequence[DonchianBar], period: int) -> list[float | None]:
    """SMA of True Range. Index i is None until i >= period (needs period TRs → period+1 bars)."""
    n = len(bars)
    out: list[float | None] = [None] * n
    if period <= 0 or n < period + 1:
        return out
    trs: list[float] = []
    for i in range(1, n):
        prev_c = bars[i - 1].close
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev_c),
            abs(bars[i].low - prev_c),
        )
        trs.append(tr)
        if len(trs) >= period:
            window = trs[-period:]
            out[i] = sum(window) / period
    return out


def size_mult_from_pot_rr(pot_rr: float, *, enabled: bool = True) -> float:
    if not enabled:
        return 1.0
    return float(min(2.0, max(0.5, 0.5 + pot_rr)))


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
    apply_quality_filter: bool = True,
    atr_period: int = 14,
    min_body_atr: float = 0.3,
    max_body_atr: float = 1.2,
    min_pot_rr: float = 0.5,
    size_by_rr: bool = True,
) -> EntrySignal | None:
    """Process the last closed bar against current state.

    Returns EntrySignal on a valid entry, else None.
    Quality filter fail keeps waiting_entry=True (retry later counters).
    Mutates `state` in place.
    """
    if not bars:
        return None

    min_bars = period + slope_lookback
    if apply_quality_filter:
        min_bars = max(min_bars, atr_period + 1)
    if len(bars) < min_bars:
        return None

    bands_list = compute_bands(bars, period, slope_lookback, tol)
    last_idx = len(bars) - 1
    bands = bands_list[last_idx]
    if bands is None:
        return None

    bar = bars[last_idx]
    currently_parallel = bands.parallel

    parallel_exit = state.prev_parallel and not currently_parallel
    if parallel_exit:
        state.trend = "up" if bar.close > bands.middle else "down"
        state.trend_ts = bar.ts
        state.waiting_entry = True

    entry: EntrySignal | None = None

    if allow_entry and state.waiting_entry and state.trend is not None:
        is_counter = (state.trend == "up" and bar.close < bar.open) or (
            state.trend == "down" and bar.close > bar.open
        )
        if is_counter and not currently_parallel:
            side = "long" if state.trend == "up" else "short"
            tp_band = bands.upper if side == "long" else bands.lower
            opp_band = bands.lower if side == "long" else bands.upper
            entry_px = bar.close
            dist_tp = abs(tp_band - entry_px)
            dist_sl = abs(entry_px - opp_band)
            pot_rr = dist_tp / dist_sl if dist_sl > 1e-12 else 0.0

            atr_val = 0.0
            body_atr = 0.0
            if apply_quality_filter:
                atrs = compute_atr(bars, atr_period)
                atr_raw = atrs[last_idx]
                if atr_raw is None or atr_raw <= 1e-12:
                    # Not enough ATR — wait for later bar
                    state.prev_parallel = currently_parallel
                    if not allow_entry:
                        state.waiting_entry = False
                    return None
                atr_val = float(atr_raw)
                body_atr = abs(bar.close - bar.open) / atr_val
                if body_atr < min_body_atr or body_atr > max_body_atr or pot_rr < min_pot_rr:
                    logging.debug(
                        "Donchian entry filter skip: body_atr=%.3f (need %.2f–%.2f) pot_rr=%.3f (need ≥%.2f)",
                        body_atr,
                        min_body_atr,
                        max_body_atr,
                        pot_rr,
                        min_pot_rr,
                    )
                    # Keep waiting_entry for a later counter candle
                    state.prev_parallel = currently_parallel
                    return None

            size_mult = size_mult_from_pot_rr(pot_rr, enabled=size_by_rr and apply_quality_filter)
            counter_label = "nến đỏ ngược chiều" if side == "long" else "nến xanh ngược chiều"
            why = (
                f"thoát song song → {state.trend.upper()}; {counter_label}; "
                f"band không song song"
            )
            entry = EntrySignal(
                side=side,
                tp_band=tp_band,
                entry_px=entry_px,
                opp_band=opp_band,
                body_atr=body_atr,
                pot_rr=pot_rr,
                size_mult=size_mult,
                atr=atr_val,
                why=why,
            )
            state.waiting_entry = False

    state.prev_parallel = currently_parallel
    # Backtest: when max open reached, discard pending entry (no queue while holding).
    if not allow_entry:
        state.waiting_entry = False
    return entry


def process_closed_bars(
    bars: Sequence[DonchianBar],
    state: SignalState,
    *,
    period: int,
    slope_lookback: int,
    tol: float,
    allow_entry: bool = True,
    apply_quality_filter: bool = True,
    atr_period: int = 14,
    min_body_atr: float = 0.3,
    max_body_atr: float = 1.2,
    min_pot_rr: float = 0.5,
    size_by_rr: bool = True,
) -> EntrySignal | None:
    """Apply every new closed bar to state.

    First run (no last_processed_ts): only the latest bar — never replay history
    into live orders. After a skipped cycle, intermediate bars update trend
    state, but a signal is returned only if it is on the latest closed bar.
    """
    if not bars:
        return None

    kwargs = dict(
        period=period,
        slope_lookback=slope_lookback,
        tol=tol,
        allow_entry=allow_entry,
        apply_quality_filter=apply_quality_filter,
        atr_period=atr_period,
        min_body_atr=min_body_atr,
        max_body_atr=max_body_atr,
        min_pot_rr=min_pot_rr,
        size_by_rr=size_by_rr,
    )

    latest_ts = bars[-1].ts
    if state.last_processed_ts is None:
        entry = check_signal(bars, state, **kwargs)
        state.last_processed_ts = latest_ts
        return entry

    out: EntrySignal | None = None
    for i, bar in enumerate(bars):
        if bar.ts <= state.last_processed_ts:
            continue
        prefix = bars[: i + 1]
        entry = check_signal(prefix, state, **kwargs)
        state.last_processed_ts = bar.ts
        if entry is not None and bar.ts == latest_ts:
            out = entry
    return out
