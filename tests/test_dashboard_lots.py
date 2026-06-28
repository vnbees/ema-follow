import unittest
from unittest.mock import MagicMock

from src.web.app import build_symbol_groups


def _lot_row(
    lot_id: int,
    symbol: str,
    opened_at: str,
    *,
    long_entry: float = 100.0,
    short_entry: float = 100.0,
    long_status: str = "open",
    short_status: str = "open",
) -> MagicMock:
    lot = MagicMock()
    data = {
        "id": lot_id,
        "symbol": symbol,
        "opened_at": opened_at,
        "long_status": long_status,
        "long_entry": long_entry,
        "long_size": 1.0,
        "short_status": short_status,
        "short_entry": short_entry,
        "short_size": 1.0,
        "margin_usdt": 1.0,
        "entry_trigger": "rsi_cross_25",
        "long_closed_at": None,
        "short_closed_at": None,
        "long_realized_pnl_usdt": None,
        "short_realized_pnl_usdt": None,
        "long_close_price": None,
        "short_close_price": None,
    }
    lot.__getitem__ = lambda self, key: data[key]
    lot.keys = lambda: data.keys()
    return lot


class TestBuildSymbolGroups(unittest.TestCase):
    def test_fifo_index_for_open_lots(self):
        lots = [
            _lot_row(1, "BTCUSDT", "2026-01-01T10:00:00+00:00"),
            _lot_row(2, "BTCUSDT", "2026-01-02T10:00:00+00:00"),
        ]
        groups = build_symbol_groups(lots, {}, {"BTCUSDT": 102.0})
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["fifo_count"], 2)
        self.assertEqual(group["lots"][0]["fifo_index"], 1)
        self.assertEqual(group["lots"][1]["fifo_index"], 2)

    def test_tp_ready_when_move_above_target(self):
        lots = [_lot_row(1, "BTCUSDT", "2026-01-01T10:00:00+00:00", long_entry=100.0)]
        groups = build_symbol_groups(lots, {}, {"BTCUSDT": 102.5})
        long_leg = groups[0]["lots"][0]["long"]
        self.assertTrue(long_leg["tp_ready"])
        self.assertAlmostEqual(long_leg["move_pct"], 2.5)

    def test_closed_lot_no_fifo_index(self):
        lots = [
            _lot_row(
                1, "BTCUSDT", "2026-01-01T10:00:00+00:00",
                long_status="closed", short_status="closed",
            ),
        ]
        groups = build_symbol_groups(lots, {}, {"BTCUSDT": 102.0})
        self.assertIsNone(groups[0]["lots"][0]["fifo_index"])
        self.assertEqual(groups[0]["fifo_count"], 0)

    def test_closed_leg_shows_realized_pnl(self):
        lot = _lot_row(
            32, "OUSDT", "2026-06-28T10:00:00+00:00",
            long_status="open", short_status="closed",
            short_entry=0.5854,
        )
        lot.__getitem__ = lambda self, key: {
            **{
                "id": 32, "symbol": "OUSDT", "opened_at": "2026-06-28T10:00:00+00:00",
                "long_status": "open", "long_entry": 0.5927, "long_size": 18.0,
                "short_status": "closed", "short_entry": 0.5854, "short_size": 18.0,
                "margin_usdt": 1.0, "entry_trigger": "rsi_cross_75",
                "long_closed_at": None, "short_closed_at": "2026-06-28T14:15:27+00:00",
                "long_realized_pnl_usdt": None, "short_realized_pnl_usdt": None,
                "long_close_price": None, "short_close_price": None,
            }
        }[key]
        groups = build_symbol_groups([lot], {}, {"OUSDT": 0.5523})
        short_leg = groups[0]["lots"][0]["short"]
        self.assertFalse(short_leg["is_open"])
        self.assertAlmostEqual(short_leg["size"], 18.0)
        self.assertIsNotNone(short_leg["pnl_usdt"])
        self.assertTrue(short_leg["pnl_estimated"])


if __name__ == "__main__":
    unittest.main()
