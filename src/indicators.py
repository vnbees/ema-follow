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
