from dataclasses import dataclass, field

from src.rsi import RsiSnapshot


@dataclass
class RsiSignal:
    side: str | None = None  # "long" | "short"
    entry_trigger: str = ""
    reasons: list[str] = field(default_factory=list)


def detect_entry_signal(snap: RsiSnapshot) -> RsiSignal:
    if not snap.ready:
        return RsiSignal(reasons=["rsi_not_ready"])

    signal = RsiSignal()
    if snap.cross_up_25:
        signal.side = "long"
        signal.entry_trigger = "rsi_cross_25"
        signal.reasons.append("entry_ok")
        return signal

    if snap.cross_down_75:
        signal.side = "short"
        signal.entry_trigger = "rsi_cross_75"
        signal.reasons.append("entry_ok")
        return signal

    signal.reasons.append("no_rsi_cross")
    return signal


def detect_dca_signal(position_side: str, snap: RsiSnapshot) -> RsiSignal | None:
    if not snap.ready:
        return None

    if position_side == "long" and snap.cross_up_25:
        return RsiSignal(
            side="long",
            entry_trigger="rsi_cross_25_dca",
            reasons=["dca_ok"],
        )

    if position_side == "short" and snap.cross_down_75:
        return RsiSignal(
            side="short",
            entry_trigger="rsi_cross_75_dca",
            reasons=["dca_ok"],
        )

    return None


def should_exit(position_side: str, snap: RsiSnapshot) -> tuple[bool, str]:
    if not snap.ready:
        return False, ""

    if position_side == "long" and snap.cross_up_75:
        return True, "rsi_cross_75"
    if position_side == "short" and snap.cross_down_25:
        return True, "rsi_cross_25"
    return False, ""
