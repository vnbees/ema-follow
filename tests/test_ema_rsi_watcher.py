import unittest
from unittest.mock import MagicMock, patch

from src.ema_rsi import store
from src.ema_rsi.watcher import _order_filled, on_position_flat


class TestOrderFilled(unittest.TestCase):
    def test_triggered_algo_is_not_filled(self) -> None:
        self.assertFalse(_order_filled({"status": "triggered", "avgPrice": 0}))

    def test_finished_requires_fill_price(self) -> None:
        self.assertFalse(_order_filled({"status": "finished", "avgPrice": 0}))
        self.assertTrue(_order_filled({"status": "finished", "avgPrice": 1.23}))

    def test_market_filled(self) -> None:
        self.assertTrue(_order_filled({"status": "filled", "avgPrice": 1.0}))


class TestOnPositionFlat(unittest.TestCase):
    def test_skips_when_position_still_open(self) -> None:
        trade = {"id": 1, "symbol": "BTWUSDT", "side": "short", "sl_order_id": "1", "tp_order_id": "2"}
        with (
            patch("src.ema_rsi.watcher.store.get_open_trade_for_symbol", return_value=trade),
            patch("src.ema_rsi.watcher.position_confirmed_flat", return_value=False),
            patch("src.ema_rsi.watcher.finalize_close") as finalize,
        ):
            on_position_flat("BTWUSDT", "short")
            finalize.assert_not_called()

    def test_no_infer_close_without_filled_order(self) -> None:
        trade = {
            "id": 1,
            "symbol": "BTWUSDT",
            "side": "short",
            "sl": 0.395,
            "tp": 0.295,
            "sl_order_id": "sl",
            "tp_order_id": "tp",
        }
        with (
            patch("src.ema_rsi.watcher.store.get_open_trade_for_symbol", return_value=trade),
            patch("src.ema_rsi.watcher.position_confirmed_flat", return_value=True),
            patch("src.ema_rsi.watcher._reason_from_orders", return_value=None),
            patch("src.ema_rsi.watcher.finalize_close") as finalize,
        ):
            on_position_flat("BTWUSDT", "short")
            finalize.assert_not_called()


class TestOrphanReopen(unittest.TestCase):
    def test_reopen_trade_clears_close_fields(self) -> None:
        with patch("src.ema_rsi.store.get_connection") as gc:
            conn = MagicMock()
            gc.return_value.__enter__.return_value = conn
            conn.execute.return_value.rowcount = 1
            self.assertTrue(store.reopen_trade(2))


if __name__ == "__main__":
    unittest.main()
