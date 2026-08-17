import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.exchange.types import Position
from src import rsi_trading
from src.rsi_trading import _scan_max_age_closes, reset_age_close_budget


def setUpModule() -> None:
    rsi_trading.PAIR_REOPEN_ON_CLOSE = False


def tearDownModule() -> None:
    rsi_trading.PAIR_REOPEN_ON_CLOSE = True


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


def _iso_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


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
        "opened_at": _iso_days_ago(5),
    }
    base.update(kwargs)
    return base


@patch("src.rsi_trading._format_close_size", side_effect=lambda _s, size: str(size))
class TestMaxAgeRule(unittest.TestCase):
    def setUp(self):
        reset_age_close_budget()

    @patch("src.notify.notify_close")
    @patch("src.rsi_trading.MAX_LOT_AGE_DAYS", 3.0)
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=99.0)
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_old_lot_closed_young_lot_kept(
        self,
        fetch_positions,
        get_lots,
        close_fill,
        close_lot_side,
        _notify,
        _fmt,
    ):
        fetch_positions.return_value = _positions(long_size=2.0)
        get_lots.return_value = [
            _make_lot(id=1, opened_at=_iso_days_ago(5)),
            _make_lot(id=2, opened_at=_iso_days_ago(1)),
        ]
        result = _scan_max_age_closes("BTCUSDT", 99.0)
        self.assertTrue(result)
        # Only the 5-day-old lot is batch-closed.
        close_fill.assert_called_once()
        self.assertAlmostEqual(close_fill.call_args[0][2], 1.0)
        close_lot_side.assert_called_once()
        self.assertEqual(close_lot_side.call_args[0][0], 1)
        self.assertEqual(close_lot_side.call_args[0][1], "long")
        self.assertEqual(close_lot_side.call_args.kwargs.get("close_reason"), "age")

    @patch("src.rsi_trading.MAX_LOT_AGE_DAYS", 0.0)
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_disabled_when_zero_days(self, fetch_positions, _fmt):
        result = _scan_max_age_closes("BTCUSDT", 100.0)
        self.assertFalse(result)
        fetch_positions.assert_not_called()

    @patch("src.notify.notify_close")
    @patch("src.rsi_trading.MAX_LOT_AGE_DAYS", 3.0)
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=99.0)
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_both_sides_expired_close_two_orders(
        self,
        fetch_positions,
        get_lots,
        close_fill,
        close_lot_side,
        _notify,
        _fmt,
    ):
        fetch_positions.return_value = _positions(long_size=1.0, short_size=1.0)
        get_lots.return_value = [
            _make_lot(
                id=7,
                long_status="open",
                short_status="open",
                short_size=1.0,
                opened_at=_iso_days_ago(4),
            ),
        ]
        result = _scan_max_age_closes("BTCUSDT", 99.0)
        self.assertTrue(result)
        self.assertEqual(close_fill.call_count, 2)  # one batch order per side
        sides = {c.args[1] for c in close_lot_side.call_args_list}
        self.assertEqual(sides, {"long", "short"})

    @patch("src.notify.notify_close")
    @patch("src.rsi_trading.MAX_LOT_AGE_DAYS", 3.0)
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=99.0)
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_budget_limits_orders_per_cycle(
        self,
        fetch_positions,
        get_lots,
        close_fill,
        _close_lot_side,
        _notify,
        _fmt,
    ):
        fetch_positions.return_value = _positions(long_size=1.0, short_size=1.0)
        get_lots.return_value = [
            _make_lot(
                id=9,
                long_status="open",
                short_status="open",
                short_size=1.0,
                opened_at=_iso_days_ago(10),
            ),
        ]
        with patch("src.rsi_trading.MAX_AGE_CLOSES_PER_CYCLE", 1):
            reset_age_close_budget()
            _scan_max_age_closes("BTCUSDT", 99.0)
        # Budget 1 → only the long batch goes out; short waits for next cycle.
        self.assertEqual(close_fill.call_count, 1)
        self.assertEqual(rsi_trading.age_close_budget_remaining(), 0)
        # Next symbol in the same cycle gets nothing.
        result = _scan_max_age_closes("ETHUSDT", 99.0)
        self.assertFalse(result)

    @patch("src.notify.notify_close")
    @patch("src.rsi_trading.MAX_LOT_AGE_DAYS", 3.0)
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=99.0)
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_never_exceeds_exchange_size(
        self,
        fetch_positions,
        get_lots,
        close_fill,
        close_lot_side,
        _notify,
        _fmt,
    ):
        # Exchange only has 1.0 long but two expired lots of 1.0 each.
        fetch_positions.return_value = _positions(long_size=1.0)
        get_lots.return_value = [
            _make_lot(id=1, opened_at=_iso_days_ago(6)),
            _make_lot(id=2, opened_at=_iso_days_ago(5)),
        ]
        _scan_max_age_closes("BTCUSDT", 99.0)
        close_fill.assert_called_once()
        self.assertAlmostEqual(close_fill.call_args[0][2], 1.0)
        close_lot_side.assert_called_once()

    @patch("src.notify.notify_close")
    @patch("src.rsi_trading._maybe_reopen_pair")
    @patch("src.rsi_trading.PAIR_REOPEN_ON_CLOSE", True)
    @patch("src.rsi_trading.MAX_LOT_AGE_DAYS", 3.0)
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=99.0)
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_age_close_does_not_reopen(
        self,
        fetch_positions,
        get_lots,
        _close_fill,
        _close_lot_side,
        maybe_reopen,
        _notify,
        _fmt,
    ):
        rsi_trading.PAIR_REOPEN_ON_CLOSE = True
        try:
            reset_age_close_budget()
            fetch_positions.return_value = _positions(long_size=1.0)
            get_lots.return_value = [_make_lot(id=1, opened_at=_iso_days_ago(6))]
            self.assertTrue(_scan_max_age_closes("BTCUSDT", 99.0, reopen_pair=True))
            maybe_reopen.assert_not_called()
        finally:
            rsi_trading.PAIR_REOPEN_ON_CLOSE = False


if __name__ == "__main__":
    unittest.main()
