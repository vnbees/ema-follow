from src.bitget_client import Candle


def sar_position(sar: float, candle: Candle) -> str:
    if sar < candle.low:
        return "below"
    if sar > candle.high:
        return "above"
    return "inside"


def detect_sar_flip(
    prev_candle: Candle,
    curr_candle: Candle,
    prev_sar: float,
    curr_sar: float,
) -> str | None:
    prev_pos = sar_position(prev_sar, prev_candle)
    curr_pos = sar_position(curr_sar, curr_candle)

    if prev_pos == "above" and curr_pos == "below":
        return "bullish_flip"
    if prev_pos == "below" and curr_pos == "above":
        return "bearish_flip"
    return None
