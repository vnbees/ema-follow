"""When exchange is flat but DB/cache still show size, close must not spam orders."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.exchange import ExchangeClientError
from src.exchange.types import Position
from src.rsi_trading import (
    AlreadyFlatError,
    _close_side_and_resolve_fill,
    _is_already_flat_error,
    _mark_exchange_side_flat,
)


def _pos(symbol: str, size: float, avg: float = 1.0) -> Position:
    return Position(symbol=symbol, side=None, size=size, avg_price=avg)


class TestAlreadyFlatHelpers(unittest.TestCase):
    def test_detects_reduce_only_code(self):
        self.assertTrue(
            _is_already_flat_error(
                ExchangeClientError("HTTP 400: code=-2022 msg=ReduceOnly Order is rejected.")
            )
        )
        self.assertFalse(_is_already_flat_error(ExchangeClientError("HTTP 400: code=-2019")))


class TestCloseSideAlreadyFlat(unittest.TestCase):
    @patch("src.rsi_trading._mark_exchange_side_flat", return_value=1)
    @patch("src.rsi_trading.close_position_side")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_skips_order_when_cache_already_flat(
        self, fetch_positions, close_side, mark_flat
    ):
        fetch_positions.return_value = {
            "long": _pos("NEARUSDT", 0.0),
            "short": _pos("NEARUSDT", 0.0),
        }
        with self.assertRaises(AlreadyFlatError):
            _close_side_and_resolve_fill("NEARUSDT", "long", 43.0, 1.75)
        close_side.assert_not_called()
        mark_flat.assert_called_once_with("NEARUSDT", "long")

    @patch("src.rsi_trading._format_close_size", return_value="43")
    @patch("src.rsi_trading._mark_exchange_side_flat", return_value=1)
    @patch("src.rsi_trading.close_position_side")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_trims_on_reduce_only_reject(
        self, fetch_positions, close_side, mark_flat, _fmt
    ):
        fetch_positions.return_value = {
            "long": _pos("NEARUSDT", 43.0, 1.74),
            "short": _pos("NEARUSDT", 0.0),
        }
        close_side.side_effect = ExchangeClientError(
            "HTTP 400: code=-2022 msg=ReduceOnly Order is rejected."
        )
        with self.assertRaises(AlreadyFlatError):
            _close_side_and_resolve_fill("NEARUSDT", "long", 43.0, 1.75)
        close_side.assert_called_once()
        mark_flat.assert_called_once_with("NEARUSDT", "long")


class TestMarkExchangeSideFlat(unittest.TestCase):
    @patch("src.rsi_trading._trim_lot_side_to_exchange", return_value=1)
    @patch("src.exchange.binance_ws.cache.CACHE")
    @patch("src.exchange.binance._invalidate_position_cache")
    def test_invalidates_and_trims(self, invalidate, cache, trim):
        cache.apply_position_updates = MagicMock()
        n = _mark_exchange_side_flat("NEARUSDT", "long")
        self.assertEqual(n, 1)
        invalidate.assert_called_once_with("NEARUSDT")
        cache.apply_position_updates.assert_called_once()
        trim.assert_called_once_with("NEARUSDT", "long", 0.0)


if __name__ == "__main__":
    unittest.main()
