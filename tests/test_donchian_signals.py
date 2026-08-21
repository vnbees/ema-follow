"""Unit tests for Donchian parallel-trend signal detection."""

import unittest
from unittest.mock import MagicMock, patch

from src.donchian.signals import (
    DonchianBar,
    EntrySignal,
    SignalState,
    check_signal,
    compute_atr,
    compute_bands,
    size_mult_from_pot_rr,
)


def _bar(
    ts: int,
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
    open_: float | None = None,
) -> DonchianBar:
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
# Existing structural tests disable quality filter (synthetic ranges fail body/RR).
NO_QF = dict(apply_quality_filter=False)


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


class TestComputeAtr(unittest.TestCase):
    def test_atr_none_until_warmup(self) -> None:
        bars = [_bar(i, 10.0, high=11.0, low=9.0) for i in range(5)]
        atrs = compute_atr(bars, period=3)
        self.assertIsNone(atrs[0])
        self.assertIsNone(atrs[2])  # need 3 TRs → index >= 3
        self.assertIsNotNone(atrs[3])
        self.assertAlmostEqual(atrs[3], 2.0)  # TR always 2.0 on these bars


class TestSizeMult(unittest.TestCase):
    def test_clip_bounds(self) -> None:
        self.assertAlmostEqual(size_mult_from_pot_rr(0.0), 0.5)
        self.assertAlmostEqual(size_mult_from_pot_rr(0.5), 1.0)
        self.assertAlmostEqual(size_mult_from_pot_rr(1.5), 2.0)
        self.assertAlmostEqual(size_mult_from_pot_rr(3.0), 2.0)

    def test_disabled(self) -> None:
        self.assertAlmostEqual(size_mult_from_pot_rr(1.5, enabled=False), 1.0)


class TestCheckSignal(unittest.TestCase):
    def _flat_bars(self, n: int, close: float = 10.0) -> list[DonchianBar]:
        return [_bar(i, close, high=close + 0.1, low=close - 0.1) for i in range(n)]

    def test_not_enough_bars_returns_none(self) -> None:
        bars = self._flat_bars(PERIOD + LOOKBACK - 1)
        state = SignalState()
        entry = check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=TOL, **NO_QF)
        self.assertIsNone(entry)

    def test_no_signal_when_parallel_all_along(self) -> None:
        bars = self._flat_bars(20)
        state = SignalState(prev_parallel=True)
        entry = check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=TOL, **NO_QF)
        self.assertIsNone(entry)

    def test_trend_set_on_parallel_exit(self) -> None:
        bars = []
        for i in range(20):
            bars.append(DonchianBar(ts=i, open=10.0, high=10.0 + i * 0.2, low=9.0, close=10.0))
        state = SignalState(prev_parallel=True)
        check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=0.0, **NO_QF)
        self.assertIsNotNone(state.trend)
        self.assertTrue(state.waiting_entry)

    def test_long_signal_on_counter_candle(self) -> None:
        bars = self._flat_bars(20, close=10.0)
        state = SignalState(trend="up", trend_ts=1, waiting_entry=True, prev_parallel=False)
        bars[-1] = DonchianBar(ts=19, open=10.5, high=10.5, low=9.8, close=9.9)
        entry = check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=0.0, **NO_QF)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.side, "long")
        self.assertGreater(entry.tp_band, 0)
        self.assertAlmostEqual(entry.entry_px, 9.9)
        self.assertFalse(state.waiting_entry)

    def test_short_signal_on_counter_candle(self) -> None:
        bars = self._flat_bars(20, close=10.0)
        state = SignalState(trend="down", trend_ts=1, waiting_entry=True, prev_parallel=False)
        bars[-1] = DonchianBar(ts=19, open=9.5, high=10.2, low=9.5, close=10.1)
        entry = check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=0.0, **NO_QF)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.side, "short")
        self.assertGreater(entry.tp_band, 0)
        self.assertAlmostEqual(entry.entry_px, 10.1)
        self.assertFalse(state.waiting_entry)

    def test_holding_clears_waiting_entry_like_backtest(self) -> None:
        bars = self._flat_bars(20, close=10.0)
        state = SignalState(trend="up", trend_ts=1, waiting_entry=True, prev_parallel=False)
        bars[-1] = DonchianBar(ts=19, open=10.5, high=10.5, low=9.8, close=9.9)
        entry = check_signal(
            bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=0.0, allow_entry=False, **NO_QF
        )
        self.assertIsNone(entry)
        self.assertFalse(state.waiting_entry)

    def test_parallel_exit_while_holding_clears_waiting_entry(self) -> None:
        bars = []
        for i in range(20):
            bars.append(DonchianBar(ts=i, open=10.0, high=10.0 + i * 0.2, low=9.0, close=10.0))
        state = SignalState(prev_parallel=True)
        check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=0.0, allow_entry=False, **NO_QF)
        self.assertIsNotNone(state.trend)
        self.assertFalse(state.waiting_entry)

    def test_no_signal_when_bands_still_parallel(self) -> None:
        bars = self._flat_bars(20, close=10.0)
        state = SignalState(trend="up", trend_ts=1, waiting_entry=True, prev_parallel=False)
        bars[-1] = DonchianBar(ts=19, open=10.5, high=10.5, low=9.8, close=9.9)
        entry = check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=1000.0, **NO_QF)
        self.assertIsNone(entry)

    def test_state_prev_parallel_updated(self) -> None:
        bars = self._flat_bars(20, close=10.0)
        state = SignalState(prev_parallel=True)
        check_signal(bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=TOL, **NO_QF)
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
        entry = process_closed_bars(
            bars, state, period=PERIOD, slope_lookback=LOOKBACK, tol=0.0, **NO_QF
        )
        self.assertIsNone(entry)
        self.assertEqual(state.last_processed_ts, 19)


