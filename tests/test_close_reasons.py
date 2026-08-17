import unittest

from src.close_reasons import (
    REASON_AGE,
    REASON_ORPHAN_BE,
    REASON_TP,
    format_close_reasons_vi,
    reason_label_vi,
)


class TestCloseReasonLabels(unittest.TestCase):
    def test_tp_includes_configured_pct(self) -> None:
        self.assertIn("chạm TP", reason_label_vi(REASON_TP))
        self.assertIn("%", reason_label_vi(REASON_TP))

    def test_orphan_and_age_labels(self) -> None:
        self.assertEqual(reason_label_vi(REASON_ORPHAN_BE), "chạm entry (chân còn lại đã TP)")
        self.assertIn("hết hạn", reason_label_vi(REASON_AGE))
        self.assertIn("ngày", reason_label_vi(REASON_AGE))

    def test_format_single_and_mixed(self) -> None:
        self.assertEqual(format_close_reasons_vi([REASON_TP]), reason_label_vi(REASON_TP))
        self.assertEqual(
            format_close_reasons_vi([REASON_TP, REASON_TP]),
            f"{reason_label_vi(REASON_TP)} ×2",
        )
        mixed = format_close_reasons_vi([REASON_TP, REASON_ORPHAN_BE, REASON_TP])
        self.assertIn("×2", mixed)
        self.assertIn(reason_label_vi(REASON_ORPHAN_BE), mixed)


if __name__ == "__main__":
    unittest.main()
