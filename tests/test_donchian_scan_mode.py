"""Tests for fixed vs volume Donchian scan pool."""

from __future__ import annotations

import importlib
import unittest
from unittest.mock import patch

from src.donchian import config as cfg_mod
from src.donchian import cycle as cycle_mod


class TestDonchianScanMode(unittest.TestCase):
    def test_backtest_symbols_count(self) -> None:
        self.assertEqual(len(cfg_mod.BACKTEST_SYMBOLS_20), 20)

    def test_fixed_top_symbols(self) -> None:
        with patch.object(cfg_mod, "SCAN_MODE", "fixed"):
            with patch.object(
                cfg_mod,
                "FIXED_SCAN_SYMBOLS",
                list(cfg_mod.BACKTEST_SYMBOLS_20),
            ):
                cycle_mod._cached_symbols = []
                cycle_mod._symbols_refreshed_at = 0.0
                syms = cycle_mod._top_symbols()
        self.assertEqual(syms, list(cfg_mod.BACKTEST_SYMBOLS_20))

    def test_breadth_universe_fixed_matches_scan(self) -> None:
        scan = ["BTCUSDT", "ETHUSDT"]
        with patch.object(cfg_mod, "SCAN_MODE", "fixed"):
            uni = cycle_mod._breadth_universe(scan)
        self.assertEqual(uni, scan)


if __name__ == "__main__":
    unittest.main()
