import unittest

from src.bitget_client import Candle
from src.rsi import RsiSnapshot, compute_rsi_series, get_rsi_snapshot


def _make_candles(closes: list[float], interval_ms: int = 300_000) -> list[Candle]:
    candles: list[Candle] = []
    for i, close in enumerate(closes):
        candles.append(
            Candle(
                timestamp=i * interval_ms,
                open=close - 0.1,
                high=close + 0.2,
                low=close - 0.2,
                close=close,
                volume=10,
            )
        )
    return candles


class TestRsiIndicator(unittest.TestCase):
    def test_series_length(self):
        closes = [100.0 + i * 0.1 for i in range(50)]
        series = compute_rsi_series(_make_candles(closes))
        self.assertEqual(len(series), 50)
        self.assertIsNotNone(series[-1])

    def test_snapshot_ready(self):
        closes = [100.0 + i * 0.1 for i in range(50)]
        snap = get_rsi_snapshot(_make_candles(closes))
        self.assertTrue(snap.ready)
        self.assertGreater(snap.rsi, 0)

    def test_insufficient_candles(self):
        closes = [100.0] * 5
        snap = get_rsi_snapshot(_make_candles(closes))
        self.assertFalse(snap.ready)

    def test_cross_flags_on_snapshot(self):
        snap = RsiSnapshot(ready=True, rsi=26.0, prev_rsi=24.0, cross_up_25=True)
        self.assertTrue(snap.cross_up_25)
        self.assertFalse(snap.cross_down_75)


if __name__ == "__main__":
    unittest.main()
