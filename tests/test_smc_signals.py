import threading
import time
import unittest
from unittest.mock import patch

from src.smc import broker
from src.smc.broker import book_qty, entry_block_reason, order_notional
from src.smc.signals import BAR_MS, Setup, TTL_BARS, expired, flipped, setups, stop_hit


def _iso(ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def _flat(n, *, low=100.0, high=110.0, close=105.0):
    ts = [i * BAR_MS for i in range(n)]
    opens = [close] * n
    highs = [high] * n
    lows = [low] * n
    closes = [close] * n
    return ts, opens, highs, lows, closes


class TestSmcSweep(unittest.TestCase):
    def test_short_sweep_targets_midpoint(self) -> None:
        ts, opens, highs, lows, closes = _flat(90)
        lows[5] = 90.0
        highs[20] = 130.0
        highs[80] = 131.5
        lows[80] = 120.0
        closes[80] = 129.0
        opens[80] = 128.0
        rows = setups(ts, opens, highs, lows, closes)
        self.assertEqual(len(rows), 1)
        setup = rows[0]
        self.assertEqual(setup.side, "short")
        self.assertEqual(setup.index, 80)
        self.assertEqual(setup.entry, 130.0)
        self.assertEqual(setup.stop, 131.5)
        self.assertAlmostEqual(setup.target, 110.0)

    def test_wide_wick_is_ignored(self) -> None:
        ts, opens, highs, lows, closes = _flat(90)
        lows[5] = 90.0
        highs[20] = 130.0
        highs[80] = 160.0
        closes[80] = 129.0
        self.assertEqual(setups(ts, opens, highs, lows, closes), [])

    def test_expire_after_fill_window(self) -> None:
        self.assertFalse(expired(0, TTL_BARS * BAR_MS))
        self.assertTrue(expired(0, (TTL_BARS + 1) * BAR_MS))

    def test_busy_coin_blocks_a_signal_on_the_same_candle(self) -> None:
        opened_ms = 1_700_000_000_000
        fill_bar = opened_ms - (opened_ms % BAR_MS)
        self.assertEqual(
            entry_block_reason(fill_bar, [{
                "created_at": _iso(fill_bar - BAR_MS),
                "closed_at": _iso(fill_bar + 60_000),
                "opened_at": _iso(fill_bar + 30_000),
            }]),
            "busy",
        )
        self.assertIsNone(entry_block_reason(fill_bar, []))

    def test_cooldown_covers_the_fill_bar_and_the_next(self) -> None:
        opened_ms = 1_700_000_100_000
        fill_bar = opened_ms - (opened_ms % BAR_MS)
        row = {
            "created_at": _iso(fill_bar - 2 * BAR_MS),
            "closed_at": _iso(fill_bar - BAR_MS),
            "opened_at": _iso(opened_ms),
        }
        self.assertEqual(entry_block_reason(fill_bar, [row]), "cooldown")
        self.assertEqual(entry_block_reason(fill_bar + BAR_MS, [row]), "cooldown")
        self.assertIsNone(entry_block_reason(fill_bar + 2 * BAR_MS, [row]))

    def test_skip_marker_does_not_block_the_next_candle(self) -> None:
        signal = 20 * BAR_MS
        marker = {
            "created_at": _iso(signal + BAR_MS + 2_000),
            "closed_at": _iso(signal + BAR_MS + 2_000),
            "opened_at": None,
            "close_reason": "busy",
        }
        self.assertIsNone(entry_block_reason(signal + BAR_MS, [marker]))

    def test_flip_swaps_side_and_exits(self) -> None:
        long = Setup("long", 10, 1_000, 100.0, 99.0, 110.0)
        short = flipped(long)
        self.assertEqual(short.side, "short")
        self.assertEqual(short.entry, 100.0)
        self.assertEqual(short.stop, 110.0)
        self.assertEqual(short.target, 99.0)
        self.assertEqual(flipped(short).side, "long")
        self.assertEqual(flipped(short).stop, 99.0)
        self.assertEqual(flipped(short).target, 110.0)

    def test_through_marker_does_not_block_the_next_candle(self) -> None:
        signal = 20 * BAR_MS
        marker = {
            "created_at": _iso(signal + BAR_MS + 2_000),
            "closed_at": _iso(signal + BAR_MS + 2_000),
            "opened_at": None,
            "close_reason": "through",
        }
        self.assertIsNone(entry_block_reason(signal + BAR_MS, [marker]))

    def test_concurrent_brackets_place_each_exit_once(self) -> None:
        row = {
            "id": 77,
            "symbol": "XLMUSDT",
            "side": "short",
            "qty": 10,
            "stop_px": 1.1,
            "tp_px": 0.9,
            "stop_order_id": "",
            "tp_order_id": "",
        }
        state = {"stop": "", "tp": "", "stop_n": 0, "tp_n": 0}
        gate = threading.Lock()

        def get_trade(_trade_id):
            with gate:
                return {**row, "stop_order_id": state["stop"], "tp_order_id": state["tp"]}

        def set_stop(_trade_id, oid):
            with gate:
                state["stop"] = oid

        def set_tp(_trade_id, oid):
            with gate:
                state["tp"] = oid

        def place_stop(*_args, **_kwargs):
            time.sleep(0.05)
            with gate:
                state["stop_n"] += 1
            return {"orderId": "stop-1"}

        def place_tp(*_args, **_kwargs):
            time.sleep(0.05)
            with gate:
                state["tp_n"] += 1
            return {"orderId": "tp-1"}

        with (
            patch.object(broker.store, "get_trade", get_trade),
            patch.object(broker.store, "set_stop_order", set_stop),
            patch.object(broker.store, "set_tp_order", set_tp),
            patch("src.exchange.binance.place_algo_close_order", place_stop),
            patch("src.exchange.binance.place_limit_close_order", place_tp),
            patch.object(broker, "fetch_contract_spec", return_value=object()),
            patch.object(broker, "format_price", side_effect=lambda px, _spec: str(px)),
            patch.object(broker, "format_size", side_effect=lambda qty, _spec: str(qty)),
            patch.object(broker, "_ensure"),
        ):
            threads = [threading.Thread(target=broker._place_brackets, args=(row,)) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)
        self.assertEqual(state["stop_n"], 1)
        self.assertEqual(state["tp_n"], 1)
        self.assertEqual(state["stop"], "stop-1")
        self.assertEqual(state["tp"], "tp-1")

    def test_missing_tp_is_still_placed_when_stop_exists(self) -> None:
        row = {
            "id": 78,
            "symbol": "XLMUSDT",
            "side": "short",
            "qty": 10,
            "stop_px": 1.1,
            "tp_px": 0.9,
            "stop_order_id": "stop-1",
            "tp_order_id": "",
        }
        state = {"stop_n": 0, "tp_n": 0}

        def place_stop(*_args, **_kwargs):
            state["stop_n"] += 1
            return {"orderId": "stop-2"}

        def place_tp(*_args, **_kwargs):
            state["tp_n"] += 1
            return {"orderId": "tp-1"}

        with (
            patch.object(broker.store, "get_trade", return_value=row),
            patch.object(broker.store, "set_stop_order"),
            patch.object(broker.store, "set_tp_order"),
            patch("src.exchange.binance.place_algo_close_order", place_stop),
            patch("src.exchange.binance.place_limit_close_order", place_tp),
            patch.object(broker, "fetch_contract_spec", return_value=object()),
            patch.object(broker, "format_price", side_effect=lambda px, _spec: str(px)),
            patch.object(broker, "format_size", side_effect=lambda qty, _spec: str(qty)),
            patch.object(broker, "_ensure"),
        ):
            broker._place_brackets(row)
        self.assertEqual(state["stop_n"], 0)
        self.assertEqual(state["tp_n"], 1)

    def test_notional_is_two_percent_margin_regardless_of_stop(self) -> None:
        # 1000 USDT equity, leverage 10 → 20 USDT margin, 200 USDT notional.
        tight = order_notional(100.0, 98.8, 101.0, 1000.0)
        wide = order_notional(100.0, 96.4, 101.0, 1000.0)
        self.assertAlmostEqual(tight, 200.0)
        self.assertAlmostEqual(wide, 200.0)

    def test_target_under_0_12_usdt_is_not_placed(self) -> None:
        self.assertIsNone(order_notional(100.0, 99.0, 100.001, 800.0))

    def test_partial_stop_fill_waits_for_the_order_size(self) -> None:
        self.assertIsNone(book_qty(882, 27))
        self.assertEqual(book_qty(882, 882), 882)
        self.assertEqual(book_qty(882, 900), 900)

    def test_stop_side(self) -> None:
        self.assertTrue(stop_hit("long", 10.0, 10.0))
        self.assertFalse(stop_hit("long", 10.0, 10.1))
        self.assertTrue(stop_hit("short", 10.0, 10.0))
        self.assertFalse(stop_hit("short", 10.0, 9.9))


if __name__ == "__main__":
    unittest.main()
