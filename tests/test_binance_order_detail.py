import unittest
from unittest.mock import patch

from src.exchange.binance import fetch_order_detail
from src.trading import _parse_fill_price


class TestBinanceOrderDetail(unittest.TestCase):
    @patch("src.exchange.binance._private_get")
    def test_fetch_order_detail_normalizes_fill(self, private_get):
        private_get.return_value = {
            "orderId": 1959162733,
            "status": "FILLED",
            "avgPrice": "0.42850",
        }
        detail = fetch_order_detail("SYNUSDT", "1959162733")
        self.assertEqual(detail["status"], "filled")
        self.assertAlmostEqual(_parse_fill_price(detail), 0.4285)


if __name__ == "__main__":
    unittest.main()
