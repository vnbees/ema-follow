def compute_ema(closes: list[float], period: int) -> float:
    if len(closes) < period:
        raise ValueError(f"Need at least {period} closes to compute EMA{period}, got {len(closes)}")

    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period

    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema

    return ema


def compute_emas(closes: list[float], periods: tuple[int, ...]) -> dict[int, float]:
    return {period: compute_ema(closes, period) for period in periods}


def compute_parabolic_sar(
    candles: list,
    af_start: float = 0.02,
    af_max: float = 0.2,
) -> list[float | None]:
    """Wilder Parabolic SAR aligned with TradingView defaults."""
    n = len(candles)
    result: list[float | None] = [None] * n
    if n < 2:
        return result

    is_uptrend = candles[1].close >= candles[0].close
    af = af_start
    if is_uptrend:
        ep = max(candles[0].high, candles[1].high)
        sar = min(candles[0].low, candles[1].low)
    else:
        ep = min(candles[0].low, candles[1].low)
        sar = max(candles[0].high, candles[1].high)

    result[0] = sar

    for i in range(1, n):
        candle = candles[i]
        prev = candles[i - 1]

        sar = sar + af * (ep - sar)

        if is_uptrend:
            sar = min(sar, prev.low)
            if i >= 2:
                sar = min(sar, candles[i - 2].low)

            if candle.low < sar:
                is_uptrend = False
                sar = ep
                ep = candle.low
                af = af_start
            else:
                if candle.high > ep:
                    ep = candle.high
                    af = min(af + af_start, af_max)
        else:
            sar = max(sar, prev.high)
            if i >= 2:
                sar = max(sar, candles[i - 2].high)

            if candle.high > sar:
                is_uptrend = True
                sar = ep
                ep = candle.high
                af = af_start
            else:
                if candle.low < ep:
                    ep = candle.low
                    af = min(af + af_start, af_max)

        result[i] = sar

    return result
