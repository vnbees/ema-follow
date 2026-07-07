import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import database as db


class TestRecentLegEvents(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.patcher = patch("src.database.DATABASE_PATH", self.db_path)
        self.patcher.start()
        db.init_db()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    @patch("src.database._utc_now", return_value="2026-06-01T10:00:00+00:00")
    def test_open_pair_emits_two_leg_events(self, _mock_now) -> None:
        db.insert_pair_lot(
            "BTCUSDT",
            long_size=0.1,
            long_entry=100.0,
            short_size=0.1,
            short_entry=100.0,
            margin_usdt=1.0,
            entry_trigger="rsi_cross_25",
        )
        events = db.get_recent_leg_events(10)
        self.assertEqual(len(events), 2)
        sides = {str(row["side"]) for row in events}
        self.assertEqual(sides, {"long", "short"})
        for row in events:
            self.assertEqual(str(row["event_type"]), "open")
            self.assertEqual(str(row["symbol"]), "BTCUSDT")

    @patch("src.database._utc_now")
    def test_close_event_sorted_before_open(self, mock_now) -> None:
        mock_now.return_value = "2026-06-01T10:00:00+00:00"
        lot_id = db.insert_pair_lot(
            "ETHUSDT",
            long_size=1.0,
            long_entry=50.0,
            short_size=1.0,
            short_entry=50.0,
            margin_usdt=1.0,
            entry_trigger="rsi_cross_75",
        )
        mock_now.return_value = "2026-06-01T11:00:00+00:00"
        db.close_lot_side(lot_id, "long", realized_pnl_usdt=0.5, close_price=50.5)

        events = db.get_recent_leg_events(10)
        self.assertEqual(str(events[0]["event_type"]), "close")
        self.assertEqual(str(events[0]["side"]), "long")
        self.assertEqual(str(events[0]["event_at"]), "2026-06-01T11:00:00+00:00")

    @patch("src.database._utc_now", return_value="2026-06-01T10:00:00+00:00")
    def test_limit_returns_most_recent(self, _mock_now) -> None:
        for idx in range(6):
            db.insert_pair_lot(
                f"SYM{idx}USDT",
                long_size=1.0,
                long_entry=1.0,
                short_size=1.0,
                short_entry=1.0,
                margin_usdt=1.0,
                entry_trigger="rsi_cross_25",
            )
        events = db.get_recent_leg_events(10)
        self.assertEqual(len(events), 10)


if __name__ == "__main__":
    unittest.main()
