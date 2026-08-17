import unittest

from src.ema_rsi.signals import (
    compute_ema_series,
    detect_entry,
    ema_cross_dir,
    evaluate_last_bar,
    levels_from_entry,
)
from src.exchange.types import Candle


def _bar(i: int, close: float, *, high: float | None = None, low: float | None = None) -> Candle:
    h = close + 1.0 if high is None else high
    l = close - 1.0 if low is None else low
    return Candle(
        timestamp=i * 300_000,
        open=close,
        high=h,
        low=l,
        close=close,
        volume=1.0,
    )


class TestEmaCross(unittest.TestCase):
    def test_cross_up_and_down(self) -> None:
        self.assertEqual(ema_cross_dir(99, 100, 101, 100), "up")
        self.assertEqual(ema_cross_dir(101, 100, 99, 100), "down")

    def test_standing_above_or_below_is_not_cross(self) -> None:
        self.assertIsNone(ema_cross_dir(101, 100, 102, 100))
        self.assertIsNone(ema_cross_dir(99, 100, 98, 100))


class TestLevelsFromEntry(unittest.TestCase):
    def test_long_rr(self) -> None:
        sized = levels_from_entry("long", 100, 90, rr=2)
        self.assertIsNotNone(sized)
        assert sized is not None
        r, tp = sized
        self.assertEqual(r, 10)
        self.assertEqual(tp, 120)

    def test_short_rr(self) -> None:
        sized = levels_from_entry("short", 100, 110, rr=2)
        self.assertIsNotNone(sized)
        assert sized is not None
        r, tp = sized
        self.assertEqual(r, 10)
        self.assertEqual(tp, 80)

    def test_invalid_sl_long_and_short(self) -> None:
        self.assertIsNone(levels_from_entry("long", 100, 100, rr=2))
        self.assertIsNone(levels_from_entry("long", 100, 101, rr=2))
        self.assertIsNone(levels_from_entry("short", 100, 100, rr=2))
        self.assertIsNone(levels_from_entry("short", 100, 99, rr=2))


class TestEvaluateLastBar(unittest.TestCase):
    def _eval(
        self,
        closes: list[float],
        rsi: list[float | None],
        ema: list[float | None],
        *,
        lows: list[float] | None = None,
        highs: list[float] | None = None,
    ):
        candles = []
        for i, close in enumerate(closes):
            low = lows[i] if lows else close - 1
            high = highs[i] if highs else close + 1
            candles.append(_bar(i, close, high=high, low=low))
        return evaluate_last_bar(candles, rsi, ema, rsi_low=25, rsi_high=75, rr=2)

    def test_long_cross_with_prior_rsi_zone(self) -> None:
        sig = self._eval(
            closes=[99, 99, 99, 99, 101],
            rsi=[50, 20, 20, 30, 40],
            ema=[100, 100, 100, 100, 100],
            lows=[98, 90, 91, 98, 100],
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, "long")
        self.assertIsNone(sig.skip_reason)
        self.assertEqual(sig.sl, 90)
        self.assertEqual(sig.entry, 101)
        self.assertEqual(sig.r, 11)
        self.assertEqual(sig.tp, 123)

    def test_short_cross_with_prior_rsi_zone(self) -> None:
        sig = self._eval(
            closes=[101, 101, 101, 101, 99],
            rsi=[50, 80, 80, 70, 60],
            ema=[100, 100, 100, 100, 100],
            highs=[102, 110, 109, 102, 100],
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.side, "short")
        self.assertEqual(sig.sl, 110)
        self.assertEqual(sig.tp, 77)

    def test_no_fire_when_standing_above_ema(self) -> None:
        sig = self._eval(
            closes=[101, 102],
            rsi=[20, 20],
            ema=[100, 100],
        )
        self.assertIsNone(sig)

    def test_skip_when_cross_candle_is_first_rsi_extreme(self) -> None:
        sig = self._eval(
            closes=[99, 101],
            rsi=[50, 20],
            ema=[100, 100],
        )
        self.assertIsNone(sig)

    def test_opposite_zone_cancels_long_arm(self) -> None:
        sig = self._eval(
            closes=[99, 99, 99, 99, 101],
            rsi=[20, 80, 50, 50, 40],
            ema=[100, 100, 100, 100, 100],
            lows=[90, 98, 98, 98, 100],
        )
        self.assertIsNone(sig)

    def test_newer_same_side_zone_replaces_sl(self) -> None:
        sig = self._eval(
            closes=[99, 99, 99, 99, 101],
            rsi=[20, 50, 20, 20, 40],
            ema=[100, 100, 100, 100, 100],
            lows=[80, 98, 92, 93, 100],
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.sl, 92)

    def test_invalid_sl_long(self) -> None:
        sig = self._eval(
            closes=[99, 99, 101],
            rsi=[20, 20, 40],
            ema=[100, 100, 100],
            lows=[105, 106, 100],
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.skip_reason, "invalid_sl")

    def test_invalid_sl_short(self) -> None:
        sig = self._eval(
            closes=[101, 101, 99],
            rsi=[80, 80, 60],
            ema=[100, 100, 100],
            highs=[90, 91, 100],
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.skip_reason, "invalid_sl")


class TestDetectEntryNeedsHistory(unittest.TestCase):
    def test_too_few_candles(self) -> None:
        candles = [_bar(i, 100) for i in range(50)]
        self.assertIsNone(detect_entry(candles, ema_period=200))

    def test_ema_series_length(self) -> None:
        closes = [float(i) for i in range(10)]
        series = compute_ema_series(closes, 5)
        self.assertEqual(len(series), 10)
        self.assertIsNone(series[3])
        self.assertIsNotNone(series[4])
        self.assertIsNotNone(series[-1])


if __name__ == "__main__":
    unittest.main()
