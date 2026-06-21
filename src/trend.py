from src.bitget_client import Candle


def detect_trend(ema20: float, ema50: float, ema100: float, ema200: float) -> str:
    if ema20 > ema50 > ema100 > ema200:
        return "uptrend"
    if ema20 < ema50 < ema100 < ema200:
        return "downtrend"
    return "sideway"


def candle_color(candle: Candle) -> str:
    if candle.close > candle.open:
        return "green"
    if candle.close < candle.open:
        return "red"
    return "neutral"
