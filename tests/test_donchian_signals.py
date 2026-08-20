"""Unit tests for Donchian parallel-trend signal detection."""

import unittest

from src.donchian.signals import DonchianBar, SignalState, check_signal, compute_bands


def _bar(ts: int, close: float, *, high: float | None = None, low: float | None = None, open_: float | None = None) -> DonchianBar:
    return DonchianBar(
        ts=ts,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
    )


PERIOD = 3
LOOKBACK = 2
TOL = 10.0  # very loose for unit tests


class TestComputeBands(unittest.TestCase):
    def test_returns_none_before_period(self) -> None:
        bars = [_bar(i, 10.0) for i in range(PERIOD - 1)]
        result = compute_bands(bars, PERIOD, LOOKBACK, TOL)
        self.assertTrue(all(b is None for b in result))

    def test_upper_lower_from_rolling_window(self) -> None:
        bars = [_bar(0, 10, high=12, low=8), _bar(1, 10, high=14, low=6), _bar(2, 10, high=11, low=9)]
        result = compute_bands(bars, 3, 2, TOL)
        b = result[2]
        self.assertIsNotNone(b)
        self.assertAlmostEqual(b.upper, 14.0)
        self.assertAlmostEqual(b.lower, 6.0)
        self.assertAlmostEqual(b.middle, 10.0)


class TestCheckSignal(unittest.TestCase):
    def _flat_bars(self, n: int, close: float = 10.0) -> list[DonchianBar]:
        return [_bar(i, close, high=close + 0.1, low=close - 0.1) for i in range(n)]

    def test_not_enough_bars_returns_none(self) -> None:
        bars = self._flat_bars(PERIOD + LOOKBACK - 1)
        state = SignalState()
        signal, tp, entry = check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=TOL)
        self.assertIsNone(signal)

    def test_no_signal_when_parallel_all_along(self) -> None:
        bars = self._flat_bars(20)
        state = SignalState(prev_parallel=True)
        signal, tp, entry = check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=TOL)
        self.assertIsNone(signal)

    def test_trend_set_on_parallel_exit(self) -> None:
        # Construct bars where upper is rising faster than lower → non-parallel at end
        bars = []
        for i in range(20):
            # Upper rises, lower stays flat → bands not parallel
            bars.append(DonchianBar(ts=i, open=10.0, high=10.0 + i * 0.2, low=9.0, close=10.0))
        state = SignalState(prev_parallel=True)
        check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=0.0)
        # trend should be set, waiting_entry=True
        self.assertIsNotNone(state.trend)
        self.assertTrue(state.waiting_entry)

    def test_long_signal_on_counter_candle(self) -> None:
        bars = self._flat_bars(20, close=10.0)
        # Manually set state: trend=up, waiting_entry=True, prev_parallel=False (non-parallel)
        state = SignalState(trend="up", trend_ts=1, waiting_entry=True, prev_parallel=False)
        # Last bar: red candle (close < open) = counter-trend for uptrend
        bars[-1] = DonchianBar(ts=19, open=10.5, high=10.5, low=9.8, close=9.9)
        signal, tp_band, entry_px = check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=0.0)
        self.assertEqual(signal, "long")
        self.assertGreater(tp_band, 0)
        self.assertAlmostEqual(entry_px, 9.9)
        self.assertFalse(state.waiting_entry)

    def test_short_signal_on_counter_candle(self) -> None:
        bars = self._flat_bars(20, close=10.0)
        state = SignalState(trend="down", trend_ts=1, waiting_entry=True, prev_parallel=False)
        # Last bar: green candle (close > open) = counter-trend for downtrend
        bars[-1] = DonchianBar(ts=19, open=9.5, high=10.2, low=9.5, close=10.1)
        signal, tp_band, entry_px = check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=0.0)
        self.assertEqual(signal, "short")
        self.assertGreater(tp_band, 0)
        self.assertAlmostEqual(entry_px, 10.1)
        self.assertFalse(state.waiting_entry)

    def test_no_signal_when_bands_still_parallel(self) -> None:
        bars = self._flat_bars(20, close=10.0)
        state = SignalState(trend="up", trend_ts=1, waiting_entry=True, prev_parallel=False)
        bars[-1] = DonchianBar(ts=19, open=10.5, high=10.5, low=9.8, close=9.9)
        # Use very loose tol so bands appear parallel
        signal, _, _ = check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=1000.0)
        self.assertIsNone(signal)

    def test_state_prev_parallel_updated(self) -> None:
        bars = self._flat_bars(20, close=10.0)
        state = SignalState(prev_parallel=True)
        check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=TOL)
        # prev_parallel should reflect last bar's parallel flag
        self.assertIsInstance(state.prev_parallel, bool)

    def test_process_closed_bars_does_not_chase_old_signal(self) -> None:
        from src.donchian.signals import process_closed_bars

        bars = self._flat_bars(20, close=10.0)
        bars[-2] = DonchianBar(ts=18, open=10.5, high=10.5, low=9.8, close=9.9)
        bars[-1] = DonchianBar(ts=19, open=9.9, high=10.2, low=9.9, close=10.1)
        state = SignalState(
            trend="up",
            waiting_entry=True,
            prev_parallel=False,
            last_processed_ts=17,
        )
        signal, _, _ = process_closed_bars(
            bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=0.0
        )
        self.assertIsNone(signal)
        self.assertEqual(state.last_processed_ts, 19)


