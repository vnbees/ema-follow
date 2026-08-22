"""Unit tests for Donchian breadth mid hard-gate."""

from __future__ import annotations

import unittest

from src.donchian.breadth import allows_side, flip_entry_signal, mid_side, vote_breadth
from src.donchian.signals import DonchianBar, EntrySignal


def _bars(closes: list[float], period: int = 20) -> list[DonchianBar]:
    """Synthetic bars: high=close+1, low=close-1 so mid tracks close band."""
    out: list[DonchianBar] = []
    for i, c in enumerate(closes):
        out.append(
            DonchianBar(
                ts=i * 900_000,
                open=c,
                high=c + 1.0,
                low=c - 1.0,
                close=c,
            )
        )
    return out


class TestBreadthVote(unittest.TestCase):
    def test_neutral_when_thin_sample(self):
        sides = ["up"] * 5 + ["down"] * 3
        v = vote_breadth(sides, ratio=1.3, min_n=12)
        self.assertIsNone(v.side)
        self.assertEqual(v.total, 8)

    def test_neutral_when_tie(self):
        sides = ["up"] * 10 + ["down"] * 10
        v = vote_breadth(sides, ratio=1.3, min_n=12)
        self.assertIsNone(v.side)

    def test_neutral_when_ratio_weak(self):
        # 11 vs 9 — lead 11 < 9*1.3=11.7
        sides = ["up"] * 11 + ["down"] * 9
        v = vote_breadth(sides, ratio=1.3, min_n=12)
        self.assertIsNone(v.side)

    def test_long_when_ups_lead(self):
        sides = ["up"] * 13 + ["down"] * 7
        v = vote_breadth(sides, ratio=1.3, min_n=12)
        self.assertEqual(v.side, "long")
        self.assertTrue(allows_side(v, "long"))
        self.assertFalse(allows_side(v, "short"))

    def test_short_when_downs_lead(self):
        sides = ["up"] * 5 + ["down"] * 15
        v = vote_breadth(sides, ratio=1.3, min_n=12)
        self.assertEqual(v.side, "short")
        self.assertFalse(allows_side(v, "long"))
        self.assertTrue(allows_side(v, "short"))

    def test_allows_both_when_none_vote(self):
        self.assertTrue(allows_side(None, "long"))
        self.assertTrue(allows_side(None, "short"))

    def test_mid_side_up_down(self):
        closes = [100.0] * 19 + [120.0]
        bars = _bars(closes)
        self.assertEqual(mid_side(bars, 20), "up")
        closes2 = [100.0] * 19 + [80.0]
        bars2 = _bars(closes2)
        self.assertEqual(mid_side(bars2, 20), "down")

    def test_mid_side_warmup(self):
        bars = _bars([100.0] * 10)
        self.assertIsNone(mid_side(bars, 20))

    def test_flip_entry_swaps_side_and_bands(self):
        from src.donchian.breadth import BreadthVote

        entry = EntrySignal(
            side="short",
            tp_band=95.0,
            entry_px=100.0,
            opp_band=110.0,
            body_atr=0.8,
            pot_rr=0.5,
            size_mult=1.0,
            atr=1.0,
            why="test",
        )
        vote = BreadthVote(side="long", ups=15, downs=5, total=20, ratio=1.3, min_n=12)
        flipped = flip_entry_signal(entry, vote=vote)
        self.assertEqual(flipped.side, "long")
        self.assertEqual(flipped.tp_band, 110.0)
        self.assertEqual(flipped.opp_band, 95.0)
        self.assertAlmostEqual(flipped.pot_rr, 10.0 / 5.0)
        self.assertIn("breadth FLIP", flipped.why)
        self.assertTrue(allows_side(vote, flipped.side))


if __name__ == "__main__":
    unittest.main()
