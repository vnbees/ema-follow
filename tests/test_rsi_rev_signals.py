import unittest

from src.exchange.types import Candle
from src.rsi_rev.signals import (
    ZONE_RSI30,
    ZONE_RSI50,
    ZONE_RSI70,
    detect_anchor_events,
    tp_price,
    trigger_from_bar,
)


def _bar(ts: int, close: float, *, high: float | None = None, low: float | None = None) -> Candle:
    hi = close if high is None else high
    lo = close if low is None else low
    return Candle(timestamp=ts, open=close, high=hi, low=lo, close=close, volume=1.0)


class TestRsiRevSignals(unittest.TestCase):
    def test_cross_into_rsi70_on_last_bar_only(self) -> None:
        candles = [_bar(1, 10), _bar(2, 10)]
        events = detect_anchor_events(candles, rsi=[69.0, 71.0])
        self.assertEqual([e.zone for e in events], [ZONE_RSI70])
        self.assertEqual(events[0].price, 10.0)
        self.assertEqual(events[0].ts, 2)

    def test_no_lookahead_prior_cross_ignored(self) -> None:
        candles = [_bar(1, 10), _bar(2, 10), _bar(3, 10)]
        events = detect_anchor_events(candles, rsi=[69.0, 71.0, 72.0])
        self.assertEqual(events, [])

    def test_cross_into_rsi30_and_mid(self) -> None:
        candles = [_bar(1, 10), _bar(2, 11)]
        thirty = detect_anchor_events(candles, rsi=[31.0, 29.0])
        self.assertEqual([e.zone for e in thirty], [ZONE_RSI30])
        mid = detect_anchor_events(candles, rsi=[47.0, 50.0])
        self.assertEqual([e.zone for e in mid], [ZONE_RSI50])
        still_mid = detect_anchor_events(candles, rsi=[49.0, 51.0])
        self.assertEqual(still_mid, [])

    def test_leave_0_5_pct_short_from_high(self) -> None:
        bar = _bar(20, 100.4, high=100.6, low=100.0)
        trig = trigger_from_bar(
            zone=ZONE_RSI70,
            anchor_ts=10,
            anchor_price=100.0,
            anchor_rsi=71.0,
            bar=bar,
        )
        self.assertIsNotNone(trig)
        assert trig is not None
        self.assertEqual(trig.side, "short")
        self.assertEqual(trig.entry, 100.4)
        self.assertAlmostEqual(trig.tp, tp_price("short", 100.0))

    def test_leave_0_5_pct_long_from_low(self) -> None:
        bar = _bar(20, 99.6, high=100.0, low=99.4)
        trig = trigger_from_bar(
            zone=ZONE_RSI30,
            anchor_ts=10,
            anchor_price=100.0,
            anchor_rsi=29.0,
            bar=bar,
        )
        self.assertIsNotNone(trig)
        assert trig is not None
        self.assertEqual(trig.side, "long")
        self.assertAlmostEqual(trig.tp, 100.0 * (1 - 0.0025))

    def test_same_bar_both_wicks_follow_close(self) -> None:
        short_bar = _bar(20, 100.2, high=100.6, low=99.4)
        long_bar = _bar(20, 99.8, high=100.6, low=99.4)
        s = trigger_from_bar(
            zone=ZONE_RSI50,
            anchor_ts=10,
            anchor_price=100.0,
            anchor_rsi=50.0,
            bar=short_bar,
        )
        lng = trigger_from_bar(
            zone=ZONE_RSI50,
            anchor_ts=10,
            anchor_price=100.0,
            anchor_rsi=50.0,
            bar=long_bar,
        )
        self.assertEqual(s.side if s else None, "short")
        self.assertEqual(lng.side if lng else None, "long")

    def test_same_anchor_bar_does_not_trigger(self) -> None:
        bar = _bar(10, 100.0, high=101.0, low=99.0)
        trig = trigger_from_bar(
            zone=ZONE_RSI70,
            anchor_ts=10,
            anchor_price=100.0,
            anchor_rsi=71.0,
            bar=bar,
        )
        self.assertIsNone(trig)

    def test_inside_band_no_trigger(self) -> None:
        bar = _bar(20, 100.0, high=100.4, low=99.6)
        trig = trigger_from_bar(
            zone=ZONE_RSI70,
            anchor_ts=10,
            anchor_price=100.0,
            anchor_rsi=71.0,
            bar=bar,
        )
        self.assertIsNone(trig)


if __name__ == "__main__":
    unittest.main()
