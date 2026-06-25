from dataclasses import dataclass, field

from src.supertrend import SuperTrendSnapshot, Trend


@dataclass
class SuperTrendSignal:
    side: str | None = None  # "long" | "short"
    entry_trigger: str = ""  # "5m_flip"
    flip_5m: bool = False
    trend_5m: Trend = ""
    trend_1h: Trend = ""
    reasons: list[str] = field(default_factory=list)


def _trend_to_side(trend: Trend) -> str | None:
    if trend == "bullish":
        return "long"
    if trend == "bearish":
        return "short"
    return None


def detect_entry_signal(
    snap_5m: SuperTrendSnapshot,
    snap_1h: SuperTrendSnapshot,
) -> SuperTrendSignal:
    if not snap_5m.ready or not snap_1h.ready:
        return SuperTrendSignal(reasons=["supertrend_not_ready"])

    signal = SuperTrendSignal(
        flip_5m=snap_5m.flipped,
        trend_5m=snap_5m.trend,
        trend_1h=snap_1h.trend,
    )

    if not snap_5m.flipped:
        signal.reasons.append("no_5m_flip")
        return signal

    side_5m = _trend_to_side(snap_5m.trend)
    side_1h = _trend_to_side(snap_1h.trend)

    if side_5m is None or side_1h is None:
        signal.reasons.append("invalid_trend")
        return signal

    if side_5m != side_1h:
        signal.reasons.append(f"1h_mismatch (5m={snap_5m.trend}, 1h={snap_1h.trend})")
        return signal

    signal.side = side_5m
    signal.entry_trigger = "5m_flip"
    signal.reasons.append("entry_ok")
    return signal


def should_exit(
    position_side: str,
    trend_5m: Trend,
    trend_1h: Trend,
) -> tuple[bool, str]:
    """Exit if either 5m or 1h trend opposes the open position."""
    if position_side == "long":
        if trend_5m == "bearish":
            return True, "trend_5m_reverse"
        if trend_1h == "bearish":
            return True, "trend_1h_reverse"
        return False, ""
    if position_side == "short":
        if trend_5m == "bullish":
            return True, "trend_5m_reverse"
        if trend_1h == "bullish":
            return True, "trend_1h_reverse"
        return False, ""
    return False, ""
