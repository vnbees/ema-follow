import unittest
from unittest.mock import patch

from src.candles import expected_last_closed_ts_ms, get_closed_candles
from src.ema_rsi.candles import confirm_entry_signal, same_entry_signal
from src.ema_rsi.signals import EntrySignal
from src.exchange.types import Candle


def _fresh_closed_bars(n: int = 220) -> list[Candle]:
    expected = expected_last_closed_ts_ms(5)
    start = expected - (n - 1) * 300_000
    return [
        Candle(timestamp=start + i * 300_000, open=1, high=1, low=1, close=1, volume=1)
        for i in range(n)
    ]


class TestSameEntrySignal(unittest.TestCase):
    def test_matches_side_ts_and_close(self) -> None:
        sig = EntrySignal(
            side="long",
            entry=100.0,
            sl=90.0,
            tp=120.0,
            r=10.0,
            zone_start_ts=1,
            zone_end_ts=2,
            signal_ts=3,
        )
        self.assertTrue(same_entry_signal(sig, sig))

    def test_mismatch_side_or_ts(self) -> None:
        base = EntrySignal(
            side="long",
            entry=100.0,
            sl=90.0,
            tp=120.0,
            r=10.0,
            zone_start_ts=1,
            zone_end_ts=2,
            signal_ts=3,
        )
        other = EntrySignal(
            side="short",
            entry=100.0,
            sl=110.0,
            tp=80.0,
            r=10.0,
            zone_start_ts=1,
            zone_end_ts=2,
            signal_ts=3,
        )
        self.assertFalse(same_entry_signal(base, other))


class TestConfirmEntrySignal(unittest.TestCase):
    def test_confirms_when_detect_entry_matches(self) -> None:
        signal = EntrySignal(
            side="long",
            entry=100.0,
            sl=90.0,
            tp=120.0,
            r=10.0,
            zone_start_ts=1,
            zone_end_ts=2,
            signal_ts=3,
        )
        with (
            patch("src.ema_rsi.candles.fetch_candles", return_value=_fresh_closed_bars()) as fetch,
            patch("src.ema_rsi.candles.detect_entry", return_value=signal) as detect,
        ):
            confirmed, skip = confirm_entry_signal("BTCUSDT", signal)
            self.assertIsNone(skip)
            self.assertIs(confirmed, signal)
            fetch.assert_called_once()
            self.assertTrue(fetch.call_args.kwargs.get("require_confirmed"))
            detect.assert_called_once()

    def test_skips_when_confirmed_signal_differs(self) -> None:
        signal = EntrySignal(
            side="long",
            entry=100.0,
            sl=90.0,
            tp=120.0,
            r=10.0,
            zone_start_ts=1,
            zone_end_ts=2,
            signal_ts=3,
        )
        with (
            patch("src.ema_rsi.candles.fetch_candles", return_value=_fresh_closed_bars()),
            patch("src.ema_rsi.candles.detect_entry", return_value=None),
        ):
            confirmed, skip = confirm_entry_signal("BTCUSDT", signal)
            self.assertIsNone(confirmed)
            self.assertEqual(skip, "signal_not_confirmed")

    def test_skips_when_stale_series(self) -> None:
        signal = EntrySignal(
            side="long",
            entry=100.0,
            sl=90.0,
            tp=120.0,
            r=10.0,
            zone_start_ts=1,
            zone_end_ts=2,
            signal_ts=3,
        )
        stale = [
            Candle(timestamp=i * 300_000, open=1, high=1, low=1, close=1, volume=1)
            for i in range(220)
        ]
        with patch("src.ema_rsi.candles.fetch_candles", return_value=stale):
            confirmed, skip = confirm_entry_signal("BTCUSDT", signal)
            self.assertIsNone(confirmed)
            self.assertEqual(skip, "stale_candles")


class TestFetchCandlesRequireConfirmed(unittest.TestCase):
    def tearDown(self) -> None:
        from src.exchange import binance
        from src.exchange.binance_ws.cache import CACHE

        with binance._candle_rest_lock:
            binance._candle_rest_at_mono.pop("BTCUSDT", None)
            binance._last_candle_rest_mono = 0.0
        CACHE.candles.pop("BTCUSDT", None)
        CACHE.candle_interval.pop("BTCUSDT", None)

    def test_forces_rest_when_ws_kline_not_fresh(self) -> None:
        from src.exchange import binance

        fake = _fresh_closed_bars(30)
        with (
            patch("src.exchange.binance_ws.manager.is_ws_enabled", return_value=True),
            patch("src.exchange.binance_ws.manager.kline_fresh", return_value=False),
            patch("src.exchange.binance.candle_rest_fresh", return_value=False),
            patch("src.exchange.binance.fetch_candles_rest", return_value=fake) as mock_rest,
            patch("src.exchange.binance.is_optional_rest_blocked", return_value=False),
            patch("src.exchange.binance_ws.persist.save_candles_snapshot"),
        ):
            out = binance.fetch_candles(
                "BTCUSDT",
                granularity="5m",
                limit=30,
                require_confirmed=True,
            )
            self.assertEqual(len(out), 30)
            mock_rest.assert_called_once()

    def test_reuses_ws_when_kline_fresh(self) -> None:
        from src.exchange import binance

        fake = _fresh_closed_bars(30)
        with (
            patch("src.exchange.binance_ws.manager.is_ws_enabled", return_value=True),
            patch("src.exchange.binance_ws.manager.kline_fresh", return_value=True),
            patch("src.exchange.binance_ws.get_candles_from_ws", return_value=fake) as mock_ws,
            patch("src.exchange.binance.fetch_candles_rest") as mock_rest,
        ):
            out = binance.fetch_candles(
                "BTCUSDT",
                granularity="5m",
                limit=30,
                require_confirmed=True,
            )
            self.assertEqual(len(out), 30)
            mock_ws.assert_called_once()
            mock_rest.assert_not_called()


if __name__ == "__main__":
    unittest.main()
