"""Pool breadth from Donchian mid — live gate / flip (BT breadth_mid + breadth_flip).

For each symbol: close > dc_middle → up, else down.
Vote long/short only when sample ≥ min_n and lead ≥ ratio × other; else None (neutral).

Modes (see config BREADTH_MODE):
- hard: conflict → skip entry
- flip: conflict → reverse side (align to vote), recompute TP/opp/pot/size_mult
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.donchian.signals import DonchianBar, EntrySignal, size_mult_from_pot_rr


@dataclass(frozen=True)
class BreadthVote:
    """Snapshot used for logging / gating."""

    side: str | None  # "long" | "short" | None (neutral)
    ups: int
    downs: int
    total: int
    ratio: float
    min_n: int

    @property
    def lead_ratio(self) -> float:
        if self.ups == self.downs:
            return 1.0
        lead, other = (self.ups, self.downs) if self.ups > self.downs else (self.downs, self.ups)
        return lead / max(other, 1)


def mid_side(bars: Sequence[DonchianBar], period: int) -> str | None:
    """Return 'up' / 'down' from last bar close vs Donchian mid, or None if warm-up."""
    n = len(bars)
    if period <= 0 or n < period:
        return None
    window = bars[-period:]
    upper = max(b.high for b in window)
    lower = min(b.low for b in window)
    middle = (upper + lower) / 2.0
    close = bars[-1].close
    return "up" if close > middle else "down"


def vote_breadth(
    sides: Sequence[str | None],
    *,
    ratio: float = 1.3,
    min_n: int = 12,
) -> BreadthVote:
    """Aggregate per-coin mid sides into a pool vote."""
    ups = sum(1 for s in sides if s == "up")
    downs = sum(1 for s in sides if s == "down")
    total = ups + downs
    if total < min_n or ups == downs:
        return BreadthVote(None, ups, downs, total, ratio, min_n)
    lead, other = (ups, downs) if ups > downs else (downs, ups)
    if ratio > 1.01 and lead < other * ratio:
        return BreadthVote(None, ups, downs, total, ratio, min_n)
    side = "long" if ups > downs else "short"
    return BreadthVote(side, ups, downs, total, ratio, min_n)


def allows_side(vote: BreadthVote | None, side: str) -> bool:
    """True if entry side matches vote (neutral = both)."""
    if vote is None or vote.side is None:
        return True
    return side == vote.side


def flip_entry_signal(entry: EntrySignal, *, vote: BreadthVote) -> EntrySignal:
    """Flip long↔short to align with breadth vote; swap TP/opp and rebuild size_mult.

    Matches BT `breadth_flip` (no pot_rr re-filter after flip).
    """
    new_side = "short" if entry.side == "long" else "long"
    # Original: long TP=upper opp=lower. Flip → short TP=old opp, opp=old tp.
    tp_band = entry.opp_band
    opp_band = entry.tp_band
    pot_rr = abs(tp_band - entry.entry_px) / max(abs(entry.entry_px - opp_band), 1e-12)
    size_mult = size_mult_from_pot_rr(pot_rr, enabled=True)
    why = (
        f"{entry.why} · breadth FLIP {entry.side}→{new_side} "
        f"(vote={vote.side} ups={vote.ups} downs={vote.downs})"
    )
    return EntrySignal(
        side=new_side,
        tp_band=tp_band,
        entry_px=entry.entry_px,
        opp_band=opp_band,
        body_atr=entry.body_atr,
        pot_rr=pot_rr,
        size_mult=size_mult,
        atr=entry.atr,
        why=why,
    )
