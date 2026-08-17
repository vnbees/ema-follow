import time
import unittest
from unittest.mock import patch

from src.exchange import binance


def _pos_row(
    symbol: str,
    side: str,
    amt: float,
    entry: float,
    *,
    pnl: float = 0.0,
    mark: float = 0.0,
) -> dict:
    return {
        "symbol": symbol,
        "positionSide": side,
        "positionAmt": str(amt),
        "entryPrice": str(entry),
        "unRealizedProfit": str(pnl),
        "markPrice": str(mark or entry),
    }


class TestBinancePositionCache(unittest.TestCase):
    def setUp(self) -> None:
        binance._rate_limited_until_ms = 0.0
        binance._invalidate_position_cache()
        self._ws_patch = patch("src.exchange.binance_ws.manager.is_ws_enabled", return_value=False)
        self._ws_patch.start()
        # Also block WS read paths imported inside fetch_* 
        self._ws_get_pos = patch("src.exchange.binance_ws.get_symbol_positions_from_ws", return_value=None)
        self._ws_get_all = patch("src.exchange.binance_ws.get_all_positions_from_ws", return_value=None)
        self._ws_get_bal = patch("src.exchange.binance_ws.get_balance_from_ws", return_value=None)
        self._ws_get_pos.start()
        self._ws_get_all.start()
        self._ws_get_bal.start()

    def tearDown(self) -> None:
        binance._rate_limited_until_ms = 0.0
        binance._invalidate_position_cache()
        self._ws_patch.stop()
        self._ws_get_pos.stop()
        self._ws_get_all.stop()
        self._ws_get_bal.stop()

    def test_fetch_symbol_positions_cached(self) -> None:
        rows = [
            _pos_row("BTCUSDT", "LONG", 1.0, 100.0, pnl=1.5),
            _pos_row("BTCUSDT", "SHORT", -2.0, 110.0, pnl=-0.5),
        ]
        with patch("src.exchange.binance._private_get", return_value=rows) as mock_get:
            a = binance.fetch_symbol_positions("BTCUSDT")
            b = binance.fetch_symbol_positions("BTCUSDT")
            self.assertEqual(mock_get.call_count, 1)
            self.assertEqual(a["long"].size, 1.0)
            self.assertEqual(b["short"].size, 2.0)
            self.assertEqual(a["long"].unrealized_pnl, 1.5)

    def test_unrealized_reuses_position_cache(self) -> None:
        rows = [_pos_row("ETHUSDT", "LONG", 3.0, 200.0, pnl=4.0)]
        with patch("src.exchange.binance._private_get", return_value=rows) as mock_get:
            binance.fetch_symbol_positions("ETHUSDT")
            pnl = binance.fetch_side_unrealized_pnl("ETHUSDT", "long")
            self.assertEqual(mock_get.call_count, 1)
            self.assertEqual(pnl, 4.0)

    def test_total_unrealized_single_all_positions_call(self) -> None:
        rows = [
            _pos_row("AAVEUSDT", "LONG", 1.0, 90.0, pnl=0.2),
            _pos_row("AAVEUSDT", "SHORT", -1.0, 91.0, pnl=0.1),
            _pos_row("XRPUSDT", "LONG", 10.0, 1.0, pnl=0.5),
        ]
        with patch("src.exchange.binance._private_get", return_value=rows) as mock_get:
            total, count = binance.fetch_total_unrealized_pnl(["AAVEUSDT", "XRPUSDT"])
            self.assertEqual(mock_get.call_count, 1)
            self.assertEqual(mock_get.call_args[0][0], "/fapi/v2/positionRisk")
            self.assertEqual(mock_get.call_args[0][1], {})
            self.assertAlmostEqual(total, 0.8)
            self.assertEqual(count, 3)

    def test_place_market_order_invalidates_cache(self) -> None:
        rows = [_pos_row("SOLUSDT", "LONG", 5.0, 150.0, pnl=1.0)]
        with patch("src.exchange.binance._private_get", return_value=rows) as mock_get:
            binance.fetch_symbol_positions("SOLUSDT")
            self.assertEqual(mock_get.call_count, 1)

        with (
            patch("src.exchange.binance._private_post", return_value={"orderId": "1", "clientOrderId": "c", "avgPrice": "150", "status": "FILLED"}),
            patch("src.exchange.binance._ensure_credentials"),
            patch("src.exchange.binance.BINANCE_API_KEY", "k"),
            patch("src.exchange.binance.BINANCE_SECRET_KEY", "s"),
            patch("src.exchange.binance_ws.on_order_placed"),
        ):
            binance.place_market_order(
                "SOLUSDT", "buy", "1", hold_side="long", trade_side="open",
            )

        with patch("src.exchange.binance._private_get", return_value=rows) as mock_get2:
            binance.fetch_symbol_positions("SOLUSDT")
            self.assertEqual(mock_get2.call_count, 1)

    def test_account_balance_cached(self) -> None:
        payload = {
            "availableBalance": "10",
            "totalMarginBalance": "100",
            "totalMaintMargin": "5",
            "totalInitialMargin": "50",
        }
        with patch("src.exchange.binance._private_get", return_value=payload) as mock_get:
            a = binance.fetch_futures_balance("BTCUSDT")
            b = binance.fetch_futures_balance("BTCUSDT")
            self.assertEqual(mock_get.call_count, 1)
            self.assertEqual(a.available, 10.0)
            self.assertEqual(b.account_equity, 100.0)


class TestVolumeRankTtl(unittest.TestCase):
    def setUp(self) -> None:
        import src.market_universe as mu

        with mu._lock:
            mu._volume_rank = []
            mu._last_refreshed = ""
            mu._last_refreshed_mono = 0.0

    def test_max_age_skips_fetch(self) -> None:
        import src.market_universe as mu

        rows = [("BTCUSDT", 1e9), ("ETHUSDT", 5e8)]
        with patch("src.market_universe.fetch_top_futures_by_volume", return_value=rows) as mock_fetch:
            first = mu.refresh_volume_rank(force=True)
            second = mu.refresh_volume_rank(max_age_sec=900)
            self.assertEqual(mock_fetch.call_count, 1)
            self.assertEqual(first[0][0], "BTCUSDT")
            self.assertEqual(second[0][0], "BTCUSDT")

    def test_expired_age_refetches(self) -> None:
        import src.market_universe as mu

        rows = [("BTCUSDT", 1e9)]
        with patch("src.market_universe.fetch_top_futures_by_volume", return_value=rows) as mock_fetch:
            mu.refresh_volume_rank(force=True)
            with mu._lock:
                mu._last_refreshed_mono = time.monotonic() - 1000
            mu.refresh_volume_rank(max_age_sec=900)
            self.assertEqual(mock_fetch.call_count, 2)

if __name__ == "__main__":
    unittest.main()
