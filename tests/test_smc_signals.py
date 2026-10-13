import unittest

from src.smc.broker import entry_block_reason
from src.smc.signals import BAR_MS, TTL_BARS, expired, setups, stop_hit


def _iso(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def _flat(n, *, low=100.0, high=110.0, close=105.0):
    ts = [i * BAR_MS for i in range(n)]
    opens = [close] * n
    highs = [high] * n
    lows = [low] * n
    closes = [close] * n
    return ts, opens, highs, lows, closes


class TestSmcSweep(unittest.TestCase):
    def test_short_sweep_targets_midpoint(self) -> None:
        ts, opens, highs, lows, closes = _flat(90)
        lows[5] = 90.0
        highs[20] = 130.0
        highs[80] = 131.5
        lows[80] = 120.0
        closes[80] = 129.0
        opens[80] = 128.0
        rows = setups(ts, opens, highs, lows, closes)
        self.assertEqual(len(rows), 1)
        setup = rows[0]
        self.assertEqual(setup.side, "short")
        self.assertEqual(setup.index, 80)
        self.assertEqual(setup.entry, 130.0)
        self.assertEqual(setup.stop, 131.5)
        self.assertAlmostEqual(setup.target, 110.0)

    def test_wide_wick_is_ignored(self) -> None:
        ts, opens, highs, lows, closes = _flat(90)
        lows[5] = 90.0
        highs[20] = 130.0
        highs[80] = 160.0
        closes[80] = 129.0
        self.assertEqual(setups(ts, opens, highs, lows, closes), [])

    def test_expire_after_fill_window(self) -> None:
        self.assertFalse(expired(0, TTL_BARS * BAR_MS))
        self.assertTrue(expired(0, (TTL_BARS + 1) * BAR_MS))

    def test_busy_coin_blocks_a_signal_on_the_same_candle(self) -> None:
        opened_ms = 1_700_000_000_000
        fill_bar = opened_ms - (opened_ms % BAR_MS)
        self.assertEqual(
            entry_block_reason(fill_bar, [{
                "created_at": _iso(fill_bar - BAR_MS),
                "closed_at": _iso(fill_bar + 60_000),
                "opened_at": _iso(fill_bar + 30_000),
            }]),
            "busy",
        )
        self.assertIsNone(entry_block_reason(fill_bar, []))

    def test_cooldown_covers_the_fill_bar_and_the_next(self) -> None:
        opened_ms = 1_700_000_100_000
        fill_bar = opened_ms - (opened_ms % BAR_MS)
        row = {
            "created_at": _iso(fill_bar - 2 * BAR_MS),
            "closed_at": _iso(fill_bar - BAR_MS),
            "opened_at": _iso(opened_ms),
        }
        self.assertEqual(entry_block_reason(fill_bar, [row]), "cooldown")
        self.assertEqual(entry_block_reason(fill_bar + BAR_MS, [row]), "cooldown")
        self.assertIsNone(entry_block_reason(fill_bar + 2 * BAR_MS, [row]))

    def test_skip_marker_does_not_block_the_next_candle(self) -> None:
        signal = 20 * BAR_MS
        marker = {
            "created_at": _iso(signal + BAR_MS + 2_000),
            "closed_at": _iso(signal + BAR_MS + 2_000),
            "opened_at": None,
            "close_reason": "busy",
        }
        self.assertIsNone(entry_block_reason(signal + BAR_MS, [marker]))

    def test_stop_side(self) -> None:
        self.assertTrue(stop_hit("long", 10.0, 10.0))
        self.assertFalse(stop_hit("long", 10.0, 10.1))
        self.assertTrue(stop_hit("short", 10.0, 10.0))
        self.assertFalse(stop_hit("short", 10.0, 9.9))


if __name__ == "__main__":
    unittest.main()
