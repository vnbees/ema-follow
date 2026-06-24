from dataclasses import dataclass

from src.config import (
    OFI_BOOK_STRONG_PCT,
    OFI_EARLY_ENTRY_SEC,
    OFI_IMBALANCE_STRONG_PCT,
)
from src.orderflow.aggregator import OrderFlowSnapshot
from src.orderflow.metrics import classify_imbalance_tier
from src.orderflow.orderbook import BookPressureSnapshot


@dataclass
class OfiEntryDecision:
    side: str | None
    reason: str


def evaluate_ofi_entry(
    snapshot: OrderFlowSnapshot,
    book: BookPressureSnapshot,
    *,
    stats_ready: bool,
) -> OfiEntryDecision:
    if not stats_ready:
        return OfiEntryDecision(None, "warmup")

    if snapshot.candle_age_sec > OFI_EARLY_ENTRY_SEC:
        return OfiEntryDecision(None, "past_early_window")

    if book.stale:
        return OfiEntryDecision(None, "book_stale")

    forming_tier = classify_imbalance_tier(snapshot.forming_imbalance_pct)
    strong_bull = forming_tier in ("strong_bull", "extreme_bull")
    strong_bear = forming_tier in ("strong_bear", "extreme_bear")

    if (
        strong_bull
        and snapshot.forming_imbalance_pct >= OFI_IMBALANCE_STRONG_PCT
        and snapshot.current_delta > 0
        and snapshot.delta_velocity > 0
        and book.book_pressure_pct >= OFI_BOOK_STRONG_PCT
    ):
        return OfiEntryDecision("long", "early_bull_flow")

    if (
        strong_bear
        and snapshot.forming_imbalance_pct <= 10000.0 / OFI_IMBALANCE_STRONG_PCT
        and snapshot.current_delta < 0
        and snapshot.delta_velocity < 0
        and book.book_pressure_pct <= 10000.0 / OFI_BOOK_STRONG_PCT
    ):
        return OfiEntryDecision("short", "early_bear_flow")

    return OfiEntryDecision(None, "no_signal")


def evaluate_ofi_exit(
    snapshot: OrderFlowSnapshot,
    book: BookPressureSnapshot,
    position_side: str,
) -> tuple[bool, str]:
    forming_tier = classify_imbalance_tier(snapshot.forming_imbalance_pct)

    if position_side == "long":
        if (
            forming_tier in ("strong_bear", "extreme_bear")
            and snapshot.current_delta < 0
            and snapshot.delta_velocity < 0
        ):
            return True, "bearish_flow_reversal"
        if not book.stale and book.book_bias == "bearish":
            return True, "book_bearish"

    if position_side == "short":
        if (
            forming_tier in ("strong_bull", "extreme_bull")
            and snapshot.current_delta > 0
            and snapshot.delta_velocity > 0
        ):
            return True, "bullish_flow_reversal"
        if not book.stale and book.book_bias == "bullish":
            return True, "book_bullish"

    return False, ""
