"""Favorable same-side add: 0.4 ATR later close, one child per original lot."""

from __future__ import annotations

import unittest

from src.donchian.favorable_add import ENTRY_FAVORABLE_ADD, add_parents, favorable_move


def _lot(**kwargs):
    base = {
        "id": 1,
        "side": "long",
        "entry_rule": "standard",
        "opened_at": "2026-10-01T00:00:00Z",
    }
    base.update(kwargs)
    return base


class FavorableMoveTests(unittest.TestCase):
    def test_long_needs_0_4_atr(self):
        self.assertFalse(favorable_move("long", 100, 103.9, 10, 0.4))
        self.assertTrue(favorable_move("long", 100, 104, 10, 0.4))

    def test_short_needs_0_4_atr(self):
        self.assertFalse(favorable_move("short", 100, 96.1, 10, 0.4))
        self.assertTrue(favorable_move("short", 100, 96, 10, 0.4))

    def test_disabled_or_bad_inputs(self):
        self.assertFalse(favorable_move("long", 100, 110, 10, 0))
        self.assertFalse(favorable_move("long", 100, 110, 0, 0.4))
        self.assertFalse(favorable_move("flat", 100, 110, 10, 0.4))


class AddParentTests(unittest.TestCase):
    def test_oldest_standard_or_blank(self):
        lots = [
            _lot(id=2, opened_at="2026-10-02T00:00:00Z"),
            _lot(id=1, entry_rule="", opened_at="2026-10-01T00:00:00Z"),
            _lot(id=3, side="short", entry_rule="stale_opposite"),
        ]
        parents = add_parents(lots)
        self.assertEqual([lot["id"] for lot in parents], [1])

    def test_existing_add_blocks_another(self):
        lots = [
            _lot(id=1),
            _lot(id=9, entry_rule=ENTRY_FAVORABLE_ADD, opened_at="2026-10-03T00:00:00Z"),
        ]
        self.assertEqual(add_parents(lots), [])

    def test_stale_opposite_is_not_a_parent(self):
        lots = [_lot(id=4, entry_rule="stale_opposite")]
        self.assertEqual(add_parents(lots), [])


if __name__ == "__main__":
    unittest.main()
