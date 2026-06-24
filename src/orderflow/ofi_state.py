from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from src.bitget_client import Candle
from src.config import OFI_SYMBOL
from src.orderflow.aggregator import OrderFlowSnapshot, close_bucket, get_snapshot
from src.orderflow.metrics import compute_next_candle_prediction, prediction_label
from src.web.time_format import format_vn_from_ms, format_vn_now


def _predict_from_snapshot(snapshot: OrderFlowSnapshot, candle: Candle) -> tuple[str, float]:
    return compute_next_candle_prediction(
        candle,
        volume_delta=snapshot.volume_delta,
        current_delta=snapshot.current_delta,
        delta_spike_ratio=snapshot.delta_spike_ratio,
        avg_delta_10=snapshot.avg_delta_10,
        buy_volume=snapshot.buy_volume,
        sell_volume=snapshot.sell_volume,
    )


@dataclass
class PredictionRecord:
    period_ms: int
    predicted: str
    probability: float
    actual: str | None = None
    correct: bool | None = None
    time_utc: str = ""  # display time (VN)


@dataclass
class OfiLiveState:
    symbol: str = OFI_SYMBOL
    last_updated: str = ""
    interval: str = "1m"
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    volume_delta: float = 0.0
    current_delta: float = 0.0
    delta_spike: float = 0.0
    ofi_bias: str = "neutral"
    last_candle_color: str = ""
    last_close: float = 0.0
    forming_open: float = 0.0
    forming_high: float = 0.0
    forming_low: float = 0.0
    forming_close: float = 0.0
    prediction: str = "green"
    prediction_label: str = "neutral"
    prediction_probability: float = 50.0
    session_total: int = 0
    session_correct: int = 0
    session_accuracy: float = 0.0
    history_candles: int = 0
    recent_predictions: list[dict] = field(default_factory=list)


_lock = Lock()
_live = OfiLiveState()
_pending: PredictionRecord | None = None
_history: deque[PredictionRecord] = deque(maxlen=240)
_last_candle_ts: int | None = None
_current_candle: Candle | None = None
_live_session: bool = False


def reset_ofi_session() -> None:
    global _pending, _last_candle_ts, _current_candle, _live, _live_session
    with _lock:
        _pending = None
        _history.clear()
        _last_candle_ts = None
        _current_candle = None
        _live = OfiLiveState()
        _live_session = False


def _format_period_ms(ts_ms: int) -> str:
    return format_vn_from_ms(ts_ms)


def _display_now() -> str:
    return format_vn_now()


def _candle_color(candle: Candle) -> str:
    if candle.close > candle.open:
        return "green"
    if candle.close < candle.open:
        return "red"
    return "neutral"


def _rebuild_live(
    snapshot: OrderFlowSnapshot,
    candle: Candle | None,
    prediction: str,
    probability: float,
) -> None:
    global _live
    label = prediction_label(prediction, probability)
    recent = [
        {
            "time": r.time_utc,
            "predicted": r.predicted,
            "probability": r.probability,
            "actual": r.actual,
            "correct": r.correct,
        }
        for r in reversed(list(_history)[-20:])
    ]
    verified = [r for r in _history if r.correct is not None]
    total = len(verified)
    correct = sum(1 for r in verified if r.correct)
    accuracy = (correct / total * 100.0) if total > 0 else 0.0

    _live = OfiLiveState(
        symbol=OFI_SYMBOL,
        last_updated=_display_now(),
        interval="1m",
        buy_volume=snapshot.buy_volume,
        sell_volume=snapshot.sell_volume,
        volume_delta=snapshot.volume_delta,
        current_delta=snapshot.current_delta,
        delta_spike=snapshot.delta_spike_ratio,
        ofi_bias=snapshot.ofi_bias,
        last_candle_color=_candle_color(candle) if candle else "",
        last_close=candle.close if candle else 0.0,
        forming_open=candle.open if candle else 0.0,
        forming_high=candle.high if candle else 0.0,
        forming_low=candle.low if candle else 0.0,
        forming_close=candle.close if candle else 0.0,
        prediction=prediction,
        prediction_label=label,
        prediction_probability=probability,
        session_total=total,
        session_correct=correct,
        session_accuracy=accuracy,
        history_candles=snapshot.history_candles,
        recent_predictions=recent,
    )


def on_candle_snapshot(candle: Candle) -> None:
    """Init from WS snapshot — do not replay history or verify predictions."""
    global _last_candle_ts, _current_candle, _live_session

    with _lock:
        _last_candle_ts = candle.timestamp
        _current_candle = candle
        _live_session = True
        snap = get_snapshot(OFI_SYMBOL, candle=candle)
        pred, prob = _predict_from_snapshot(snap, candle)
        _rebuild_live(snap, candle, pred, prob)


