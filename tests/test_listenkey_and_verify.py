import unittest
from unittest.mock import MagicMock, patch

from src.exchange.binance_ws import manager as ws_manager
from src.exchange.types import Position
from src.rsi_trading import _verify_side_reduced


class TestListenKeyLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        ws_manager._listen_key_validated = False

    def tearDown(self) -> None:
        ws_manager._listen_key_validated = False

    @patch("src.exchange.binance_ws.persist.save_listen_key")
    @patch("src.exchange.binance_ws.persist.clear_listen_key")
    @patch("src.exchange.binance_ws.persist.load_listen_key", return_value="dead-key")
    @patch("src.exchange.binance.is_rate_limited", return_value=False)
    @patch("src.exchange.binance.rate_limit_remaining_sec", return_value=0.0)
    def test_dead_disk_key_recreates(
        self,
        _remaining,
        _limited,
        _load,
        clear_key,
        save_key,
    ):
        from src.exchange import binance as binance_mod

        with (
            patch.object(
                binance_mod,
                "_private_request",
                side_effect=binance_mod.NonRetriableApiError(
                    "HTTP 400: code=-1125 msg=This listenKey does not exist."
                ),
            ),
            patch.object(
                binance_mod,
                "_private_post",
                return_value={"listenKey": "fresh-key"},
            ) as post,
        ):
            key = ws_manager._create_listen_key()
        self.assertEqual(key, "fresh-key")
        clear_key.assert_called()
        post.assert_called_once()
        save_key.assert_called_with("fresh-key")
        self.assertTrue(ws_manager._listen_key_validated)

    @patch("src.exchange.binance_ws.persist.clear_listen_key")
    @patch("src.exchange.binance.is_rate_limited", return_value=False)
    def test_keepalive_clears_dead_key(self, _limited, clear_key):
        from src.exchange import binance as binance_mod

        ws_manager._listen_key_validated = True
        with patch.object(
            binance_mod,
            "_private_request",
            side_effect=binance_mod.NonRetriableApiError(
                "HTTP 400: code=-1125 msg=This listenKey does not exist."
            ),
        ):
            with self.assertRaises(binance_mod.NonRetriableApiError):
                ws_manager._keepalive_listen_key("dead-key")
        clear_key.assert_called_once()
        self.assertFalse(ws_manager._listen_key_validated)


class TestVerifySideReduced(unittest.TestCase):
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_residual_above_closed_qty_is_ok(self, fetch_pos):
        # Closed 1.36 from 2.70 → remaining 1.34; must NOT warn.
        fetch_pos.return_value = {
            "long": Position("HYPEUSDT", "long", 1.34, 50.0),
            "short": Position("HYPEUSDT", "short", 0.0, 0.0),
        }
        with patch("src.rsi_trading.logging.error") as err:
            _verify_side_reduced("HYPEUSDT", "long", 2.70, closed_size=1.36)
            err.assert_not_called()

    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_unchanged_size_warns(self, fetch_pos):
        fetch_pos.return_value = {
            "long": Position("HYPEUSDT", "long", 2.70, 50.0),
            "short": Position("HYPEUSDT", "short", 0.0, 0.0),
        }
        with patch("src.rsi_trading.logging.error") as err:
            _verify_side_reduced("HYPEUSDT", "long", 2.70, closed_size=1.36)
            err.assert_called_once()


if __name__ == "__main__":
    unittest.main()
