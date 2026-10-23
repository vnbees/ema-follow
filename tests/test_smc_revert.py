import unittest

from src.smc.revert import (
    MAKER_FEE,
    TAKER_FEE,
    TP_ATR_MULT,
    book_in_profit,
    candle_color,
    entry_side,
    fee_floor_distance,
    nearest_per_side,
    net_after_fees,
    tp_distance,
    tp_price,
    unrealized_net,
)


class RevertRuleTests(unittest.TestCase):
    def test_green_candle_above_the_signal_is_a_short(self) -> None:
        self.assertEqual(candle_color(100, 101), "green")
        self.assertEqual(entry_side(100, 101.0, "green"), "short")
        self.assertIsNone(entry_side(100, 101.0, "red"))
        self.assertIsNone(entry_side(100, 100.9, "green"))

    def test_red_candle_below_the_signal_is_a_long(self) -> None:
        self.assertEqual(candle_color(100, 99), "red")
        self.assertEqual(entry_side(100, 99.0, "red"), "long")
        self.assertIsNone(entry_side(100, 99.0, "green"))
        self.assertIsNone(entry_side(100, 99.1, "red"))

    def test_history_covers_a_full_seven_day_window(self) -> None:
        from src.smc.outlook import BARS_7D
        from src.smc.revert import HISTORY_BARS

        self.assertGreaterEqual(HISTORY_BARS, BARS_7D + 400)

    def test_same_side_signals_share_one_order_on_the_nearest_level(self) -> None:
        near = {"id": 1, "level_px": 0.1349}
        far = {"id": 2, "level_px": 0.1418}
        other = {"id": 3, "level_px": 0.11}
        chosen = nearest_per_side(
            [
                (far, "long", 0.115),
                (near, "long", 0.070),
                (other, "short", 0.12),
            ]
        )
        self.assertEqual({side: level["id"] for level, side in chosen}, {"long": 1, "short": 3})

    def test_a_doji_does_not_enter(self) -> None:
        self.assertIsNone(candle_color(100, 100))
        self.assertIsNone(entry_side(100, 102, None))

    def test_take_profit_follows_atr_and_stays_above_fees(self) -> None:
        entry = 100.0
        atr = 1.5
        self.assertEqual(TP_ATR_MULT, 1.0)
        self.assertAlmostEqual(tp_distance(entry, atr), atr)
        self.assertAlmostEqual(tp_price("long", entry, atr), 101.5)
        self.assertAlmostEqual(tp_price("short", entry, atr), 98.5)
        self.assertGreater(net_after_fees("long", entry, tp_price("long", entry, atr)), 0)
        self.assertGreater(net_after_fees("short", entry, tp_price("short", entry, atr)), 0)
        tiny = 0.00001
        floor = fee_floor_distance(entry)
        self.assertGreater(tp_distance(entry, tiny), tiny)
        self.assertAlmostEqual(tp_distance(entry, tiny), floor)
        self.assertGreater(net_after_fees("long", entry, entry + floor), 0)
        self.assertGreater(floor, entry * (TAKER_FEE + MAKER_FEE))

    def test_a_profitable_book_blocks_a_new_order(self) -> None:
        # Short from 102, mark 101: about 0.1 gross on qty 1, fees are tiny.
        winning = {"status": "open", "side": "short", "fill_px": 102.0, "qty": 1.0, "fee_open": 0.02}
        losing = {"status": "open", "side": "short", "fill_px": 100.0, "qty": 1.0, "fee_open": 0.02}
        self.assertTrue(book_in_profit([winning], 101.0))
        self.assertFalse(book_in_profit([losing], 101.0))
        self.assertFalse(book_in_profit([winning, losing], 101.5))
        self.assertFalse(book_in_profit([], 101.0))
        self.assertGreater(unrealized_net("short", 102, 101, 1, 0.02), 0)
