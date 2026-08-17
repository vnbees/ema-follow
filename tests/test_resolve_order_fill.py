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

    @patch("src.trading.time.sleep")
    @patch("src.trading._fill_from_ws", return_value=None)
    @patch("src.trading.fetch_side_mark_price", return_value=101.0)
    @patch("src.trading.exchange_fetch_order_detail")
    @patch("src.trading._uds_connected", return_value=False)
    @patch("src.trading._optional_rest_blocked", return_value=False)
    def test_poll_order_detail(self, _blocked, _uds, fetch_detail, _mark, _ws, _sleep):
        fetch_detail.return_value = {"status": "filled", "avgPrice": "98.25"}
        price = resolve_order_fill(
            "BTCUSDT",
            {"orderId": "42"},
            fallback_price=97.0,
        )
        self.assertAlmostEqual(price, 98.25)
        fetch_detail.assert_called_once()

    @patch("src.trading.time.sleep")
    @patch("src.trading._fill_from_ws", return_value=None)
    @patch("src.trading.fetch_side_mark_price", return_value=0.0)
    @patch("src.trading.exchange_fetch_order_detail")
    @patch("src.trading._uds_connected", return_value=False)
    @patch("src.trading._optional_rest_blocked", return_value=False)
    def test_fallback_price(self, _blocked, _uds, fetch_detail, _mark, _ws, _sleep):
        fetch_detail.return_value = {"status": "new", "avgPrice": "0"}
        price = resolve_order_fill(
            "BTCUSDT",
            {"orderId": "99"},
            fallback_price=96.5,
        )
        self.assertAlmostEqual(price, 96.5)

    @patch("src.trading.time.sleep")
    @patch("src.trading._fill_from_ws", return_value=None)
    @patch("src.trading.fetch_side_mark_price", return_value=0.3309)
    @patch("src.trading.exchange_fetch_order_detail")
    @patch("src.trading._optional_rest_blocked", return_value=True)
    def test_skip_rest_fill_poll_during_resume(self, _blocked, fetch_detail, _mark, _ws, _sleep):
        price = resolve_order_fill(
            "TRXUSDT",
            {"orderId": "18653664913"},
            fallback_price=0.33,
        )
        self.assertAlmostEqual(price, 0.3309)
        fetch_detail.assert_not_called()

    @patch("src.trading.time.sleep")
    @patch("src.trading._fill_from_ws", return_value=None)
    @patch("src.trading.fetch_side_mark_price", return_value=0.3309)
    @patch("src.trading.exchange_fetch_order_detail")
    @patch("src.trading._uds_connected", return_value=True)
    @patch("src.trading._optional_rest_blocked", return_value=False)
    def test_skip_rest_fill_poll_when_uds_connected(
        self, _blocked, _uds, fetch_detail, _mark, _ws, _sleep
    ):
        price = resolve_order_fill(
            "TRXUSDT",
            {"orderId": "18653664913"},
            fallback_price=0.33,
        )
        self.assertAlmostEqual(price, 0.3309)
        fetch_detail.assert_not_called()

    @patch("src.trading.time.sleep")
    @patch("src.trading._fill_from_ws", return_value=0.5797)
    @patch("src.trading.exchange_fetch_order_detail")
    def test_prefers_ws_fill(self, fetch_detail, _ws, _sleep):
        price = resolve_order_fill(
            "JTOUSDT",
            {"orderId": "8664245171"},
            fallback_price=0.58,
        )
        self.assertAlmostEqual(price, 0.5797)
        fetch_detail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
