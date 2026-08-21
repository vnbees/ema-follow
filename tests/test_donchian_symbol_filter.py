"""Tests for Donchian symbol pool filter."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from src.donchian import symbol_filter as sf
from src.exchange.types import Candle


def _candle(ts_ms: int, *, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=ts_ms, open=o, high=h, low=l, close=c, volume=1.0)


class TestDonchianSymbolFilter(unittest.TestCase):
    def test_rejects_new_listing(self) -> None:
        now_ms = int(time.time() * 1000)
        onboard = now_ms - 30 * 86400000
        with patch.object(sf, "_load_exchange_symbols", return_value={"BTWUSDT": {"onboardDate": onboard}}):
            ok, reason = sf.is_scan_eligible("BTWUSDT")
        self.assertFalse(ok)
        self.assertIn("listing", reason)

    def test_accepts_old_listing(self) -> None:
        now_ms = int(time.time() * 1000)
        onboard = now_ms - 400 * 86400000
        with patch.object(sf, "_load_exchange_symbols", return_value={"BTCUSDT": {"onboardDate": onboard}}):
            with patch.object(sf, "range_24h_pct", return_value=4.0):
                ok, reason = sf.is_scan_eligible("BTCUSDT")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_rejects_high_24h_range_on_alt_only(self) -> None:
        now_ms = int(time.time() * 1000)
        onboard = now_ms - 400 * 86400000
        with patch.object(sf, "_load_exchange_symbols", return_value={"PUMPUSDT": {"onboardDate": onboard}}):
            with patch.object(sf, "range_24h_pct", return_value=22.0):
                ok, reason = sf.is_scan_eligible("PUMPUSDT")
        self.assertFalse(ok)
        self.assertIn("24h range", reason)

    def test_major_skips_vol_cap(self) -> None:
        now_ms = int(time.time() * 1000)
        onboard = now_ms - 400 * 86400000
        with patch.object(sf, "_load_exchange_symbols", return_value={"ETHUSDT": {"onboardDate": onboard}}):
            with patch.object(sf, "range_24h_pct", return_value=25.0):
                ok, reason = sf.is_scan_eligible("ETHUSDT")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_filter_ranked_respects_limit(self) -> None:
        now_ms = int(time.time() * 1000)
        onboard = now_ms - 400 * 86400000
        info = {sym: {"onboardDate": onboard} for sym in ("BTCUSDT", "ETHUSDT", "NEWUSDT")}
        ranked = [("BTCUSDT", 1e9), ("NEWUSDT", 5e8), ("ETHUSDT", 4e8)]
        with patch.object(sf, "_load_exchange_symbols", return_value=info):
            with patch.object(sf, "range_24h_pct", return_value=3.0):
                with patch.object(sf, "listing_age_days", side_effect=lambda s: 400.0 if s != "NEWUSDT" else 10.0):
                    selected, skipped = sf.filter_ranked_symbols(ranked, limit=2)
        self.assertEqual(selected, ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(skipped[0][0], "NEWUSDT")

    def test_range_24h_from_cache(self) -> None:
        bar_ms = 15 * 60 * 1000
        now_ms = int(time.time() * 1000)
        base = now_ms - 100 * bar_ms
        candles = [
            _candle(base + i * bar_ms, o=100, h=110, l=90, c=100) for i in range(96)
        ]
        mock_cache = unittest.mock.MagicMock()
        mock_cache.get_candles.return_value = candles
        with patch("src.exchange.binance_ws.cache.CACHE", mock_cache):
            rng = sf.range_24h_pct("BTCUSDT", interval="15m")
        self.assertIsNotNone(rng)
        assert rng is not None
        self.assertAlmostEqual(rng, 20.0)


if __name__ == "__main__":
    unittest.main()
