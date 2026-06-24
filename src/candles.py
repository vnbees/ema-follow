from datetime import datetime, timezone

from src.bitget_client import Candle
from src.config import INTERVAL_MINUTES


def _current_period_start_ms(interval_minutes: int = INTERVAL_MINUTES) -> int:
    interval_ms = interval_minutes * 60 * 1000
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return (now_ms // interval_ms) * interval_ms


def period_start_ms(ts_ms: int, interval_minutes: int = INTERVAL_MINUTES) -> int:
    interval_ms = interval_minutes * 60 * 1000
    return (ts_ms // interval_ms) * interval_ms


def get_closed_candles(
    candles: list[Candle],
    interval_minutes: int = INTERVAL_MINUTES,
) -> list[Candle]:
    """Return only fully closed candles (exclude the currently forming candle)."""
    period_start = _current_period_start_ms(interval_minutes)
    return [c for c in candles if c.timestamp < period_start]


def get_last_closed_candle(
    candles: list[Candle],
    interval_minutes: int = INTERVAL_MINUTES,
) -> Candle:
    closed = get_closed_candles(candles, interval_minutes)
    if not closed:
        raise ValueError("No closed candles available")
    return closed[-1]
