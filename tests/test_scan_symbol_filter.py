import unittest

from src.exchange.symbols import is_scan_symbol, is_tradeable_symbol


class TestScanSymbolFilter(unittest.TestCase):
    def test_usdt_pairs_allowed(self):
        self.assertTrue(is_scan_symbol("BTCUSDT"))
        self.assertTrue(is_scan_symbol("AGLDUSDT"))

    def test_usdc_excluded(self):
        self.assertFalse(is_scan_symbol("BTCUSDC"))
        self.assertFalse(is_scan_symbol("USDCUSDT"))
        self.assertFalse(is_tradeable_symbol("USDCUSDT"))


if __name__ == "__main__":
    unittest.main()
