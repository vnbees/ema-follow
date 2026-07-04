import unittest
from unittest.mock import patch

from src.exchange.binance import close_position_side, market_order_params, place_market_order


class TestBinanceHedgeOrderParams(unittest.TestCase):
    def test_long_open_and_close(self):
        self.assertEqual(market_order_params("long", "open"), ("BUY", "LONG"))
        self.assertEqual(market_order_params("long", "close"), ("SELL", "LONG"))

    def test_short_open_and_close(self):
        self.assertEqual(market_order_params("short", "open"), ("SELL", "SHORT"))
        self.assertEqual(market_order_params("short", "close"), ("BUY", "SHORT"))

    @patch("src.exchange.binance._private_post")
    def test_close_does_not_send_reduce_only(self, private_post):
        private_post.return_value = {"orderId": 1, "clientOrderId": "x", "status": "FILLED"}
        close_position_side("AGLDUSDT", "long", "10")
        params = private_post.call_args[0][1]
        self.assertEqual(params["side"], "SELL")
        self.assertEqual(params["positionSide"], "LONG")
        self.assertNotIn("reduceOnly", params)

    @patch("src.exchange.binance._private_post")
    def test_open_does_not_send_reduce_only(self, private_post):
        private_post.return_value = {"orderId": 2, "clientOrderId": "y", "status": "FILLED"}
        place_market_order(
            "AGLDUSDT", "buy", "10", hold_side="long", trade_side="open",
        )
        params = private_post.call_args[0][1]
        self.assertNotIn("reduceOnly", params)


if __name__ == "__main__":
    unittest.main()
