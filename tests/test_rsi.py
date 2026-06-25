import unittest

from src.bitget_client import Candle
from src.rsi import RsiSnapshot, compute_rsi_series, get_rsi_snapshot
from src.rsi_signals import detect_dca_signal, detect_entry_signal, should_exit


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


class TestRsiSignals(unittest.TestCase):
    def test_not_ready(self):
        sig = detect_entry_signal(RsiSnapshot(ready=False))
        self.assertIsNone(sig.side)
        self.assertIn("rsi_not_ready", sig.reasons)

    def test_long_entry_cross_up_25(self):
        snap = RsiSnapshot(ready=True, rsi=26.0, prev_rsi=24.0, cross_up_25=True)
        sig = detect_entry_signal(snap)
        self.assertEqual(sig.side, "long")
        self.assertEqual(sig.entry_trigger, "rsi_cross_25")

    def test_short_entry_cross_down_75(self):
        snap = RsiSnapshot(ready=True, rsi=74.0, prev_rsi=76.0, cross_down_75=True)
        sig = detect_entry_signal(snap)
        self.assertEqual(sig.side, "short")
        self.assertEqual(sig.entry_trigger, "rsi_cross_75")

    def test_no_signal(self):
        snap = RsiSnapshot(ready=True, rsi=50.0, prev_rsi=49.0)
        sig = detect_entry_signal(snap)
        self.assertIsNone(sig.side)
        self.assertIn("no_rsi_cross", sig.reasons)


class TestRsiExit(unittest.TestCase):
    def test_long_exit_cross_up_75(self):
        snap = RsiSnapshot(ready=True, rsi=76.0, prev_rsi=74.0, cross_up_75=True)
        exit_flag, reason = should_exit("long", snap)
        self.assertTrue(exit_flag)
        self.assertEqual(reason, "rsi_cross_75")

    def test_short_exit_cross_down_25(self):
        snap = RsiSnapshot(ready=True, rsi=24.0, prev_rsi=26.0, cross_down_25=True)
        exit_flag, reason = should_exit("short", snap)
        self.assertTrue(exit_flag)
        self.assertEqual(reason, "rsi_cross_25")

    def test_long_hold(self):
        snap = RsiSnapshot(ready=True, rsi=50.0, prev_rsi=48.0)
        exit_flag, reason = should_exit("long", snap)
        self.assertFalse(exit_flag)

    def test_short_hold(self):
        snap = RsiSnapshot(ready=True, rsi=50.0, prev_rsi=52.0)
        exit_flag, reason = should_exit("short", snap)
        self.assertFalse(exit_flag)


class TestRsiDca(unittest.TestCase):
    def test_long_dca_cross_up_25(self):
        snap = RsiSnapshot(ready=True, rsi=26.0, prev_rsi=24.0, cross_up_25=True)
        sig = detect_dca_signal("long", snap)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, "long")
        self.assertEqual(sig.entry_trigger, "rsi_cross_25_dca")

    def test_short_dca_cross_down_75(self):
        snap = RsiSnapshot(ready=True, rsi=74.0, prev_rsi=76.0, cross_down_75=True)
        sig = detect_dca_signal("short", snap)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, "short")
        self.assertEqual(sig.entry_trigger, "rsi_cross_75_dca")

    def test_long_no_dca_on_cross_down_75(self):
        snap = RsiSnapshot(ready=True, rsi=74.0, prev_rsi=76.0, cross_down_75=True)
        self.assertIsNone(detect_dca_signal("long", snap))

    def test_short_no_dca_on_cross_down_25(self):
        snap = RsiSnapshot(ready=True, rsi=24.0, prev_rsi=26.0, cross_down_25=True)
        self.assertIsNone(detect_dca_signal("short", snap))

    def test_opposite_signal_while_long(self):
        snap = RsiSnapshot(ready=True, rsi=74.0, prev_rsi=76.0, cross_down_75=True)
        self.assertIsNone(detect_dca_signal("long", snap))


if __name__ == "__main__":
    unittest.main()
