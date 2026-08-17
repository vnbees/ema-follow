from __future__ import annotations

from dataclasses import dataclass

from src.ema_rsi.config import EMA_PERIOD, RR, RSI_HIGH, RSI_LOW, RSI_PERIOD
from src.exchange.types import Candle
from src.rsi import compute_rsi_series


@dataclass(frozen=True)
class EntrySignal:
    side: str
    entry: float
    sl: float
    tp: float
    r: float
    zone_start_ts: int
    zone_end_ts: int
    signal_ts: int
    skip_reason: str | None = None


def compute_ema_series(closes: list[float], period: int) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period or period <= 0:
        return out
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    out[period - 1] = ema
    for i in range(period, n):
        ema = (closes[i] - ema) * multiplier + ema
        out[i] = ema
    return out


def ema_cross_dir(
    prev_close: float,
    prev_ema: float,
    close: float,
    ema: float,
) -> str | None:
    crossed_up = prev_close <= prev_ema and close > ema
    crossed_down = prev_close >= prev_ema and close < ema
    if crossed_up and not crossed_down:
        return "up"
    if crossed_down and not crossed_up:
        return "down"
    return None


def _extreme_runs(
    rsi: list[float | None],
    *,
    below: float | None,
    above: float | None,
    last_i: int,
) -> list[tuple[int, int]]:
    """Inclusive (start, end) runs in [0, last_i] matching the RSI extreme."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i in range(last_i + 1):
        val = rsi[i]
        hit = val is not None and (
            (below is not None and val < below) or (above is not None and val > above)
        )
        if hit:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, last_i))
    return runs


def _latest_zone_before_cross(
    rsi: list[float | None],
    *,
    below: float | None,
    above: float | None,
    opposite_below: float | None,
    opposite_above: float | None,
    cross_i: int,
) -> tuple[int, int] | None:
    runs = _extreme_runs(rsi, below=below, above=above, last_i=cross_i)
    zone = None
    for start, end in runs:
        if start < cross_i:
            zone = (start, end)
    if zone is None:
        return None
    start, end = zone
    opposite = _extreme_runs(
        rsi, below=opposite_below, above=opposite_above, last_i=cross_i - 1
    )
    for opp_start, _opp_end in opposite:
        if start < opp_start < cross_i:
            return None
    return start, end


def _sl_from_zone(candles: list[Candle], start: int, end: int, side: str) -> float:
    window = candles[start : end + 1]
    if side == "long":
        return min(c.low for c in window)
    return max(c.high for c in window)


def levels_from_entry(side: str, entry: float, sl: float, rr: float = RR) -> tuple[float, float] | None:
    """Return (r, tp) or None when SL is on the wrong side / zero R."""
    if side == "long":
        if sl >= entry:
            return None
        r = entry - sl
        return r, entry + rr * r
    if sl <= entry:
        return None
    r = sl - entry
    return r, entry - rr * r


def evaluate_last_bar(
    candles: list[Candle],
    rsi: list[float | None],
    ema: list[float | None],
    *,
    rsi_low: float = RSI_LOW,
    rsi_high: float = RSI_HIGH,
    rr: float = RR,
) -> EntrySignal | None:
    i = len(candles) - 1
    if i < 1:
        return None
    if len(rsi) != len(candles) or len(ema) != len(candles):
        return None
    prev_ema = ema[i - 1]
    curr_ema = ema[i]
    if prev_ema is None or curr_ema is None:
        return None
    prev = candles[i - 1]
    curr = candles[i]
    direction = ema_cross_dir(prev.close, prev_ema, curr.close, curr_ema)
    if direction is None:
        return None

    if direction == "up":
        zone = _latest_zone_before_cross(
            rsi,
            below=rsi_low,
            above=None,
            opposite_below=None,
            opposite_above=rsi_high,
            cross_i=i,
        )
        if zone is None:
            return None
        start, end = zone
        sl = _sl_from_zone(candles, start, end, "long")
        sized = levels_from_entry("long", curr.close, sl, rr)
        if sized is None:
            return EntrySignal(
                side="long",
                entry=curr.close,
                sl=sl,
                tp=0.0,
                r=0.0,
                zone_start_ts=candles[start].timestamp,
                zone_end_ts=candles[end].timestamp,
                signal_ts=curr.timestamp,
                skip_reason="invalid_sl",
            )
        r, tp = sized
        return EntrySignal(
            side="long",
            entry=curr.close,
            sl=sl,
            tp=tp,
            r=r,
            zone_start_ts=candles[start].timestamp,
            zone_end_ts=candles[end].timestamp,
            signal_ts=curr.timestamp,
        )

    zone = _latest_zone_before_cross(
        rsi,
        below=None,
        above=rsi_high,
        opposite_below=rsi_low,
        opposite_above=None,
        cross_i=i,
    )
    if zone is None:
        return None
    start, end = zone
    sl = _sl_from_zone(candles, start, end, "short")
    sized = levels_from_entry("short", curr.close, sl, rr)
    if sized is None:
        return EntrySignal(
            side="short",
            entry=curr.close,
            sl=sl,
            tp=0.0,
            r=0.0,
            zone_start_ts=candles[start].timestamp,
            zone_end_ts=candles[end].timestamp,
            signal_ts=curr.timestamp,
            skip_reason="invalid_sl",
        )
    r, tp = sized
    return EntrySignal(
        side="short",
        entry=curr.close,
        sl=sl,
        tp=tp,
        r=r,
        zone_start_ts=candles[start].timestamp,
        zone_end_ts=candles[end].timestamp,
        signal_ts=curr.timestamp,
    )


def detect_entry(
    candles: list[Candle],
    *,
    rsi_period: int = RSI_PERIOD,
    ema_period: int = EMA_PERIOD,
    rsi_low: float = RSI_LOW,
    rsi_high: float = RSI_HIGH,
    rr: float = RR,
) -> EntrySignal | None:
    if len(candles) < ema_period + 2:
        return None
    closes = [c.close for c in candles]
    rsi = compute_rsi_series(candles, rsi_period)
    ema = compute_ema_series(closes, ema_period)
    return evaluate_last_bar(
        candles, rsi, ema, rsi_low=rsi_low, rsi_high=rsi_high, rr=rr
    )
