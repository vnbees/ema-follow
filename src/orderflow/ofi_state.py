from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from src.bitget_client import Candle
from src.config import OFI_HISTORY_CANDLES, OFI_SYMBOL
from src.orderflow.aggregator import OrderFlowSnapshot, close_bucket, get_snapshot
from src.orderflow.metrics import (
    compute_ofi_signal,
    compute_pnl_stats,
    prediction_label,
    trade_pnl_pct,
)
from src.orderflow.orderbook import get_book_pressure
from src.web.time_format import format_vn_from_ms, format_vn_now


def _predict_from_snapshot(snapshot: OrderFlowSnapshot, candle: Candle) -> tuple[str, float, object]:
    book = get_book_pressure()
    signal = compute_ofi_signal(
        closed_candle=candle,
        volume_delta=snapshot.volume_delta,
        current_delta=snapshot.current_delta,
        delta_spike_ratio=snapshot.delta_spike_ratio,
        avg_delta_10=snapshot.avg_delta_10,
        buy_volume=snapshot.buy_volume,
        sell_volume=snapshot.sell_volume,
        forming_buy_volume=snapshot.forming_buy_volume,
        forming_sell_volume=snapshot.forming_sell_volume,
        forming_imbalance_pct=snapshot.forming_imbalance_pct,
        imbalance_pct=snapshot.imbalance_pct,
        book_pressure_pct=book.book_pressure_pct,
        book_bias=book.book_bias,
        delta_velocity=snapshot.delta_velocity,
        candle_age_sec=snapshot.candle_age_sec,
        book_stale=book.stale,
    )
    return signal.direction, signal.probability, signal


@dataclass
class PredictionRecord:
    period_ms: int
    predicted: str
    probability: float
    actual: str | None = None
    correct: bool | None = None
    pnl_pct: float | None = None
    time_utc: str = ""


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
    total_pnl_pct: float = 0.0
    trade_wins: int = 0
    trade_losses: int = 0
    trade_flats: int = 0
    max_win_streak: int = 0
    max_loss_streak: int = 0
    trade_total: int = 0
    history_candles: int = 0
    stats_ready: bool = False
    recent_predictions: list[dict] = field(default_factory=list)
    imbalance_pct: float = 100.0
    forming_imbalance_pct: float = 100.0
    imbalance_tier: str = "neutral"
    forming_imbalance_tier: str = "neutral"
    book_bid_volume: float = 0.0
    book_ask_volume: float = 0.0
    book_pressure_pct: float = 100.0
    book_bias: str = "neutral"
    book_stale: bool = True
    candle_age_sec: float = 0.0
    delta_velocity: float = 0.0
    early_signal: str = "none"
    signal_mode: str = ""
    forming_buy_volume: float = 0.0
    forming_sell_volume: float = 0.0


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
    signal=None,
) -> None:
    global _live
    label = prediction_label(prediction, probability)
    ready = snapshot.history_candles >= OFI_HISTORY_CANDLES
    book = get_book_pressure()

    if signal is None and candle is not None:
        _, _, signal = _predict_from_snapshot(snapshot, candle)
    elif signal is None:
        from src.orderflow.metrics import OfiSignal
        signal = OfiSignal(
            direction=prediction,
            probability=probability,
            early_signal="none",
            signal_mode="",
            imbalance_tier="neutral",
            forming_imbalance_tier="neutral",
            book_bias=book.book_bias,
            score_bull=0,
            score_bear=0,
        )

    if ready:
        pnl_stats = compute_pnl_stats(list(_history))
        recent = [
            {
                "time": r.time_utc,
                "predicted": r.predicted,
                "probability": r.probability,
                "actual": r.actual,
                "correct": r.correct,
                "pnl_pct": r.pnl_pct,
            }
            for r in reversed(list(_history)[-20:])
        ]
        verified = [r for r in _history if r.correct is not None]
        total = len(verified)
        correct = sum(1 for r in verified if r.correct)
        accuracy = (correct / total * 100.0) if total > 0 else 0.0
    else:
        pnl_stats = {
            "total_pnl_pct": 0.0,
            "trade_wins": 0,
            "trade_losses": 0,
            "trade_flats": 0,
            "max_win_streak": 0,
            "max_loss_streak": 0,
            "trade_total": 0,
        }
        recent = []
        total = 0
        correct = 0
        accuracy = 0.0

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
        total_pnl_pct=pnl_stats["total_pnl_pct"],
        trade_wins=pnl_stats["trade_wins"],
        trade_losses=pnl_stats["trade_losses"],
        trade_flats=pnl_stats["trade_flats"],
        max_win_streak=pnl_stats["max_win_streak"],
        max_loss_streak=pnl_stats["max_loss_streak"],
        trade_total=pnl_stats["trade_total"],
        history_candles=snapshot.history_candles,
        stats_ready=ready,
        recent_predictions=recent,
        imbalance_pct=snapshot.imbalance_pct,
        forming_imbalance_pct=snapshot.forming_imbalance_pct,
        imbalance_tier=signal.imbalance_tier,
        forming_imbalance_tier=signal.forming_imbalance_tier,
        book_bid_volume=book.bid_volume,
        book_ask_volume=book.ask_volume,
        book_pressure_pct=book.book_pressure_pct,
        book_bias=book.book_bias if not book.stale else "stale",
        book_stale=book.stale,
        candle_age_sec=snapshot.candle_age_sec,
        delta_velocity=snapshot.delta_velocity,
        early_signal=signal.early_signal,
        signal_mode=signal.signal_mode,
        forming_buy_volume=snapshot.forming_buy_volume,
        forming_sell_volume=snapshot.forming_sell_volume,
    )


