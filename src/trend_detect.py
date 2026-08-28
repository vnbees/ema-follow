"""15m TREND detection — optimized from 30/60/90d sweep (Jul–Aug 2026).

Ground truth used in research: forward 24h |return| >= 2% on 15m bars.

Profiles (pick one):
  best    — |ROC96| >= 3.5%           BTC ~65% precision, F1 ~48 (30d)
  stable  — |ROC96| >= 3.0%           better 60/90d recall
  pool    — |ROC96| >= 2.0% + EMA stack   multi-coin F1 ~47

ROC96 = close pct change over 96 bars (24h @ 15m).
EMA stack = EMA9 > EMA20 > EMA50 (up) or mirrored (down).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from src.exchange.types import Candle

ROC_24H_BARS = 96
EMA_FAST = 9
EMA_MID = 20
EMA_SLOW = 50

TrendProfile = Literal["best", "stable", "pool"]

_PROFILE_THRESHOLDS: dict[TrendProfile, float] = {
    "best": 3.5,
    "stable": 3.0,
    "pool": 2.0,
}


@dataclass
class TrendSnapshot:
    ready: bool = False
    is_trend: bool = False
    direction: int = 0  # 1 up, -1 down, 0 flat/unknown
    roc96_pct: float = 0.0
    ema_stack: bool = False
    profile: TrendProfile = "best"


def _ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _roc_pct(closes: list[float], lookback: int) -> float | None:
    if len(closes) <= lookback:
        return None
    base = closes[-1 - lookback]
    if base == 0:
        return None
    return (closes[-1] / base - 1.0) * 100.0


def _ema_stack(ema9: float, ema20: float, ema50: float) -> bool:
    up = ema9 > ema20 > ema50
    down = ema9 < ema20 < ema50
    return up or down


def compute_trend_snapshot(
    candles: Sequence[Candle],
    profile: TrendProfile = "best",
) -> TrendSnapshot:
    """Return TREND state from closed 15m candles (last candle = current bar)."""
    snap = TrendSnapshot(profile=profile)
    min_bars = ROC_24H_BARS + EMA_SLOW + 2
    if len(candles) < min_bars:
        return snap

    closes = [float(c.close) for c in candles]
    roc = _roc_pct(closes, ROC_24H_BARS)
    if roc is None:
        return snap

    e9 = _ema(closes, EMA_FAST)[-1]
    e20 = _ema(closes, EMA_MID)[-1]
    e50 = _ema(closes, EMA_SLOW)[-1]
    stack = _ema_stack(e9, e20, e50)

    snap.ready = True
    snap.roc96_pct = roc
    snap.ema_stack = stack
    snap.direction = 1 if roc > 0 else (-1 if roc < 0 else 0)

    th = _PROFILE_THRESHOLDS[profile]
    momentum = abs(roc) >= th
    if profile == "pool":
        snap.is_trend = momentum and stack
    else:
        snap.is_trend = momentum
    return snap


def is_trend_15m(candles: Sequence[Candle], profile: TrendProfile = "best") -> bool:
    return compute_trend_snapshot(candles, profile=profile).is_trend
