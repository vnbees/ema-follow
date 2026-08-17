import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import database as db


class TestDailyCloseReport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.patcher = patch("src.database.DATABASE_PATH", self.db_path)
        self.patcher.start()
        db.init_db()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    def _open_lot(self, symbol: str = "BTCUSDT") -> int:
        return db.insert_pair_lot(
            symbol,
            long_size=1.0,
            long_entry=100.0,
            short_size=1.0,
            short_entry=100.0,
            margin_usdt=1.0,
            entry_trigger="inventory",
        )

    def test_groups_by_vn_date_and_omits_unknown_reasons(self) -> None:
        with patch("src.database._utc_now") as mock_now:
            mock_now.return_value = "2026-08-16T01:00:00+00:00"
            lot_tp = self._open_lot("AAAUSDT")
            lot_orphan = self._open_lot("BBBUSDT")
            lot_age = self._open_lot("CCCUSDT")
            lot_old = self._open_lot("DDDUSDT")
            lot_manual = self._open_lot("EEEUSDT")

            # UTC 16:00 on 16th → VN 23:00 16th
            mock_now.return_value = "2026-08-16T16:00:00+00:00"
            db.close_lot_side(
                lot_tp, "long", realized_pnl_usdt=1.25, close_price=101.25, close_reason="tp"
            )
            db.close_lot_side(
                lot_orphan,
                "short",
                realized_pnl_usdt=0.10,
                close_price=99.90,
                close_reason="orphan_be",
            )

            # UTC 18:00 on 16th → VN 01:00 17th
            mock_now.return_value = "2026-08-16T18:00:00+00:00"
            db.close_lot_side(
                lot_age, "long", realized_pnl_usdt=-0.40, close_price=99.60, close_reason="age"
            )
            db.close_lot_side(
                lot_old, "long", realized_pnl_usdt=9.99, close_price=110.0, close_reason=None
            )
            db.close_lot_side(
                lot_manual,
                "short",
                realized_pnl_usdt=0.50,
                close_price=99.50,
                close_reason="manual",
            )

        now_utc = datetime(2026, 8, 17, 5, 0, tzinfo=timezone.utc)
        report = db.get_daily_close_report(days=3, now_utc=now_utc)
        by_date = {row["date"]: row for row in report["rows"]}

        day16 = by_date["2026-08-16"]
        self.assertEqual(day16["tp_count"], 1)
        self.assertAlmostEqual(day16["tp_pnl"], 1.25)
        self.assertEqual(day16["orphan_be_count"], 1)
        self.assertAlmostEqual(day16["orphan_be_pnl"], 0.10)
        self.assertEqual(day16["age_count"], 0)
        self.assertEqual(day16["total_count"], 2)
        self.assertAlmostEqual(day16["total_pnl"], 1.35)

        day17 = by_date["2026-08-17"]
        self.assertEqual(day17["tp_count"], 0)
        self.assertEqual(day17["age_count"], 1)
        self.assertAlmostEqual(day17["age_pnl"], -0.40)
        self.assertEqual(day17["total_count"], 1)

        self.assertEqual(report["totals"]["tp_count"], 1)
        self.assertEqual(report["totals"]["orphan_be_count"], 1)
        self.assertEqual(report["totals"]["age_count"], 1)
        self.assertEqual(report["totals"]["total_count"], 3)
        self.assertAlmostEqual(report["totals"]["total_pnl"], 0.95)

    def test_recent_leg_events_include_close_reason(self) -> None:
        with patch("src.database._utc_now") as mock_now:
            mock_now.return_value = "2026-08-17T01:00:00+00:00"
            lot_id = self._open_lot()
            mock_now.return_value = "2026-08-17T02:00:00+00:00"
            db.close_lot_side(
                lot_id, "long", realized_pnl_usdt=0.5, close_price=100.5, close_reason="tp"
            )
        events = db.get_recent_leg_events(10)
        close_events = [row for row in events if row["event_type"] == "close"]
        self.assertEqual(len(close_events), 1)
        self.assertEqual(close_events[0]["close_reason"], "tp")


if __name__ == "__main__":
    unittest.main()
