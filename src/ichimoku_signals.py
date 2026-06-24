from dataclasses import dataclass, field

from src.config import ICHIMOKU_RETEST_TOLERANCE_TICKS
from src.ichimoku import IchimokuSnapshot


@dataclass
class IchimokuSignal:
    side: str | None = None
    trigger: str | None = None
    consensus_ok: bool = False
    reasons: list[str] = field(default_factory=list)


def _kijun_retest_zone(kijun: float, tick_size: float) -> tuple[float, float]:
    band = ICHIMOKU_RETEST_TOLERANCE_TICKS * tick_size
    return kijun - band, kijun + band


def _long_breakout(snap: IchimokuSnapshot) -> bool:
    return snap.prev_close <= snap.prev_kijun and snap.close > snap.kijun


def _long_retest(snap: IchimokuSnapshot, tick_size: float) -> bool:
    low_band, high_band = _kijun_retest_zone(snap.prev_kijun, tick_size)
    touched = snap.prev_low <= high_band and snap.prev_high >= low_band
    green_bounce = snap.close > snap.kijun and snap.close > snap.open
    return touched and green_bounce


def _short_breakout(snap: IchimokuSnapshot) -> bool:
    return snap.prev_close >= snap.prev_kijun and snap.close < snap.kijun


def _short_retest(snap: IchimokuSnapshot, tick_size: float) -> bool:
    low_band, high_band = _kijun_retest_zone(snap.prev_kijun, tick_size)
    touched = snap.prev_low <= high_band and snap.prev_high >= low_band
    red_reject = snap.close < snap.kijun and snap.close < snap.open
    return touched and red_reject


def detect_ichimoku_signal(snap: IchimokuSnapshot, tick_size: float) -> IchimokuSignal:
    if not snap.ready:
        return IchimokuSignal(reasons=["ichimoku_not_ready"])

    reasons: list[str] = []

    long_consensus = (
        snap.price_vs_kumo == "above"
        and snap.kumo_color == "green"
        and snap.kumo_rising
        and snap.chikou_bullish
    )
    short_consensus = (
        snap.price_vs_kumo == "below"
        and snap.kumo_color == "red"
        and snap.kumo_falling
        and snap.chikou_bearish
    )

    if long_consensus:
        reasons.append("long_consensus")
        trigger = None
        if _long_breakout(snap):
            trigger = "breakout"
        elif _long_retest(snap, tick_size):
            trigger = "retest"
        if trigger:
            return IchimokuSignal(
                side="long",
                trigger=trigger,
                consensus_ok=True,
                reasons=reasons + [f"trigger_{trigger}"],
            )
        return IchimokuSignal(consensus_ok=True, reasons=reasons + ["no_trigger"])

    if short_consensus:
        reasons.append("short_consensus")
        trigger = None
        if _short_breakout(snap):
            trigger = "breakout"
        elif _short_retest(snap, tick_size):
            trigger = "retest"
        if trigger:
            return IchimokuSignal(
                side="short",
                trigger=trigger,
                consensus_ok=True,
                reasons=reasons + [f"trigger_{trigger}"],
            )
        return IchimokuSignal(consensus_ok=True, reasons=reasons + ["no_trigger"])

    if snap.price_vs_kumo != "above" and snap.price_vs_kumo != "below":
        reasons.append("price_inside_kumo")
    if not snap.chikou_bullish and not snap.chikou_bearish:
        reasons.append("chikou_neutral")
    return IchimokuSignal(consensus_ok=False, reasons=reasons or ["no_consensus"])
