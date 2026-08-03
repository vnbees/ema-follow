import time
import unittest
from unittest.mock import patch

from src.exchange.binance import _listing_age_ok, _scan_universe_from_info


def _item(symbol: str, listed_days_ago: float) -> dict:
    return {
        "symbol": symbol,
        "contractType": "PERPETUAL",
        "status": "TRADING",
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "onboardDate": (time.time() - listed_days_ago * 86_400) * 1000,
    }


class TestListingAgeOk(unittest.TestCase):
    @patch("src.config.MIN_LISTING_AGE_DAYS", 30.0)
    def test_old_symbol_passes(self):
        self.assertTrue(_listing_age_ok(_item("BTCUSDT", 900)))

    @patch("src.config.MIN_LISTING_AGE_DAYS", 30.0)
    def test_new_listing_blocked(self):
        self.assertFalse(_listing_age_ok(_item("NIGHTUSDT", 10)))

    @patch("src.config.MIN_LISTING_AGE_DAYS", 0.0)
    def test_disabled_lets_everything_through(self):
        self.assertTrue(_listing_age_ok(_item("NIGHTUSDT", 1)))

    @patch("src.config.MIN_LISTING_AGE_DAYS", 30.0)
    def test_missing_onboard_date_passes(self):
        item = _item("BTCUSDT", 900)
        item.pop("onboardDate")
        self.assertTrue(_listing_age_ok(item))


class TestScanUniverse(unittest.TestCase):
    @patch("src.config.MIN_LISTING_AGE_DAYS", 30.0)
    def test_universe_excludes_new_listing(self):
        info = {
            "symbols": [
                _item("BTCUSDT", 900),
                _item("NIGHTUSDT", 5),
            ]
        }
        universe = _scan_universe_from_info(info)
        self.assertIn("BTCUSDT", universe)
        self.assertNotIn("NIGHTUSDT", universe)

    @patch("src.config.MIN_LISTING_AGE_DAYS", 30.0)
    def test_universe_still_excludes_non_perp(self):
        item = _item("ETHUSDT_240927", 900)
        item["contractType"] = "CURRENT_QUARTER"
        info = {"symbols": [item, _item("ETHUSDT", 900)]}
        universe = _scan_universe_from_info(info)
        self.assertEqual(universe, {"ETHUSDT"})


if __name__ == "__main__":
    unittest.main()
