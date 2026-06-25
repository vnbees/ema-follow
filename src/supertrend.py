from dataclasses import dataclass

from src.bitget_client import Candle
from src.config import SUPERTREND_ATR_PERIOD, SUPERTREND_MULTIPLIER

Trend = str  # "bullish" | "bearish"


@dataclass
class SuperTrendSnapshot:
    ready: bool = False
    trend: Trend = ""
    prev_trend: Trend = ""
    flipped: bool = False
    supertrend_value: float = 0.0
    close: float = 0.0
    atr: float = 0.0


def granularity_to_minutes(granularity: str) -> int:
    g = granularity.strip().upper()
    if g.endswith("M"):
        return int(g[:-1])
    if g.endswith("H"):
        return int(g[:-1]) * 60
    if g.endswith("D"):
        return int(g[:-1]) * 24 * 60
    raise ValueError(f"Unsupported granularity: {granularity}")


def _wilder_atr(candles: list[Candle], period: int) -> list[float | None]:
    n = len(candles)
    atr: list[float | None] = [None] * n
    if n < period + 1:
        return atr

    tr_values: list[float] = []
    for i in range(1, n):
        prev_close = candles[i - 1].close
        tr = max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - prev_close),
            abs(candles[i].low - prev_close),
        )
        tr_values.append(tr)

    first_atr = sum(tr_values[:period]) / period
    atr[period] = first_atr
    prev = first_atr
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + tr_values[i - 1]) / period
        atr[i] = prev
    return atr


def compute_supertrend_series(
    candles: list[Candle],
    period: int = SUPERTREND_ATR_PERIOD,
    multiplier: float = SUPERTREND_MULTIPLIER,
) -> tuple[list[Trend | None], list[float | None]]:
    n = len(candles)
    trends: list[Trend | None] = [None] * n
    st_lines: list[float | None] = [None] * n
    if n < period + 1:
        return trends, st_lines

    atr = _wilder_atr(candles, period)
    final_upper = [0.0] * n
    final_lower = [0.0] * n

    for i in range(period, n):
        atr_val = atr[i]
        if atr_val is None:
            continue
        hl2 = (candles[i].high + candles[i].low) / 2
        basic_upper = hl2 + multiplier * atr_val
        basic_lower = hl2 - multiplier * atr_val

        if i == period:
            final_upper[i] = basic_upper
            final_lower[i] = basic_lower
        else:
            prev_upper = final_upper[i - 1]
            prev_lower = final_lower[i - 1]
            if basic_upper < prev_upper or candles[i - 1].close > prev_upper:
                final_upper[i] = basic_upper
            else:
                final_upper[i] = prev_upper
            if basic_lower > prev_lower or candles[i - 1].close < prev_lower:
                final_lower[i] = basic_lower
            else:
                final_lower[i] = prev_lower

        prev_trend = trends[i - 1]
        if prev_trend is None:
            if candles[i].close > final_upper[i]:
                trends[i] = "bullish"
                st_lines[i] = final_lower[i]
            else:
                trends[i] = "bearish"
                st_lines[i] = final_upper[i]
        elif prev_trend == "bullish":
            if candles[i].close < final_lower[i]:
                trends[i] = "bearish"
                st_lines[i] = final_upper[i]
            else:
                trends[i] = "bullish"
                st_lines[i] = final_lower[i]
        else:
            if candles[i].close > final_upper[i]:
                trends[i] = "bullish"
                st_lines[i] = final_lower[i]
            else:
                trends[i] = "bearish"
                st_lines[i] = final_upper[i]

    return trends, st_lines


def get_supertrend_snapshot(
    candles: list[Candle],
    period: int = SUPERTREND_ATR_PERIOD,
    multiplier: float = SUPERTREND_MULTIPLIER,
) -> SuperTrendSnapshot:
    min_bars = period + 2
    if len(candles) < min_bars:
        return SuperTrendSnapshot(ready=False)

    trends, st_lines = compute_supertrend_series(candles, period, multiplier)
    i = len(candles) - 1
    prev_i = i - 1
    if trends[i] is None or trends[prev_i] is None:
        return SuperTrendSnapshot(ready=False)

    curr = trends[i]
    prev = trends[prev_i]
    return SuperTrendSnapshot(
        ready=True,
        trend=curr,
        prev_trend=prev,
        flipped=curr != prev,
        supertrend_value=st_lines[i] or 0.0,
        close=candles[i].close,
        atr=0.0,
    )
