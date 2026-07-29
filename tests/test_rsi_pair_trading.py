import unittest
from unittest.mock import patch

from src.exchange.types import Position
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

    @patch("src.rsi_trading._flush_post_order_reconcile")
    @patch("src.rsi_trading._format_close_size", return_value="2")
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=103.0)
    @patch("src.rsi_trading._open_pair")
    @patch("src.rsi_trading._take_profit_aggregate_side")
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    @patch("src.rsi_trading.is_tradeable_symbol", return_value=True)
    def test_batch_lot_tp_one_close_one_reopen(
        self,
        _tradeable,
        fetch_positions,
        get_lots,
        take_agg,
        open_pair,
        close_side,
        close_db,
        _fmt,
        _flush,
    ):
        """Two lots hit TP but aggregate avg does not → one batched close + one reopen."""
        # Aggregate avg 100 → mark 101 = 1% < 2% default TP
        fetch_positions.return_value = _positions(
            long_size=2.0, long_avg=100.0, short_size=0.0,
        )
        get_lots.return_value = [
            _make_lot(id=11, long_entry=98.0, long_size=1.0, short_status="closed"),
            _make_lot(id=12, long_entry=98.5, long_size=1.0, short_status="closed"),
        ]
        snap = RsiSnapshot(ready=True, rsi=26.0, cross_up_25=True, close=101.0)
        # mark 101 vs entry 98 = ~3% → both lots TP; agg 100→101 = 1% no
        with patch("src.notify.notify_close") as notify:
            result = _scan_take_profits(
                "BTCUSDT",
                101.0,
                snap,
                trigger="rsi_cross_25",
                reopen_pair=True,
                tp_target_pct=2.0,
            )
        self.assertTrue(result)
        take_agg.assert_not_called()
        close_side.assert_called_once()
        self.assertEqual(close_side.call_args[0][1], "long")
        self.assertAlmostEqual(close_side.call_args[0][2], 2.0)
        self.assertEqual(close_db.call_count, 2)
        open_pair.assert_called_once()
        self.assertIn("tp_batch_long", open_pair.call_args[0][2])
        notify.assert_called_once()

    @patch("src.rsi_trading._flush_post_order_reconcile")
    @patch("src.rsi_trading._format_close_size", return_value="2")
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=103.0)
    @patch("src.rsi_trading._open_pair")
    @patch("src.rsi_trading._take_profit_aggregate_side")
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_batch_lot_tp_cycle_no_reopen(
        self,
        fetch_positions,
        get_lots,
        take_agg,
        open_pair,
        close_side,
        close_db,
        _fmt,
        _flush,
    ):
        fetch_positions.return_value = _positions(
            long_size=2.0, long_avg=100.0, short_size=0.0,
        )
        get_lots.return_value = [
            _make_lot(id=1, long_entry=98.0, long_size=1.0, short_status="closed"),
            _make_lot(id=2, long_entry=98.0, long_size=1.0, short_status="closed"),
        ]
        snap = RsiSnapshot(ready=True, rsi=50.0, close=101.0)
        with patch("src.notify.notify_close"):
            result = _scan_take_profits(
                "BTCUSDT", 101.0, snap, trigger="cycle", reopen_pair=False,
                tp_target_pct=2.0,
            )
        self.assertTrue(result)
        close_side.assert_called_once()
        open_pair.assert_not_called()
        self.assertEqual(close_db.call_count, 2)

    @patch("src.rsi_trading._flush_post_order_reconcile")
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading._close_side_and_resolve_fill")
    @patch("src.rsi_trading._open_pair")
    @patch("src.rsi_trading._take_profit_aggregate_side")
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_batch_tp_skips_when_exchange_flat(
        self,
        fetch_positions,
        get_lots,
        take_agg,
        open_pair,
        close_side,
        close_db,
        _flush,
    ):
        """Phantom SHORT lots in DB but exchange S=0 → no reduceOnly order."""
        fetch_positions.return_value = _positions(
            long_size=1.0, long_avg=100.0, short_size=0.0,
        )
        lot_a = _make_lot(
            id=1722,
            long_status="closed",
            short_entry=0.40,
            short_size=500.0,
        )
        lot_b = _make_lot(
            id=1730,
            long_status="closed",
            short_entry=0.41,
            short_size=500.0,
        )
        # Trim reads lots repeatedly; after each close_db, still return remaining
        # until closed_ids guard stops — return same list is fine with guard.
        get_lots.return_value = [lot_a, lot_b]
        snap = RsiSnapshot(ready=True, rsi=50.0, close=0.30)
        with patch("src.notify.notify_close"):
            result = _scan_take_profits(
                "FETUSDT",
                0.30,
                snap,
                trigger="cycle",
                reopen_pair=False,
                tp_target_pct=2.0,
            )
        self.assertFalse(result)
        take_agg.assert_not_called()
        close_side.assert_not_called()
        open_pair.assert_not_called()
        self.assertEqual(close_db.call_count, 2)
        sides = [c.args[1] for c in close_db.call_args_list]
        self.assertEqual(sides, ["short", "short"])


class TestSyncLotsWithExchange(unittest.TestCase):
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading.fetch_symbol_positions")
    def test_trims_phantom_short_lots(self, fetch_positions, get_lots, close_db):
        from src.rsi_trading import _sync_lots_with_exchange

        fetch_positions.return_value = _positions(long_size=1.0, short_size=0.0)
        lot_a = _make_lot(id=1, long_size=1.0, short_size=2.0)
        lot_b = _make_lot(id=2, long_status="closed", short_size=3.0)
        # closed_ids guard lets us return the same open list until both ids closed
        get_lots.return_value = [lot_a, lot_b]
        _sync_lots_with_exchange("FETUSDT")
        self.assertEqual(close_db.call_count, 2)
        self.assertTrue(all(c.args[1] == "short" for c in close_db.call_args_list))
        closed_ids = {c.args[0] for c in close_db.call_args_list}
        self.assertEqual(closed_ids, {1, 2})


class TestEvaluateRsiTrade(unittest.TestCase):
    def test_no_cross_runs_cycle_scan_only(self):
        with (
            patch("src.margin_guard.should_block_new_entries", return_value=False),
            patch("src.margin_guard.effective_tp_pct", return_value=2.0),
            patch("src.rsi_trading.MARGIN_PREFLIGHT_ENABLED", False),
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
            patch("src.margin_guard.should_block_new_entries", return_value=False),
            patch("src.margin_guard.effective_tp_pct", return_value=2.0),
            patch("src.rsi_trading.MARGIN_PREFLIGHT_ENABLED", False),
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
            patch("src.margin_guard.should_block_new_entries", return_value=False),
            patch("src.margin_guard.effective_tp_pct", return_value=2.0),
            patch("src.rsi_trading.MARGIN_PREFLIGHT_ENABLED", False),
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
            patch("src.margin_guard.should_block_new_entries", return_value=False),
            patch("src.margin_guard.effective_tp_pct", return_value=2.0),
            patch("src.rsi_trading.MARGIN_PREFLIGHT_ENABLED", False),
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
