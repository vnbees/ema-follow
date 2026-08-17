"""Inventory pair mode: orphan BE target + reopen-on-close + no RSI entry."""

import unittest
from unittest.mock import patch

from src.rsi import RsiSnapshot
from src.rsi_trading import (
    ensure_one_inventory_symbol,
    evaluate_rsi_trade,
    leg_tp_target_pct,
    partner_is_closed,
)


def _lot(
    *,
    lot_id: int = 1,
    long_status: str = "open",
    short_status: str = "open",
    long_entry: float = 100.0,
    short_entry: float = 100.0,
    long_size: float = 1.0,
    short_size: float = 1.0,
) -> dict:
    return {
        "id": lot_id,
        "symbol": "BTCUSDT",
        "long_status": long_status,
        "short_status": short_status,
        "long_entry": long_entry,
        "short_entry": short_entry,
        "long_size": long_size,
        "short_size": short_size,
        "opened_at": "2026-01-01T00:00:00+00:00",
    }


class TestOrphanBeTarget(unittest.TestCase):
    def test_both_open_uses_base_tp(self) -> None:
        lot = _lot()
        self.assertFalse(partner_is_closed(lot, "long"))
        self.assertAlmostEqual(leg_tp_target_pct(lot, "long", base_tp_pct=0.5), 0.5)
        self.assertAlmostEqual(leg_tp_target_pct(lot, "short", base_tp_pct=0.5), 0.5)

    def test_partner_closed_forces_be(self) -> None:
        lot = _lot(long_status="closed", short_status="open")
        self.assertTrue(partner_is_closed(lot, "short"))
        self.assertAlmostEqual(leg_tp_target_pct(lot, "short", base_tp_pct=0.5), 0.0)
        self.assertAlmostEqual(leg_tp_target_pct(lot, "long", base_tp_pct=0.5), 0.5)


class TestInventoryNoRsiEntry(unittest.TestCase):
    @patch("src.rsi_trading.RSI_ENTRY_ENABLED", False)
    @patch("src.rsi_trading.PAIR_REOPEN_ON_CLOSE", True)
    @patch("src.rsi_trading.has_credentials", return_value=True)
    @patch("src.rsi_trading._trading_enabled", return_value=True)
    @patch("src.rsi_trading.is_tradeable_symbol", return_value=True)
    @patch("src.rsi_trading._sync_lots_with_exchange")
    @patch("src.rsi_trading._update_status")
    @patch("src.rsi_trading._scan_take_profits", return_value=False)
    @patch("src.rsi_trading._scan_breakeven_closes", return_value=False)
    @patch("src.rsi_trading._scan_max_age_closes", return_value=False)
    @patch("src.rsi_trading._open_pair")
    @patch("src.rsi_trading.should_block_new_entries", create=True)
    def test_evaluate_skips_rsi_stack(
        self,
        _block,
        open_pair,
        _age,
        _be,
        scan_tp,
        _status,
        _sync,
        *_rest,
    ) -> None:
        with patch("src.margin_guard.effective_tp_pct", return_value=0.5), patch(
            "src.margin_guard.should_block_new_entries", return_value=False
        ):
            snap = RsiSnapshot(
                ready=True,
                rsi=30.0,
                prev_rsi=20.0,
                close=100.0,
                cross_up_25=True,
            )
            evaluate_rsi_trade("BTCUSDT", snap, mark=100.0)
        open_pair.assert_not_called()
        scan_tp.assert_called_once()
        self.assertTrue(scan_tp.call_args.kwargs["reopen_pair"])
        self.assertAlmostEqual(scan_tp.call_args.kwargs["tp_target_pct"], 0.5)


