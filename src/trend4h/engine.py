"""Build closed 4h bars and the desired Supertrend+ADX side."""

from __future__ import annotations

from dataclasses import dataclass

from src.exchange.types import Candle
from src.trend4h.config import (
    ADX_MIN,
    ADX_PERIOD,
    BAR_MS,
    ST_FACTOR,
    ST_PERIOD,
)
from src.trend4h.indicators import want_side


@dataclass(frozen=True)
class Bar4h:
    ts: int
    open: float
    high: float
    low: float
    close: float


def resample_closed_4h(candles: list[Candle], now_ms: int) -> list[Bar4h]:
    """15m (or any) candles → completed 4h buckets aligned to epoch."""
    buckets: dict[int, list[Candle]] = {}
    for candle in candles:
        start = (int(candle.timestamp) // BAR_MS) * BAR_MS
        buckets.setdefault(start, []).append(candle)
    out: list[Bar4h] = []
    for start in sorted(buckets):
        if start + BAR_MS > now_ms:
            continue
        rows = sorted(buckets[start], key=lambda c: int(c.timestamp))
        out.append(
            Bar4h(
                ts=start,
                open=float(rows[0].open),
                high=max(float(c.high) for c in rows),
                low=min(float(c.low) for c in rows),
                close=float(rows[-1].close),
            )
        )
    return out


def merge_bars(history: list[Bar4h], extra: list[Bar4h]) -> list[Bar4h]:
    by = {b.ts: b for b in history}
    for bar in extra:
        by[bar.ts] = bar
    return [by[ts] for ts in sorted(by)]


def desired_side(bars: list[Bar4h]) -> int:
    if not bars:
        return 0
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    return want_side(
        highs,
        lows,
        closes,
        adx_min=ADX_MIN,
        st_period=ST_PERIOD,
        st_factor=ST_FACTOR,
        adx_period=ADX_PERIOD,
    )[-1]


def bars_from_klines(rows: list[Candle], now_ms: int) -> list[Bar4h]:
    """Native 4h klines. Drop the still-open bucket."""
    out: list[Bar4h] = []
    for candle in rows:
        ts = int(candle.timestamp)
        if ts + BAR_MS > now_ms:
            continue
        out.append(
            Bar4h(
                ts=ts,
                open=float(candle.open),
                high=float(candle.high),
                low=float(candle.low),
                close=float(candle.close),
            )
        )
    return out
