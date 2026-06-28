from dataclasses import dataclass, field

from src.config import PAIR_PROFIT_TARGET_PCT
from src.rsi import RsiSnapshot


@dataclass
class RsiSignal:
    side: str | None = None  # "pair" when cross entry event
    entry_trigger: str = ""
    reasons: list[str] = field(default_factory=list)


def detect_pair_event(snap: RsiSnapshot) -> RsiSignal | None:
    if not snap.ready:
        return None
    if snap.cross_up_25:
        return RsiSignal(side="pair", entry_trigger="rsi_cross_25", reasons=["cross_up_25"])
    if snap.cross_down_75:
        return RsiSignal(side="pair", entry_trigger="rsi_cross_75", reasons=["cross_down_75"])
    return None


def detect_entry_signal(snap: RsiSnapshot) -> RsiSignal:
    """Legacy alias for scan compatibility."""
    event = detect_pair_event(snap)
    if event:
        return event
    return RsiSignal(reasons=["no_rsi_cross"])


def price_move_pct(side: str, entry: float, mark: float) -> float:
    if entry <= 0 or mark <= 0:
        return 0.0
    if side == "long":
        return (mark - entry) / entry * 100
    return (entry - mark) / entry * 100


def should_take_profit(
    side: str,
    entry: float,
    mark: float,
    target_pct: float | None = None,
) -> bool:
    target = PAIR_PROFIT_TARGET_PCT if target_pct is None else target_pct
    return price_move_pct(side, entry, mark) >= target - 1e-9
