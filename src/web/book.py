"""Fresh dashboard book: numbers start at the anchor, history stays on the other tab."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src import database as db

ANCHOR = 1051.0
_VN = ZoneInfo("Asia/Ho_Chi_Minh")


def book_epoch() -> dict | None:
    raw = db.get_setting("book_epoch_at", "")
    if not raw:
        return None
    try:
        equity = float(db.get_setting("book_epoch_equity", "") or 0)
        signed = float(db.get_setting("book_epoch_signed", "") or 0)
    except ValueError:
        return None
    return {"at": raw, "equity": equity, "signed": signed, "anchor": ANCHOR}


def book_since(book: str) -> str | None:
    if book != "fresh":
        return None
    epoch = book_epoch()
    return epoch["at"] if epoch else None


def fresh_total(equity: float) -> float:
    """Live futures plus bot skim since the anchor. 1051 is only the comparison line."""
    epoch = book_epoch()
    if equity <= 0 and epoch is not None:
        equity = epoch["equity"]
    signed_since = db.signed_spot_flow() - (epoch["signed"] if epoch else 0.0)
    return equity + signed_since


def dual_book() -> dict | None:
    """Dual-limit dashboard anchor: futures equity when the mode first ran."""
    raw = db.get_setting("dual_book_at", "")
    if not raw:
        return None
    try:
        anchor = float(db.get_setting("dual_book_anchor", "") or 0)
        signed = float(db.get_setting("dual_book_signed", "") or 0)
    except ValueError:
        return None
    if anchor <= 0:
        return None
    return {"at": raw, "anchor": anchor, "signed": signed}


def ensure_dual_book(equity: float) -> dict | None:
    """Set the anchor once, from the first valid futures equity. Later calls keep it."""
    existing = dual_book()
    if existing is not None or equity <= 0:
        return existing
    db.set_setting("dual_book_at", datetime.now(timezone.utc).isoformat())
    db.set_setting("dual_book_anchor", f"{float(equity):.4f}")
    db.set_setting("dual_book_signed", f"{db.signed_spot_flow():.4f}")
    return dual_book()


def dual_withdrawn(book: dict | None) -> float:
    """Net skim to spot since the dual anchor."""
    if book is None:
        return 0.0
    return db.signed_spot_flow() - float(book["signed"])


def epoch_vn_date() -> str | None:
    epoch = book_epoch()
    if epoch is None:
        return None
    try:
        dt = datetime.fromisoformat(epoch["at"].replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_VN).strftime("%Y-%m-%d")
