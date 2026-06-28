import unittest
from unittest.mock import patch

from src.bitget_client import Position
from src.rsi import RsiSnapshot
from src.rsi_trading import _scan_take_profits, evaluate_rsi_trade


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


def _make_lot(**kwargs) -> dict:
    base = {
        "id": 1,
        "symbol": "BTCUSDT",
        "long_status": "open",
        "long_entry": 98.0,
        "long_size": 1.0,
        "short_status": "open",
        "short_entry": 102.0,
        "short_size": 1.0,
    }
    base.update(kwargs)
    return base


class TestScanTakeProfits(unittest.TestCase):
    @patch("src.rsi_trading._open_pair")
    @patch("src.rsi_trading._take_profit_lot_side")
    @patch("src.rsi_trading._take_profit_aggregate_side")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_cycle_no_reopen(
        self,
        fetch_positions,
        take_agg,
        take_lot,
        open_pair,
    ):
        fetch_positions.return_value = _positions(
            long_size=2.0, long_avg=100.0, short_size=2.0,
        )
        snap = RsiSnapshot(ready=True, rsi=50.0, close=102.5)
        result = _scan_take_profits(
            "BTCUSDT", 102.5, snap, trigger="cycle", reopen_pair=False,
        )
        self.assertTrue(result)
        take_agg.assert_called_once()
        self.assertFalse(take_agg.call_args.kwargs.get("reopen_pair", True))
        open_pair.assert_not_called()

    @patch("src.rsi_trading._open_pair")
    @patch("src.rsi_trading._take_profit_aggregate_side")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_cross_reopen_passed(
        self,
        fetch_positions,
        take_agg,
        open_pair,
    ):
        fetch_positions.return_value = _positions(
            long_size=2.0, long_avg=100.0,
        )
        snap = RsiSnapshot(ready=True, rsi=26.0, cross_up_25=True, close=102.5)
        _scan_take_profits(
            "BTCUSDT", 102.5, snap, trigger="rsi_cross_25", reopen_pair=True,
        )
        self.assertTrue(take_agg.call_args.kwargs["reopen_pair"])


class TestEvaluateRsiTrade(unittest.TestCase):
    def test_no_cross_runs_cycle_scan_only(self):
        with (
            patch("src.rsi_trading._open_pair") as open_pair,
            patch("src.rsi_trading._scan_take_profits") as scan_tp,
            patch("src.rsi_trading._update_status"),
            patch("src.rsi_trading._sync_lots_with_exchange"),
            patch("src.rsi_trading.ensure_symbol_configured"),
            patch("src.rsi_trading.fetch_side_mark_price", return_value=100.0),
            patch("src.rsi_trading.has_credentials", return_value=True),
            patch("src.rsi_trading.TRADING_ENABLED", True),
        ):
            scan_tp.return_value = False
            snap = RsiSnapshot(ready=True, rsi=50.0, prev_rsi=49.0)
            evaluate_rsi_trade("BTCUSDT", snap)
            scan_tp.assert_called_once()
            self.assertFalse(scan_tp.call_args.kwargs["reopen_pair"])
            open_pair.assert_not_called()

    def test_cross_tp_then_no_stack(self):
        with (
            patch("src.rsi_trading._open_pair") as open_pair,
            patch("src.rsi_trading._scan_take_profits") as scan_tp,
            patch("src.rsi_trading._update_status"),
            patch("src.rsi_trading._sync_lots_with_exchange"),
            patch("src.rsi_trading.ensure_symbol_configured"),
            patch("src.rsi_trading.fetch_side_mark_price", return_value=100.0),
            patch("src.rsi_trading.has_credentials", return_value=True),
            patch("src.rsi_trading.TRADING_ENABLED", True),
        ):
            scan_tp.side_effect = [False, True]
            snap = RsiSnapshot(
                ready=True, rsi=26.0, prev_rsi=24.0, cross_up_25=True, close=100.0,
            )
            evaluate_rsi_trade("BTCUSDT", snap)
            self.assertEqual(scan_tp.call_count, 2)
            self.assertTrue(scan_tp.call_args_list[1].kwargs["reopen_pair"])
            open_pair.assert_not_called()

    def test_cross_stack_when_no_tp(self):
        with (
            patch("src.rsi_trading._open_pair") as open_pair,
            patch("src.rsi_trading._scan_take_profits", return_value=False),
            patch("src.rsi_trading.db.symbol_has_open_lots", return_value=True),
            patch("src.rsi_trading._update_status"),
            patch("src.rsi_trading._sync_lots_with_exchange"),
            patch("src.rsi_trading.ensure_symbol_configured"),
            patch("src.rsi_trading.fetch_side_mark_price", return_value=100.0),
            patch("src.rsi_trading.has_credentials", return_value=True),
            patch("src.rsi_trading.TRADING_ENABLED", True),
        ):
            snap = RsiSnapshot(
                ready=True, rsi=26.0, prev_rsi=24.0, cross_up_25=True, close=100.5,
            )
            evaluate_rsi_trade("BTCUSDT", snap)
            open_pair.assert_called_once()
            self.assertIn("stack", open_pair.call_args[0][2])

    def test_first_pair_entry_for_new_symbol(self):
        with (
            patch("src.rsi_trading._open_pair") as open_pair,
            patch("src.rsi_trading._scan_take_profits", return_value=False),
            patch("src.rsi_trading.db.symbol_has_open_lots", return_value=False),
            patch("src.rsi_trading.can_open_new_symbol", return_value=True),
            patch("src.rsi_trading._update_status"),
            patch("src.rsi_trading._sync_lots_with_exchange"),
            patch("src.rsi_trading.ensure_symbol_configured"),
            patch("src.rsi_trading.fetch_side_mark_price", return_value=100.0),
            patch("src.rsi_trading.has_credentials", return_value=True),
            patch("src.rsi_trading.TRADING_ENABLED", True),
        ):
            snap = RsiSnapshot(
                ready=True, rsi=74.0, prev_rsi=76.0, cross_down_75=True, close=100.0,
            )
            evaluate_rsi_trade("ETHUSDT", snap)
            open_pair.assert_called_once_with("ETHUSDT", snap, "rsi_cross_75")


if __name__ == "__main__":
    unittest.main()
