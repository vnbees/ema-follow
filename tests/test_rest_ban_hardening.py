import unittest
from unittest.mock import patch

from src.exchange.binance_ws.cache import BinanceWsCache
from src.exchange.types import Position


class TestApplyPositionUpdates(unittest.TestCase):
    def test_empty_update_does_not_bump_freshness(self):
        cache = BinanceWsCache()
        cache.positions_updated_at = 0.0
        cache.apply_position_updates([], [])
        self.assertEqual(cache.positions_updated_at, 0.0)

    def test_real_update_bumps_freshness(self):
        cache = BinanceWsCache()
        cache.positions_updated_at = 0.0
        pos = Position(symbol="BTCUSDT", side="long", size=1.0, avg_price=100.0)
        cache.apply_position_updates([pos], [])
        self.assertGreater(cache.positions_updated_at, 0.0)


class TestFetchPositionsNoAutoFlush(unittest.TestCase):
    @patch("src.exchange.binance_ws.flush_pending_reconcile")
    @patch("src.exchange.binance_ws.get_symbol_positions_lenient", return_value=None)
    @patch(
        "src.exchange.binance_ws.get_symbol_positions_from_ws",
        return_value={
            "long": Position("BTCUSDT", "long", 1.0, 100.0),
            "short": Position("BTCUSDT", "short", 0.0, 0.0),
        },
    )
    @patch("src.exchange.binance_ws.watch_symbols")
    def test_ws_hit_does_not_flush(self, _watch, _ws, _lenient, flush):
        from src.exchange import binance as binance_mod

        out = binance_mod.fetch_symbol_positions("BTCUSDT")
        self.assertEqual(out["long"].size, 1.0)
        flush.assert_not_called()


class TestFlushSymbolScoped(unittest.TestCase):
    def setUp(self):
        from src.exchange.binance_ws import manager as m

        m._pending_reconcile = False
        m._pending_reconcile_symbols.clear()

    @patch("src.exchange.binance_ws.manager.reconcile_account_state")
    @patch("src.exchange.binance_ws.manager._reconcile_symbols_rest", return_value=True)
    @patch("src.exchange.binance.is_rate_limited", return_value=False)
    @patch("src.exchange.binance_ws.manager.positions_fresh", return_value=False)
    def test_flush_uses_symbol_reconcile_not_full_book(
        self, _fresh, _limited, sym_rec, full_rec
    ):
        from src.exchange.binance_ws import manager as m

        m.on_order_placed("NEARUSDT")
        with patch("src.exchange.binance_ws.manager._UDS_WAIT_SEC", 0.0):
            m.flush_pending_reconcile(wait_uds_sec=0.0)
        sym_rec.assert_called_once()
        full_rec.assert_not_called()
        self.assertFalse(m.pending_reconcile())

    @patch("src.exchange.binance_ws.manager._reconcile_symbols_rest", return_value=False)
    @patch("src.exchange.binance.is_rate_limited", return_value=False)
    @patch("src.exchange.binance_ws.manager.positions_fresh", return_value=False)
    def test_failed_reconcile_keeps_pending(self, _fresh, _limited, sym_rec):
        from src.exchange.binance_ws import manager as m

        m.on_order_placed("NEARUSDT")
        with patch("src.exchange.binance_ws.manager._UDS_WAIT_SEC", 0.0):
            m.flush_pending_reconcile(wait_uds_sec=0.0)
        self.assertTrue(m.pending_reconcile())
        self.assertIn("NEARUSDT", m._pending_reconcile_symbols)


if __name__ == "__main__":
    unittest.main()
