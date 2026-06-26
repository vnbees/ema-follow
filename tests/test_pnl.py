import unittest

from src.pnl import roi_pct, total_margin_deployed


class TestPnl(unittest.TestCase):
    def test_total_margin_deployed(self):
        self.assertEqual(total_margin_deployed(5, 0), 5.0)
        self.assertEqual(total_margin_deployed(5, 2), 15.0)

    def test_roi_pct(self):
        self.assertEqual(roi_pct(1.0, 10.0), 10.0)
        self.assertEqual(roi_pct(-0.5, 5.0), -10.0)

    def test_roi_none_when_no_margin(self):
        self.assertIsNone(roi_pct(1.0, 0))


if __name__ == "__main__":
    unittest.main()
