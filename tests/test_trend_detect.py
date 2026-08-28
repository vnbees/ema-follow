from src.exchange.types import Candle
from src.trend_detect import compute_trend_snapshot, is_trend_15m


def _flat_candles(n: int, price: float = 100.0) -> list[Candle]:
    return [
        Candle(timestamp=i * 900_000, open=price, high=price, low=price, close=price, volume=1.0)
        for i in range(n)
    ]


def _ramp_candles(n: int, start: float, step: float) -> list[Candle]:
    out: list[Candle] = []
    px = start
    for i in range(n):
        out.append(Candle(timestamp=i * 900_000, open=px, high=px, low=px, close=px, volume=1.0))
        px += step
    return out


def test_not_ready_with_few_bars():
    snap = compute_trend_snapshot(_flat_candles(50))
    assert snap.ready is False
    assert snap.is_trend is False


def test_flat_market_not_trend():
    snap = compute_trend_snapshot(_flat_candles(200), profile="best")
    assert snap.ready is True
    assert snap.is_trend is False
    assert snap.roc96_pct == 0.0


def test_strong_ramp_detects_trend_best():
    # +4% over last 96 bars → ROC96 ~4%
    candles = _flat_candles(104, 100.0)
    ramp = _ramp_candles(96, 100.0, 100.0 * 0.04 / 95)
    candles.extend(ramp[1:])
    snap = compute_trend_snapshot(candles, profile="best")
    assert snap.ready is True
    assert snap.is_trend is True
    assert snap.direction == 1
    assert abs(snap.roc96_pct) >= 3.5


def test_pool_requires_ema_stack():
    candles = _flat_candles(104, 100.0)
    ramp = _ramp_candles(96, 100.0, 100.0 * 0.03 / 95)
    candles.extend(ramp[1:])
    snap = compute_trend_snapshot(candles, profile="pool")
    assert snap.ready is True
    assert snap.is_trend is True
    assert is_trend_15m(candles, profile="pool") is True
