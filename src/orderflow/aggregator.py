from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
import time

from src.bitget_client import Candle
from src.config import OFI_HISTORY_CANDLES, OFI_INTERVAL_MINUTES
from src.orderflow.metrics import compute_imbalance_pct, compute_ofi_bias


@dataclass
class CandleBucket:
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    trade_count: int = 0
    period_start_ms: int = 0

    @property
    def volume_delta(self) -> float:
        return self.buy_volume - self.sell_volume


@dataclass
class OrderFlowSnapshot:
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    volume_delta: float = 0.0
    current_delta: float = 0.0
    avg_delta_10: float = 0.0
    delta_spike_ratio: float = 0.0
    ofi_bias: str = "neutral"
    history_candles: int = 0
    forming_buy_volume: float = 0.0
    forming_sell_volume: float = 0.0
    forming_imbalance_pct: float = 100.0
    imbalance_pct: float = 100.0
    delta_velocity: float = 0.0
    candle_age_sec: float = 0.0
    current_period_ms: int = 0


@dataclass
class _SymbolState:
    current: CandleBucket = field(default_factory=CandleBucket)
    closed: deque[CandleBucket] = field(default_factory=lambda: deque(maxlen=OFI_HISTORY_CANDLES))
    last_closed_period_ms: int | None = None
    delta_samples: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=30))


_lock = Lock()
_states: dict[str, _SymbolState] = {}


def _period_start_ms(ts_ms: int) -> int:
    interval_ms = OFI_INTERVAL_MINUTES * 60 * 1000
    return (ts_ms // interval_ms) * interval_ms


def _get_state(symbol: str) -> _SymbolState:
    symbol = symbol.upper()
    if symbol not in _states:
        _states[symbol] = _SymbolState()
    return _states[symbol]


def _normalize_side(side: str) -> str:
    return side.lower().strip()


def _record_delta_sample(state: _SymbolState, current_delta: float) -> float:
    now = time.time()
    state.delta_samples.append((now, current_delta))
    cutoff = now - 1.0
    past_delta = current_delta
    for ts, delta in state.delta_samples:
        if ts <= cutoff:
            past_delta = delta
    return current_delta - past_delta


def on_trade(symbol: str, side: str, size: float, ts_ms: int) -> None:
    symbol = symbol.upper()
    trade_period = _period_start_ms(ts_ms)
    normalized = _normalize_side(side)

    with _lock:
        state = _get_state(symbol)
        bucket = state.current

        if bucket.period_start_ms == 0:
            bucket.period_start_ms = trade_period
        elif trade_period > bucket.period_start_ms:
            state.closed.append(
                CandleBucket(
                    buy_volume=bucket.buy_volume,
                    sell_volume=bucket.sell_volume,
                    trade_count=bucket.trade_count,
                    period_start_ms=bucket.period_start_ms,
                )
            )
            bucket.buy_volume = 0.0
            bucket.sell_volume = 0.0
            bucket.trade_count = 0
            bucket.period_start_ms = trade_period
        elif trade_period < bucket.period_start_ms:
            return

        if normalized == "buy":
            bucket.buy_volume += size
        elif normalized == "sell":
            bucket.sell_volume += size
        else:
            return
        bucket.trade_count += 1


def close_bucket(symbol: str, closed_candle_ts_ms: int) -> None:
    symbol = symbol.upper()
    target_period = _period_start_ms(closed_candle_ts_ms)
    next_period = target_period + OFI_INTERVAL_MINUTES * 60 * 1000

    with _lock:
        state = _get_state(symbol)
        if state.last_closed_period_ms == target_period:
            return

        bucket = state.current
        if bucket.period_start_ms == target_period:
            if bucket.trade_count > 0:
                state.closed.append(
                    CandleBucket(
                        buy_volume=bucket.buy_volume,
                        sell_volume=bucket.sell_volume,
                        trade_count=bucket.trade_count,
                        period_start_ms=bucket.period_start_ms,
                    )
                )
            state.current = CandleBucket(period_start_ms=next_period)
        elif bucket.period_start_ms > 0 and bucket.period_start_ms < target_period:
            if bucket.trade_count > 0:
                state.closed.append(
                    CandleBucket(
                        buy_volume=bucket.buy_volume,
                        sell_volume=bucket.sell_volume,
                        trade_count=bucket.trade_count,
                        period_start_ms=bucket.period_start_ms,
                    )
                )
            state.current = CandleBucket(period_start_ms=next_period)

        state.last_closed_period_ms = target_period


def get_snapshot(symbol: str, candle: Candle | None = None) -> OrderFlowSnapshot:
    symbol = symbol.upper()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    with _lock:
        state = _get_state(symbol)
        last_closed = state.closed[-1] if state.closed else None
        current = state.current

        buy_volume = last_closed.buy_volume if last_closed else 0.0
        sell_volume = last_closed.sell_volume if last_closed else 0.0
        volume_delta = last_closed.volume_delta if last_closed else 0.0
        current_delta = current.volume_delta
        forming_buy = current.buy_volume
        forming_sell = current.sell_volume

        deltas = [abs(b.volume_delta) for b in state.closed if b.trade_count > 0]
        history_candles = len(deltas)
        avg_delta_10 = sum(deltas) / len(deltas) if deltas else 0.0

        if avg_delta_10 > 0 and last_closed is not None:
            delta_spike_ratio = abs(last_closed.volume_delta) / avg_delta_10
        else:
            delta_spike_ratio = 0.0

        delta_velocity = _record_delta_sample(state, current_delta)
        period_ms = current.period_start_ms or _period_start_ms(now_ms)
        candle_age_sec = max(0.0, (now_ms - period_ms) / 1000.0)

    imbalance_pct = compute_imbalance_pct(buy_volume, sell_volume)
    forming_imbalance_pct = compute_imbalance_pct(forming_buy, forming_sell)

    ofi_bias = "neutral"
    if candle is not None and last_closed is not None:
        ofi_bias = compute_ofi_bias(candle, volume_delta, delta_spike_ratio)
    elif candle is not None:
        ofi_bias = compute_ofi_bias(candle, current_delta, delta_spike_ratio)

    return OrderFlowSnapshot(
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        volume_delta=volume_delta,
        current_delta=current_delta,
        avg_delta_10=avg_delta_10,
        delta_spike_ratio=delta_spike_ratio,
        ofi_bias=ofi_bias,
        history_candles=history_candles,
        forming_buy_volume=forming_buy,
        forming_sell_volume=forming_sell,
        forming_imbalance_pct=forming_imbalance_pct,
        imbalance_pct=imbalance_pct,
        delta_velocity=delta_velocity,
        candle_age_sec=candle_age_sec,
        current_period_ms=period_ms,
    )
