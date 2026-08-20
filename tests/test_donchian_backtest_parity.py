"""Live signal + TP must match backtest bar-by-bar (TP may fire intra-bar)."""

from __future__ import annotations

import unittest

from src.donchian.signals import DonchianBar, SignalState, check_signal, compute_bands, rolling_channel
from src.donchian.watcher import _tp_hit


def _bar(i: int, o: float, h: float, l: float, c: float) -> DonchianBar:
    return DonchianBar(ts=i, open=o, high=h, low=l, close=c)


def _walk_live(bars: list[DonchianBar], period: int, lookback: int, tol: float):
    """Replay like the bot: one closed bar at a time, TP on later bars via live channel."""
    state = SignalState(prev_parallel=False)
    pos: dict | None = None
    trades: list[tuple[str, int]] = []  # (event, ts)
    hist: list[DonchianBar] = []
    for bar in bars:
        hist.append(bar)
        if pos is not None and bar.ts > pos["entry_ts"]:
            ch = rolling_channel([b.high for b in hist], [b.low for b in hist], period)
            if ch is not None:
                upper, lower = ch
                if _tp_hit(pos["side"], upper=upper, lower=lower, high=bar.high, low=bar.low, mark=0.0):
                    trades.append((f"close_{pos['side']}", bar.ts))
                    pos = None
        if pos is not None:
            check_signal(hist, state, period=period, slope_lookback=lookback, tol=tol, allow_entry=False)
            continue
        signal, tp, _ = check_signal(hist, state, period=period, slope_lookback=lookback, tol=tol)
        if signal:
            trades.append((f"open_{signal}", bar.ts))
            pos = {"side": signal, "entry_ts": bar.ts, "tp": tp}
    return trades


def _walk_backtest(bars: list[DonchianBar], period: int, lookback: int, tol: float):
    bands = compute_bands(bars, period, lookback, tol)
    trend = None
    waiting = False
    prev_par = False
    pos = None
    trades: list[tuple[str, int]] = []
    for i, bar in enumerate(bars):
        b = bands[i]
        if b is None:
            continue
        if pos is not None:
            hit = (pos == "long" and bar.high >= b.upper) or (pos == "short" and bar.low <= b.lower)
            if hit:
                trades.append((f"close_{pos}", bar.ts))
                pos = None
        par_exit = prev_par and not b.parallel
        if par_exit:
            trend = "up" if bar.close > b.middle else "down"
            waiting = True
        if waiting and trend is not None and pos is None:
            counter = (trend == "up" and bar.close < bar.open) or (trend == "down" and bar.close > bar.open)
            if counter and not b.parallel:
                side = "long" if trend == "up" else "short"
                trades.append((f"open_{side}", bar.ts))
                pos = side
                waiting = False
        prev_par = b.parallel
    return trades


class TestBacktestParity(unittest.TestCase):
    def test_synthetic_path_matches(self) -> None:
        bars: list[DonchianBar] = []
        # Flat parallel warmup
        for i in range(30):
            bars.append(_bar(i, 10.0, 10.2, 9.8, 10.0))
        # Expand upper faster → non-parallel, close above middle → up trend
        for i in range(30, 36):
            bars.append(_bar(i, 10.0, 10.2 + (i - 29) * 0.5, 9.8, 10.1))
        # Red pullback, still expanding → long
        bars.append(_bar(36, 10.3, 10.4, 9.7, 9.9))
        # Next bars: push into upper band
        for i in range(37, 45):
            hi = 10.2 + (i - 29) * 0.5
            bars.append(_bar(i, 10.0, hi, 9.7, 10.0))

        live = _walk_live(bars, period=20, lookback=5, tol=0.015)
        bt = _walk_backtest(bars, period=20, lookback=5, tol=0.015)
        self.assertEqual(live, bt)
        self.assertTrue(any(e[0].startswith("open_") for e in bt))
