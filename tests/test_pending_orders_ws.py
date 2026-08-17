"""openOrders must not REST every 5m cycle when UDS is alive."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.exchange.binance_ws.cache import CACHE
from src.exchange.types import PendingOrder


class TestPendingOrdersWsFirst(unittest.TestCase):
    def setUp(self) -> None:
        from src.exchange import binance as binance_mod

        CACHE.pending_by_symbol.clear()
        CACHE.user_connected = True
        CACHE.user_last_msg_at = CACHE.user_last_msg_at or 1.0
        # Force "touched recently"
        from src.exchange.binance_ws import cache as cache_mod

        CACHE.user_last_msg_at = cache_mod._now()
        CACHE.positions_updated_at = cache_mod._now()
        binance_mod._pending_orders_rest_at_mono.clear()
        binance_mod._pending_orders_rest_cache.clear()

    def tearDown(self) -> None:
        CACHE.pending_by_symbol.clear()
        CACHE.user_connected = False
        CACHE.user_last_msg_at = 0.0

    def test_missing_pending_key_returns_empty_not_none(self) -> None:
        from src.exchange.binance_ws import manager as mgr

        with (
            patch.object(mgr, "is_ws_enabled", return_value=True),
            patch.object(mgr, "_user_stream_alive", return_value=True),
        ):
            out = mgr.get_pending_from_ws("BTCUSDT")
        self.assertEqual(out, [])

    def test_fetch_pending_skips_rest_when_uds_alive(self) -> None:
        from src.exchange import binance as binance_mod

        with (
            patch(
                "src.exchange.binance_ws.get_pending_from_ws",
                return_value=[],
            ),
            patch.object(binance_mod, "_private_get") as get,
        ):
            out = binance_mod.fetch_pending_orders("BTCUSDT")
        self.assertEqual(out, [])
        get.assert_not_called()

    def test_fetch_pending_uses_ws_limits(self) -> None:
        from src.exchange import binance as binance_mod

        order = PendingOrder("1", "c", "buy", 1.0, 2.0)
        with (
            patch(
                "src.exchange.binance_ws.get_pending_from_ws",
                return_value=[order],
            ),
            patch.object(binance_mod, "_private_get") as get,
        ):
            out = binance_mod.fetch_pending_orders("BTCUSDT")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].order_id, "1")
        get.assert_not_called()

    def test_rest_fallback_throttled(self) -> None:
        from src.exchange import binance as binance_mod

        rows = [
            {
                "orderId": 9,
                "clientOrderId": "x",
                "side": "BUY",
                "type": "LIMIT",
                "price": "1",
                "origQty": "2",
            }
        ]
        with (
            patch("src.exchange.binance_ws.get_pending_from_ws", return_value=None),
            patch.object(binance_mod, "is_optional_rest_blocked", return_value=False),
            patch.object(binance_mod, "_private_get", return_value=rows) as get,
        ):
            a = binance_mod.fetch_pending_orders("ETHUSDT")
            b = binance_mod.fetch_pending_orders("ETHUSDT")
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertEqual(get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
