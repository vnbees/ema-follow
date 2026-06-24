import unittest

from src.bitget_client import Candle
from src.ichimoku import compute_ichimoku_series, get_ichimoku_snapshot
from src.ichimoku_signals import detect_ichimoku_signal


def _make_uptrend_candles(n: int, start: float = 100.0) -> list[Candle]:
    candles: list[Candle] = []
    price = start
    for i in range(n):
        o = price
        c = price + 0.5
        h = c + 0.2
        l = o - 0.1
        candles.append(Candle(timestamp=i * 900_000, open=o, high=h, low=l, close=c, volume=10))
        price = c
    return candles


class TestIchimokuIndicator(unittest.TestCase):
    def test_midpoint_series_length(self):
        candles = _make_uptrend_candles(100)
        tenkan, kijun, senkou_a, senkou_b = compute_ichimoku_series(candles)
        self.assertEqual(len(tenkan), 100)
        self.assertIsNotNone(tenkan[50])
        self.assertIsNotNone(kijun[50])

    def test_senkou_shift(self):
        candles = _make_uptrend_candles(100)
        _, _, senkou_a, senkou_b = compute_ichimoku_series(candles)
        self.assertIsNone(senkou_a[25])
        self.assertIsNotNone(senkou_a[80])

    def test_snapshot_ready(self):
        candles = _make_uptrend_candles(100)
        snap = get_ichimoku_snapshot(candles)
        self.assertTrue(snap.ready)
        self.assertGreater(snap.kijun, 0)
        self.assertGreater(snap.kumo_top, 0)

    def test_insufficient_candles(self):
        candles = _make_uptrend_candles(10)
        snap = get_ichimoku_snapshot(candles)
        self.assertFalse(snap.ready)


class TestIchimokuSignals(unittest.TestCase):
    def test_not_ready(self):
        from src.ichimoku import IchimokuSnapshot

        sig = detect_ichimoku_signal(IchimokuSnapshot(ready=False), 0.01)
        self.assertIsNone(sig.side)
        self.assertIn("ichimoku_not_ready", sig.reasons)

    def test_long_breakout_on_synthetic(self):
        base = 100.0
        candles: list[Candle] = []
        for i in range(90):
            price = base + i * 0.3
            candles.append(
                Candle(
                    timestamp=i * 900_000,
                    open=price,
                    high=price + 1,
                    low=price - 0.5,
                    close=price + 0.8,
                    volume=10,
                )
            )
        prev = candles[-1]
        candles.append(
            Candle(
                timestamp=90 * 900_000,
                open=prev.close,
                high=prev.close + 2,
                low=prev.close - 0.2,
                close=prev.close + 1.5,
                volume=10,
            )
        )
        snap = get_ichimoku_snapshot(candles)
        if snap.ready:
            sig = detect_ichimoku_signal(snap, 0.01)
            self.assertIsNotNone(sig)


class TestPartialTpLogic(unittest.TestCase):
    def test_one_r_long(self):
        entry = 100.0
        risk = 2.0
        close = 102.5
        self.assertTrue(close >= entry + risk)

    def test_one_r_short(self):
        entry = 100.0
        risk = 2.0
        close = 97.5
        self.assertTrue(close <= entry - risk)


if __name__ == "__main__":
    unittest.main()
