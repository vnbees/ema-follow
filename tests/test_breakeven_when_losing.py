import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.exchange.types import Position
from src import rsi_trading
from src.rsi_trading import (
    _scan_breakeven_closes,
    arm_breakeven_if_needed,
    clear_breakeven_arm,
    is_breakeven_armed,
    reset_breakeven_arms,
)


def _positions(
    symbol: str = "BTCUSDT",
    long_size: float = 0.0,
    long_avg: float = 100.0,
    short_size: float = 0.0,
    short_avg: float = 100.0,
) -> dict[str, Position]:
    return {
        "long": Position(symbol, "long", long_size, long_avg),
        "short": Position(symbol, "short", short_size, short_avg),
    }


def _iso_hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _make_lot(**kwargs) -> dict:
    base = {
        "id": 1,
        "symbol": "BTCUSDT",
        "long_status": "open",
        "long_entry": 100.0,
        "long_size": 1.0,
        "short_status": "closed",
        "short_entry": 100.0,
        "short_size": 0.0,
        "opened_at": _iso_hours_ago(30),
    }
    base.update(kwargs)
    return base


@patch("src.rsi_trading._format_close_size", side_effect=lambda _s, size: str(size))
class TestBreakevenWhenLosing(unittest.TestCase):
    def setUp(self):
        reset_breakeven_arms()

    def tearDown(self):
        reset_breakeven_arms()

    @patch("src.rsi_trading.BREAKEVEN_WHEN_LOSING_ENABLED", False)
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_disabled_when_flag_off(self, fetch_positions, _fmt):
        result = _scan_breakeven_closes("BTCUSDT", 100.0)
        self.assertFalse(result)
        fetch_positions.assert_not_called()

    @patch("src.notify.notify_close")
    @patch("src.rsi_trading.BREAKEVEN_AFTER_HOURS", 24.0)
    @patch("src.rsi_trading.BREAKEVEN_WHEN_LOSING_ENABLED", True)
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=100.0)
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_young_underwater_lot_not_closed(
        self,
        fetch_positions,
        get_lots,
        close_fill,
        close_lot_side,
        _notify,
        _fmt,
    ):
        fetch_positions.return_value = _positions(long_size=1.0)
        get_lots.return_value = [_make_lot(opened_at=_iso_hours_ago(10))]
        # Underwater but only 10h old → no BE close.
        result = _scan_breakeven_closes("BTCUSDT", 95.0)
        self.assertFalse(result)
        close_fill.assert_not_called()
        close_lot_side.assert_not_called()
        self.assertFalse(is_breakeven_armed(1, "long"))

    @patch("src.notify.notify_close")
    @patch("src.rsi_trading.BREAKEVEN_AFTER_HOURS", 24.0)
    @patch("src.rsi_trading.BREAKEVEN_WHEN_LOSING_ENABLED", True)
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=100.5)
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_old_green_lot_not_be_closed(
        self,
        fetch_positions,
        get_lots,
        close_fill,
        close_lot_side,
        _notify,
        _fmt,
    ):
        fetch_positions.return_value = _positions(long_size=1.0)
        get_lots.return_value = [_make_lot(opened_at=_iso_hours_ago(30))]
        # Profitable but below TP — keep hunting TP, do not BE-close.
        result = _scan_breakeven_closes("BTCUSDT", 100.5)
        self.assertFalse(result)
        close_fill.assert_not_called()
        self.assertFalse(is_breakeven_armed(1, "long"))

    @patch("src.notify.notify_close")
    @patch("src.rsi_trading.BREAKEVEN_AFTER_HOURS", 24.0)
    @patch("src.rsi_trading.BREAKEVEN_WHEN_LOSING_ENABLED", True)
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=99.0)
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_old_underwater_not_yet_at_entry(
        self,
        fetch_positions,
        get_lots,
        close_fill,
        close_lot_side,
        _notify,
        _fmt,
    ):
        fetch_positions.return_value = _positions(long_size=1.0)
        get_lots.return_value = [_make_lot(opened_at=_iso_hours_ago(30))]
        result = _scan_breakeven_closes("BTCUSDT", 95.0)
        self.assertFalse(result)
        close_fill.assert_not_called()
        # Armed while waiting for BE.
        self.assertTrue(is_breakeven_armed(1, "long"))

    @patch("src.notify.notify_close")
    @patch("src.rsi_trading.BREAKEVEN_AFTER_HOURS", 24.0)
    @patch("src.rsi_trading.BREAKEVEN_WHEN_LOSING_ENABLED", True)
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=100.0)
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_old_underwater_closes_at_breakeven(
        self,
        fetch_positions,
        get_lots,
        close_fill,
        close_lot_side,
        _notify,
        _fmt,
    ):
        fetch_positions.return_value = _positions(long_size=1.0)
        get_lots.return_value = [_make_lot(opened_at=_iso_hours_ago(30))]
        # First tick underwater → arm.
        _scan_breakeven_closes("BTCUSDT", 95.0)
        self.assertTrue(is_breakeven_armed(1, "long"))
        # Second tick back at entry → close.
        result = _scan_breakeven_closes("BTCUSDT", 100.0)
        self.assertTrue(result)
        close_fill.assert_called_once()
        close_lot_side.assert_called_once()
        self.assertEqual(close_lot_side.call_args[0][0], 1)
        self.assertEqual(close_lot_side.call_args[0][1], "long")
        self.assertFalse(is_breakeven_armed(1, "long"))

    @patch("src.rsi_trading.BREAKEVEN_AFTER_HOURS", 24.0)
    @patch("src.rsi_trading.BREAKEVEN_WHEN_LOSING_ENABLED", True)
    def test_sticky_arm_persists_after_recovery_above_entry(self, _fmt):
        opened = datetime.now(timezone.utc) - timedelta(hours=30)
        # Go underwater → arm.
        self.assertTrue(
            arm_breakeven_if_needed(7, "long", opened, entry=100.0, mark=95.0)
        )
        self.assertTrue(is_breakeven_armed(7, "long"))
        # Recover to +0.3% — still armed (sticky), so BE target stays 0%.
        self.assertTrue(
            arm_breakeven_if_needed(7, "long", opened, entry=100.0, mark=100.3)
        )
        self.assertTrue(is_breakeven_armed(7, "long"))
        clear_breakeven_arm(7, "long")
        self.assertFalse(is_breakeven_armed(7, "long"))

    @patch("src.notify.notify_close")
    @patch("src.rsi_trading.BREAKEVEN_AFTER_HOURS", 24.0)
    @patch("src.rsi_trading.BREAKEVEN_WHEN_LOSING_ENABLED", True)
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=100.0)
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_sticky_closes_when_mark_back_at_entry_after_partial_recovery(
        self,
        fetch_positions,
        get_lots,
        close_fill,
        close_lot_side,
        _notify,
        _fmt,
    ):
        fetch_positions.return_value = _positions(long_size=1.0)
        get_lots.return_value = [_make_lot(id=3, opened_at=_iso_hours_ago(40))]
        _scan_breakeven_closes("BTCUSDT", 90.0)  # arm
        # Partial recovery still below entry — wait.
        self.assertFalse(_scan_breakeven_closes("BTCUSDT", 98.0))
        close_fill.assert_not_called()
        # At BE → close (sticky), even though we never re-saw underwater this tick.
        self.assertTrue(_scan_breakeven_closes("BTCUSDT", 100.0))
        close_lot_side.assert_called_once()
        self.assertEqual(close_lot_side.call_args[0][0], 3)

    @patch("src.notify.notify_close")
    @patch("src.rsi_trading._open_pair")
    @patch("src.rsi_trading.BREAKEVEN_AFTER_HOURS", 24.0)
    @patch("src.rsi_trading.BREAKEVEN_WHEN_LOSING_ENABLED", True)
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=100.0)
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_be_close_does_not_reopen(
        self,
        fetch_positions,
        get_lots,
        _close_fill,
        _close_lot_side,
        _open_pair,
        _notify,
        _fmt,
    ):
        fetch_positions.return_value = _positions(long_size=1.0)
        get_lots.return_value = [_make_lot(opened_at=_iso_hours_ago(30))]
        _scan_breakeven_closes("BTCUSDT", 95.0)
        _scan_breakeven_closes("BTCUSDT", 100.0)
        _open_pair.assert_not_called()


class TestIsUnderwater(unittest.TestCase):
    def test_long_short(self):
        from src.rsi_signals import is_underwater

        self.assertTrue(is_underwater("long", 100.0, 99.0))
        self.assertFalse(is_underwater("long", 100.0, 100.0))
        self.assertFalse(is_underwater("long", 100.0, 101.0))
        self.assertTrue(is_underwater("short", 100.0, 101.0))
        self.assertFalse(is_underwater("short", 100.0, 99.0))


if __name__ == "__main__":
    unittest.main()
