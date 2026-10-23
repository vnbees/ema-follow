import unittest
from datetime import datetime

from src.exchange.types import Candle
from src.fade5m.config import H4_MS, M5_MS, VN_TZ, entries_open, parse_start_at
from src.fade5m.engine import (
    Bar5m,
    bars_from_klines,
    book_unrealized,
    bucket_4h,
    candle_side,
    closed_blocks,
    fee_floor_distance,
    fourh_open,
    is_last_5m_of_4h,
    last_atr,
    lots_on_side,
    merge_bars,
    opposite_qty,
    signal_side,
    skip_add_when_profit,
    tp_price,
)


def _candle(ts, o, h, l, c):
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=1.0, quote_volume=1.0)


class StartAtTests(unittest.TestCase):
    def test_parse_vn_naive(self):
        dt = parse_start_at("2026-10-20 19:00")
        self.assertIsNotNone(dt)
        assert dt is not None
        self.assertEqual(dt.hour, 19)
        self.assertEqual(str(dt.tzinfo), str(VN_TZ))

    def test_entries_open_before_after(self):
        import src.fade5m.config as cfg

        start = parse_start_at("2026-10-20 19:00")
        assert start is not None
        before = datetime(2026, 10, 20, 18, 59, tzinfo=VN_TZ)
        after = datetime(2026, 10, 20, 19, 0, tzinfo=VN_TZ)
        old = cfg.START_AT
        old_live = cfg.LIVE_NOW
        cfg.START_AT = start
        cfg.LIVE_NOW = ("OPUSDT",)
        try:
            self.assertFalse(cfg.entries_open(before))
            self.assertTrue(cfg.entries_open(after))
            self.assertTrue(cfg.entries_open(before, symbol="OPUSDT"))
            self.assertFalse(cfg.entries_open(before, symbol="BTCUSDT"))
            self.assertTrue(cfg.entries_open(after, symbol="BTCUSDT"))
            cfg.LIVE_NOW = ()
            self.assertFalse(cfg.entries_open(before, symbol="OPUSDT"))
        finally:
            cfg.START_AT = old
            cfg.LIVE_NOW = old_live

    def test_trade_symbols_switches_at_time(self):
        import src.fade5m.config as cfg

        start = parse_start_at("2026-10-20 23:00")
        assert start is not None
        before = datetime(2026, 10, 20, 22, 59, tzinfo=VN_TZ)
        after = datetime(2026, 10, 20, 23, 0, tzinfo=VN_TZ)
        old = cfg.SYMBOLS
        old_next = cfg.SYMBOLS_NEXT
        old_switch = cfg.SWITCH_AT
        cfg.SYMBOLS = ("BTCUSDT", "OPUSDT")
        cfg.SYMBOLS_NEXT = ("OPUSDT", "DOGEUSDT")
        cfg.SWITCH_AT = start
        try:
            self.assertEqual(cfg.trade_symbols(before), ("BTCUSDT", "OPUSDT"))
            self.assertEqual(cfg.trade_symbols(after), ("OPUSDT", "DOGEUSDT"))
            self.assertIn("DOGEUSDT", cfg.watch_symbols())
            self.assertIn("BTCUSDT", cfg.watch_symbols())
        finally:
            cfg.SYMBOLS = old
            cfg.SYMBOLS_NEXT = old_next
            cfg.SWITCH_AT = old_switch


class BucketTests(unittest.TestCase):
    def test_4h_bucket_aligns_utc(self):
        self.assertEqual(bucket_4h(0), 0)
        self.assertEqual(bucket_4h(H4_MS - 1), 0)
        self.assertEqual(bucket_4h(H4_MS), H4_MS)

    def test_last_5m_of_4h(self):
        last = H4_MS - M5_MS
        self.assertTrue(is_last_5m_of_4h(last))
        self.assertFalse(is_last_5m_of_4h(last - M5_MS))
        self.assertFalse(is_last_5m_of_4h(0))


class SignalTests(unittest.TestCase):
    def test_4h_green_5m_red_is_long(self):
        bar = Bar5m(ts=M5_MS, open=100, high=101, low=98, close=99)
        self.assertEqual(signal_side(bar, h4_open=98), 1)

    def test_4h_red_5m_green_is_short(self):
        bar = Bar5m(ts=M5_MS, open=100, high=102, low=99, close=101)
        self.assertEqual(signal_side(bar, h4_open=102), -1)

    def test_same_color_is_flat(self):
        bar = Bar5m(ts=M5_MS, open=100, high=103, low=99, close=102)
        self.assertEqual(signal_side(bar, h4_open=101), 0)

    def test_doji_is_flat(self):
        bar = Bar5m(ts=M5_MS, open=100, high=101, low=99, close=100)
        self.assertEqual(signal_side(bar, h4_open=99), 0)
        self.assertEqual(candle_side(100, 100), 0)


class ProfitSkipTests(unittest.TestCase):
    def test_empty_book_allows_add(self):
        self.assertFalse(skip_add_when_profit([], 100.0))

    def test_long_in_profit_blocks_add(self):
        opens = [("long", 100.0, 10.0)]
        self.assertTrue(skip_add_when_profit(opens, 101.0))
        self.assertFalse(skip_add_when_profit(opens, 99.0))
        self.assertFalse(skip_add_when_profit(opens, 100.0))

    def test_short_in_profit_blocks_add(self):
        opens = [("short", 100.0, 10.0)]
        self.assertTrue(skip_add_when_profit(opens, 99.0))
        self.assertFalse(skip_add_when_profit(opens, 101.0))

    def test_net_book_not_single_lot(self):
        opens = [("long", 100.0, 1.0), ("long", 104.0, 1.0)]
        self.assertFalse(skip_add_when_profit(opens, 101.0))
        self.assertTrue(skip_add_when_profit(opens, 103.0))
        self.assertAlmostEqual(book_unrealized(opens, 103.0), 2.0)