class TestInventoryBootstrap(unittest.TestCase):
    @patch("src.rsi_trading.INVENTORY_BOOTSTRAP_PER_CYCLE", 1)
    @patch("src.rsi_trading.can_open_new_symbol", return_value=True)
    @patch("src.rsi_trading.is_tradeable_symbol", return_value=True)
    @patch("src.rsi_trading.db.symbol_has_open_lots", return_value=False)
    @patch("src.rsi_trading.db.count_open_symbols", return_value=5)
    @patch("src.rsi_trading._open_pair", return_value=99)
    @patch("src.rsi_trading.fetch_side_mark_price", return_value=1.0)
    def test_opens_one_symbol(self, _mark, open_pair, *_rest) -> None:
        with patch("src.margin_guard.should_block_new_entries", return_value=False), patch(
            "src.exchange.binance.is_rate_limited", return_value=False
        ), patch(
            "src.exchange.binance_ws.get_mark_from_ws", return_value=1.23
        ):
            opened = ensure_one_inventory_symbol(
                [("ETHUSDT", 1e9), ("BTCUSDT", 9e8)],
                exclude=set(),
            )
        self.assertEqual(opened, "ETHUSDT")
        open_pair.assert_called_once()
        self.assertEqual(open_pair.call_args.args[0], "ETHUSDT")
        self.assertEqual(open_pair.call_args.args[2], "inventory_bootstrap")

    @patch("src.rsi_trading.INVENTORY_BOOTSTRAP_PER_CYCLE", 1)
    @patch("src.rsi_trading.can_open_new_symbol", return_value=False)
    @patch("src.rsi_trading._open_pair")
    def test_skips_when_full(self, open_pair, *_rest) -> None:
        opened = ensure_one_inventory_symbol([("ETHUSDT", 1.0)])
        self.assertIsNone(opened)
        open_pair.assert_not_called()

    @patch("src.rsi_trading.INVENTORY_BOOTSTRAP_PER_CYCLE", 1)
    @patch("src.rsi_trading.can_open_new_symbol", return_value=True)
    @patch("src.rsi_trading.is_tradeable_symbol", return_value=True)
    @patch("src.rsi_trading.db.symbol_has_open_lots", return_value=False)
    @patch("src.rsi_trading.db.count_open_symbols", return_value=1)
    @patch("src.rsi_trading.fetch_side_mark_price", return_value=1.0)
    @patch("src.rsi_trading._open_pair")
    def test_skips_symbol_when_open_raises_and_tries_next(
        self, open_pair, *_rest
    ) -> None:
        from src.exchange.types import ExchangeClientError

        open_pair.side_effect = [
            ExchangeClientError("Contract spec not found for SNDKUSDT"),
            42,
        ]
        with patch("src.margin_guard.should_block_new_entries", return_value=False), patch(
            "src.exchange.binance.is_rate_limited", return_value=False
        ), patch(
            "src.exchange.binance_ws.get_mark_from_ws", return_value=1.0
        ):
            opened = ensure_one_inventory_symbol(
                [("SNDKUSDT", 2e9), ("ETHUSDT", 1e9)],
                exclude=set(),
            )
        self.assertEqual(opened, "ETHUSDT")
        self.assertEqual(open_pair.call_count, 2)


