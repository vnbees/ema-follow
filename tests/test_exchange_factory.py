import unittest
from unittest.mock import patch

from src.exchange import get_client, has_credentials


class TestExchangeFactory(unittest.TestCase):
    def setUp(self) -> None:
        get_client.cache_clear()

    def tearDown(self) -> None:
        get_client.cache_clear()

    @patch("src.config.EXCHANGE", "bitget")
    @patch("src.config.BITGET_API_KEY", "k")
    @patch("src.config.BITGET_SECRET_KEY", "s")
    @patch("src.config.BITGET_PASSPHRASE", "p")
    def test_bitget_credentials(self):
        from src.exchange import bitget as bitget_mod

        client = get_client()
        self.assertIsInstance(client, bitget_mod.BitgetExchange)
        self.assertTrue(has_credentials())

    @patch("src.config.EXCHANGE", "binance")
    @patch("src.config.BINANCE_API_KEY", "k")
    @patch("src.config.BINANCE_SECRET_KEY", "s")
    @patch("src.config.BITGET_API_KEY", "")
    def test_binance_credentials(self):
        from src.exchange import binance as binance_mod

        client = get_client()
        self.assertIsInstance(client, binance_mod.BinanceExchange)
        self.assertTrue(has_credentials())

    @patch("src.config.EXCHANGE", "binance")
    @patch("src.config.BINANCE_API_KEY", "")
    @patch("src.config.BINANCE_SECRET_KEY", "")
    def test_binance_missing_credentials(self):
        self.assertFalse(has_credentials())


if __name__ == "__main__":
    unittest.main()
