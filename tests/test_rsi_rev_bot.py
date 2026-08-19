import unittest
from unittest.mock import MagicMock, patch

from src.exchange.types import FuturesAccountBalance
from src.rsi_rev.trading import _real_order_id, compute_margin_usdt, realized_pnl, try_open


class TestRsiRevSizing(unittest.TestCase):
    def test_margin_is_half_percent(self) -> None:
        with patch("src.rsi_rev.trading.MARGIN_PCT", 0.5):
            self.assertEqual(compute_margin_usdt(1000), 5.0)

    def test_realized_pnl_sides(self) -> None:
        self.assertEqual(realized_pnl("long", 100, 110, 2), 20)
        self.assertEqual(realized_pnl("short", 100, 90, 2), 20)

    def test_real_order_id_rejects_mock(self) -> None:
        self.assertEqual(_real_order_id(MagicMock()), "")
        self.assertEqual(_real_order_id({"orderId": MagicMock()}), "")
        self.assertEqual(_real_order_id({"orderId": 12345}), "12345")


class TestMainEntrypoint(unittest.TestCase):
    def test_main_delegates_to_rsi_rev(self) -> None:
        from src import main as main_mod

        with patch("src.rsi_rev.cycle.main") as rsi_main:
            main_mod.main()
            rsi_main.assert_called_once()


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


class TestOpenSkip(unittest.TestCase):
    def test_open_skips_when_available_below_margin(self) -> None:
        from src.rsi_rev.signals import EntryTrigger, ZONE_RSI70

        trigger = EntryTrigger(
            side="long",
            entry=10.0,
            tp=9.975,
            anchor_ts=1,
            anchor_price=10.05,
            anchor_rsi=71.0,
            zone=ZONE_RSI70,
            signal_ts=2,
        )
        bal = FuturesAccountBalance(
            margin_coin="USDT",
            available=5.0,
            account_equity=1000.0,
            usdt_equity=1000.0,
        )
        with (
            patch("src.rsi_rev.trading.is_trading_enabled", return_value=True),
            patch("src.rsi_rev.trading.has_credentials", return_value=True),
            patch("src.rsi_rev.trading.MAX_OPEN", 0),
            patch("src.rsi_rev.trading.MARGIN_PCT", 1.0),
            patch("src.rsi_rev.store.count_open", return_value=0),
            patch("src.rsi_rev.store.has_open_lot", return_value=False),
            patch("src.rsi_rev.trading.configure_symbol_trading"),
            patch("src.rsi_rev.trading.live_account_balance", return_value=bal),
            patch("src.rsi_rev.store.record_skip") as skip,
            patch("src.rsi_rev.store.insert_lot") as insert_lot,
            patch("src.rsi_rev.trading.place_market_order") as place,
        ):
            status = try_open("LINKUSDT", trigger)
            self.assertEqual(status, "cap_skip")
            skip.assert_called_once()
            place.assert_not_called()
            insert_lot.assert_not_called()


class TestWarmupCandles(unittest.TestCase):
    def test_ready_when_ws_has_enough_closed_bars(self) -> None:
        import time as time_mod

        from src.exchange.types import Candle
        from src.rsi_rev.candles import warmup_symbol_candles

        now = int(time_mod.time() * 1000)
        bars = [
            Candle(
                timestamp=now - (i + 2) * 300_000,
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=1.0,
            )
            for i in range(25)
        ]
        with patch("src.rsi_rev.candles.fetch_candles", return_value=bars) as fetch:
            self.assertEqual(warmup_symbol_candles("LINKUSDT"), "ready")
            fetch.assert_called_once()
            self.assertTrue(fetch.call_args.kwargs.get("ws_only"))

    def test_blocked_when_optional_rest_blocked(self) -> None:
        from src.exchange.types import ExchangeClientError
        from src.rsi_rev.candles import warmup_symbol_candles

        with (
            patch(
                "src.rsi_rev.candles.fetch_candles",
                side_effect=ExchangeClientError("no ws"),
            ),
            patch("src.rsi_rev.candles.EXCHANGE", "binance"),
            patch("src.exchange.binance.is_optional_rest_blocked", return_value=True),
            patch("src.exchange.binance.optional_rest_blocked_sec", return_value=40),
            patch("src.exchange.binance.boot_optional_rest_slot") as slot,
        ):
            self.assertEqual(warmup_symbol_candles("SUIUSDT"), "blocked")
            slot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