class TestBatchOrphanBe(unittest.TestCase):
    @patch("src.rsi_trading.AGGREGATE_TP_ENABLED", False)
    @patch("src.rsi_trading.PAIR_REOPEN_ON_CLOSE", True)
    @patch("src.rsi_trading._maybe_reopen_pair")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=100.0)
    @patch("src.rsi_trading.fetch_symbol_positions")
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading._format_close_size", return_value="1")
    @patch("src.rsi_trading.notify_close", create=True)
    def test_orphan_closes_at_be_without_reopen(
        self,
        _notify,
        _fmt,
        get_lots,
        close_side,
        fetch_pos,
        close_fill,
        maybe_reopen,
    ) -> None:
        from src.exchange.types import Position
        from src.rsi_trading import _take_profit_lots_side_batched

        get_lots.return_value = [
            _lot(lot_id=7, long_status="closed", short_status="open", short_entry=100.0)
        ]
        fetch_pos.return_value = {
            "long": Position(symbol="BTCUSDT", side="long", size=0, avg_price=0),
            "short": Position(symbol="BTCUSDT", side="short", size=1.0, avg_price=100.0),
        }
        snap = RsiSnapshot(ready=False, rsi=0.0, close=100.0)
        with patch("src.notify.notify_close"):
            took = _take_profit_lots_side_batched(
                "BTCUSDT",
                100.0,
                snap,
                "realtime",
                "short",
                reopen_pair=True,
                tp_target_pct=0.5,
            )
        self.assertTrue(took)
        close_fill.assert_called_once()
        close_side.assert_called_once()
        self.assertEqual(close_side.call_args.kwargs.get("close_reason"), "orphan_be")
        maybe_reopen.assert_not_called()

    @patch("src.rsi_trading.AGGREGATE_TP_ENABLED", False)
    @patch("src.rsi_trading.PAIR_REOPEN_ON_CLOSE", True)
    @patch("src.rsi_trading._maybe_reopen_pair")
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=100.6)
    @patch("src.rsi_trading.fetch_symbol_positions")
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading._format_close_size", return_value="1")
    @patch("src.rsi_trading.notify_close", create=True)
    def test_tp_close_still_reopens(
        self,
        _notify,
        _fmt,
        get_lots,
        close_side,
        fetch_pos,
        close_fill,
        maybe_reopen,
    ) -> None:
        from src.exchange.types import Position
        from src.rsi_trading import _take_profit_lots_side_batched

        get_lots.return_value = [
            _lot(lot_id=3, long_status="open", short_status="open", long_entry=100.0)
        ]
        fetch_pos.return_value = {
            "long": Position(symbol="BTCUSDT", side="long", size=1.0, avg_price=100.0),
            "short": Position(symbol="BTCUSDT", side="short", size=1.0, avg_price=100.0),
        }
        snap = RsiSnapshot(ready=False, rsi=0.0, close=100.6)
        with patch("src.notify.notify_close"):
            took = _take_profit_lots_side_batched(
                "BTCUSDT",
                100.6,
                snap,
                "realtime",
                "long",
                reopen_pair=True,
                tp_target_pct=0.5,
            )
        self.assertTrue(took)
        self.assertEqual(close_side.call_args.kwargs.get("close_reason"), "tp")
        maybe_reopen.assert_called_once()

    @patch("src.rsi_trading.AGGREGATE_TP_ENABLED", False)
    @patch("src.rsi_trading.PAIR_REOPEN_ON_CLOSE", False)
    @patch("src.rsi_trading._close_side_and_resolve_fill", return_value=100.6)
    @patch("src.rsi_trading.fetch_symbol_positions")
    @patch("src.rsi_trading.db.close_lot_side")
    @patch("src.rsi_trading.db.get_open_pair_lots")
    @patch("src.rsi_trading._format_close_size", return_value="2")
    def test_mixed_batch_records_tp_and_orphan_reasons(
        self,
        _fmt,
        get_lots,
        close_side,
        fetch_pos,
        close_fill,
    ) -> None:
        from src.exchange.types import Position
        from src.rsi_trading import _take_profit_lots_side_batched

        get_lots.return_value = [
            _lot(lot_id=1, long_status="open", short_status="open", long_entry=100.0),
            _lot(lot_id=2, long_status="open", short_status="closed", long_entry=100.0),
        ]
        fetch_pos.return_value = {
            "long": Position(symbol="BTCUSDT", side="long", size=2.0, avg_price=100.0),
            "short": Position(symbol="BTCUSDT", side="short", size=0, avg_price=0),
        }
        snap = RsiSnapshot(ready=False, rsi=0.0, close=100.6)
        with patch("src.notify.notify_close") as notify:
            took = _take_profit_lots_side_batched(
                "BTCUSDT",
                100.6,
                snap,
                "realtime",
                "long",
                reopen_pair=False,
                tp_target_pct=0.5,
            )
        self.assertTrue(took)
        reasons = [c.kwargs.get("close_reason") for c in close_side.call_args_list]
        self.assertEqual(reasons, ["tp", "orphan_be"])
        content = notify.call_args.kwargs.get("reason_text") or ""
        self.assertIn("chạm TP", content)
        self.assertIn("chạm entry", content)


if __name__ == "__main__":
    unittest.main()
