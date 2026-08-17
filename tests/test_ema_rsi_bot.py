import unittest
from unittest.mock import MagicMock, patch

from src.ema_rsi.trading import (
    can_open_symbol,
    compute_margin_usdt,
    live_account_balance,
    realized_pnl,
)
from src.exchange.types import Position


class TestSizingAndPnl(unittest.TestCase):
    def test_margin_is_one_percent(self) -> None:
        with patch("src.ema_rsi.trading.MARGIN_PCT", 1.0), patch(
            "src.ema_rsi.trading.MARGIN_MIN_USDT", 1.0
        ):
            self.assertEqual(compute_margin_usdt(1000), 10.0)

    def test_realized_pnl_sides(self) -> None:
        self.assertEqual(realized_pnl("long", 100, 110, 2), 20)
        self.assertEqual(realized_pnl("short", 100, 90, 2), 20)

    def test_live_balance_uses_rest_not_ws_cache(self) -> None:
        from src.exchange.types import FuturesAccountBalance

        rest_bal = FuturesAccountBalance(
            margin_coin="USDT",
            available=80.0,
            account_equity=123.45,
            usdt_equity=123.45,
        )
        with (
            patch("src.config.EXCHANGE", "binance"),
            patch("src.exchange.binance.is_optional_rest_blocked", return_value=False),
            patch("src.exchange.binance.fetch_futures_balance_rest", return_value=rest_bal) as rest,
            patch("src.ema_rsi.trading.fetch_futures_balance") as ws_bal,
            patch("src.exchange.binance_ws.cache.CACHE.set_balance"),
            patch("src.exchange.binance_ws.persist.save_account_snapshot"),
        ):
            got = live_account_balance("BTCUSDT")
            self.assertEqual(got.account_equity, 123.45)
            rest.assert_called_once()
            ws_bal.assert_not_called()

    def test_live_balance_skips_when_rest_blocked(self) -> None:
        from src.exchange import ExchangeClientError

        with (
            patch("src.config.EXCHANGE", "binance"),
            patch("src.exchange.binance.is_optional_rest_blocked", return_value=True),
            patch("src.exchange.binance.optional_rest_blocked_sec", return_value=30.0),
        ):
            with self.assertRaises(ExchangeClientError):
                live_account_balance("BTCUSDT")


class TestCanOpenSymbol(unittest.TestCase):
    def test_skip_when_max_open(self) -> None:
        with (
            patch("src.ema_rsi.trading.MAX_OPEN", 20),
            patch("src.ema_rsi.store.count_open", return_value=20),
        ):
            self.assertFalse(can_open_symbol("BTCUSDT", occupied=set()))

    def test_skip_when_db_open_or_exchange_position(self) -> None:
        with (
            patch("src.ema_rsi.trading.MAX_OPEN", 20),
            patch("src.ema_rsi.store.count_open", return_value=1),
            patch("src.ema_rsi.store.get_open_trade_for_symbol", return_value={"id": 1}),
        ):
            self.assertFalse(can_open_symbol("BTCUSDT", occupied=set()))

        with (
            patch("src.ema_rsi.trading.MAX_OPEN", 20),
            patch("src.ema_rsi.store.count_open", return_value=1),
            patch("src.ema_rsi.store.get_open_trade_for_symbol", return_value=None),
        ):
            self.assertFalse(can_open_symbol("BTCUSDT", occupied={"BTCUSDT"}))
            self.assertTrue(can_open_symbol("ETHUSDT", occupied={"BTCUSDT"}))

    def test_occupied_includes_exchange_positions(self) -> None:
        from src.ema_rsi.trading import occupied_symbols

        with (
            patch("src.ema_rsi.store.get_open_trades", return_value=[]),
            patch(
                "src.ema_rsi.trading.fetch_all_open_positions",
                return_value=[
                    Position(symbol="BTCUSDT", side="short", size=1.0, avg_price=1.0),
                ],
            ),
        ):
            self.assertIn("BTCUSDT", occupied_symbols())


class TestOpenErrorNotify(unittest.TestCase):
    def test_open_failed_notifies_discord(self) -> None:
        from src.ema_rsi.signals import EntrySignal
        from src.ema_rsi.trading import open_signal
        from src.exchange import ExchangeClientError

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
            patch("src.ema_rsi.trading.is_trading_enabled", return_value=True),
            patch("src.ema_rsi.trading.has_credentials", return_value=True),
            patch("src.ema_rsi.trading.can_open_symbol", return_value=True),
            patch("src.ema_rsi.store.mark_signal_seen", return_value=True),
            patch(
                "src.ema_rsi.trading.configure_symbol_trading",
                side_effect=ExchangeClientError("timeout"),
            ),
            patch("src.ema_rsi.trading.notify_error") as nerr,
        ):
            self.assertIsNone(open_signal("BTCUSDT", signal))
            nerr.assert_called_once()
            self.assertEqual(nerr.call_args[0][0], "EMA-RSI open BTCUSDT")
            self.assertIn("timeout", nerr.call_args[0][1])


class TestMainEntrypoint(unittest.TestCase):
    def test_main_delegates_to_ema_rsi(self) -> None:
        from src import main as main_mod

        with patch("src.ema_rsi.cycle.main") as ema_main:
            main_mod.main()
            ema_main.assert_called_once()


class TestAlgoOrders(unittest.TestCase):
    @patch("src.exchange.binance._private_post")
    def test_stop_market_close_position(self, private_post: MagicMock) -> None:
        from src.exchange.binance import place_algo_close_order

        private_post.return_value = {"algoId": 9, "clientAlgoId": "ersl1", "algoStatus": "NEW"}
        place_algo_close_order(
            "BTCUSDT",
            hold_side="long",
            order_type="STOP_MARKET",
            stop_price="90.0",
            client_oid="ersl1",
        )
        params = private_post.call_args[0][1]
        self.assertEqual(private_post.call_args[0][0], "/fapi/v1/algoOrder")
        self.assertEqual(params["algoType"], "CONDITIONAL")
        self.assertEqual(params["type"], "STOP_MARKET")
        self.assertEqual(params["side"], "SELL")
        self.assertEqual(params["positionSide"], "LONG")
        self.assertEqual(params["closePosition"], "true")
        self.assertEqual(params["workingType"], "CONTRACT_PRICE")
        self.assertEqual(params["triggerPrice"], "90.0")
        self.assertEqual(params["clientAlgoId"], "ersl1")
        self.assertNotIn("stopPrice", params)


if __name__ == "__main__":
    unittest.main()
