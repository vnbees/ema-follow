import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from src import database as db
from src.spot_transfer import (
    _PREPARED_DATES,
    ensure_available_for_transfer,
    process_daily_spot_transfer,
)


VN = ZoneInfo("Asia/Ho_Chi_Minh")


class TestSpotTransfer(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.patcher = patch("src.database.DATABASE_PATH", self.db_path)
        self.patcher.start()
        db.init_db()
        db.set_spot_transfer_enabled(True)
        db.set_spot_transfer_amount(4.0)
        _PREPARED_DATES.clear()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    @patch("src.spot_transfer.has_credentials", return_value=True)
    @patch("src.spot_transfer.fetch_futures_balance")
    def test_ensure_available_already_enough(self, fetch_bal, _creds) -> None:
        fetch_bal.return_value = MagicMock(available=10.0)
        ok, closes = ensure_available_for_transfer(4.0, "BTCUSDT")
        self.assertTrue(ok)
        self.assertEqual(closes, 0)

    @patch("src.spot_transfer.has_credentials", return_value=True)
    @patch("src.spot_transfer.transfer_futures_to_spot")
    @patch("src.spot_transfer.fetch_spot_balance", return_value=12.5)
    @patch("src.spot_transfer.fetch_futures_balance")
    @patch("src.spot_transfer.ensure_available_for_transfer", return_value=(True, 0))
    @patch("src.spot_transfer._vn_now")
    def test_execute_once_per_day(
        self,
        mock_now,
        _ensure,
        fetch_bal,
        _spot_bal,
        transfer,
        _creds,
    ) -> None:
        mock_now.return_value = datetime(2026, 7, 13, 8, 0, tzinfo=VN)
        fetch_bal.return_value = MagicMock(available=20.0)
        transfer.return_value = {"tranId": "abc", "clientOid": "c1"}

        process_daily_spot_transfer("BTCUSDT")
        process_daily_spot_transfer("BTCUSDT")

        self.assertEqual(transfer.call_count, 1)
        self.assertTrue(db.has_successful_transfer_on_date("2026-07-13"))
        rows = db.get_spot_transfers(5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "success")
        self.assertAlmostEqual(float(rows[0]["amount"]), 4.0)

    @patch("src.spot_transfer.has_credentials", return_value=True)
    @patch("src.spot_transfer.transfer_futures_to_spot")
    @patch("src.spot_transfer.ensure_available_for_transfer")
    @patch("src.spot_transfer._vn_now")
    def test_prepare_window_does_not_transfer(
        self,
        mock_now,
        ensure,
        transfer,
        _creds,
    ) -> None:
        mock_now.return_value = datetime(2026, 7, 13, 6, 55, tzinfo=VN)
        ensure.return_value = (True, 1)

        process_daily_spot_transfer("BTCUSDT")

        ensure.assert_called_once()
        transfer.assert_not_called()
        self.assertFalse(db.has_successful_transfer_on_date("2026-07-13"))

    @patch("src.spot_transfer.has_credentials", return_value=True)
    @patch("src.spot_transfer.fetch_futures_balance")
    @patch("src.spot_transfer.ensure_available_for_transfer", return_value=(False, 2))
    @patch("src.spot_transfer._vn_now")
    def test_failed_transfer_recorded(
        self,
        mock_now,
        _ensure,
        fetch_bal,
        _creds,
    ) -> None:
        mock_now.return_value = datetime(2026, 7, 13, 7, 5, tzinfo=VN)
        fetch_bal.return_value = MagicMock(available=1.0)

        process_daily_spot_transfer("BTCUSDT")

        rows = db.get_spot_transfers(5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(int(rows[0]["legs_closed"]), 2)


class TestSpotSnapshots(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.patcher = patch("src.database.DATABASE_PATH", self.db_path)
        self.patcher.start()
        db.init_db()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    @patch("src.database._utc_now", return_value="2026-07-13T01:00:00+00:00")
    def test_insert_and_clear(self, _now) -> None:
        db.insert_spot_snapshot(10.5)
        self.assertEqual(len(db.get_spot_snapshots()), 1)
        counts = db.clear_dashboard_history(reset_baseline=False)
        self.assertEqual(counts["spot_snapshots"], 1)
        self.assertEqual(counts["spot_transfers"], 0)


if __name__ == "__main__":
    unittest.main()
