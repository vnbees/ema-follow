"""One same-side add after the original lot is already in favor by N ATRs."""

from __future__ import annotations

ENTRY_FAVORABLE_ADD = "favorable_add"
_PARENT_RULES = {"", "standard"}


def _field(lot, key: str, default=None):
    try:
        if hasattr(lot, "keys") and key not in lot.keys():
            return default
        return lot[key]
    except (KeyError, IndexError, TypeError):
        return default


def favorable_move(side: str, entry_px: float, close_px: float, atr: float, mult: float) -> bool:
    """True when `close_px` is at least `mult` ATRs in favor of `side`."""
    if mult <= 0 or atr <= 0 or entry_px <= 0 or close_px <= 0:
        return False
    if side.lower() == "long":
        return close_px - entry_px >= mult * atr
    if side.lower() == "short":
        return entry_px - close_px >= mult * atr
    return False


def add_parents(open_lots) -> list:
    """One original lot per side that does not already have a favorable add.

    Parents are `standard` or a blank rule (lots opened before the column
    existed). `stale_opposite` and an existing add are not parents.
    """
    by_side: dict[str, list] = {}
    for lot in open_lots:
        side = str(_field(lot, "side", "") or "").lower()
        if side:
            by_side.setdefault(side, []).append(lot)
    parents = []
    for rows in by_side.values():
        rules = [str(_field(lot, "entry_rule", "") or "").lower() for lot in rows]
        if ENTRY_FAVORABLE_ADD in rules:
            continue
        standards = [lot for lot, rule in zip(rows, rules) if rule in _PARENT_RULES]
        if not standards:
            continue
        standards.sort(key=lambda lot: str(_field(lot, "opened_at", "") or ""))
        parents.append(standards[0])
    return parents
