import unittest

from src.web.number_format import format_dashboard_price, format_dashboard_size


class TestDashboardNumberFormat(unittest.TestCase):
    def test_low_price_six_decimals(self):
        self.assertEqual(format_dashboard_price(0.0353), "0.035300")
        self.assertEqual(format_dashboard_price(0.03528), "0.035280")

    def test_mid_price_four_decimals(self):
        self.assertEqual(format_dashboard_price(1.2345), "1.2345")
        self.assertEqual(format_dashboard_price(88.71), "88.7100")

    def test_high_price_two_decimals(self):
        self.assertEqual(format_dashboard_price(91234.5), "91234.50")

    def test_size_formatting(self):
        self.assertEqual(format_dashboard_size(618.5), "618.50")


if __name__ == "__main__":
    unittest.main()
