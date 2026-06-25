from dataclasses import dataclass

from src.bitget_client import Candle
from src.config import (
    RSI_LONG_ENTRY,
    RSI_LONG_EXIT,
    RSI_PERIOD,
    RSI_SHORT_ENTRY,
    RSI_SHORT_EXIT,
)


@dataclass
class RsiSnapshot:
    ready: bool = False
    rsi: float = 0.0
    prev_rsi: float = 0.0
    close: float = 0.0
    cross_up_25: bool = False
    cross_up_75: bool = False
    cross_down_75: bool = False
    cross_down_25: bool = False


def compute_rsi_series(candles: list[Candle], period: int = RSI_PERIOD) -> list[float | None]:
    n = len(candles)
    rsi_values: list[float | None] = [None] * n
    if n < period + 1:
        return rsi_values

    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, period + 1):
        change = candles[i].close - candles[i - 1].close
        avg_gain += max(change, 0.0)
        avg_loss += max(-change, 0.0)
    avg_gain /= period
    avg_loss /= period

    if avg_loss == 0:
        rsi_values[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_values[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period + 1, n):
        change = candles[i].close - candles[i - 1].close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi_values[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_values[i] = 100.0 - (100.0 / (1.0 + rs))

    return rsi_values


def _cross_up(prev: float, curr: float, level: float) -> bool:
    return prev <= level and curr > level


def _cross_down(prev: float, curr: float, level: float) -> bool:
    return prev >= level and curr < level


def get_rsi_snapshot(
    candles: list[Candle],
    period: int = RSI_PERIOD,
) -> RsiSnapshot:
    min_bars = period + 2
    if len(candles) < min_bars:
        return RsiSnapshot(ready=False)

    rsi_series = compute_rsi_series(candles, period)
    i = len(candles) - 1
    prev_i = i - 1
    curr_rsi = rsi_series[i]
    prev_rsi_val = rsi_series[prev_i]
    if curr_rsi is None or prev_rsi_val is None:
        return RsiSnapshot(ready=False)

    return RsiSnapshot(
        ready=True,
        rsi=curr_rsi,
        prev_rsi=prev_rsi_val,
        close=candles[i].close,
        cross_up_25=_cross_up(prev_rsi_val, curr_rsi, RSI_LONG_ENTRY),
        cross_up_75=_cross_up(prev_rsi_val, curr_rsi, RSI_LONG_EXIT),
        cross_down_75=_cross_down(prev_rsi_val, curr_rsi, RSI_SHORT_ENTRY),
        cross_down_25=_cross_down(prev_rsi_val, curr_rsi, RSI_SHORT_EXIT),
    )
