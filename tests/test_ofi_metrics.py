import unittest

from src.bitget_client import Candle
from src.orderflow.aggregator import OrderFlowSnapshot
from src.orderflow.metrics import (
    classify_book_bias,
    classify_imbalance_tier,
    compute_book_pressure_near_mid,
    compute_book_pressure_pct,
    compute_imbalance_pct,
    compute_ofi_signal,
)
from src.orderflow.orderbook import BookPressureSnapshot
from src.orderflow.trading_signals import evaluate_ofi_entry as entry_signal


class TestImbalance(unittest.TestCase):
    def test_balanced(self):
        self.assertAlmostEqual(compute_imbalance_pct(100, 100), 100.0)

    def test_strong_bull(self):
        self.assertAlmostEqual(compute_imbalance_pct(200, 100), 200.0)
        self.assertEqual(classify_imbalance_tier(200.0), "strong_bull")

    def test_extreme_bull(self):
        self.assertEqual(classify_imbalance_tier(300.0), "extreme_bull")

    def test_zero_sell(self):
        self.assertEqual(compute_imbalance_pct(50, 0), 999.0)

    def test_strong_bear(self):
        self.assertEqual(classify_imbalance_tier(50.0), "strong_bear")


class TestBookPressure(unittest.TestCase):
    def test_pressure_ratio(self):
        self.assertAlmostEqual(compute_book_pressure_pct(150, 100), 150.0)
        self.assertEqual(classify_book_bias(150.0), "bullish")

    def test_near_mid(self):
        bids = [(100.0, 10.0), (99.9, 5.0)]
        asks = [(100.1, 8.0), (100.2, 4.0)]
        bid_v, ask_v, pct = compute_book_pressure_near_mid(
            bids, asks, tick_size=0.1, tick_range=5
        )
        self.assertGreater(bid_v, 0)
        self.assertGreater(ask_v, 0)
        self.assertGreater(pct, 0)


class TestOfiSignal(unittest.TestCase):
    def _candle(self) -> Candle:
        return Candle(timestamp=0, open=100, high=101, low=99, close=100.5, volume=10)

    def test_bullish_signal(self):
        signal = compute_ofi_signal(
            closed_candle=self._candle(),
            volume_delta=50,
            current_delta=80,
            delta_spike_ratio=2.0,
            avg_delta_10=40,
            buy_volume=200,
            sell_volume=100,
            forming_buy_volume=300,
            forming_sell_volume=100,
            forming_imbalance_pct=300.0,
            imbalance_pct=200.0,
            book_pressure_pct=180.0,
            book_bias="bullish",
            delta_velocity=10.0,
            candle_age_sec=3.0,
        )
        self.assertEqual(signal.direction, "green")
        self.assertGreater(signal.probability, 55)
        self.assertEqual(signal.early_signal, "green")


class TestEntrySignal(unittest.TestCase):
    def test_warmup_blocks(self):
        snap = OrderFlowSnapshot(current_period_ms=1, candle_age_sec=2)
        book = BookPressureSnapshot(stale=False, book_pressure_pct=200)
        d = entry_signal(snap, book, stats_ready=False)
        self.assertIsNone(d.side)
        self.assertEqual(d.reason, "warmup")

    def test_long_entry(self):
        snap = OrderFlowSnapshot(
            forming_imbalance_pct=250.0,
            current_delta=50.0,
            delta_velocity=5.0,
            candle_age_sec=3.0,
            current_period_ms=1000,
        )
        book = BookPressureSnapshot(stale=False, book_pressure_pct=180.0, book_bias="bullish")
        d = entry_signal(snap, book, stats_ready=True)
        self.assertEqual(d.side, "long")


if __name__ == "__main__":
    unittest.main()
