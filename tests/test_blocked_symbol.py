import unittest
from unittest.mock import patch

from src.rsi_trading import _open_pair
from src.rsi import RsiSnapshot


class TestBlockedSymbolTrading(unittest.TestCase):
    @patch("src.rsi_trading.place_market_order")
    def test_open_pair_skips_usdc(self, place_order):
        snap = RsiSnapshot(ready=True, rsi=50.0, close=1.0)
        result = _open_pair("USDCUSDT", snap, "rsi_cross_75")
        self.assertIsNone(result)
        place_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
