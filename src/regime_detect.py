"""15m regime detection — dynamic baseline, coin-agnostic (Jul–Aug 2026 research).

Không hardcode ngưỡng % theo coin. Mỗi coin tự tính baseline 30 ngày:
  base_rng = median(biên độ 24h qua, 30d)
  base_roc = median(|ROC 24h|, 30d)

SIDEWAY = rng96 <= K_SW * base_rng  AND  |roc96| <= K_SW * base_roc
TREND   = rng96 >= K_TR * base_rng  AND  |roc96| >= K_TR * base_roc
MIXED   = còn lại

Pool 20 coin (90d eval, adaptive truth): SW prec ~39% min ~30% mọi coin;
TR prec ~63%. Không cần biết symbol.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Literal, Sequence

from src.exchange.types import Candle

RNG_BARS = 96  # 24h @ 15m
BASELINE_BARS = 2880  # 30d @ 15m
MIN_BASELINE_BARS = 672  # 7d minimum warmup
K_SW = 0.92  # compressed vs own 30d median
K_TR = 1.05  # expanded vs own 30d median

Regime = Literal["SIDEWAY", "TREND", "MIXED", "UNKNOWN"]


@dataclass
class RegimeSnapshot:
    ready: bool = False
    regime: Regime = "UNKNOWN"
    rng96_pct: float = 0.0
    roc96_pct: float = 0.0
    base_rng_pct: float = 0.0
    base_roc_pct: float = 0.0
    rng_ratio: float = 0.0  # rng96 / base_rng
    roc_ratio: float = 0.0
    direction: int = 0  # 1 up, -1 down, 0 flat


def _bar_range_pct(candles: Sequence[Candle], end: int, window: int = RNG_BARS) -> float | None:
    start = end - window + 1
    if start < 0:
        return None
    seg = candles[start : end + 1]
    hi = max(float(c.high) for c in seg)
    lo = min(float(c.low) for c in seg)
    close = float(seg[-1].close)
    if close <= 0:
        return None
    return (hi - lo) / close * 100.0


def _roc_pct(candles: Sequence[Candle], end: int, lookback: int = RNG_BARS) -> float | None:
    if end - lookback < 0:
        return None
    base = float(candles[end - lookback].close)
    if base == 0:
        return None
    return (float(candles[end].close) / base - 1.0) * 100.0


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def compute_regime_snapshot(candles: Sequence[Candle]) -> RegimeSnapshot:
    """Classify SIDEWAY / TREND / MIXED from closed 15m candles."""
    snap = RegimeSnapshot()
    n = len(candles)
    min_bars = MIN_BASELINE_BARS + RNG_BARS
    if n < min_bars:
        return snap

    end = n - 1
    rng_now = _bar_range_pct(candles, end)
    roc_now = _roc_pct(candles, end)
    if rng_now is None or roc_now is None:
        return snap

    baseline_start = max(RNG_BARS - 1, end - BASELINE_BARS)
    rng_hist: list[float] = []
    roc_hist: list[float] = []
    for i in range(baseline_start, end):
        r = _bar_range_pct(candles, i)
        c = _roc_pct(candles, i)
        if r is not None:
            rng_hist.append(r)
        if c is not None:
            roc_hist.append(abs(c))

    base_rng = _median(rng_hist)
    base_roc = _median(roc_hist)
    if base_rng is None or base_rng <= 0:
        return snap
    if base_roc is None:
        base_roc = 0.01
    else:
        base_roc = max(base_roc, 0.01)

    snap.ready = True
    snap.rng96_pct = rng_now
    snap.roc96_pct = roc_now
    snap.base_rng_pct = base_rng
    snap.base_roc_pct = base_roc
    snap.rng_ratio = rng_now / base_rng
    snap.roc_ratio = abs(roc_now) / base_roc
    snap.direction = 1 if roc_now > 0 else (-1 if roc_now < 0 else 0)

    abs_roc = abs(roc_now)
    if rng_now <= K_SW * base_rng and abs_roc <= K_SW * base_roc:
        snap.regime = "SIDEWAY"
    elif rng_now >= K_TR * base_rng and abs_roc >= K_TR * base_roc:
        snap.regime = "TREND"
    else:
        snap.regime = "MIXED"
    return snap


def regime_15m(candles: Sequence[Candle]) -> Regime:
    return compute_regime_snapshot(candles).regime


def is_sideway_15m(candles: Sequence[Candle]) -> bool:
    return compute_regime_snapshot(candles).regime == "SIDEWAY"


def is_trend_15m(candles: Sequence[Candle]) -> bool:
    return compute_regime_snapshot(candles).regime == "TREND"
