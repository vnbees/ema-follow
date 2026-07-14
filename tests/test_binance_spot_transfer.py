import unittest
from unittest.mock import patch

from src.exchange import binance as bn


class TestBinanceSpotTransfer(unittest.TestCase):
    @patch("src.exchange.binance._spot_private_request")
    def test_transfer_params(self, spot_req) -> None:
        spot_req.return_value = {"tranId": 12345}
        result = bn.transfer_futures_to_spot("USDT", 4.0)
        spot_req.assert_called_once_with(
            "POST",
            "/sapi/v1/asset/transfer",
            {
                "type": "UMFUTURE_MAIN",
                "asset": "USDT",
                "amount": 4.0,
            },
        )
        self.assertEqual(result["tranId"], "12345")

    @patch("src.exchange.binance._spot_private_request")
    def test_fetch_spot_balance(self, spot_req) -> None:
        spot_req.return_value = [{"asset": "USDT", "free": "10.5", "locked": "0.5"}]
        bal = bn.fetch_spot_balance("USDT")
        self.assertAlmostEqual(bal, 11.0)


if __name__ == "__main__":
    unittest.main()