def on_candle_update(candle: Candle) -> None:
    global _pending, _last_candle_ts, _current_candle, _live_session

    with _lock:
        if not _live_session:
            _last_candle_ts = candle.timestamp
            _current_candle = candle
            _live_session = True
            snap = get_snapshot(OFI_SYMBOL, candle=candle)
            pred, prob = _predict_from_snapshot(snap, candle)
            _rebuild_live(snap, candle, pred, prob)
            return

        if _last_candle_ts is not None and candle.timestamp < _last_candle_ts:
            return

        if _last_candle_ts is not None and candle.timestamp > _last_candle_ts:
            closed = _current_candle
            if closed is not None:
                close_bucket(OFI_SYMBOL, closed.timestamp)
                snap = get_snapshot(OFI_SYMBOL, candle=closed)

                if _pending is not None:
                    actual = _candle_color(closed)
                    _pending.actual = actual
                    if actual != "neutral":
                        _pending.correct = _pending.predicted == actual
                    _history.append(_pending)
                    _pending = None

                predicted, prob = _predict_from_snapshot(snap, closed)
                _pending = PredictionRecord(
                    period_ms=candle.timestamp,
                    predicted=predicted,
                    probability=prob,
                    time_utc=_format_period_ms(candle.timestamp),
                )
                _rebuild_live(snap, closed, predicted, prob)
            _last_candle_ts = candle.timestamp
            _current_candle = candle
            return

        if _last_candle_ts == candle.timestamp:
            _current_candle = candle
            snap = get_snapshot(OFI_SYMBOL, candle=candle)
            pred = _pending.predicted if _pending else "green"
            prob = _pending.probability if _pending else 50.0
            if _pending is None:
                pred, prob = _predict_from_snapshot(snap, candle)
            _rebuild_live(snap, candle, pred, prob)
            return

        _last_candle_ts = candle.timestamp
        _current_candle = candle
        snap = get_snapshot(OFI_SYMBOL, candle=candle)
        pred = _pending.predicted if _pending else "green"
        prob = _pending.probability if _pending else 50.0
        if _pending is None:
            pred, prob = _predict_from_snapshot(snap, candle)
        _rebuild_live(snap, candle, pred, prob)


def refresh_live_from_trades() -> None:
    with _lock:
        candle = _current_candle
        snap = get_snapshot(OFI_SYMBOL, candle=candle)
        pred = _live.prediction or "green"
        prob = _live.prediction_probability
        if _pending is not None:
            pred = _pending.predicted
            prob = _pending.probability
        elif candle is not None:
            pred, prob = _predict_from_snapshot(snap, candle)
        _rebuild_live(snap, candle, pred, prob)


def get_live_state() -> OfiLiveState:
    with _lock:
        return OfiLiveState(
            symbol=_live.symbol,
            last_updated=_live.last_updated,
            interval=_live.interval,
            buy_volume=_live.buy_volume,
            sell_volume=_live.sell_volume,
            volume_delta=_live.volume_delta,
            current_delta=_live.current_delta,
            delta_spike=_live.delta_spike,
            ofi_bias=_live.ofi_bias,
            last_candle_color=_live.last_candle_color,
            last_close=_live.last_close,
            forming_open=_live.forming_open,
            forming_high=_live.forming_high,
            forming_low=_live.forming_low,
            forming_close=_live.forming_close,
            prediction=_live.prediction,
            prediction_label=_live.prediction_label,
            prediction_probability=_live.prediction_probability,
            session_total=_live.session_total,
            session_correct=_live.session_correct,
            session_accuracy=_live.session_accuracy,
            history_candles=_live.history_candles,
            recent_predictions=list(_live.recent_predictions),
        )


def live_state_to_dict() -> dict:
    state = get_live_state()
    return {
        "symbol": state.symbol,
        "last_updated": state.last_updated,
        "interval": state.interval,
        "buy_volume": state.buy_volume,
        "sell_volume": state.sell_volume,
        "volume_delta": state.volume_delta,
        "current_delta": state.current_delta,
        "delta_spike": state.delta_spike,
        "ofi_bias": state.ofi_bias,
        "last_candle_color": state.last_candle_color,
        "last_close": state.last_close,
        "forming": {
            "open": state.forming_open,
            "high": state.forming_high,
            "low": state.forming_low,
            "close": state.forming_close,
        },
        "prediction": state.prediction,
        "prediction_label": state.prediction_label,
        "prediction_probability": state.prediction_probability,
        "session_total": state.session_total,
        "session_correct": state.session_correct,
        "session_accuracy": state.session_accuracy,
        "history_candles": state.history_candles,
        "recent_predictions": state.recent_predictions,
    }
