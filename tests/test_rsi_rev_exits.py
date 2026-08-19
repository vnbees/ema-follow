import unittest

from src.rsi_rev.signals import exit_reason_for_mark, exit_status_label, tp_price


class TestRsiRevExits(unittest.TestCase):
    def test_long_tp_before_be_and_timeout(self) -> None:
        anchor = 100.0
        tp = tp_price("long", anchor)
        reason = exit_reason_for_mark(
            side="long",
            mark=tp,
            entry=99.5,
            tp=tp,
            age_hours=200,
            be_after_hours=168,
            max_age_days=30,
        )
        self.assertEqual(reason, "TP")

    def test_short_tp_before_be(self) -> None:
        anchor = 100.0
        tp = tp_price("short", anchor)
        reason = exit_reason_for_mark(
            side="short",
            mark=tp,
            entry=100.5,
            tp=tp,
            age_hours=200,
            be_after_hours=168,
            max_age_days=30,
        )
        self.assertEqual(reason, "TP")

    def test_be_only_after_7_days(self) -> None:
        early = exit_reason_for_mark(
            side="long",
            mark=99.5,
            entry=99.5,
            tp=99.75,
            age_hours=24,
            be_after_hours=168,
            max_age_days=30,
        )
        later = exit_reason_for_mark(
            side="long",
            mark=99.5,
            entry=99.5,
            tp=99.75,
            age_hours=168,
            be_after_hours=168,
            max_age_days=30,
        )
        self.assertIsNone(early)
        self.assertEqual(later, "BE_AFTER_7D")

    def test_short_be_when_mark_back_to_entry(self) -> None:
        reason = exit_reason_for_mark(
            side="short",
            mark=100.5,
            entry=100.5,
            tp=100.25,
            age_hours=170,
            be_after_hours=168,
            max_age_days=30,
        )
        self.assertEqual(reason, "BE_AFTER_7D")

    def test_timeout_after_30_days(self) -> None:
        # Price has not returned to entry and has not hit TP.
        reason = exit_reason_for_mark(
            side="long",
            mark=99.6,
            entry=99.5,
            tp=99.75,
            age_hours=30 * 24,
            be_after_hours=168,
            max_age_days=30,
        )
        self.assertEqual(reason, "TIMEOUT_30D")

    def test_long_and_short_independent(self) -> None:
        long_reason = exit_reason_for_mark(
            side="long",
            mark=99.8,
            entry=99.5,
            tp=99.75,
            age_hours=10,
            be_after_hours=168,
            max_age_days=30,
        )
        short_reason = exit_reason_for_mark(
            side="short",
            mark=100.4,
            entry=100.5,
            tp=100.25,
            age_hours=10,
            be_after_hours=168,
            max_age_days=30,
        )
        self.assertEqual(long_reason, "TP")
        self.assertIsNone(short_reason)

    def test_exit_status_labels(self) -> None:
        self.assertEqual(exit_status_label(3, 168, 30), "chờ TP")
        self.assertEqual(
            exit_status_label(170, 168, 30),
            "sau 7 ngày: chờ BE về entry",
        )
        self.assertEqual(exit_status_label(29 * 24, 168, 30), "gần 30 ngày")


if __name__ == "__main__":
    unittest.main()
