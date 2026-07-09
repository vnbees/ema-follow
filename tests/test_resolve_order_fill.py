import unittest
from unittest.mock import patch

from src.trading import resolve_order_fill


class TestResolveOrderFill(unittest.TestCase):
    def test_from_order_response(self):
        price = resolve_order_fill(
            "BTCUSDT",
            {"orderId": "1", "avgPrice": "100.5"},
            fallback_price=99.0,
        )
        self.assertAlmostEqual(price, 100.5)

    @patch("src.trading.fetch_side_mark_price", return_value=101.0)
    @patch("src.trading.exchange_fetch_order_detail")
    def test_poll_order_detail(self, fetch_detail, _mark):
        fetch_detail.return_value = {"status": "filled", "avgPrice": "98.25"}
        price = resolve_order_fill(
            "BTCUSDT",
            {"orderId": "42"},
            fallback_price=97.0,
        )
        self.assertAlmostEqual(price, 98.25)
        fetch_detail.assert_called()

    @patch("src.trading.fetch_side_mark_price", return_value=0.0)
    @patch("src.trading.exchange_fetch_order_detail")
    def test_fallback_price(self, fetch_detail, _mark):
        fetch_detail.return_value = {"status": "new", "avgPrice": "0"}
        price = resolve_order_fill(
            "BTCUSDT",
            {"orderId": "99"},
            fallback_price=96.5,
        )
        self.assertAlmostEqual(price, 96.5)


if __name__ == "__main__":
    unittest.main()
