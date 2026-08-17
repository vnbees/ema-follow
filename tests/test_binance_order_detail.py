import unittest
from unittest.mock import patch

from src.exchange.binance import fetch_order_detail
from src.trading import _parse_fill_price


class TestBinanceOrderDetail(unittest.TestCase):
    @patch("src.exchange.binance.is_optional_rest_blocked", return_value=False)
    @patch("src.exchange.binance._private_get")
    def test_fetch_order_detail_normalizes_fill(self, private_get, _blocked):
        private_get.return_value = {
            "orderId": 1959162733,
            "status": "FILLED",
            "avgPrice": "0.42850",
        }
        detail = fetch_order_detail("SYNUSDT", "1959162733")
        self.assertEqual(detail["status"], "filled")
        self.assertAlmostEqual(_parse_fill_price(detail), 0.4285)
        private_get.assert_called_once()
        self.assertEqual(private_get.call_args.kwargs.get("priority"), "optional")


if __name__ == "__main__":
    unittest.main()
