"""Unit tests for Binance market + user WS parsers/cache (no live network)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.exchange.binance_ws.cache import BinanceWsCache, CACHE
from src.exchange.binance_ws.market_stream import (
    apply_market_payload,
    parse_kline_message,
    parse_mark_price_message,
    parse_mini_ticker_message,
)
from src.exchange.binance_ws.user_stream import apply_user_payload, parse_account_update, parse_order_trade_update
from src.exchange.types import Candle, FuturesAccountBalance, Position


class TestMarketParsers(unittest.TestCase):
    def test_parse_closed_kline_only_applied(self) -> None:
        payload = {
            "e": "kline",
            "s": "BTCUSDT",
            "k": {
                "t": 1_700_000_000_000,
                "o": "1",
                "h": "2",
                "l": "0.5",
                "c": "1.5",
                "v": "10",
                "i": "5m",
                "x": True,
            },
        }
        parsed = parse_kline_message(payload)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        symbol, interval, candle, closed = parsed
        self.assertEqual(symbol, "BTCUSDT")
        self.assertEqual(interval, "5m")
        self.assertTrue(closed)
        self.assertEqual(candle.close, 1.5)

        open_payload = {
            "e": "kline",
            "k": {
                "t": 1_700_000_000_000,
                "o": "1",
                "h": "2",
                "l": "0.5",
                "c": "1.6",
                "v": "11",
                "i": "5m",
                "x": False,
                "s": "BTCUSDT",
            },
        }
        parsed_open = parse_kline_message(open_payload)
        self.assertIsNotNone(parsed_open)
        assert parsed_open is not None
        self.assertFalse(parsed_open[3])

    def test_mini_ticker_quote_volume(self) -> None:
        rows = parse_mini_ticker_message(
            {
                "stream": "!miniTicker@arr",
                "data": [
                    {"s": "ETHUSDT", "q": "12345.5"},
                    {"s": "BTCUSDT", "q": "99999"},
                ],
            }
        )
        self.assertEqual(rows[0], ("ETHUSDT", 12345.5))
        self.assertEqual(rows[1][0], "BTCUSDT")

    def test_mini_ticker_raw_array(self) -> None:
        """Binance /ws SUBSCRIBE pushes !miniTicker@arr as a top-level JSON array."""
        rows = parse_mini_ticker_message(
            [
                {"e": "24hrMiniTicker", "s": "ETHUSDT", "q": "5000"},
                {"e": "24hrMiniTicker", "s": "BTCUSDT", "q": "9000"},
            ]
        )
        self.assertEqual(rows, [("ETHUSDT", 5000.0), ("BTCUSDT", 9000.0)])

    def test_apply_mini_ticker_raw_array_seeds_rank(self) -> None:
        CACHE.quote_volumes.clear()
        CACHE.mini_ticker_seeded = False
        CACHE.market_last_msg_at = 0.0
        CACHE.candle_last_msg_at.clear()
        batch = [{"e": "24hrMiniTicker", "s": f"SYM{i}USDT", "q": str(1000 - i)} for i in range(90)]
        apply_market_payload(batch)
        self.assertTrue(CACHE.mini_ticker_seeded)
        self.assertGreaterEqual(len(CACHE.quote_volumes), 80)
        # miniTicker must NOT count as kline health
        self.assertEqual(CACHE.candle_last_msg_at, {})

    def test_kline_raw_and_combined_bump_candle_health(self) -> None:
        CACHE.candles.clear()
        CACHE.candle_interval.clear()
        CACHE.candle_last_msg_at.clear()
        apply_market_payload(
            {
                "e": "kline",
                "k": {
                    "t": 1_000,
                    "o": "1",
                    "h": "2",
                    "l": "1",
                    "c": "1.5",
                    "v": "3",
                    "i": "5m",
                    "x": True,
                    "s": "BTCUSDT",
                },
            }
        )
        self.assertIn("BTCUSDT", CACHE.candle_last_msg_at)
        age = CACHE.candle_age_sec("BTCUSDT")
        self.assertIsNotNone(age)
        assert age is not None
        self.assertLess(age, 1.0)

        apply_market_payload(
            {
                "stream": "ethusdt@kline_5m",
                "data": {
                    "e": "kline",
                    "k": {
                        "t": 2_000,
                        "o": "1",
                        "h": "2",
                        "l": "1",
                        "c": "1.6",
                        "v": "4",
                        "i": "5m",
                        "x": False,
                        "s": "ETHUSDT",
                    },
                },
            }
        )
        self.assertIn("ETHUSDT", CACHE.candle_last_msg_at)
        rows = CACHE.get_candles("ETHUSDT", "5m", 5)
        self.assertIsNotNone(rows)
        assert rows is not None
        self.assertEqual(rows[-1].close, 1.6)

    def test_mark_price_array(self) -> None:
        rows = parse_mark_price_message(
            {
                "stream": "!markPrice@arr@1s",
                "data": [{"e": "markPriceUpdate", "s": "XRPUSDT", "p": "1.23"}],
            }
        )
        self.assertEqual(rows, [("XRPUSDT", 1.23)])


class TestCacheCandlesAndRank(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = BinanceWsCache()

    def test_upsert_closed_candle_and_get(self) -> None:
        c1 = Candle(timestamp=100, open=1, high=2, low=0.5, close=1.5, volume=1)
        c2 = Candle(timestamp=200, open=1.5, high=2, low=1, close=1.8, volume=2)
        self.cache.set_candles("BTCUSDT", "5m", [c1])
        self.cache.upsert_closed_candle("BTCUSDT", "5m", c2)
        got = self.cache.get_candles("BTCUSDT", "5m", 10)
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(len(got), 2)
        self.assertEqual(got[-1].timestamp, 200)

    def test_rank_sort_parity(self) -> None:
        self.cache.set_quote_volumes({"AAAUSDT": 10, "BBBUSDT": 50, "CCCUSDT": 30}, seeded=True)
        ranked = self.cache.ranked_volumes()
        self.assertEqual([s for s, _ in ranked], ["BBBUSDT", "CCCUSDT", "AAAUSDT"])


class TestApplyMarketPayload(unittest.TestCase):
    def setUp(self) -> None:
        CACHE.candles.clear()
        CACHE.quote_volumes.clear()
        CACHE.marks.clear()
        CACHE.market_last_msg_at = 0.0

    def test_apply_closed_kline_ignores_open(self) -> None:
        apply_market_payload(
            {
                "e": "kline",
                "k": {
                    "t": 2000,
                    "o": "1",
                    "h": "1.2",
                    "l": "0.9",
                    "c": "1.1",
                    "v": "2",
                    "i": "5m",
                    "x": False,
                    "s": "SOLUSDT",
                },
            }
        )
        rows = CACHE.get_candles("SOLUSDT", "5m", 5)
        self.assertIsNotNone(rows)
        assert rows is not None
        self.assertEqual(rows[-1].timestamp, 2000)
        self.assertEqual(rows[-1].close, 1.1)

        apply_market_payload(
            {
                "e": "kline",
                "k": {
                    "t": 10,
                    "o": "1",
                    "h": "2",
                    "l": "1",
                    "c": "1.5",
                    "v": "3",
                    "i": "5m",
                    "x": True,
                    "s": "SOLUSDT",
                },
            }
        )
        rows = CACHE.get_candles("SOLUSDT", "5m", 5)
        # get_candles requires min length — seed enough
        CACHE.set_candles(
            "SOLUSDT",
            "5m",
            [Candle(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(25)],
        )
        apply_market_payload(
            {
                "e": "kline",
                "k": {
                    "t": 1000,
                    "o": "1",
                    "h": "2",
                    "l": "1",
                    "c": "1.5",
                    "v": "3",
                    "i": "5m",
                    "x": True,
                    "s": "SOLUSDT",
                },
            }
        )
        rows = CACHE.get_candles("SOLUSDT", "5m", 30)
        self.assertIsNotNone(rows)
        assert rows is not None
        self.assertEqual(rows[-1].close, 1.5)


class TestVolumeRankFallback(unittest.TestCase):
    def test_fetch_top_futures_skips_rest_when_ws_enabled_and_empty(self) -> None:
        from src.exchange import binance

        binance._volume_rank_rest_at_mono = 0.0
        binance._volume_rank_rest_cache = []
        CACHE.quote_volumes.clear()
        CACHE.mini_ticker_seeded = False
        with (
            patch("src.exchange.binance_ws.manager.is_ws_enabled", return_value=True),
            patch("src.exchange.binance.fetch_top_futures_by_volume_rest") as mock_rest,
            patch("src.exchange.binance.is_optional_rest_blocked", return_value=False),
        ):
            ranked = binance.fetch_top_futures_by_volume(limit=2)
            self.assertEqual(ranked, [])
            mock_rest.assert_not_called()

    def test_fetch_top_futures_rest_when_ws_ticker_seed_enabled(self) -> None:
        from src.exchange import binance

        binance._volume_rank_rest_at_mono = 0.0
        binance._volume_rank_rest_cache = []
        CACHE.quote_volumes.clear()
        CACHE.mini_ticker_seeded = False
        fake = [("BTCUSDT", 1e9), ("ETHUSDT", 5e8)]
        with (
            patch("src.exchange.binance_ws.manager.is_ws_enabled", return_value=True),
            patch("src.config.BINANCE_WS_REST_TICKER_SEED", True),
            patch("src.exchange.binance.fetch_top_futures_by_volume_rest", return_value=fake) as mock_rest,
            patch("src.exchange.binance.is_optional_rest_blocked", return_value=False),
        ):
            ranked = binance.fetch_top_futures_by_volume(limit=2)
            self.assertEqual(ranked, fake)
            mock_rest.assert_called_once()
            self.assertTrue(CACHE.mini_ticker_seeded)

    def test_fetch_top_futures_uses_ws_rank_without_exchange_info_rest(self) -> None:
        from src.exchange import binance

        binance._volume_rank_rest_at_mono = 0.0
        binance._volume_rank_rest_cache = []
        CACHE.set_quote_volumes({"BTCUSDT": 1e9, "ETHUSDT": 5e8}, seeded=True)
        with (
            patch("src.exchange.binance_ws.manager.is_ws_enabled", return_value=True),
            patch("src.exchange.binance._load_exchange_info") as mock_info,
            patch(
                "src.exchange.binance._perpetual_universe_from_disk",
                return_value=None,
            ),
            patch("src.exchange.binance.fetch_top_futures_by_volume_rest") as mock_rest,
            patch("src.exchange.binance._public_get") as mock_get,
        ):
            ranked = binance.fetch_top_futures_by_volume(limit=2)
        self.assertEqual(ranked[0][0], "BTCUSDT")
        mock_info.assert_not_called()
        mock_rest.assert_not_called()
        mock_get.assert_not_called()

    def test_ws_rank_drops_tradifi_via_disk_universe(self) -> None:
        from src.exchange import binance

        binance._volume_rank_rest_at_mono = 0.0
        binance._volume_rank_rest_cache = []
        CACHE.set_quote_volumes(
            {"SNDKUSDT": 2e9, "BTCUSDT": 1e9, "ETHUSDT": 5e8},
            seeded=True,
        )
        with (
            patch("src.exchange.binance_ws.manager.is_ws_enabled", return_value=True),
            patch(
                "src.exchange.binance._perpetual_universe_from_disk",
                return_value={"BTCUSDT", "ETHUSDT"},
            ),
            patch("src.exchange.binance._load_exchange_info") as mock_info,
            patch("src.exchange.binance.fetch_top_futures_by_volume_rest") as mock_rest,
            patch("src.exchange.binance._public_get") as mock_get,
        ):
            ranked = binance.fetch_top_futures_by_volume(limit=5)
        self.assertEqual([s for s, _ in ranked], ["BTCUSDT", "ETHUSDT"])
        mock_info.assert_not_called()
        mock_rest.assert_not_called()
        mock_get.assert_not_called()

    def test_scan_universe_excludes_tradifi_perpetual(self) -> None:
        from src.exchange import binance

        info = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "contractType": "PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                },
                {
                    "symbol": "SNDKUSDT",
                    "contractType": "TRADIFI_PERPETUAL",
                    "status": "TRADING",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                },
            ]
        }
        universe = binance._scan_universe_from_info(info)
        self.assertIn("BTCUSDT", universe)
        self.assertNotIn("SNDKUSDT", universe)

    def test_fetch_top_futures_uses_rest_cache_within_throttle(self) -> None:
        from src.exchange import binance
        import time

        fake = [("BTCUSDT", 1e9)]
        binance._volume_rank_rest_cache = fake
        binance._volume_rank_rest_at_mono = time.monotonic()
        CACHE.quote_volumes.clear()
        CACHE.mini_ticker_seeded = False
        with (
            patch("src.exchange.binance_ws.manager.is_ws_enabled", return_value=True),
            patch("src.exchange.binance.fetch_top_futures_by_volume_rest") as mock_rest,
            patch("src.exchange.binance.is_rate_limited", return_value=False),
        ):
            ranked = binance.fetch_top_futures_by_volume(limit=1)
            self.assertEqual(ranked, fake)
            mock_rest.assert_not_called()


class TestUserDataParsers(unittest.TestCase):
    def test_account_update_positions(self) -> None:
        updates, closed, balances = parse_account_update(
            {
                "e": "ACCOUNT_UPDATE",
                "a": {
                    "B": [{"a": "USDT", "wb": "1000", "cw": "1000"}],
                    "P": [
                        {"s": "BTCUSDT", "pa": "0.1", "ep": "50000", "up": "1.5", "ps": "LONG"},
                        {"s": "BTCUSDT", "pa": "0", "ep": "0", "up": "0", "ps": "SHORT"},
                    ],
                },
            }
        )
        self.assertEqual(balances["USDT"], 1000.0)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].side, "long")
        self.assertEqual(closed, [("BTCUSDT", "short")])

    def test_order_trade_filled(self) -> None:
        detail, pending, remove = parse_order_trade_update(
            {
                "e": "ORDER_TRADE_UPDATE",
                "o": {
                    "i": 99,
                    "X": "FILLED",
                    "ap": "1.1",
                    "s": "ETHUSDT",
                    "o": "MARKET",
                    "S": "BUY",
                    "p": "0",
                    "q": "1",
                    "c": "oid",
                },
            }
        )
        self.assertEqual(detail["orderId"], "99")
        self.assertEqual(detail["status"], "filled")
        self.assertIsNone(pending)
        self.assertFalse(remove)

    def test_apply_account_update_to_cache(self) -> None:
        CACHE.set_positions([], {})
        CACHE.set_balance(
            FuturesAccountBalance(
                margin_coin="USDT",
                available=500,
                account_equity=1000,
                usdt_equity=1000,
                total_maint_margin=10,
                total_initial_margin=20,
            )
        )
        apply_user_payload(
            {
                "e": "ACCOUNT_UPDATE",
                "a": {
                    "B": [{"a": "USDT", "wb": "900"}],
                    "P": [{"s": "AAVEUSDT", "pa": "2", "ep": "100", "up": "0.5", "ps": "LONG"}],
                },
            }
        )
        sides = CACHE.get_symbol_positions("AAVEUSDT")
        self.assertIsNotNone(sides)
        assert sides is not None
        self.assertEqual(sides["long"].size, 2.0)
        bal = CACHE.get_balance()
        self.assertIsNotNone(bal)
        assert bal is not None
        # UDS wb must not overwrite REST equity/available (wallet ≠ margin balance).
        self.assertEqual(bal.available, 500)
        self.assertEqual(bal.account_equity, 1000)
        self.assertEqual(bal.usdt_equity, 1000)
        self.assertEqual(bal.total_maint_margin, 10)
        self.assertEqual(bal.total_initial_margin, 20)


class TestWsFetchFallback(unittest.TestCase):
    def test_fetch_candles_falls_back_to_rest(self) -> None:
        from src.exchange import binance

        fake = [Candle(timestamp=i * 1000, open=1, high=1, low=1, close=1, volume=1) for i in range(30)]
        with (
            patch("src.exchange.binance_ws.manager.is_ws_enabled", return_value=True),
            patch("src.exchange.binance_ws.get_candles_from_ws", return_value=None),
            patch("src.exchange.binance.fetch_candles_rest", return_value=fake) as mock_rest,
        ):
            out = binance.fetch_candles("BTCUSDT", granularity="5m", limit=30)
            self.assertEqual(len(out), 30)
            mock_rest.assert_called_once()

    def test_fetch_mark_uses_ws_when_available(self) -> None:
        from src.exchange import binance

        with (
            patch("src.exchange.binance_ws.get_mark_from_ws", return_value=42.5),
            patch("src.exchange.binance._public_get") as mock_get,
        ):
            mark = binance.fetch_side_mark_price("BTCUSDT")
            self.assertEqual(mark, 42.5)
            mock_get.assert_not_called()


class TestRateLimitPersist(unittest.TestCase):
    def test_persist_and_restore_cooldown(self) -> None:
        from src.exchange import binance
        import tempfile
        from pathlib import Path

        until = binance._now_ms() + 120_000
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binance_rate_limit_until_ms"
            old = binance._RATE_LIMIT_FILE
            old_until = binance._rate_limited_until_ms
            try:
                binance._RATE_LIMIT_FILE = path
                binance._rate_limited_until_ms = 0.0
                with patch("src.notify.notify_error"):
                    binance._set_rate_limited_until(until, kind="padded")
                self.assertTrue(path.is_file())
                binance._rate_limited_until_ms = 0.0
                binance._rate_limit_kind = "ban"
                binance._load_persisted_rate_limit()
                self.assertGreater(binance.rate_limit_remaining_sec(), 60.0)
                self.assertEqual(binance._rate_limit_kind, "padded")
            finally:
                binance._RATE_LIMIT_FILE = old
                binance._rate_limited_until_ms = old_until
                if old_until <= binance._now_ms():
                    binance._clear_persisted_rate_limit()


class TestPostOrderReconcileDebounce(unittest.TestCase):
    def setUp(self) -> None:
        from src.exchange.binance_ws import manager as mgr

        self.mgr = mgr
        with mgr._lock:
            mgr._pending_reconcile = False
            mgr._pending_reconcile_symbols.clear()
        with CACHE.lock:
            CACHE.positions_updated_at = 0.0
            CACHE.account_updated_at = 0.0

    def tearDown(self) -> None:
        with self.mgr._lock:
            self.mgr._pending_reconcile = False
            self.mgr._pending_reconcile_symbols.clear()

    def test_n_orders_at_most_one_reconcile(self) -> None:
        with (
            patch.object(self.mgr, "is_ws_enabled", return_value=True),
            patch.object(self.mgr, "reconcile_account_state") as mock_rec,
            patch.object(self.mgr, "watch_symbols"),
        ):
            self.mgr.on_order_placed("BTCUSDT")
            self.mgr.on_order_placed("BTCUSDT")
            self.mgr.on_order_placed("ETHUSDT")
            mock_rec.assert_not_called()
            self.assertTrue(self.mgr.pending_reconcile())
            with patch.object(self.mgr, "_reconcile_symbols_rest", return_value=True) as mock_sym:
                used_rest = self.mgr.flush_pending_reconcile(wait_uds_sec=0.0)
            self.assertTrue(used_rest)
            mock_rec.assert_not_called()
            mock_sym.assert_called_once()
            self.assertFalse(self.mgr.pending_reconcile())
            # Second flush is no-op
            self.mgr.flush_pending_reconcile(wait_uds_sec=0.0)
            mock_sym.assert_called_once()

    def test_uds_fresh_skips_rest_reconcile(self) -> None:
        from src.exchange.binance_ws.cache import _now

        with CACHE.lock:
            now = _now()
            CACHE.positions_updated_at = now
            CACHE.account_updated_at = now

        with (
            patch.object(self.mgr, "is_ws_enabled", return_value=True),
            patch.object(self.mgr, "reconcile_account_state") as mock_rec,
            patch.object(self.mgr, "watch_symbols"),
        ):
            # Mark pending without wiping freshness (simulate UDS already landed)
            with self.mgr._lock:
                self.mgr._pending_reconcile = True
                self.mgr._pending_reconcile_symbols.add("BTCUSDT")
            used_rest = self.mgr.flush_pending_reconcile(wait_uds_sec=0.2)
            self.assertFalse(used_rest)
            mock_rec.assert_not_called()
            self.assertFalse(self.mgr.pending_reconcile())

    def test_on_order_placed_does_not_force_reconcile(self) -> None:
        with (
            patch.object(self.mgr, "is_ws_enabled", return_value=True),
            patch.object(self.mgr, "reconcile_account_state") as mock_rec,
            patch.object(self.mgr, "watch_symbols") as mock_watch,
        ):
            self.mgr.on_order_placed("XRPUSDT")
            mock_rec.assert_not_called()
            mock_watch.assert_called_once()
            self.assertTrue(self.mgr.pending_reconcile())
            with CACHE.lock:
                self.assertEqual(CACHE.positions_updated_at, 0.0)

class TestCandleStaleness(unittest.TestCase):
    def test_is_candle_series_stale(self) -> None:
        from src.candles import is_candle_series_stale
        from src.bitget_client import Candle

        base = Candle(timestamp=4000, open=1, high=1, low=1, close=1, volume=1)
        fresh = Candle(timestamp=5000, open=1, high=1, low=1, close=1, volume=1)
        with patch("src.candles.expected_last_closed_ts_ms", return_value=5000):
            self.assertTrue(is_candle_series_stale([base], interval_minutes=5))
            self.assertFalse(is_candle_series_stale([base, fresh], interval_minutes=5))

    def test_get_candles_from_ws_skips_stale_cache(self) -> None:
        from src.exchange.binance_ws import get_candles_from_ws

        CACHE.set_candles(
            "BTCUSDT",
            "5m",
            [
                Candle(timestamp=i * 300_000, open=1, high=1, low=1, close=1, volume=1)
                for i in range(30)
            ],
        )
        with (
            patch("src.exchange.binance_ws.manager.is_ws_enabled", return_value=True),
            patch("src.exchange.binance_ws.manager.watch_symbols"),
            patch("src.candles.expected_last_closed_ts_ms", return_value=9_000_000),
        ):
            self.assertIsNone(get_candles_from_ws("BTCUSDT", "5m", 25))


class TestBootReconcileSkip(unittest.TestCase):
    def setUp(self) -> None:
        from src.exchange.binance_ws import manager as mgr

        self.mgr = mgr
        with mgr._lock:
            mgr._uds_connect_count = 0
        with CACHE.lock:
            CACHE.balance = None
            CACHE.positions_by_symbol.clear()
            CACHE.all_positions.clear()
            CACHE.positions_updated_at = 0.0
            CACHE.account_updated_at = 0.0
            CACHE.last_reconcile_at = 0.0
            CACHE.user_connected = False
            CACHE.user_last_msg_at = 0.0

    def test_load_account_snapshot_marks_reconciled(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from src.exchange.binance_ws import persist as persist_mod

        payload = {
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "long",
                    "size": 0.1,
                    "avg_price": 50000,
                    "unrealized_pnl": 1.0,
                }
            ],
            "balance": {
                "margin_coin": "USDT",
                "available": 100,
                "account_equity": 1000,
                "usdt_equity": 1000,
                "total_maint_margin": 10,
                "total_initial_margin": 20,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            acct = Path(tmp) / "binance_ws_account.json"
            acct.write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(persist_mod, "_ACCOUNT_FILE", acct):
                self.assertTrue(persist_mod.load_account_snapshot())
        self.assertGreater(CACHE.last_reconcile_at, 0.0)
        self.assertIsNotNone(CACHE.balance)
        self.assertGreater(CACHE.positions_updated_at, 0.0)

    def test_first_uds_connect_skips_rest_when_disk_cache(self) -> None:
        CACHE.set_balance(
            FuturesAccountBalance(
                margin_coin="USDT",
                available=100,
                account_equity=1000,
                usdt_equity=1000,
                total_maint_margin=10,
                total_initial_margin=20,
            )
        )
        CACHE.set_positions([], {})
        CACHE.mark_reconciled()
        with (
            patch("src.exchange.binance.is_rate_limited", return_value=False),
            patch.object(self.mgr, "reconcile_account_state") as mock_rec,
        ):
            self.mgr._on_user_stream_connected()
            mock_rec.assert_not_called()
            self.assertEqual(self.mgr._uds_connect_count, 1)

    def test_first_uds_connect_skips_rest_even_without_cache(self) -> None:
        """Cold start without disk: deferred path reconciles later, not UDS handshake."""
        with (
            patch("src.exchange.binance.is_rate_limited", return_value=False),
            patch.object(self.mgr, "reconcile_account_state") as mock_rec,
        ):
            self.mgr._on_user_stream_connected()
            mock_rec.assert_not_called()
            self.assertEqual(self.mgr._uds_connect_count, 1)

    def test_first_uds_connect_skips_rest_when_rate_limited(self) -> None:
        CACHE.set_balance(
            FuturesAccountBalance(
                margin_coin="USDT",
                available=100,
                account_equity=1000,
                usdt_equity=1000,
                total_maint_margin=10,
                total_initial_margin=20,
            )
        )
        CACHE.mark_reconciled()
        with (
            patch("src.exchange.binance.is_optional_rest_blocked", return_value=True),
            patch("src.exchange.binance.optional_rest_blocked_sec", return_value=120.0),
            patch.object(self.mgr, "reconcile_account_state") as mock_rec,
        ):
            self.mgr._on_user_stream_connected()
            mock_rec.assert_not_called()

    def test_second_uds_connect_force_reconciles(self) -> None:
        CACHE.set_balance(
            FuturesAccountBalance(
                margin_coin="USDT",
                available=100,
                account_equity=1000,
                usdt_equity=1000,
                total_maint_margin=10,
                total_initial_margin=20,
            )
        )
        CACHE.mark_reconciled()
        with (
            patch("src.exchange.binance.is_rate_limited", return_value=False),
            patch.object(self.mgr, "reconcile_account_state") as mock_rec,
        ):
            self.mgr._on_user_stream_connected()  # first: skip REST
            self.mgr._on_user_stream_connected()  # reconnect: REST once
            self.assertEqual(mock_rec.call_count, 1)
            mock_rec.assert_called_with(force=False)

    def test_positions_fresh_while_uds_alive(self) -> None:
        from src.exchange.binance_ws.cache import _now

        with CACHE.lock:
            CACHE.positions_updated_at = _now() - 200.0
            CACHE.user_connected = True
            CACHE.user_last_msg_at = _now()
        self.assertTrue(self.mgr.positions_fresh())


class TestUserStreamRecvTimeout(unittest.TestCase):
    def test_timeout_error_str_is_empty(self) -> None:
        """Documents why empty 'user WS error:' appeared in Railway logs."""
        self.assertEqual(str(TimeoutError()), "")


class TestWatchSetPrune(unittest.TestCase):
    def test_set_watched_symbols_replaces(self) -> None:
        from src.exchange.binance_ws import manager as mgr

        mgr.watch_symbols(["AAAUSDT", "BBBUSDT"])
        mgr.set_watched_symbols(["AAAUSDT", "CCCUSDT"])
        self.assertEqual(mgr.watched_symbols(), {"AAAUSDT", "CCCUSDT"})


class TestCandleRestCooldown(unittest.TestCase):
    def setUp(self) -> None:
        from src.exchange import binance

        binance._candle_rest_at_mono.clear()
        binance._last_candle_rest_mono = 0.0
        CACHE.candles.clear()
        CACHE.candle_interval.clear()
        CACHE.candle_last_msg_at.clear()

    def test_second_fetch_within_cooldown_skips_rest_when_fresh(self) -> None:
        from src.exchange import binance
        from src.candles import expected_last_closed_ts_ms

        fake = [
            Candle(timestamp=i * 300_000, open=1, high=1, low=1, close=1, volume=1)
            for i in range(30)
        ]
        # Make last closed match expected so series is not stale after REST seed.
        expected = expected_last_closed_ts_ms(5)
        fake[-1] = Candle(
            timestamp=expected, open=1, high=1, low=1, close=1.2, volume=1
        )
        with (
            patch("src.exchange.binance_ws.manager.is_ws_enabled", return_value=True),
            patch("src.exchange.binance_ws.get_candles_from_ws", return_value=None),
            patch("src.exchange.binance_ws.manager.watch_symbols"),
            patch("src.exchange.binance.fetch_candles_rest", return_value=fake) as mock_rest,
            patch("src.exchange.binance.is_rate_limited", return_value=False),
            patch("src.exchange.binance_ws.persist.save_candles_snapshot"),
        ):
            out1 = binance.fetch_candles("BTCUSDT", granularity="5m", limit=25)
            out2 = binance.fetch_candles("BTCUSDT", granularity="5m", limit=25)
            self.assertGreaterEqual(len(out1), 25)
            self.assertGreaterEqual(len(out2), 25)
            mock_rest.assert_called_once()


if __name__ == "__main__":
    unittest.main()
