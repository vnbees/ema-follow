from dataclasses import dataclass

from src.bitget_client import Candle
from src.config import (
    ICHIMOKU_DISPLACEMENT,
    ICHIMOKU_KIJUN,
    ICHIMOKU_MIN_CANDLES,
    ICHIMOKU_SENKOU_B,
    ICHIMOKU_TENKAN,
)


@dataclass
class IchimokuSnapshot:
    ready: bool = False
    tenkan: float = 0.0
    kijun: float = 0.0
    senkou_a: float = 0.0
    senkou_b: float = 0.0
    kumo_top: float = 0.0
    kumo_bottom: float = 0.0
    kumo_color: str = "neutral"
    kumo_rising: bool = False
    kumo_falling: bool = False
    price_vs_kumo: str = "inside"
    chikou_bullish: bool = False
    chikou_bearish: bool = False
    ichimoku_trend: str = "neutral"
    close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    prev_close: float = 0.0
    prev_kijun: float = 0.0
    prev_open: float = 0.0
    prev_high: float = 0.0
    prev_low: float = 0.0


def _midpoint(candles: list[Candle], end_idx: int, period: int) -> float | None:
    if end_idx < period - 1:
        return None
    start = end_idx - period + 1
    highs = [candles[i].high for i in range(start, end_idx + 1)]
    lows = [candles[i].low for i in range(start, end_idx + 1)]
    return (max(highs) + min(lows)) / 2.0


def compute_ichimoku_series(candles: list[Candle]) -> tuple[
    list[float | None],
    list[float | None],
    list[float | None],
    list[float | None],
]:
    n = len(candles)
    tenkan: list[float | None] = [None] * n
    kijun: list[float | None] = [None] * n
    senkou_a: list[float | None] = [None] * n
    senkou_b: list[float | None] = [None] * n

    for i in range(n):
        tenkan[i] = _midpoint(candles, i, ICHIMOKU_TENKAN)
        kijun[i] = _midpoint(candles, i, ICHIMOKU_KIJUN)
        raw_b = _midpoint(candles, i, ICHIMOKU_SENKOU_B)
        if tenkan[i] is not None and kijun[i] is not None:
            shifted = i + ICHIMOKU_DISPLACEMENT
            if shifted < n:
                senkou_a[shifted] = (tenkan[i] + kijun[i]) / 2.0
        if raw_b is not None:
            shifted = i + ICHIMOKU_DISPLACEMENT
            if shifted < n:
                senkou_b[shifted] = raw_b

    return tenkan, kijun, senkou_a, senkou_b


def get_ichimoku_snapshot(candles: list[Candle]) -> IchimokuSnapshot:
    if len(candles) < ICHIMOKU_MIN_CANDLES:
        return IchimokuSnapshot(ready=False)

    tenkan, kijun, senkou_a, senkou_b = compute_ichimoku_series(candles)
    i = len(candles) - 1
    prev_i = i - 1

    t = tenkan[i]
    k = kijun[i]
    sa = senkou_a[i]
    sb = senkou_b[i]
    if t is None or k is None or sa is None or sb is None:
        return IchimokuSnapshot(ready=False)

    prev_k = kijun[prev_i]
    if prev_k is None:
        return IchimokuSnapshot(ready=False)

    candle = candles[i]
    prev = candles[prev_i]
    kumo_top = max(sa, sb)
    kumo_bottom = min(sa, sb)
    kumo_color = "green" if sa > sb else "red" if sa < sb else "neutral"

    prev_sa = senkou_a[prev_i]
    prev_sb = senkou_b[prev_i]
    kumo_rising = (
        prev_sa is not None
        and prev_sb is not None
        and sa > prev_sa
        and sb > prev_sb
    )
    kumo_falling = (
        prev_sa is not None
        and prev_sb is not None
        and sa < prev_sa
        and sb < prev_sb
    )

    if candle.close > kumo_top:
        price_vs_kumo = "above"
    elif candle.close < kumo_bottom:
        price_vs_kumo = "below"
    else:
        price_vs_kumo = "inside"

    chikou_idx = i - ICHIMOKU_DISPLACEMENT
    chikou_bullish = chikou_idx >= 0 and candle.close > candles[chikou_idx].close
    chikou_bearish = chikou_idx >= 0 and candle.close < candles[chikou_idx].close

    if price_vs_kumo == "above" and kumo_color == "green":
        ichimoku_trend = "bullish"
    elif price_vs_kumo == "below" and kumo_color == "red":
        ichimoku_trend = "bearish"
    else:
        ichimoku_trend = "neutral"

    return IchimokuSnapshot(
        ready=True,
        tenkan=t,
        kijun=k,
        senkou_a=sa,
        senkou_b=sb,
        kumo_top=kumo_top,
        kumo_bottom=kumo_bottom,
        kumo_color=kumo_color,
        kumo_rising=kumo_rising,
        kumo_falling=kumo_falling,
        price_vs_kumo=price_vs_kumo,
        chikou_bullish=chikou_bullish,
        chikou_bearish=chikou_bearish,
        ichimoku_trend=ichimoku_trend,
        close=candle.close,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        prev_close=prev.close,
        prev_kijun=prev_k,
        prev_open=prev.open,
        prev_high=prev.high,
        prev_low=prev.low,
    )
