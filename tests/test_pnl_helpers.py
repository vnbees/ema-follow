import unittest

from src.pnl import estimate_tp_pnl_usdt, leg_realized_pnl, leg_unrealized_pnl


class TestPnlHelpers(unittest.TestCase):
    def test_leg_unrealized_long(self):
        self.assertAlmostEqual(leg_unrealized_pnl("long", 100.0, 2.0, 102.0), 4.0)

    def test_leg_unrealized_short(self):
        self.assertAlmostEqual(leg_unrealized_pnl("short", 100.0, 2.0, 98.0), 4.0)

    def test_leg_realized_from_stored(self):
        pnl = leg_realized_pnl(
            "short", 0.5854, 18.0,
            realized_pnl_usdt=0.21,
            close_price=None,
        )
        self.assertAlmostEqual(pnl, 0.21)

    def test_leg_realized_from_close_price(self):
        pnl = leg_realized_pnl(
            "short", 100.0, 1.0,
            realized_pnl_usdt=None,
            close_price=98.0,
        )
        self.assertAlmostEqual(pnl, 2.0)

    def test_estimate_tp_pnl(self):
        pnl = estimate_tp_pnl_usdt(0.5854, 18.0, 2.0)
        self.assertAlmostEqual(pnl, 0.210744)


if __name__ == "__main__":
    unittest.main()
