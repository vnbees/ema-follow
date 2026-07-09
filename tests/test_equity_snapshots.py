import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src import database as db


class TestEquitySnapshots(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.patcher = patch("src.database.DATABASE_PATH", self.db_path)
        self.patcher.start()
        db.init_db()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    @patch("src.database._utc_now")
    def test_insert_and_query_since(self, mock_now) -> None:
        mock_now.side_effect = [
            "2026-06-01T08:00:00+00:00",
            "2026-06-01T10:00:00+00:00",
            "2026-06-01T12:00:00+00:00",
        ]
        db.insert_equity_snapshot(400.0, 200.0, maint_margin_pct=5.0)
        db.insert_equity_snapshot(410.0, 210.0, maint_margin_pct=5.1)
        db.insert_equity_snapshot(420.0, 220.0, maint_margin_pct=5.2)

        since = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
        rows = db.get_equity_snapshots(since)
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(float(rows[0]["equity"]), 410.0)
        self.assertAlmostEqual(float(rows[1]["equity"]), 420.0)

    @patch("src.database._utc_now")
    def test_prune_removes_old_rows(self, mock_now) -> None:
        mock_now.return_value = "2026-01-01T00:00:00+00:00"
        db.insert_equity_snapshot(100.0, 50.0)

        mock_now.return_value = datetime.now(timezone.utc).isoformat()
        db.insert_equity_snapshot(200.0, 100.0)

        deleted = db.prune_equity_snapshots(older_than_days=90)
        self.assertEqual(deleted, 1)
        rows = db.get_equity_snapshots()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(float(rows[0]["equity"]), 200.0)

    @patch("src.database._utc_now", return_value="2026-06-01T10:00:00+00:00")
    def test_clear_dashboard_history_removes_snapshots(self, _mock_now) -> None:
        db.insert_equity_snapshot(300.0, 150.0)
        counts = db.clear_dashboard_history(reset_baseline=False)
        self.assertEqual(counts["equity_snapshots"], 1)
        self.assertEqual(len(db.get_equity_snapshots()), 0)


if __name__ == "__main__":
    unittest.main()
