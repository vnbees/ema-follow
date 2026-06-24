from src.bitget_client import Candle
from src.config import OFI_SPIKE_THRESHOLD

NEUTRAL_PROB_THRESHOLD = 55.0


def compute_ofi_bias(
    candle: Candle,
    volume_delta: float,
    delta_spike_ratio: float,
    spike_threshold: float = OFI_SPIKE_THRESHOLD,
) -> str:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return "neutral"

    close_position = (candle.close - candle.low) / candle_range

    if (
        close_position <= 0.30
        and volume_delta > 0
        and delta_spike_ratio >= spike_threshold
    ):
        return "bullish"

    if (
        close_position >= 0.70
        and volume_delta < 0
        and delta_spike_ratio >= spike_threshold
    ):
        return "bearish"

    return "neutral"


def prediction_label(direction: str, probability: float) -> str:
    """UI label: neutral when confidence is low, else green/red."""
    if probability < NEUTRAL_PROB_THRESHOLD:
        return "neutral"
    return direction


def compute_next_candle_prediction(
    closed_candle: Candle,
    *,
    volume_delta: float,
    current_delta: float,
    delta_spike_ratio: float,
    avg_delta_10: float,
    buy_volume: float,
    sell_volume: float,
) -> tuple[str, float]:
    """Always green or red; probability 50-92 reflects confidence."""
    bias = compute_ofi_bias(
        closed_candle,
        volume_delta,
        delta_spike_ratio,
    )

    spike = delta_spike_ratio
    total_vol = buy_volume + sell_volume
    buy_ratio = buy_volume / total_vol if total_vol > 0 else 0.5

    if bias == "bullish":
        prob = 58.0 + min(25.0, max(0.0, spike - 1.0) * 10.0) + (buy_ratio - 0.5) * 20.0
        return "green", min(92.0, prob)

    if bias == "bearish":
        prob = 58.0 + min(25.0, max(0.0, spike - 1.0) * 10.0) + (0.5 - buy_ratio) * 20.0
        return "red", min(92.0, prob)

    if volume_delta > 0:
        prob = 52.0 + min(12.0, abs(volume_delta) / max(avg_delta_10, 1e-9) * 4.0)
        return "green", min(74.0, prob)

    if volume_delta < 0:
        prob = 52.0 + min(12.0, abs(volume_delta) / max(avg_delta_10, 1e-9) * 4.0)
        return "red", min(74.0, prob)

    if current_delta > 0:
        return "green", 52.0

    if current_delta < 0:
        return "red", 52.0

    if buy_ratio > 0.5:
        return "green", 51.0

    if buy_ratio < 0.5:
        return "red", 51.0

    return "green", 50.0
