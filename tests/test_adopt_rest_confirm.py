"""Adopt must not invent lots from stale WS ghosts."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.exchange import ExchangeClientError
from src.exchange.types import Position
from src.rsi_positions import sync_exchange_positions


def _pos(symbol: str, side: str, size: float, avg: float = 1.0) -> Position:
    return Position(symbol=symbol, side=side, size=size, avg_price=avg)


class TestAdoptRequiresRestConfirm(unittest.TestCase):
    @patch("src.rsi_positions.db")
    @patch("src.rsi_positions.fetch_all_open_positions")
    @patch("src.rsi_positions.has_credentials", return_value=True)
    def test_skips_adopt_when_rest_flat(self, _creds, fetch_all, db):
        db.get_all_open_pair_lots.return_value = []
        db.symbol_has_open_lots.return_value = False
        fetch_all.return_value = [_pos("NEARUSDT", "long", 43.0, 1.74)]

        rest_flat = {
            "long": _pos("NEARUSDT", None, 0.0),
            "short": _pos("NEARUSDT", None, 0.0),
        }
        cache = MagicMock()
        with (
            patch(
                "src.exchange.binance.fetch_symbol_positions_rest",
                return_value=rest_flat,
            ) as rest,
            patch("src.exchange.binance_ws.cache.CACHE", cache),
        ):
            managed = sync_exchange_positions()

        rest.assert_called_once_with("NEARUSDT")
        db.insert_pair_lot.assert_not_called()
        cache.apply_position_updates.assert_called_once()
        self.assertEqual(managed, [])

    @patch("src.rsi_positions.db")
    @patch("src.rsi_positions.fetch_all_open_positions")
    @patch("src.rsi_positions.has_credentials", return_value=True)
    def test_adopts_after_rest_confirms(self, _creds, fetch_all, db):
        db.get_all_open_pair_lots.return_value = []
        db.symbol_has_open_lots.return_value = False
        db.insert_pair_lot.return_value = 99
        fetch_all.return_value = [_pos("NEARUSDT", "long", 43.0, 1.74)]

        rest_live = {
            "long": _pos("NEARUSDT", "long", 43.0, 1.74),
            "short": _pos("NEARUSDT", None, 0.0),
        }
        with patch(
            "src.exchange.binance.fetch_symbol_positions_rest",
            return_value=rest_live,
        ):
            managed = sync_exchange_positions()

        db.insert_pair_lot.assert_called_once()
        db.close_lot_side.assert_called_once_with(
            99, "short", realized_pnl_usdt=0.0, close_price=None,
        )
        self.assertEqual(managed, ["NEARUSDT"])

    @patch("src.rsi_positions.db")
    @patch("src.rsi_positions.fetch_all_open_positions")
    @patch("src.rsi_positions.has_credentials", return_value=True)
    def test_skips_adopt_when_rest_errors(self, _creds, fetch_all, db):
        db.get_all_open_pair_lots.return_value = []
        db.symbol_has_open_lots.return_value = False
        fetch_all.return_value = [_pos("NEARUSDT", "long", 43.0, 1.74)]
        with patch(
            "src.exchange.binance.fetch_symbol_positions_rest",
            side_effect=ExchangeClientError("boom"),
        ):
            sync_exchange_positions()
        db.insert_pair_lot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
