import unittest

from src.smc.track import (
    bias_for_trade,
    bracket_outcomes,
    exit_level,
    level_reference,
    observe,
    past_price,
    path_pnl,
    prior_bias,
    prior_direction,
    remember_mark,
)


def _trade(**extra):
    row = {
        "id": 7,
        "symbol": "BTCUSDT",
        "side": "long",
        "status": "closed",
        "close_reason": "time",
        "opened_at": "2026-10-16T14:00:00+00:00",
        "closed_at": "2026-10-16T14:41:00+00:00",
        "fill_px": 100.0,
        "entry_px": 100.0,
        "stop_px": 90.0,
        "tp_px": 110.0,
        "qty": 1.0,
        "fee_open": 0.05,
        "close_px": 101.0,
        "pnl_usdt": 0.5,
        "shadow_status": "",
    }
    row.update(extra)
    return row


class TrackPathTests(unittest.TestCase):
    def test_same_minute_stop_wins_over_target(self) -> None:
        self.assertEqual(exit_level("long", 90.0, 110.0, 111.0, 89.0), (90.0, "sl"))

    def test_time_close_keeps_tracking_until_stop(self) -> None:
        live: dict[int, dict] = {}
        first = observe(0, {"BTCUSDT": 101.0}, [_trade()], live)
        self.assertEqual([event["kind"] for event in first], ["point"])
        self.assertAlmostEqual(first[0]["pnl"], path_pnl("long", 100.0, 1.0, 0.05, 101.0, maker=False))
        later = observe(60_000, {"BTCUSDT": 89.0}, [_trade()], live)
        kinds = [event["kind"] for event in later]
        self.assertEqual(kinds, ["point", "shadow"])
        self.assertEqual(later[1]["status"], "sl")
        self.assertEqual(later[1]["hit_px"], 90.0)
        self.assertNotIn(7, live)

    def test_real_target_closes_the_shadow_without_a_new_path(self) -> None:
        events = observe(
            0,
            {},
            [_trade(status="closed", close_reason="tp", close_px=110.0, pnl_usdt=9.0)],
            {},
        )
        self.assertEqual(events[0]["kind"], "shadow")
        self.assertEqual(events[0]["status"], "tp")
        self.assertEqual(events[0]["pnl"], 9.0)


class PriorMoveTests(unittest.TestCase):
    def test_long_rise_is_with_the_trade_and_a_drop_is_against(self) -> None:
        self.assertEqual(prior_bias("long", 100.2, 100.0)[0], "with")
        self.assertEqual(prior_bias("long", 99.8, 100.0)[0], "against")

    def test_short_drop_is_with_the_trade(self) -> None:
        direction, bps = prior_bias("short", 99.8, 100.0)
        self.assertEqual(direction, "with")
        self.assertGreater(bps, 0)

    def test_a_move_under_one_basis_point_is_flat(self) -> None:
        self.assertEqual(prior_bias("long", 100.005, 100.0)[0], "flat")

    def test_bias_uses_the_mark_from_fifteen_minutes_before_the_fill(self) -> None:
        book: dict[str, list[tuple[int, float]]] = {}
        opened = 20 * 60_000
        for minute in range(0, 21):
            remember_mark("BTCUSDT", minute * 60_000, 100.0 + minute, book)
        self.assertEqual(past_price("BTCUSDT", opened, book), 105.0)
        trade = _trade(opened_at="2026-10-16T14:20:00+00:00", fill_px=120.0, side="long")
        opened_ms = 20 * 60_000
        self.assertEqual(
            bias_for_trade({**trade, "opened_at": "1970-01-01T00:20:00+00:00"}, book)["direction"],
            "with",
        )
        self.assertIsNone(past_price("BTCUSDT", opened_ms - 30 * 60_000, book))

    def test_direction_is_unknown_when_the_buffer_starts_after_the_lookback(self) -> None:
        book: dict[str, list[tuple[int, float]]] = {}
        remember_mark("BTCUSDT", 20 * 60_000, 100.0, book)
        self.assertEqual(
            prior_direction("BTCUSDT", "long", 100.0, "1970-01-01T00:20:00+00:00", book, 20 * 60_000),
            "unknown",
        )
        self.assertIsNone(
            prior_direction("BTCUSDT", "long", 100.0, "1970-01-01T00:20:00+00:00", {}, 20 * 60_000)
        )


class BracketReferenceTests(unittest.TestCase):
    def test_take_profit_is_maker_and_stop_is_taker_plus_the_cut(self) -> None:
        row = _trade(cut_pnl=-1.0)
        tp, sl = bracket_outcomes(row)
        self.assertAlmostEqual(tp, 10 - 0.05 - 110 * 0.0002 - 1)
        self.assertAlmostEqual(sl, -10 - 0.05 - 90 * 0.0005 - 1)

    def test_one_stop_may_cost_the_wins_that_come_with_the_winrate(self) -> None:
        trades = []
        for i in range(9):
            trades.append({"status": "closed", "pnl_usdt": 1, "tp_if_usdt": 1.0, "sl_if_usdt": -4.0})
        trades.append({"status": "closed", "pnl_usdt": -4, "tp_if_usdt": 1.0, "sl_if_usdt": -4.0})
        levels = level_reference(trades)
        self.assertAlmostEqual(levels["win_rate"], 90.0)
        self.assertAlmostEqual(levels["avg_tp"], 1.0)
        self.assertAlmostEqual(levels["avg_sl"], -4.0)
        self.assertAlmostEqual(levels["acceptable_sl"], -9.0)


if __name__ == "__main__":
    unittest.main()
