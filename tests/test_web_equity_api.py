import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import database as db
from src.web.app import build_equity_history_payload


class TestEquityHistoryApi(unittest.TestCase):
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
    def test_equity_history_returns_points(self, mock_now) -> None:
        mock_now.return_value = datetime.now(timezone.utc).isoformat()
        db.insert_equity_snapshot(437.75, 167.13, maint_margin_pct=7.0)
        db.set_baseline_equity(485.0)

        data = build_equity_history_payload("7d")
        self.assertEqual(data["range"], "7d")
        self.assertEqual(len(data["points"]), 1)
        self.assertAlmostEqual(data["points"][0]["equity"], 437.75)
        self.assertAlmostEqual(data["points"][0]["available"], 167.13)
        self.assertAlmostEqual(data["baseline_equity"], 485.0)
        self.assertIn("time_vn", data["points"][0])

    def test_equity_history_defaults_invalid_range_to_7d(self) -> None:
        data = build_equity_history_payload("invalid")
        self.assertEqual(data["range"], "7d")


if __name__ == "__main__":
    unittest.main()
