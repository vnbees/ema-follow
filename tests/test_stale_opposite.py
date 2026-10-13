from datetime import datetime, timedelta, timezone

from src.donchian.stale_opposite import qualifies_stale_opposite


def _lot(side: str, *, hours_ago: float, entry: float = 100.0, size: float = 1.0):
    opened = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"side": side, "entry_px": entry, "size": size, "opened_at": opened.isoformat()}


def test_allows_opposite_when_old_and_red():
    now = datetime.now(timezone.utc)
    lots = [_lot("long", hours_ago=7, entry=100)]
    assert qualifies_stale_opposite(lots, "short", mark=99, now=now, min_hours=6)


def test_blocks_when_young_or_green_or_same_side():
    now = datetime.now(timezone.utc)
    young = [_lot("long", hours_ago=2, entry=100)]
    green = [_lot("long", hours_ago=8, entry=100)]
    assert not qualifies_stale_opposite(young, "short", mark=90, now=now, min_hours=6)
    assert not qualifies_stale_opposite(green, "short", mark=101, now=now, min_hours=6)
    assert not qualifies_stale_opposite([_lot("short", hours_ago=8)], "short", mark=90, now=now, min_hours=6)


def test_disabled_when_hours_not_positive():
    now = datetime.now(timezone.utc)
    lots = [_lot("long", hours_ago=10, entry=100)]
    assert not qualifies_stale_opposite(lots, "short", mark=90, now=now, min_hours=0)
