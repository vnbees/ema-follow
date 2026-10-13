import unittest
from unittest.mock import patch

from src.exchange.types import ContractSpec, ExchangeClientError, PendingOrder
from src.donchian import trading
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

    @patch("src.donchian.trading._matching_close_limit", return_value=None)
    @patch("src.donchian.trading.has_credentials", return_value=True)
    @patch("src.donchian.trading.is_trading_enabled", return_value=True)
    @patch("src.donchian.trading.fetch_contract_spec", return_value=SPEC)
    @patch("src.donchian.trading._held_size", return_value=0.01)
    @patch("src.donchian.trading.store.set_tp_limit")
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.exchange.binance.place_limit_close_order")
    def test_places_when_missing(self, place, get_lot, set_tp, _held, _spec, _on, _cred, _match):
        get_lot.return_value = self._lot_row()
        place.return_value = {"orderId": "77", "status": "new", "avgPrice": "0"}
        sync_tp_limit(self._lot_row(), 101.256)
        place.assert_called_once()
        args = place.call_args[0]
        self.assertEqual(args[0], "BTCUSDT")
        self.assertEqual(args[1], "long")
        self.assertEqual(args[3], "101.26")
        set_tp.assert_called_once_with(1, "77", 101.26, 0.01)

    @patch("src.donchian.trading.has_credentials", return_value=True)
    @patch("src.donchian.trading.is_trading_enabled", return_value=True)
    @patch("src.donchian.trading.fetch_contract_spec", return_value=SPEC)
    @patch("src.donchian.trading._held_size", return_value=9.0)
    @patch("src.donchian.trading.store.set_tp_limit")
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.exchange.binance.place_limit_close_order")
    def test_reduce_only_adopts_resting_limit(self, place, get_lot, set_tp, _held, _spec, _on, _cred):
        trading._tp_retry_after.clear()
        get_lot.return_value = self._lot_row(symbol="UNIUSDT", side="short", size=9)
        place.side_effect = ExchangeClientError(
            "HTTP 400: code=-2022 msg=ReduceOnly Order is rejected."
        )
        resting = PendingOrder("900", "btp_old", "buy", 7.14, 9)

        def match(_symbol, _side, _px, _place, *, rest):
            return resting if rest else None

        with patch("src.donchian.trading._matching_close_limit", side_effect=match):
            sync_tp_limit(self._lot_row(symbol="UNIUSDT", side="short", size=9), 7.14)
        set_tp.assert_called_once_with(1, "900", 7.14, 9.0)
        place.assert_called_once()

    @patch("src.donchian.trading._matching_close_limit", return_value=None)
    @patch("src.donchian.trading.has_credentials", return_value=True)
    @patch("src.donchian.trading.is_trading_enabled", return_value=True)
    @patch("src.donchian.trading.fetch_contract_spec", return_value=SPEC)
    @patch("src.donchian.trading._held_size", return_value=9.0)
    @patch("src.donchian.trading.notify_error")
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.exchange.binance.place_limit_close_order")
    def test_rejected_place_waits_before_retry(self, place, get_lot, _notify, _held, _spec, _on, _cred, _match):
        trading._tp_retry_after.clear()
        get_lot.return_value = self._lot_row()
        place.side_effect = ExchangeClientError(
            "HTTP 400: code=-2022 msg=ReduceOnly Order is rejected."
        )
        sync_tp_limit(self._lot_row(), 101.25)
        sync_tp_limit(self._lot_row(), 101.25)
        place.assert_called_once()

    @patch("src.donchian.trading._finalize_close", return_value=True)
    @patch("src.donchian.trading._fee_from_order", return_value=0.01)
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.donchian.trading.store.find_open_lots_by_tp_order")
    def test_fill_closes_lot(self, find_lot, get_lot, _fee, finalize):
        lot = self._lot_row(tp_limit_order_id="77", tp_limit_px=101.26)
        find_lot.return_value = [lot]
        get_lot.return_value = lot
        on_tp_limit_order_update(
            {"orderId": "77", "status": "filled", "avgPrice": "101.26"}
        )
        finalize.assert_called_once()
        kwargs = finalize.call_args.kwargs
        self.assertEqual(kwargs["fill"], 101.26)
        self.assertEqual(kwargs["close_oid"], "77")
        self.assertEqual(kwargs["reason"], "TP_BAND")

    @patch("src.donchian.trading._finalize_close", return_value=True)
    @patch("src.donchian.trading._fee_from_order", return_value=0.03)
    @patch("src.donchian.trading.store.get_open_lots")
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.donchian.trading.store.find_open_lots_by_tp_order")
    def test_shared_fill_closes_every_covered_lot(self, find_lots, get_lot, open_lots, _fee, finalize):
        a = self._lot_row(id=1, size=0.01, tp_limit_order_id="77", tp_limit_px=101.26)
        b = self._lot_row(id=2, size=0.02, tp_limit_order_id="")
        rows = {1: a, 2: b}
        find_lots.return_value = [a]
        get_lot.side_effect = lambda lot_id: rows[lot_id]
        open_lots.return_value = [a, b]
        on_tp_limit_order_update(
            {"orderId": "77", "status": "filled", "avgPrice": "101.26", "executedQty": "0.03"}
        )
        self.assertEqual(finalize.call_count, 2)
        fees = sorted(round(c.kwargs["fee_close"], 4) for c in finalize.call_args_list)
        self.assertEqual(fees, [0.01, 0.02])

    @patch("src.donchian.trading._tp_order_dead", return_value=False)
    @patch("src.donchian.trading._matching_close_limit", return_value=None)
    @patch("src.donchian.trading.has_credentials", return_value=True)
    @patch("src.donchian.trading.is_trading_enabled", return_value=True)
    @patch("src.donchian.trading.fetch_contract_spec", return_value=SPEC)
    @patch("src.donchian.trading._held_size", return_value=0.03)
    @patch("src.donchian.trading.store.clear_tp_limit")
    @patch("src.donchian.trading.store.set_tp_limit")
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.exchange.binance.cancel_limit_order")
    @patch("src.exchange.binance.place_limit_close_order")
    def test_same_price_resizes_when_position_grew(
        self, place, cancel, get_lot, set_tp, _clear, _held, _spec, _on, _cred, _match, _dead
    ):
        trading._tp_retry_after.clear()
        get_lot.return_value = self._lot_row(tp_limit_order_id="55", tp_limit_px=101.25, tp_limit_qty=0.01)
        place.return_value = {"orderId": "56", "status": "new", "avgPrice": "0"}
        sync_tp_limit(self._lot_row(), 101.25)
        cancel.assert_called_once_with("BTCUSDT", "55")
        self.assertEqual(place.call_args[0][2], "0.03")
        set_tp.assert_called_once_with(1, "56", 101.25, 0.03)

    @patch("src.donchian.trading._finalize_close", return_value=True)
    @patch("src.donchian.trading._fee_from_order", return_value=0.0)
    @patch("src.donchian.trading.store.clear_tp_limit")
    @patch("src.donchian.trading.store.get_open_lots")
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.donchian.trading.store.find_open_lots_by_tp_order")
    def test_undersized_shared_fill_closes_only_covered_lots(
        self, find_lots, get_lot, open_lots, clear_tp, _fee, finalize
    ):
        a = self._lot_row(id=1183, symbol="OPUSDT", side="short", size=1546.5, tp_limit_order_id="77", tp_limit_px=0.12816)
        b = self._lot_row(id=1184, symbol="OPUSDT", side="short", size=1552.8, tp_limit_order_id="77", tp_limit_px=0.12816)
        rows = {1183: a, 1184: b}
        find_lots.return_value = [a, b]
        get_lot.side_effect = lambda lot_id: rows[lot_id]
        open_lots.return_value = [a, b]
        on_tp_limit_order_update(
            {"orderId": "77", "status": "filled", "avgPrice": "0.12816", "executedQty": "1546.5"}
        )
        finalize.assert_called_once()
        self.assertEqual(int(finalize.call_args[0][0]["id"]), 1183)
        clear_tp.assert_called_once_with(1184)

    @patch("src.donchian.trading._tp_order_dead", return_value=False)
    @patch("src.donchian.trading._matching_close_limit", return_value=None)
    @patch("src.donchian.trading.has_credentials", return_value=True)
    @patch("src.donchian.trading.is_trading_enabled", return_value=True)
    @patch("src.donchian.trading.fetch_contract_spec", return_value=SPEC)
    @patch("src.donchian.trading._held_size", return_value=None)
    @patch("src.donchian.trading.store.find_open_lots_by_tp_order")
    @patch("src.donchian.trading.store.get_open_lots")
    @patch("src.donchian.trading.store.clear_tp_limit")
    @patch("src.donchian.trading.store.set_tp_limit")
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.exchange.binance.cancel_limit_order")
    @patch("src.exchange.binance.place_limit_close_order")
    def test_resize_from_db_lots_when_ws_unknown_and_moves_siblings(
        self, place, cancel, get_lot, set_tp, _clear, open_lots, find_lots, _held, _spec, _on, _cred, _match, _dead
    ):
        trading._tp_retry_after.clear()
        a = self._lot_row(id=1, size=0.01, tp_limit_order_id="55", tp_limit_px=101.25, tp_limit_qty=None)
        b = self._lot_row(id=2, size=0.02, tp_limit_order_id="55", tp_limit_px=101.25, tp_limit_qty=None)
        rows = {1: a, 2: b}
        get_lot.side_effect = lambda lot_id: rows[lot_id]
        open_lots.return_value = [a, b]
        find_lots.return_value = [a, b]
        place.return_value = {"orderId": "56", "status": "new", "avgPrice": "0"}
        sync_tp_limit(a, 101.25)
        cancel.assert_called_once_with("BTCUSDT", "55")
        self.assertEqual(place.call_args[0][2], "0.03")
        self.assertEqual(
            sorted(c.args for c in set_tp.call_args_list),
            [(1, "56", 101.25, 0.03), (2, "56", 101.25, 0.03)],
        )

    @patch("src.donchian.trading._tp_order_dead", return_value=False)
    @patch("src.donchian.trading.has_credentials", return_value=True)
    @patch("src.donchian.trading.is_trading_enabled", return_value=True)
    @patch("src.donchian.trading.fetch_contract_spec", return_value=SPEC)
    @patch("src.donchian.trading._held_size", return_value=0.01)
    @patch("src.donchian.trading.store.get_lot")
    @patch("src.exchange.binance.place_limit_close_order")
    def test_same_price_and_size_is_left_alone(self, place, get_lot, _held, _spec, _on, _cred, _dead):
        get_lot.return_value = self._lot_row(tp_limit_order_id="55", tp_limit_px=101.25, tp_limit_qty=0.01)
        sync_tp_limit(self._lot_row(), 101.25)
        place.assert_not_called()


class TestMatchingCloseLimit(unittest.TestCase):
    @patch("src.donchian.trading.store.pending_entry_order_ids", return_value={"500"})
    @patch("src.exchange.binance.fetch_pending_orders")
    def test_skips_entry_orders_and_other_position_side(self, pending, _ids):
        pending.return_value = [
            PendingOrder("500", "bde", "sell", 101.25, 1, "long"),
            PendingOrder("501", "bde", "sell", 101.25, 1, "short"),
            PendingOrder("502", "btp", "sell", 101.25, 1, "long"),
        ]
        found = trading._matching_close_limit("BTCUSDT", "long", 101.25, 2, rest=False)
        self.assertEqual(found.order_id, "502")
