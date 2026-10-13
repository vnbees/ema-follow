"""Opposite entry while a same-coin lot is already open, stale, and losing."""

from __future__ import annotations

from datetime import datetime, timezone

ENTRY_STANDARD = "standard"
ENTRY_STALE_OPPOSITE = "stale_opposite"


def unrealized_pnl(side: str, entry: float, mark: float, size: float) -> float:
    if side == "long":
        return (mark - entry) * size
    return (entry - mark) * size


def _opened_at(lot) -> datetime | None:
    raw = lot["opened_at"] if hasattr(lot, "keys") else lot.get("opened_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _field(lot, key: str, default=None):
    try:
        if hasattr(lot, "keys") and key not in lot.keys():
            return default
        return lot[key]
    except (KeyError, IndexError, TypeError):
        return default


def qualifies_stale_opposite(
    open_lots,
    new_side: str,
    mark: float,
    now: datetime,
    *,
    min_hours: float,
) -> bool:
    """True when `new_side` may open beside an opposite lot that is old and red.

    Same side already open → False. Needs mark > 0. Age is from `opened_at`.
    Underwater means gross unrealized PnL at `mark` is ≤ 0.
    """
    if min_hours <= 0 or mark <= 0:
        return False
    side = new_side.lower()
    opposite = []
    for lot in open_lots:
        held = str(_field(lot, "side", "")).lower()
        if held == side:
            return False
        if held:
            opposite.append(lot)
    if not opposite:
        return False
    for lot in opposite:
        opened = _opened_at(lot)
        if opened is None:
            continue
        age_h = (now - opened).total_seconds() / 3600.0
        if age_h < min_hours:
            continue
        entry = float(_field(lot, "entry_px", 0) or 0)
        size = float(_field(lot, "size", 0) or 0)
        if entry <= 0 or size <= 0:
            continue
        if unrealized_pnl(str(_field(lot, "side")), entry, mark, size) <= 0:
            return True
    return False