class FlipCoverTests(unittest.TestCase):
    def test_opposite_qty_covers_other_side(self):
        opens = [("long", 100.0, 8.0), ("long", 101.0, 2.0), ("short", 99.0, 1.0)]
        self.assertAlmostEqual(opposite_qty(opens, "short"), 10.0)
        self.assertAlmostEqual(opposite_qty(opens, "long"), 1.0)
        self.assertEqual(opposite_qty([], "long"), 0.0)

    def test_skip_profit_only_same_side(self):
        longs = [("long", 100.0, 10.0)]
        shorts = [("short", 100.0, 10.0)]
        self.assertTrue(skip_add_when_profit(lots_on_side(longs + shorts, "long"), 101.0))
        self.assertFalse(skip_add_when_profit(lots_on_side(longs + shorts, "short"), 101.0))


class BarTests(unittest.TestCase):
    def test_drops_open_5m(self):
        start = 0
        rows = [_candle(start + i * M5_MS, 1, 1, 1, 1) for i in range(3)]
        now = start + 2 * M5_MS + 1
        bars = bars_from_klines(rows, now)
        self.assertEqual([b.ts for b in bars], [0, M5_MS])

    def test_fourh_open_is_first_bar_in_bucket(self):
        bars = [
            Bar5m(ts=0, open=10, high=11, low=9, close=10.5),
            Bar5m(ts=M5_MS, open=10.5, high=12, low=10, close=11),
            Bar5m(ts=H4_MS, open=20, high=21, low=19, close=20),
        ]
        self.assertEqual(fourh_open(bars, 0), 10)
        self.assertEqual(fourh_open(bars, H4_MS), 20)

    def test_merge_overwrites_same_ts(self):
        a = [Bar5m(ts=0, open=1, high=1, low=1, close=1)]
        b = [Bar5m(ts=0, open=1, high=2, low=1, close=2)]
        merged = merge_bars(a, b)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].close, 2)


class TpAtrTests(unittest.TestCase):
    def test_last_atr_needs_history(self):
        bars = [Bar5m(ts=i * M5_MS, open=10, high=11, low=9, close=10) for i in range(10)]
        self.assertIsNone(last_atr(bars, period=14))

    def test_constant_range_atr(self):
        bars = [Bar5m(ts=i * M5_MS, open=10, high=11, low=9, close=10) for i in range(20)]
        atr = last_atr(bars, period=14)
        self.assertIsNotNone(atr)
        assert atr is not None
        self.assertGreater(atr, 1.9)
        self.assertLess(atr, 2.1)

    def test_long_tp_is_entry_plus_atr(self):
        self.assertAlmostEqual(tp_price("long", 100.0, 1.0), 101.0)
        self.assertAlmostEqual(tp_price("short", 100.0, 1.0), 99.0)

    def test_tiny_atr_uses_fee_floor(self):
        floor = fee_floor_distance(100.0)
        self.assertGreater(floor, 0)
        self.assertAlmostEqual(tp_price("long", 100.0, 1e-9), 100.0 + floor)


class ClosedBlockTests(unittest.TestCase):
    def test_groups_by_4h_and_skips_user_flat(self):
        rows = [
            {
                "status": "closed",
                "reason": "4h_end",
                "bucket_ts": 0,
                "symbol": "OPUSDT",
                "side": "long",
                "qty": 1,
                "entry_px": 1.0,
                "limit_px": 1.0,
                "exit_px": 1.1,
                "pnl": 0.5,
                "opened_ts": 10,
                "closed_ts": 20,
                "bar_ts": 10,
            },
            {
                "status": "closed",
                "reason": "4h_end",
                "bucket_ts": 0,
                "symbol": "SOLUSDT",
                "side": "short",
                "qty": 1,
                "entry_px": 2.0,
                "limit_px": 2.0,
                "exit_px": 1.9,
                "pnl": 0.25,
                "opened_ts": 11,
                "closed_ts": 20,
                "bar_ts": 11,
            },
            {
                "status": "closed",
                "reason": "user_flat",
                "bucket_ts": 0,
                "symbol": "BTCUSDT",
                "side": "long",
                "qty": 1,
                "pnl": 0,
                "opened_ts": 12,
                "closed_ts": 20,
                "bar_ts": 12,
            },
            {
                "status": "cancelled",
                "reason": "4h_flip",
                "bucket_ts": 0,
                "symbol": "ADAUSDT",
                "pnl": 0,
                "bar_ts": 13,
            },
            {
                "status": "closed",
                "reason": "4h_end",
                "bucket_ts": H4_MS,
                "symbol": "XRPUSDT",
                "side": "long",
                "qty": 1,
                "entry_px": 1.0,
                "exit_px": 1.0,
                "pnl": -0.1,
                "opened_ts": 30,
                "closed_ts": 40,
                "bar_ts": 30,
            },
        ]
        blocks = closed_blocks(rows, H4_MS)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["bucket_ts"], H4_MS)
        self.assertEqual(blocks[0]["end_ts"], H4_MS * 2)
        self.assertEqual(blocks[0]["n"], 1)
        self.assertAlmostEqual(blocks[0]["pnl"], -0.1)
        self.assertEqual(blocks[1]["n"], 2)
        self.assertAlmostEqual(blocks[1]["pnl"], 0.75)
        self.assertEqual(blocks[1]["orders"][0]["symbol"], "OPUSDT")
        self.assertEqual(blocks[1]["orders"][0]["opened_ts"], 10)
        self.assertEqual(blocks[1]["orders"][0]["closed_ts"], 20)


if __name__ == "__main__":
    unittest.main()
