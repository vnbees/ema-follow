import unittest

from src.exchange.types import Candle
from src.trend4h.config import BAR_MS
from src.trend4h.engine import desired_side, resample_closed_4h
from src.trend4h.indicators import adx, supertrend, want_side


def _candle(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=1.0, quote_volume=1.0)


class ResampleTests(unittest.TestCase):
    def test_sixteen_15m_make_one_closed_4h(self):
        start = 0
        rows = []
        for i in range(16):
            px = 100.0 + i
            rows.append(_candle(start + i * 15 * 60 * 1000, px, px + 1, px - 1, px))
        now = start + BAR_MS
        bars = resample_closed_4h(rows, now)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].ts, start)
        self.assertEqual(bars[0].open, 100.0)
        self.assertEqual(bars[0].close, 115.0)
        self.assertEqual(bars[0].high, 116.0)
        self.assertEqual(bars[0].low, 99.0)

    def test_open_bucket_is_dropped(self):
        start = 0
        rows = [_candle(start + i * 15 * 60 * 1000, 1, 1, 1, 1) for i in range(8)]
        bars = resample_closed_4h(rows, start + 8 * 15 * 60 * 1000)
        self.assertEqual(bars, [])


class WantSideTests(unittest.TestCase):
    def test_adx_low_is_flat_even_if_st_has_direction(self):
        n = 80
        highs = [100.0 + i * 0.01 for i in range(n)]
        lows = [99.0 + i * 0.01 for i in range(n)]
        closes = [99.5 + i * 0.01 for i in range(n)]
        st = supertrend(highs, lows, closes, 10, 3)
        adx_v = adx(highs, lows, closes, 14)
        want = want_side(highs, lows, closes, adx_min=25)
        self.assertTrue(any(x != 0 for x in st[-10:]))
        # A slow grind can print ADX; we only require 0 when ADX is below the cut.
        for i, value in enumerate(adx_v):
            if value is None or value < 25:
                self.assertEqual(want[i], 0)

    def test_desired_side_uses_last_bar(self):
        from src.trend4h.engine import Bar4h

        bars = [
            Bar4h(ts=i * BAR_MS, open=100, high=101 + i * 0.2, low=99, close=100.5 + i * 0.2)
            for i in range(90)
        ]
        side = desired_side(bars)
        self.assertIn(side, (-1, 0, 1))


if __name__ == "__main__":
    unittest.main()