class TestQualityFilter(unittest.TestCase):
    """body_atr + pot_rr filters; ATR patched so body ratio is deterministic."""

    def _counter_long_bars(
        self,
        *,
        open_: float,
        close: float,
        upper: float = 12.0,
        lower: float = 8.0,
    ) -> list[DonchianBar]:
        bars: list[DonchianBar] = []
        for i in range(22):
            hi = 10.5 + i * 0.05
            bars.append(DonchianBar(ts=i, open=10.0, high=hi, low=9.5, close=10.0))
        bars.append(DonchianBar(ts=22, open=10.0, high=upper, low=9.5, close=10.0))
        bars.append(DonchianBar(ts=23, open=10.0, high=upper, low=lower, close=10.0))
        bars.append(
            DonchianBar(
                ts=24,
                open=open_,
                high=max(open_, close, 10.2),
                low=min(open_, close, lower),
                close=close,
            )
        )
        return bars

    def _fixed_atr(self, bars, period):  # noqa: ANN001
        out: list[float | None] = [None] * len(bars)
        out[-1] = 1.0
        return out

    def test_accepts_body_in_range_and_sets_size_mult(self) -> None:
        bars = self._counter_long_bars(open_=10.0, close=9.3)  # body=0.7 → body_atr=0.7
        state = SignalState(trend="up", trend_ts=1, waiting_entry=True, prev_parallel=False)
        with patch("src.donchian.signals.compute_atr", side_effect=self._fixed_atr):
            entry = check_signal(
                bars,
                state,
                period=PERIOD,
                slope_lookback=LOOKBACK,
                tol=0.0,
                atr_period=5,
                min_body_atr=0.3,
                max_body_atr=1.2,
                min_pot_rr=0.5,
                size_by_rr=True,
            )
        self.assertIsInstance(entry, EntrySignal)
        assert entry is not None
        self.assertEqual(entry.side, "long")
        self.assertAlmostEqual(entry.body_atr, 0.7, places=5)
        self.assertGreaterEqual(entry.pot_rr, 0.5)
        self.assertAlmostEqual(entry.size_mult, min(2.0, 0.5 + entry.pot_rr))
        self.assertFalse(state.waiting_entry)

    def test_rejects_body_too_large_keeps_waiting(self) -> None:
        bars = self._counter_long_bars(open_=10.0, close=7.5)  # body=2.5 → body_atr=2.5
        state = SignalState(trend="up", trend_ts=1, waiting_entry=True, prev_parallel=False)
        with patch("src.donchian.signals.compute_atr", side_effect=self._fixed_atr):
            entry = check_signal(
                bars,
                state,
                period=PERIOD,
                slope_lookback=LOOKBACK,
                tol=0.0,
                atr_period=5,
                min_body_atr=0.3,
                max_body_atr=1.2,
                min_pot_rr=0.5,
            )
        self.assertIsNone(entry)
        self.assertTrue(state.waiting_entry)

    def test_rejects_low_pot_rr_keeps_waiting(self) -> None:
        bars = self._counter_long_bars(open_=10.5, close=10.0, upper=10.2, lower=8.0)
        state = SignalState(trend="up", trend_ts=1, waiting_entry=True, prev_parallel=False)
        with patch("src.donchian.signals.compute_atr", side_effect=self._fixed_atr):
            entry = check_signal(
                bars,
                state,
                period=PERIOD,
                slope_lookback=LOOKBACK,
                tol=0.0,
                atr_period=5,
                min_body_atr=0.3,
                max_body_atr=1.2,
                min_pot_rr=0.5,
            )
        self.assertIsNone(entry)
        self.assertTrue(state.waiting_entry)


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
        from src.donchian.trading import _real_order_id

        self.assertEqual(_real_order_id(MagicMock()), "")
        self.assertEqual(_real_order_id({"orderId": MagicMock()}), "")
        self.assertEqual(_real_order_id({"orderId": 123456}), "123456")
        self.assertEqual(_real_order_id({"orderId": "abc"}), "abc")

    def test_notify_open_includes_why_and_size(self) -> None:
        from src.donchian import trading as trading_mod

        with (
            patch("src.notify.discord_configured", return_value=True),
            patch("src.notify._send_discord") as send,
            patch("src.notify._fmt_px", side_effect=lambda x: f"{x:.2f}"),
        ):
            trading_mod._notify_open(
                "LINKUSDT",
                "long",
                trend="up",
                entry=10.0,
                tp_band=11.0,
                size=1.5,
                margin_usdt=13.5,
                equity=1000.0,
                opp_band=9.0,
                body_atr=0.72,
                pot_rr=0.85,
                size_mult=1.35,
                why="thoát song song → UP; nến đỏ ngược chiều; band không song song",
            )
        self.assertTrue(send.called)
        title, body = send.call_args[0]
        self.assertIn("LINKUSDT", title)
        self.assertIn("lý do:", body)
        self.assertIn("body_atr=0.72", body)
        self.assertIn("pot_rr=0.85", body)
        self.assertIn("size_mult=1.35", body)
        self.assertIn("margin=13.50", body)

    @patch("src.donchian.trading.SIZE_BY_RR", True)
    @patch("src.donchian.trading.MARGIN_PCT", 0.01)
    @patch("src.donchian.trading.is_trading_enabled", return_value=True)
    @patch("src.donchian.trading.has_credentials", return_value=True)
    @patch("src.donchian.trading.store")
    @patch("src.donchian.trading.configure_symbol_trading")
    @patch("src.donchian.trading.live_account_balance")
    @patch("src.donchian.trading.fetch_contract_spec")
    @patch("src.donchian.trading.notional_to_size", return_value="1.0")
    @patch("src.donchian.trading.place_market_order", return_value={"orderId": 99})
    @patch("src.donchian.trading.resolve_order_fill", return_value=10.0)
    @patch("src.donchian.trading.resolve_order_commission", return_value=0.01)
    @patch("src.donchian.trading._get_mark", return_value=10.0)
    @patch("src.donchian.trading._notify_open")
    def test_open_lot_margin_uses_size_mult(
        self,
        _notify,
        _mark,
        _fee,
        _fill,
        _order,
        _size,
        _spec,
        balance,
        _cfg,
        store,
        *_rest,
    ) -> None:
        from src.donchian.trading import open_lot

        bal = MagicMock()
        bal.account_equity = 1000.0
        bal.available = 1000.0
        balance.return_value = bal
        store.count_open.return_value = 0
        store.has_open_lot_for_symbol.return_value = False
        store.insert_lot.return_value = 1
        _spec.return_value = MagicMock()

        status = open_lot(
            "LINKUSDT",
            side="long",
            trend="up",
            trend_ts=1,
            entry_ts=2,
            tp_band=11.0,
            size_mult=1.35,
            body_atr=0.7,
            pot_rr=0.85,
            opp_band=9.0,
            why="test",
        )
        self.assertEqual(status, "opened")
        kwargs = store.insert_lot.call_args.kwargs
        self.assertAlmostEqual(kwargs["margin_usdt"], 13.5)  # 1000 * 0.01 * 1.35
        self.assertAlmostEqual(kwargs["size_mult"], 1.35)
        self.assertAlmostEqual(kwargs["body_atr"], 0.7)


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
        cols = {row[1] for row in conn.execute("PRAGMA table_info(donchian_lots)")}
        self.assertIn("body_atr", cols)
        self.assertIn("size_mult", cols)
        conn.close()


if __name__ == "__main__":
    unittest.main()
