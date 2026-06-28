import unittest
from datetime import date

from src.web.calendar_build import build_rsi_pnl_calendar, utc_timestamp_to_vn_date


class TestUtcTimestampToVnDate(unittest.TestCase):
    def test_iso_utc_before_midnight_vn(self):
        # 2026-06-19 18:00 UTC = 2026-06-20 01:00 VN
        self.assertEqual(
            utc_timestamp_to_vn_date("2026-06-19T18:00:00+00:00"),
            date(2026, 6, 20),
        )

    def test_legacy_utc_suffix(self):
        self.assertEqual(
            utc_timestamp_to_vn_date("2026-06-20 10:00:00 UTC"),
            date(2026, 6, 20),
        )


class TestBuildRsiPnlCalendar(unittest.TestCase):
    def _trade(
        self,
        symbol: str,
        closed_at: str,
        pnl: float | None,
        close_reason: str = "rsi_cross_75",
    ) -> dict:
        return {
            "symbol": symbol,
            "side": "long",
            "closed_at": closed_at,
            "realized_pnl_usdt": pnl,
            "close_reason": close_reason,
            "margin_usdt": 5.0,
            "dca_count": 0,
        }

    def test_same_day_pnl_sum(self):
        trades = [
            self._trade("BTCUSDT", "2026-06-20T08:00:00+00:00", 3.5),
            self._trade("ETHUSDT", "2026-06-20T14:00:00+00:00", -1.2),
        ]
        cal = build_rsi_pnl_calendar(2026, 6, trades)
        day20 = None
        for week in cal["weeks"]:
            for cell in week:
                if cell.get("day") == 20:
                    day20 = cell
                    break
        self.assertIsNotNone(day20)
        self.assertAlmostEqual(day20["daily_pnl"], 2.3)
        self.assertEqual(day20["trade_count"], 2)
        self.assertEqual(day20["pnl_count"], 2)
        self.assertAlmostEqual(cal["month_summary"]["total_pnl"], 2.3)
        self.assertEqual(cal["month_summary"]["total_trades"], 2)

    def test_exchange_closed_no_pnl_in_sum(self):
        trades = [
            self._trade("BTCUSDT", "2026-06-20T08:00:00+00:00", 5.0),
            self._trade("SOLUSDT", "2026-06-20T10:00:00+00:00", None, "exchange_closed"),
        ]
        cal = build_rsi_pnl_calendar(2026, 6, trades)
        day20 = None
        for week in cal["weeks"]:
            for cell in week:
                if cell.get("day") == 20:
                    day20 = cell
        self.assertIsNotNone(day20)
        self.assertAlmostEqual(day20["daily_pnl"], 5.0)
        self.assertEqual(day20["trade_count"], 2)
        self.assertEqual(day20["pnl_count"], 1)
        self.assertAlmostEqual(cal["month_summary"]["total_pnl"], 5.0)
        self.assertEqual(cal["month_summary"]["total_trades"], 2)
        self.assertEqual(cal["month_summary"]["total_with_pnl"], 1)

    def test_other_month_excluded(self):
        trades = [
            self._trade("BTCUSDT", "2026-05-30T10:00:00+00:00", 10.0),
            self._trade("ETHUSDT", "2026-06-01T00:00:00+00:00", 2.0),
        ]
        cal = build_rsi_pnl_calendar(2026, 6, trades)
        self.assertEqual(cal["month_summary"]["total_trades"], 1)
        self.assertAlmostEqual(cal["month_summary"]["total_pnl"], 2.0)
        self.assertEqual(len(cal["month_events"]), 1)
        self.assertEqual(cal["month_events"][0]["symbol"], "ETHUSDT")


if __name__ == "__main__":
    unittest.main()
