from dataclasses import dataclass

from src.bitget_client import Candle
from src.config import (
    OFI_BOOK_STRONG_PCT,
    OFI_DELTA_SPIKE_MIN,
    OFI_EARLY_ENTRY_SEC,
    OFI_IMBALANCE_EXTREME_PCT,
    OFI_IMBALANCE_STRONG_PCT,
    OFI_SPIKE_THRESHOLD,
)

NEUTRAL_PROB_THRESHOLD = 55.0
IMBALANCE_CAP_PCT = 999.0


def compute_imbalance_pct(buy_volume: float, sell_volume: float) -> float:
    """Buy/Sell volume ratio as percentage (100 = balanced)."""
    if sell_volume > 0:
        return buy_volume / sell_volume * 100.0
    if buy_volume > 0:
        return IMBALANCE_CAP_PCT
    return 100.0


def classify_imbalance_tier(
    imbalance_pct: float,
    *,
    strong_pct: float = OFI_IMBALANCE_STRONG_PCT,
    extreme_pct: float = OFI_IMBALANCE_EXTREME_PCT,
) -> str:
    bear_strong = 10000.0 / strong_pct if strong_pct > 0 else 50.0
    bear_extreme = 10000.0 / extreme_pct if extreme_pct > 0 else 33.33
    if imbalance_pct >= extreme_pct:
        return "extreme_bull"
    if imbalance_pct >= strong_pct:
        return "strong_bull"
    if imbalance_pct <= bear_extreme:
        return "extreme_bear"
    if imbalance_pct <= bear_strong:
        return "strong_bear"
    return "neutral"


def compute_book_pressure_pct(bid_volume: float, ask_volume: float) -> float:
    if ask_volume > 0:
        return bid_volume / ask_volume * 100.0
    if bid_volume > 0:
        return IMBALANCE_CAP_PCT
    return 100.0


def classify_book_bias(
    book_pressure_pct: float,
    *,
    strong_pct: float = OFI_BOOK_STRONG_PCT,
) -> str:
    bear_threshold = 10000.0 / strong_pct if strong_pct > 0 else 66.67
    if book_pressure_pct >= strong_pct:
        return "bullish"
    if book_pressure_pct <= bear_threshold:
        return "bearish"
    return "neutral"


def compute_book_pressure_near_mid(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    *,
    tick_size: float,
    tick_range: int,
) -> tuple[float, float, float]:
    """Sum bid/ask size within tick_range of mid; return (bid_vol, ask_vol, pressure_pct)."""
    if not bids or not asks or tick_size <= 0:
        return 0.0, 0.0, 100.0

    best_bid = max(p for p, _ in bids)
    best_ask = min(p for p, _ in asks)
    mid = (best_bid + best_ask) / 2.0
    band = tick_range * tick_size
    bid_floor = mid - band
    ask_ceiling = mid + band

    bid_vol = sum(size for price, size in bids if price >= bid_floor)
    ask_vol = sum(size for price, size in asks if price <= ask_ceiling)
    return bid_vol, ask_vol, compute_book_pressure_pct(bid_vol, ask_vol)


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
    if probability < NEUTRAL_PROB_THRESHOLD:
        return "neutral"
    return direction


def trade_pnl_pct(predicted: str, candle: Candle) -> float:
    if candle.open <= 0:
        return 0.0
    move_pct = (candle.close - candle.open) / candle.open * 100.0
    if predicted == "green":
        return move_pct
    if predicted == "red":
        return -move_pct
    return 0.0


def compute_pnl_stats(records: list) -> dict:
    verified = [r for r in records if r.pnl_pct is not None]
    total_pnl = sum(r.pnl_pct for r in verified)
    wins = sum(1 for r in verified if r.pnl_pct > 0)
    losses = sum(1 for r in verified if r.pnl_pct < 0)
    flats = sum(1 for r in verified if r.pnl_pct == 0)

    max_win_streak = 0
    max_loss_streak = 0
    cur_win = 0
    cur_loss = 0
    for r in verified:
        if r.pnl_pct > 0:
            cur_win += 1
            cur_loss = 0
            max_win_streak = max(max_win_streak, cur_win)
        elif r.pnl_pct < 0:
            cur_loss += 1
            cur_win = 0
            max_loss_streak = max(max_loss_streak, cur_loss)
        else:
            cur_win = 0
            cur_loss = 0

    return {
        "total_pnl_pct": total_pnl,
        "trade_wins": wins,
        "trade_losses": losses,
        "trade_flats": flats,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "trade_total": len(verified),
    }


@dataclass
class OfiSignal:
    direction: str
    probability: float
    early_signal: str
    signal_mode: str
    imbalance_tier: str
    forming_imbalance_tier: str
    book_bias: str
    score_bull: int
    score_bear: int


