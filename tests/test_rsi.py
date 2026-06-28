import unittest

from src.bitget_client import Candle
from src.rsi import RsiSnapshot, compute_rsi_series, get_rsi_snapshot
from src.rsi_signals import (
    detect_entry_signal,
    detect_pair_event,
    price_move_pct,
    should_take_profit,
)


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


class TestPairSignals(unittest.TestCase):
    def test_not_ready(self):
        self.assertIsNone(detect_pair_event(RsiSnapshot(ready=False)))
        sig = detect_entry_signal(RsiSnapshot(ready=False))
        self.assertIsNone(sig.side)
        self.assertIn("no_rsi_cross", sig.reasons)

    def test_pair_cross_up_25(self):
        snap = RsiSnapshot(ready=True, rsi=26.0, prev_rsi=24.0, cross_up_25=True)
        sig = detect_pair_event(snap)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, "pair")
        self.assertEqual(sig.entry_trigger, "rsi_cross_25")

    def test_pair_cross_down_75(self):
        snap = RsiSnapshot(ready=True, rsi=74.0, prev_rsi=76.0, cross_down_75=True)
        sig = detect_pair_event(snap)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, "pair")
        self.assertEqual(sig.entry_trigger, "rsi_cross_75")

    def test_no_cross(self):
        snap = RsiSnapshot(ready=True, rsi=50.0, prev_rsi=49.0)
        self.assertIsNone(detect_pair_event(snap))
        sig = detect_entry_signal(snap)
        self.assertIsNone(sig.side)
        self.assertIn("no_rsi_cross", sig.reasons)


class TestPriceMovePct(unittest.TestCase):
    def test_long_profit(self):
        self.assertAlmostEqual(price_move_pct("long", 100.0, 102.0), 2.0)

    def test_short_profit(self):
        self.assertAlmostEqual(price_move_pct("short", 100.0, 98.0), 2.0)

    def test_should_take_profit_at_target(self):
        self.assertTrue(should_take_profit("long", 100.0, 102.0, target_pct=2.0))

    def test_should_not_take_profit_below_target(self):
        self.assertFalse(should_take_profit("long", 100.0, 101.0, target_pct=2.0))


if __name__ == "__main__":
    unittest.main()
