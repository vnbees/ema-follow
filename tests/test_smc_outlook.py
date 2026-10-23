import unittest

from src.smc.outlook import BARS_1D, BARS_4H, level_for, pooled_rates
from src.smc.signals import BAR_MS


def _flat(n, *, low=100.0, high=110.0, close=105.0):
    ts = [i * BAR_MS for i in range(n)]
    highs = [high] * n
    lows = [low] * n
    closes = [close] * n
    return ts, highs, lows, closes


class OutlookTests(unittest.TestCase):
    def test_latest_signal_is_the_return_level(self) -> None:
        ts, highs, lows, closes = _flat(90)
        lows[5] = 90.0
        highs[20] = 130.0
        highs[80] = 131.5
        lows[80] = 120.0
        closes[80] = 129.0
        now = ts[80] + 2 * BAR_MS
        row = level_for("BTCUSDT", ts, highs, lows, closes, 128.0, now)
        self.assertTrue(row["has_signal"])
        self.assertEqual(row["side"], "long")
        self.assertEqual(row["entry"], 130.0)
        self.assertEqual(row["signal_ts"], ts[80])
        self.assertEqual(row["prob_4h"], 65.1)
        self.assertEqual(row["prob_1d"], 85.1)
        self.assertEqual(row["prob_7d"], 94.1)
        self.assertEqual(row["prob_source"], "coin")
        self.assertEqual(row["deadline_4h"], ts[80] + BARS_4H * BAR_MS)
        self.assertEqual(row["deadline_1d"], ts[80] + BARS_1D * BAR_MS)
        self.assertEqual(row["deadline_7d"], ts[80] + 672 * BAR_MS)
        self.assertTrue(row["window_4h_open"])
        self.assertTrue(row["window_1d_open"])
        self.assertAlmostEqual(row["distance_pct"], (128.0 - 130.0) / 130.0 * 100.0)

    def test_unmeasured_coin_uses_the_pooled_rate(self) -> None:
        h4, d1, d7, n1, n4, n7 = pooled_rates()
        self.assertEqual(n4, 19987)
        self.assertEqual(n1, 19939)
        self.assertEqual(n7, 19549)
        self.assertEqual(h4, 67.8)
        self.assertEqual(d1, 86.8)
        self.assertEqual(d7, 95.2)
        ts, highs, lows, closes = _flat(90)
        lows[5] = 90.0
        highs[20] = 130.0
        highs[80] = 131.5
        lows[80] = 120.0
        closes[80] = 129.0
        row = level_for("ZZZUSDT", ts, highs, lows, closes, 130.0, ts[80] + BAR_MS)
        self.assertEqual(row["prob_source"], "pooled")
        self.assertEqual(row["prob_4h"], h4)
        self.assertEqual(row["prob_1d"], d1)
        self.assertEqual(row["prob_7d"], d7)

    def test_every_scanned_coin_has_its_own_rate(self) -> None:
        from src.donchian.config import FIXED_SCAN_SYMBOLS
        from src.smc.outlook import MEASURED

        self.assertEqual(set(MEASURED), set(FIXED_SCAN_SYMBOLS))
        row = level_for("ADAUSDT", *_flat(90), 130.0, 0)
        self.assertEqual(row["prob_source"], "coin")
        self.assertEqual(row["prob_4h"], 68.4)
        self.assertEqual(row["prob_1d"], 86.0)

    def test_window_closes_after_the_horizon(self) -> None:
        ts, highs, lows, closes = _flat(90)
        lows[5] = 90.0
        highs[20] = 130.0
        highs[80] = 131.5
        lows[80] = 120.0
        closes[80] = 129.0
        row = level_for("ETHUSDT", ts, highs, lows, closes, 100.0, ts[80] + (BARS_4H + 1) * BAR_MS)
        self.assertFalse(row["window_4h_open"])
        self.assertEqual(row["window_4h"], "đã qua")
        self.assertTrue(row["window_1d_open"])

    def test_no_signal(self) -> None:
        ts, highs, lows, closes = _flat(90)
        row = level_for("BTCUSDT", ts, highs, lows, closes, 105.0, ts[-1])
        self.assertFalse(row["has_signal"])
        self.assertEqual(row["prob_1d"], 85.1)
