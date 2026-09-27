import unittest
from unittest.mock import patch

from src.exchange.types import ContractSpec
from src.donchian.trading import _tp_prices_match, on_tp_limit_order_update, sync_tp_limit


SPEC = ContractSpec(
    symbol="BTCUSDT",
    volume_place=3,
    price_place=2,
    min_trade_num=0.001,
    min_trade_usdt=5,
    size_multiplier=0.001,
)


class TestTpPriceMatch(unittest.TestCase):
    def test_same_tick_skips(self):
        self.assertTrue(_tp_prices_match(100.12, 100.12, 2))

    def test_new_tick_replaces(self):
        self.assertFalse(_tp_prices_match(100.12, 100.13, 2))


class TestPlaceLimitClose(unittest.TestCase):
    @patch("src.exchange.binance._private_post")
    def test_hedge_limit_has_no_reduce_only(self, private_post):
        from src.exchange.binance import place_limit_close_order

        private_post.return_value = {
            "orderId": 9,
            "clientOrderId": "btp_x",
            "status": "NEW",
            "avgPrice": "0",
        }
        place_limit_close_order("BTCUSDT", "long", "0.01", "100.50")
        params = private_post.call_args[0][1]
        self.assertEqual(params["type"], "LIMIT")
        self.assertEqual(params["timeInForce"], "GTC")
        self.assertEqual(params["side"], "SELL")
        self.assertEqual(params["positionSide"], "LONG")
        self.assertEqual(params["price"], "100.50")
        self.assertNotIn("reduceOnly", params)


class TestSyncTpLimit(unittest.TestCase):
    def _lot_row(self, **overrides):
        base = {
            "id": 1,
            "symbol": "BTCUSDT",
            "side": "long",
            "status": "open",
            "size": 0.01,
            "entry_px": 100.0,
            "tp_band": 101.0,
            "opened_at": "2026-09-26T00:00:00+00:00",
            "tp_limit_order_id": "",
            "tp_limit_px": None,
        }
        base.update(overrides)
        return base

    @patch("src.donchian.trading.has_credentials", return_value=True)
    @patch("src.donchian.trading.is_trading_enabled", return_value=True)
    @patch("src.donchian.trading.fetch_contract_spec", return_value=SPEC)
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.exchange.binance.place_limit_close_order")
    def test_same_price_does_not_replace(self, place, get_lot, _spec, _on, _cred):
        get_lot.return_value = self._lot_row(tp_limit_order_id="55", tp_limit_px=101.25)
        sync_tp_limit(self._lot_row(), 101.25)
        place.assert_not_called()

    @patch("src.donchian.trading.has_credentials", return_value=True)
    @patch("src.donchian.trading.is_trading_enabled", return_value=True)
    @patch("src.donchian.trading.fetch_contract_spec", return_value=SPEC)
    @patch("src.donchian.trading._held_size", return_value=0.01)
    @patch("src.donchian.trading.store.set_tp_limit")
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.exchange.binance.place_limit_close_order")
    def test_places_when_missing(self, place, get_lot, set_tp, _held, _spec, _on, _cred):
        get_lot.return_value = self._lot_row()
        place.return_value = {"orderId": "77", "status": "new", "avgPrice": "0"}
        sync_tp_limit(self._lot_row(), 101.256)
        place.assert_called_once()
        args = place.call_args[0]
        self.assertEqual(args[0], "BTCUSDT")
        self.assertEqual(args[1], "long")
        self.assertEqual(args[3], "101.26")
        set_tp.assert_called_once_with(1, "77", 101.26)

    @patch("src.donchian.trading._finalize_close", return_value=True)
    @patch("src.donchian.trading._fee_from_order", return_value=0.01)
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.donchian.trading.store.find_open_lot_by_tp_order")
    def test_fill_closes_lot(self, find_lot, get_lot, _fee, finalize):
        lot = self._lot_row(tp_limit_order_id="77", tp_limit_px=101.26)
        find_lot.return_value = lot
        get_lot.return_value = lot
        on_tp_limit_order_update(
            {"orderId": "77", "status": "filled", "avgPrice": "101.26"}
        )
        finalize.assert_called_once()
        kwargs = finalize.call_args.kwargs
        self.assertEqual(kwargs["fill"], 101.26)
        self.assertEqual(kwargs["close_oid"], "77")
        self.assertEqual(kwargs["reason"], "TP_BAND")
