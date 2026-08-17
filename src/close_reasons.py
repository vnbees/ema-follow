"""Stable close-reason codes for Discord, DB, and the daily dashboard report."""

from __future__ import annotations

from collections import Counter

REASON_TP = "tp"
REASON_ORPHAN_BE = "orphan_be"
REASON_AGE = "age"
REASON_BE_TIME = "be_time"
REASON_MANUAL = "manual"
REASON_FORCE = "force"
REASON_SYNC = "sync"

REPORT_REASONS = (REASON_TP, REASON_ORPHAN_BE, REASON_AGE)


def reason_label_vi(reason: str) -> str:
    from src.config import MAX_LOT_AGE_DAYS, PAIR_PROFIT_TARGET_PCT

    if reason == REASON_TP:
        return f"chạm TP {PAIR_PROFIT_TARGET_PCT:g}%"
    if reason == REASON_ORPHAN_BE:
        return "chạm entry (chân còn lại đã TP)"
    if reason == REASON_AGE:
        days = MAX_LOT_AGE_DAYS
        day_txt = f"{days:g}" if float(days) != int(days) else str(int(days))
        return f"hết hạn {day_txt} ngày"
    if reason == REASON_BE_TIME:
        return "chạm entry (BE theo thời gian)"
    if reason == REASON_MANUAL:
        return "đóng tay trên dashboard"
    if reason == REASON_FORCE:
        return "đóng bắt buộc"
    if reason == REASON_SYNC:
        return "đồng bộ DB với sàn"
    return reason or "—"


def format_close_reasons_vi(reasons: list[str]) -> str:
    counts = Counter(r for r in reasons if r)
    if not counts:
        return ""
    if len(counts) == 1:
        reason, n = next(iter(counts.items()))
        label = reason_label_vi(reason)
        return f"{label} ×{n}" if n > 1 else label
    parts = [f"{reason_label_vi(reason)} ×{n}" for reason, n in counts.items()]
    return "; ".join(parts)
