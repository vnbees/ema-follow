from __future__ import annotations

from dataclasses import dataclass

from src.exchange.types import Candle
from src.rsi import compute_rsi_series
from src.rsi_rev.config import MID_HIGH, MID_LOW, MOVE_AWAY_PCT, RSI_PERIOD, ZONE_PCT

ZONE_RSI70 = "rsi70"
ZONE_RSI30 = "rsi30"
ZONE_RSI50 = "rsi50"

ZONE_LABELS = {
    ZONE_RSI70: "RSI >= 70",
    ZONE_RSI30: "RSI <= 30",
    ZONE_RSI50: f"RSI {MID_LOW:.0f}-{MID_HIGH:.0f}",
}


@dataclass(frozen=True)
class AnchorEvent:
    zone: str
    ts: int
    price: float
    rsi: float


@dataclass(frozen=True)
class EntryTrigger:
    side: str
    entry: float
    tp: float
    anchor_ts: int
    anchor_price: float
    anchor_rsi: float
    zone: str
    signal_ts: int


def tp_price(side: str, anchor: float, zone_pct: float = ZONE_PCT) -> float:
    if side == "short":
        return anchor * (1 + zone_pct)
    return anchor * (1 - zone_pct)


def detect_anchor_events(
    candles: list[Candle],
    rsi: list[float | None] | None = None,
) -> list[AnchorEvent]:
    """Events on the last closed bar only (no look-ahead)."""
    if rsi is None:
        rsi = compute_rsi_series(candles, RSI_PERIOD)
    if len(candles) < 2 or len(rsi) < 2:
        return []
    i = len(candles) - 1
    cur, prev = rsi[i], rsi[i - 1]
    if cur is None or prev is None:
        return []
    events: list[AnchorEvent] = []
    bar = candles[i]
    if cur >= 70 and prev < 70:
        events.append(AnchorEvent(ZONE_RSI70, bar.timestamp, bar.close, cur))
    if cur <= 30 and prev > 30:
        events.append(AnchorEvent(ZONE_RSI30, bar.timestamp, bar.close, cur))
    in_mid = MID_LOW <= cur <= MID_HIGH
    prev_mid = MID_LOW <= prev <= MID_HIGH
    if in_mid and not prev_mid:
        events.append(AnchorEvent(ZONE_RSI50, bar.timestamp, bar.close, cur))
    return events


def trigger_from_bar(
    *,
    zone: str,
    anchor_ts: int,
    anchor_price: float,
    anchor_rsi: float,
    bar: Candle,
    move_away_pct: float = MOVE_AWAY_PCT,
) -> EntryTrigger | None:
    """First 0.5% leave on this closed bar. Same-bar both wicks: follow close vs anchor."""
    if bar.timestamp <= anchor_ts:
        return None
    up = bar.high >= anchor_price * (1 + move_away_pct)
    dn = bar.low <= anchor_price * (1 - move_away_pct)
    side: str | None = None
    if up and dn:
        side = "short" if bar.close >= anchor_price else "long"
    elif up:
        side = "short"
    elif dn:
        side = "long"
    if side is None:
        return None
    return EntryTrigger(
        side=side,
        entry=bar.close,
        tp=tp_price(side, anchor_price),
        anchor_ts=anchor_ts,
        anchor_price=anchor_price,
        anchor_rsi=anchor_rsi,
        zone=zone,
        signal_ts=bar.timestamp,
    )


def lot_age_hours(opened_at_epoch: float, now_epoch: float) -> float:
    return max(0.0, (now_epoch - opened_at_epoch) / 3600.0)


def exit_reason_for_mark(
    *,
    side: str,
    mark: float,
    entry: float,
    tp: float,
    age_hours: float,
    be_after_hours: float,
    max_age_days: float,
) -> str | None:
    """Priority: TP, then BE after 7d, then timeout 30d."""
    hit_tp = (side == "long" and mark >= tp) or (side == "short" and mark <= tp)
    if hit_tp:
        return "TP"
    if age_hours >= be_after_hours:
        hit_be = (side == "long" and mark <= entry) or (side == "short" and mark >= entry)
        if hit_be:
            return "BE_AFTER_7D"
    if age_hours >= max_age_days * 24:
        return "TIMEOUT_30D"
    return None


def exit_status_label(age_hours: float, be_after_hours: float, max_age_days: float) -> str:
    if age_hours >= max_age_days * 24 * 0.9:
        return "gần 30 ngày"
    if age_hours >= be_after_hours:
        return "sau 7 ngày: chờ BE về entry"
    return "chờ TP"