def on_candle_snapshot(candle: Candle) -> None:
    global _last_candle_ts, _current_candle, _live_session

    with _lock:
        _last_candle_ts = candle.timestamp
        _current_candle = candle
        _live_session = True
        snap = get_snapshot(OFI_SYMBOL, candle=candle)
        pred, prob, signal = _predict_from_snapshot(snap, candle)
        _rebuild_live(snap, candle, pred, prob, signal)


def on_candle_update(candle: Candle) -> None:
    global _pending, _last_candle_ts, _current_candle, _live_session

    with _lock:
        if not _live_session:
            _last_candle_ts = candle.timestamp
            _current_candle = candle
            _live_session = True
            snap = get_snapshot(OFI_SYMBOL, candle=candle)
            pred, prob, signal = _predict_from_snapshot(snap, candle)
            _rebuild_live(snap, candle, pred, prob, signal)
            return

        if _last_candle_ts is not None and candle.timestamp < _last_candle_ts:
            return

        if _last_candle_ts is not None and candle.timestamp > _last_candle_ts:
            closed = _current_candle
            if closed is not None:
                close_bucket(OFI_SYMBOL, closed.timestamp)
                snap = get_snapshot(OFI_SYMBOL, candle=closed)
                predicted, prob, signal = _predict_from_snapshot(snap, closed)

                if snap.history_candles >= OFI_HISTORY_CANDLES:
                    if _pending is not None:
                        actual = _candle_color(closed)
                        _pending.actual = actual
                        _pending.pnl_pct = trade_pnl_pct(_pending.predicted, closed)
                        if actual != "neutral":
                            _pending.correct = _pending.predicted == actual
                        elif _pending.pnl_pct is not None:
                            _pending.correct = _pending.pnl_pct >= 0
                        _history.append(_pending)
                        _pending = None

                    _pending = PredictionRecord(
                        period_ms=candle.timestamp,
                        predicted=predicted,
                        probability=prob,
                        time_utc=_format_period_ms(candle.timestamp),
                    )
                else:
                    _pending = None
                _rebuild_live(snap, closed, predicted, prob, signal)
            _last_candle_ts = candle.timestamp
            _current_candle = candle
            return

        if _last_candle_ts == candle.timestamp:
            _current_candle = candle
            snap = get_snapshot(OFI_SYMBOL, candle=candle)
            if _pending is not None:
                pred, prob = _pending.predicted, _pending.probability
                _, _, signal = _predict_from_snapshot(snap, candle)
            else:
                pred, prob, signal = _predict_from_snapshot(snap, candle)
            _rebuild_live(snap, candle, pred, prob, signal)
            return

        _last_candle_ts = candle.timestamp
        _current_candle = candle
        snap = get_snapshot(OFI_SYMBOL, candle=candle)
        if _pending is not None:
            pred, prob = _pending.predicted, _pending.probability
            _, _, signal = _predict_from_snapshot(snap, candle)
        else:
            pred, prob, signal = _predict_from_snapshot(snap, candle)
        _rebuild_live(snap, candle, pred, prob, signal)


def refresh_live_from_trades() -> None:
    with _lock:
        candle = _current_candle
        snap = get_snapshot(OFI_SYMBOL, candle=candle)
        if _pending is not None:
            pred, prob = _pending.predicted, _pending.probability
            if candle is not None:
                _, _, signal = _predict_from_snapshot(snap, candle)
            else:
                signal = None
        elif candle is not None:
            pred, prob, signal = _predict_from_snapshot(snap, candle)
        else:
            pred = _live.prediction or "green"
            prob = _live.prediction_probability
            signal = None
        _rebuild_live(snap, candle, pred, prob, signal)


def get_live_state() -> OfiLiveState:
    with _lock:
        return OfiLiveState(**{f.name: getattr(_live, f.name) for f in OfiLiveState.__dataclass_fields__.values()})


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
        "total_pnl_pct": state.total_pnl_pct,
        "trade_wins": state.trade_wins,
        "trade_losses": state.trade_losses,
        "trade_flats": state.trade_flats,
        "max_win_streak": state.max_win_streak,
        "max_loss_streak": state.max_loss_streak,
        "trade_total": state.trade_total,
        "history_candles": state.history_candles,
        "stats_ready": state.stats_ready,
        "warmup_candles": OFI_HISTORY_CANDLES,
        "recent_predictions": state.recent_predictions,
        "imbalance_pct": state.imbalance_pct,
        "forming_imbalance_pct": state.forming_imbalance_pct,
        "imbalance_tier": state.imbalance_tier,
        "forming_imbalance_tier": state.forming_imbalance_tier,
        "book_bid_volume": state.book_bid_volume,
        "book_ask_volume": state.book_ask_volume,
        "book_pressure_pct": state.book_pressure_pct,
        "book_bias": state.book_bias,
        "book_stale": state.book_stale,
        "candle_age_sec": state.candle_age_sec,
        "delta_velocity": state.delta_velocity,
        "early_signal": state.early_signal,
        "signal_mode": state.signal_mode,
        "forming_buy_volume": state.forming_buy_volume,
        "forming_sell_volume": state.forming_sell_volume,
    }
