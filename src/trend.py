from src.bitget_client import Candle


def detect_trend(ema34: float, ema89: float, ema144: float, ema200: float) -> str:
    if ema34 > ema89 > ema144 > ema200:
        return "uptrend"
    if ema34 < ema89 < ema144 < ema200:
        return "downtrend"
    return "sideway"


def candle_color(candle: Candle) -> str:
    if candle.close > candle.open:
        return "green"
    if candle.close < candle.open:
        return "red"
    return "neutral"