class TestRollingChannel(unittest.TestCase):
    def test_upper_lower_include_latest_bar(self) -> None:
        from src.donchian.signals import rolling_channel

        highs = [10.0, 11.0, 12.0]
        lows = [9.0, 8.5, 9.2]
        ch = rolling_channel(highs, lows, 3)
        self.assertIsNotNone(ch)
        self.assertAlmostEqual(ch[0], 12.0)
        self.assertAlmostEqual(ch[1], 8.5)

    def test_channel_moves_with_new_high(self) -> None:
        from src.donchian.signals import rolling_channel

        highs = [10.0] * 19 + [12.0]
        lows = [9.0] * 20
        ch = rolling_channel(highs, lows, 20)
        self.assertAlmostEqual(ch[0], 12.0)


class TestWatcherTpHit(unittest.TestCase):
    def test_long_waits_for_current_upper_not_old_10(self) -> None:
        from src.donchian.watcher import _tp_hit

        self.assertFalse(_tp_hit("long", upper=12.0, lower=8.0, high=10.5, low=9.5, mark=10.5))
        self.assertTrue(_tp_hit("long", upper=12.0, lower=8.0, high=12.0, low=9.5, mark=11.9))
        self.assertTrue(_tp_hit("long", upper=12.0, lower=8.0, high=11.5, low=9.5, mark=12.0))

    def test_short_hits_current_lower(self) -> None:
        from src.donchian.watcher import _tp_hit

        self.assertFalse(_tp_hit("short", upper=12.0, lower=8.0, high=10.0, low=8.5, mark=8.5))
        self.assertTrue(_tp_hit("short", upper=12.0, lower=8.0, high=10.0, low=8.0, mark=8.2))


class TestDonchianTrading(unittest.TestCase):
    def test_realized_pnl_long(self) -> None:
        from src.donchian.trading import realized_pnl

        self.assertAlmostEqual(realized_pnl("long", 100, 110, 2), 20.0)

    def test_realized_pnl_short(self) -> None:
        from src.donchian.trading import realized_pnl

        self.assertAlmostEqual(realized_pnl("short", 100, 90, 2), 20.0)

    def test_real_order_id_rejects_mock(self) -> None:
        from unittest.mock import MagicMock
        from src.donchian.trading import _real_order_id

        self.assertEqual(_real_order_id(MagicMock()), "")
        self.assertEqual(_real_order_id({"orderId": MagicMock()}), "")
        self.assertEqual(_real_order_id({"orderId": 123456}), "123456")
        self.assertEqual(_real_order_id({"orderId": "abc"}), "abc")


class TestDonchianStore(unittest.TestCase):
    def test_ensure_schema_idempotent(self) -> None:
        import sqlite3
        from src.donchian.store import ensure_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        ensure_schema(conn)  # second call must not raise
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("donchian_lots", tables)
        self.assertIn("donchian_state", tables)
        conn.close()


if __name__ == "__main__":
    unittest.main()
