import unittest

from src.order_sizing import compute_entry_margin_usdt, margin_to_notional


class TestOrderSizing(unittest.TestCase):
    def test_min_margin_when_equity_low(self):
        self.assertEqual(compute_entry_margin_usdt(100), 1.0)

    def test_half_percent_equity(self):
        self.assertEqual(compute_entry_margin_usdt(1000), 5.0)

    def test_zero_equity_uses_min(self):
        self.assertEqual(compute_entry_margin_usdt(0), 1.0)

    def test_notional(self):
        self.assertEqual(margin_to_notional(5), 50.0)


if __name__ == "__main__":
    unittest.main()
