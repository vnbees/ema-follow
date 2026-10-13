"""Dual limit entry: post-only LONG + SHORT per signal, 10-coin cap, expiry, fills → lots."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import database as db
from src.donchian import dual_limit, store
from src.donchian.dual_limit import (
    DualSignal,
    coin_slot_ok,
    expire_at_ms,
    maker_prices,
    side_bands,
    side_pot_rr,
)
from src.exchange.types import ContractSpec, FuturesAccountBalance

SPEC = ContractSpec(
    symbol="LINKUSDT",
    volume_place=2,
    price_place=3,
    min_trade_num=0.01,
    min_trade_usdt=5,
    size_multiplier=0.01,
)
BAR_MS = 15 * 60 * 1000


class PureRulesTests(unittest.TestCase):
    def test_coin_cap_blocks_only_new_coins(self):
        active = {f"C{i}USDT" for i in range(10)}
        self.assertTrue(coin_slot_ok("C3USDT", active, 10))
        self.assertFalse(coin_slot_ok("NEWUSDT", active, 10))
        self.assertTrue(coin_slot_ok("NEWUSDT", set(list(active)[:9]), 10))
        self.assertTrue(coin_slot_ok("NEWUSDT", active, 0))

    def test_maker_prices_round_away_from_the_book(self):
        self.assertEqual(maker_prices(10.1234, 10.1251, 3), (10.123, 10.126))

    def test_maker_prices_split_when_quotes_collapse(self):
        long_px, short_px = maker_prices(10.0, 10.0, 3)
        self.assertEqual(long_px, 10.0)
        self.assertAlmostEqual(short_px, 10.001)

    def test_expiry_after_one_full_bar_post_signal(self):
        self.assertEqual(expire_at_ms(0, 1), 2 * BAR_MS)
        self.assertEqual(expire_at_ms(0, 3), 4 * BAR_MS)

    def test_side_bands_and_rr(self):
        self.assertEqual(side_bands("long", 110, 90), (110, 90))
        self.assertEqual(side_bands("short", 110, 90), (90, 110))
        self.assertAlmostEqual(side_pot_rr("long", 100, 110, 90), 1.0)
        self.assertAlmostEqual(side_pot_rr("short", 105, 110, 90), 3.0)


class _DbCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.patcher = patch("src.database.DATABASE_PATH", Path(self.tmp.name) / "t.db")
        self.patcher.start()
        db.init_db()
        store.ensure_schema()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    def _entry(self, side="long", order_id="11", size=2.0, price=10.0, symbol="LINKUSDT"):
        eid = store.insert_entry_order(
            pair_key=f"{symbol}:0",
            symbol=symbol,
            side=side,
            order_id=order_id,
            client_oid="bde_x",
            price=price,
            size=size,
            margin_usdt=2.0,
            tp_band=11.0 if side == "long" else 9.0,
            opp_band=9.0 if side == "long" else 11.0,
            expire_ms=2 * BAR_MS,
            trend="up",
            entry_ts=0,
        )
        return eid


@patch("src.donchian.dual_limit.publish_open")
@patch("src.donchian.trading._notify_open")
class FillTests(_DbCase):
    def test_ws_fill_creates_lot_once(self, _notify, _pub):
        self._entry()
        detail = {"orderId": "11", "status": "filled", "executedQty": "2", "avgPrice": "10.0", "commission": "0.004"}
        dual_limit.on_entry_order_update(detail)
        dual_limit.on_entry_order_update(detail)
        lots = store.get_open_lots("LINKUSDT")
        self.assertEqual(len(lots), 1)
        lot = lots[0]
        self.assertEqual(lot["entry_rule"], "dual_limit")
        self.assertEqual(lot["side"], "long")
        self.assertAlmostEqual(lot["size"], 2.0)
        self.assertAlmostEqual(lot["fee_open_usdt"], 0.004)
        self.assertEqual(store.get_pending_entries(), [])

    def test_cancel_with_partial_fill_keeps_filled_part(self, _notify, _pub):
        self._entry(side="short", order_id="12")
        dual_limit.on_entry_order_update(
            {"orderId": "12", "status": "canceled", "executedQty": "0.5", "avgPrice": "10.0"}
        )
        lots = store.get_open_lots("LINKUSDT")
        self.assertEqual(len(lots), 1)
        self.assertAlmostEqual(lots[0]["size"], 0.5)
        self.assertAlmostEqual(lots[0]["fee_open_usdt"], 0.5 * 10.0 * 0.0002)
        self.assertEqual(store.entry_order_stats()["partial"], 1)

    def test_unfilled_cancel_frees_the_coin(self, _notify, _pub):
        self._entry(order_id="13")
        self.assertEqual(store.active_coins(), {"LINKUSDT"})
        dual_limit.on_entry_order_update({"orderId": "13", "status": "canceled", "executedQty": "0"})
        self.assertEqual(store.get_open_lots(), [])
        self.assertEqual(store.active_coins(), set())

    @patch("src.exchange.binance.cancel_limit_order_detail")
    def test_expire_cancels_only_due_orders(self, cancel, _notify, _pub):
        self._entry(order_id="21")
        cancel.return_value = {"orderId": "21", "status": "canceled", "executedQty": "0", "avgPrice": "0"}
        self.assertEqual(dual_limit.expire_pending(2 * BAR_MS - 1), 0)
        cancel.assert_not_called()
        self.assertEqual(dual_limit.expire_pending(2 * BAR_MS), 1)
        cancel.assert_called_once_with("LINKUSDT", "21")
        self.assertEqual(store.get_pending_entries(), [])

    @patch("src.exchange.binance.fetch_order_full")
    @patch("src.exchange.binance.cancel_limit_order_detail", return_value=None)
    def test_expire_unknown_order_reads_final_state(self, _cancel, fetch, _notify, _pub):
        self._entry(order_id="22")
        fetch.return_value = {"orderId": "22", "status": "filled", "executedQty": "2", "avgPrice": "9.99"}
        dual_limit.expire_pending(2 * BAR_MS)
        lots = store.get_open_lots()
        self.assertEqual(len(lots), 1)
        self.assertAlmostEqual(lots[0]["entry_px"], 9.99)


class PlacePairTests(_DbCase):
    def _sig(self, symbol="LINKUSDT"):
        return DualSignal(
            symbol=symbol, trend="up", trend_ts=0, entry_ts=0, close=10.0,
            upper=11.0, lower=9.0, size_mult=1.0, body_atr=0.5, pot_rr=1.0, atr=0.2, why="test",
        )

    def _patches(self, available=1000.0):
        bal = FuturesAccountBalance("USDT", available, 1000.0, 1000.0)
        return [
            patch("src.donchian.dual_limit.is_trading_enabled", return_value=True),
            patch("src.donchian.dual_limit.has_credentials", return_value=True),
            patch("src.donchian.dual_limit.configure_symbol_trading"),
            patch("src.donchian.trading.live_account_balance", return_value=bal),
            patch("src.donchian.dual_limit.fetch_contract_spec", return_value=SPEC),
            patch("src.exchange.binance.fetch_book_ticker", return_value=(9.998, 10.002)),
            patch("src.exchange.binance.is_rate_limited", return_value=False),
        ]

    def _run(self, sig, available=1000.0, place=None):
        ps = self._patches(available)
        for p in ps:
            p.start()
        try:
            with patch("src.exchange.binance.place_limit_open_order") as mock_place:
                ids = iter(range(100, 200))
                mock_place.side_effect = place or (
                    lambda *a, **k: {"orderId": str(next(ids)), "clientOid": "c", "status": "new"}
                )
                result = dual_limit.place_pair(sig)
                return result, mock_place
        finally:
            for p in ps:
                p.stop()

    def test_places_post_only_long_at_bid_and_short_at_ask(self):
        (status, committed), mock_place = self._run(self._sig())
        self.assertEqual(status, "placed")
        self.assertAlmostEqual(committed, 20.0)
        calls = [(c.args[1], c.args[3], c.kwargs.get("post_only")) for c in mock_place.call_args_list]
        self.assertEqual(calls, [("long", "9.998", True), ("short", "10.002", True)])
        sides = sorted(str(r["side"]) for r in store.get_pending_entries())
        self.assertEqual(sides, ["long", "short"])

    def test_same_coin_can_stack_another_pair(self):
        self._run(self._sig())
        (status, _), _ = self._run(self._sig())
        self.assertEqual(status, "placed")
        self.assertEqual(len(store.get_pending_entries("LINKUSDT")), 4)

    def test_eleventh_coin_is_refused(self):
        for i in range(10):
            self._entry(order_id=f"9{i}", symbol=f"C{i}USDT")
        (status, _), mock_place = self._run(self._sig("NEWUSDT"))
        self.assertEqual(status, "coin_cap")
        mock_place.assert_not_called()

    def test_needs_margin_for_both_sides(self):
        (status, _), mock_place = self._run(self._sig(), available=15.0)
        self.assertEqual(status, "cap_skip")
        mock_place.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class TestGroupPositions(unittest.TestCase):
    def _lot(self, symbol, side, entry, size, margin, mark):
        sign = 1.0 if side == "long" else -1.0
        return {
            "symbol": symbol, "side": side, "entry": entry, "size": size, "margin_usdt": margin,
            "tp_band": 0.1235, "unrealized_pnl": sign * (mark - entry) * size,
        }

    def test_aggregates_like_exchange_and_flags_sync(self):
        from src.exchange.types import Position
        from src.web.app import _group_positions

        mark = 0.143836
        lots = [
            self._lot("OPUSDT", "short", 0.12994, 1546.5, 20.0944, mark),
            self._lot("OPUSDT", "short", 0.12938, 1552.8, 20.0944, mark),
            self._lot("UNIUSDT", "short", 7.362, 28.0, 19.97, 7.35),
        ]
        exchange = [
            Position("OPUSDT", "short", 3099.3, 0.12965943),
            Position("UNIUSDT", "short", 30.0, 7.36),
            Position("ETHUSDT", "long", 0.1, 2000.0),
        ]
        rows = {r["symbol"]: r for r in _group_positions(lots, exchange, {"OPUSDT": mark, "UNIUSDT": 7.35})}

        op = rows["OPUSDT"]
        self.assertEqual(op["lots"], 2)
        self.assertAlmostEqual(op["size"], 3099.3)
        self.assertAlmostEqual(op["entry"], 0.12965943, places=7)
        self.assertAlmostEqual(op["unrealized_pnl"], op["exchange_unrealized"], places=4)
        self.assertAlmostEqual(op["roi_entry_pct"], op["unrealized_pnl"] / 40.1888 * 100, places=6)
        self.assertAlmostEqual(op["roi_pct"], op["unrealized_pnl"] / (3099.3 * mark / 10) * 100, places=6)
        self.assertEqual(op["sync"], "ok")
        self.assertEqual(rows["UNIUSDT"]["sync"], "mismatch")
        self.assertEqual(rows["ETHUSDT"]["lots"], 0)
        self.assertEqual(rows["ETHUSDT"]["sync"], "mismatch")
        self.assertIsNone(rows["ETHUSDT"]["unrealized_pnl"])

    def test_unknown_without_ws(self):
        from src.web.app import _group_positions

        rows = _group_positions([self._lot("DOTUSDT", "short", 1.0875, 191.5, 20.83, 1.2659)], None, {"DOTUSDT": 1.2659})
        self.assertEqual(rows[0]["sync"], "unknown")
        self.assertAlmostEqual(rows[0]["roi_pct"], -140.9, places=1)
        self.assertAlmostEqual(rows[0]["roi_entry_pct"], -164.0, places=0)