def compute_ofi_signal(
    *,
    closed_candle: Candle,
    volume_delta: float,
    current_delta: float,
    delta_spike_ratio: float,
    avg_delta_10: float,
    buy_volume: float,
    sell_volume: float,
    forming_buy_volume: float,
    forming_sell_volume: float,
    forming_imbalance_pct: float,
    imbalance_pct: float,
    book_pressure_pct: float,
    book_bias: str,
    delta_velocity: float,
    candle_age_sec: float,
    book_stale: bool = False,
) -> OfiSignal:
    """Score OFI direction from forming delta, imbalance ratio, and book pressure."""
    forming_tier = classify_imbalance_tier(forming_imbalance_pct)
    closed_tier = classify_imbalance_tier(imbalance_pct)

    delta_spike_current = (
        abs(current_delta) / max(avg_delta_10, 1e-9) if avg_delta_10 > 0 else 0.0
    )

    bull_score = 0
    bear_score = 0

    if forming_tier in ("strong_bull", "extreme_bull") or closed_tier in ("strong_bull", "extreme_bull"):
        bull_score += 2 if forming_tier == "extreme_bull" or closed_tier == "extreme_bull" else 1
    if forming_tier in ("strong_bear", "extreme_bear") or closed_tier in ("strong_bear", "extreme_bear"):
        bear_score += 2 if forming_tier == "extreme_bear" or closed_tier == "extreme_bear" else 1

    if current_delta > 0 and delta_spike_current >= OFI_DELTA_SPIKE_MIN:
        bull_score += 1
    if current_delta < 0 and delta_spike_current >= OFI_DELTA_SPIKE_MIN:
        bear_score += 1

    if book_bias == "bullish" and not book_stale:
        bull_score += 1
    if book_bias == "bearish" and not book_stale:
        bear_score += 1

    early_signal = "none"
    if candle_age_sec <= OFI_EARLY_ENTRY_SEC:
        if current_delta > 0 and delta_velocity > 0 and bull_score >= 2:
            early_signal = "green"
        elif current_delta < 0 and delta_velocity < 0 and bear_score >= 2:
            early_signal = "red"

    bias = compute_ofi_bias(closed_candle, volume_delta, delta_spike_ratio)
    if bias == "bullish":
        bull_score += 1
    elif bias == "bearish":
        bear_score += 1

    if bull_score > bear_score:
        direction = "green"
        prob = 52.0 + min(40.0, bull_score * 8.0)
        if early_signal == "green":
            prob = min(92.0, prob + 10.0)
        signal_mode = "early_delta" if early_signal == "green" else "forming_flow"
    elif bear_score > bull_score:
        direction = "red"
        prob = 52.0 + min(40.0, bear_score * 8.0)
        if early_signal == "red":
            prob = min(92.0, prob + 10.0)
        signal_mode = "early_delta" if early_signal == "red" else "forming_flow"
    else:
        total_vol = forming_buy_volume + forming_sell_volume
        if total_vol > 0 and forming_buy_volume >= forming_sell_volume:
            direction = "green"
        else:
            direction = "red"
        prob = 50.0 + min(8.0, delta_spike_ratio * 2.0)
        signal_mode = "neutral_fallback"

    tier = forming_tier if forming_tier != "neutral" else closed_tier
    return OfiSignal(
        direction=direction,
        probability=min(92.0, prob),
        early_signal=early_signal,
        signal_mode=signal_mode,
        imbalance_tier=tier,
        forming_imbalance_tier=forming_tier,
        book_bias=book_bias if not book_stale else "stale",
        score_bull=bull_score,
        score_bear=bear_score,
    )


def compute_next_candle_prediction(
    closed_candle: Candle,
    *,
    volume_delta: float,
    current_delta: float,
    delta_spike_ratio: float,
    avg_delta_10: float,
    buy_volume: float,
    sell_volume: float,
    forming_buy_volume: float = 0.0,
    forming_sell_volume: float = 0.0,
    forming_imbalance_pct: float = 100.0,
    imbalance_pct: float = 100.0,
    book_pressure_pct: float = 100.0,
    book_bias: str = "neutral",
    delta_velocity: float = 0.0,
    candle_age_sec: float = 60.0,
    book_stale: bool = False,
) -> tuple[str, float]:
    signal = compute_ofi_signal(
        closed_candle=closed_candle,
        volume_delta=volume_delta,
        current_delta=current_delta,
        delta_spike_ratio=delta_spike_ratio,
        avg_delta_10=avg_delta_10,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        forming_buy_volume=forming_buy_volume or current_delta,
        forming_sell_volume=forming_sell_volume,
        forming_imbalance_pct=forming_imbalance_pct,
        imbalance_pct=imbalance_pct,
        book_pressure_pct=book_pressure_pct,
        book_bias=book_bias,
        delta_velocity=delta_velocity,
        candle_age_sec=candle_age_sec,
        book_stale=book_stale,
    )
    return signal.direction, signal.probability
