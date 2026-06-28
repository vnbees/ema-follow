import unittest

from src.bitget_client import _market_order_side


class TestBitgetHedgeOrderSide(unittest.TestCase):
    def test_long_open_and_close_use_buy(self):
        self.assertEqual(_market_order_side("long", "open"), "buy")
        self.assertEqual(_market_order_side("long", "close"), "buy")

    def test_short_open_and_close_use_sell(self):
        self.assertEqual(_market_order_side("short", "open"), "sell")
        self.assertEqual(_market_order_side("short", "close"), "sell")


if __name__ == "__main__":
    unittest.main()
