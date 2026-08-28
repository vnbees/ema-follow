from src.exchange.types import Candle
from src.regime_detect import K_SW, compute_regime_snapshot, is_sideway_15m

def _candles(n: int, price: float = 100.0, spread: float = 0.0) -> list[Candle]:
    out: list[Candle] = []
    for i in range(n):
        lo = price - spread
        hi = price + spread
        out.append(
            Candle(
                timestamp=i * 900_000,
                open=price,
                high=hi,
                low=lo,
                close=price,
                volume=1.0,
            )
        )
    return out


def _tight_then_expand(n_tight: int, n_expand: int, tight: float, jump: float) -> list[Candle]:
    tight_bars = _candles(n_tight, 100.0, tight)
    px = 100.0
    out = list(tight_bars)
    for i in range(n_expand):
        px += jump
        out.append(
            Candle(
                timestamp=(n_tight + i) * 900_000,
                open=px - jump,
                high=px + 0.5,
                low=px - 0.5,
                close=px,
                volume=1.0,
            )
        )
    return out


def test_not_ready_short_history():
    snap = compute_regime_snapshot(_candles(100))
    assert snap.ready is False
    assert snap.regime == "UNKNOWN"


def test_sideway_compressed_vs_baseline():
    # Volatile 30d history, then tight flat tail → SIDEWAY vs own baseline
    volatile = _tight_then_expand(3100, 200, tight=2.0, jump=0.0)
    start_ts = (3100 + 200) * 900_000
    flat_tail = [
        Candle(timestamp=start_ts + i * 900_000, open=100.0, high=100.02, low=99.98, close=100.0, volume=1.0)
        for i in range(150)
    ]
    bars = volatile + flat_tail
    snap = compute_regime_snapshot(bars)
    assert snap.ready is True
    assert snap.regime == "SIDEWAY"
    assert snap.rng_ratio <= K_SW
    assert is_sideway_15m(bars) is True


def test_trend_expanded_vs_baseline():
    bars = _tight_then_expand(3200, 120, tight=0.05, jump=0.15)
    snap = compute_regime_snapshot(bars)
    assert snap.ready is True
    assert snap.regime == "TREND"
    assert snap.rng_ratio >= 1.05
    assert snap.direction == 1
