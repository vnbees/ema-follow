"""Entry: a closed bar on 5m/15m/1h, then bet at the next open of that timeframe.

The forming bar is never evaluated (closeTime must be < now). Latch + SQZ +
body are read on that closed bar only. Any TV label that is not blue or red
(Doji D, spinning STW/STB, or both colors on one bar) clears the latch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from src.predict5m.candles import latest_labels
from src.predict5m.config import (
    FEE_WIN_FRAC,
    PLACE_GRACE_MS,
    STAKE_USDT,
    TARGET_USDT,
    WIN_PROFIT_MULT,
    interval_ms,
)
from src.predict5m.sqz import hist_matches_label, squeeze


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: int  # +1 UP, -1 DOWN
    bar_ts: int
    close_ts: int
    entry_ts: int
    label: int
    hist: str
    dot: str
    open_px: float
    close_px: float
    interval: str = "5m"


def take_closed(
    ts: np.ndarray,
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    close_ts: np.ndarray,
    now_ms: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Drop the forming bar. Evaluation is allowed only when closeTime < now."""
    mask = np.asarray(close_ts, dtype=np.int64) < int(now_ms)
    return (
        np.asarray(ts)[mask],
        np.asarray(o)[mask],
        np.asarray(h)[mask],
        np.asarray(l)[mask],
        np.asarray(c)[mask],
        np.asarray(close_ts, dtype=np.int64)[mask],
    )


def opposite_body(label: int, open_px: float, close_px: float) -> bool:
    if label == 1:
        return close_px < open_px
    if label == -1:
        return close_px > open_px
    return False


def qualifies(
    *,
    label: int,
    hist: str | None,
    dot: str | None,
    open_px: float,
    close_px: float,
) -> bool:
    if label not in (1, -1):
        return False
    if dot != "gray":
        return False
    if not hist_matches_label(label, hist):
        return False
    if not opposite_body(label, open_px, close_px):
        return False
    return True


def _signal_at(
    symbol: str,
    i: int,
    ts: np.ndarray,
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    close_ts: np.ndarray,
    labels: np.ndarray,
    hist: list,
    dot: list,
    interval: str = "5m",
) -> Signal | None:
    lab = int(labels[i])
    if not qualifies(
        label=lab,
        hist=hist[i],
        dot=dot[i],
        open_px=float(o[i]),
        close_px=float(c[i]),
    ):
        return None
    bar_ms = interval_ms(interval)
    return Signal(
        symbol=symbol.upper(),
        side=lab,
        bar_ts=int(ts[i]),
        close_ts=int(close_ts[i]),
        entry_ts=int(ts[i]) + bar_ms,
        label=lab,
        hist=str(hist[i]),
        dot=str(dot[i]),
        open_px=float(o[i]),
        close_px=float(c[i]),
        interval=interval,
    )


def signal_on_close(
    symbol: str,
    ts: np.ndarray,
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    close_ts: np.ndarray,
    now_ms: int,
    interval: str = "5m",
) -> Signal | None:
    """Last *closed* bar of `interval` only. The candle still running is ignored."""
    ts, o, h, l, c, close_ts = take_closed(ts, o, h, l, c, close_ts, now_ms)
    n = len(c)
    if n < 80:
        return None
    labels = latest_labels(o, h, l, c)
    hist, dot, _ = squeeze(h.tolist(), l.tolist(), c.tolist())
    return _signal_at(
        symbol, n - 1, ts, o, h, l, c, close_ts, labels, hist, dot, interval=interval
    )


def last_valid_closed(
    symbol: str,
    ts: np.ndarray,
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    close_ts: np.ndarray,
    now_ms: int,
    interval: str = "5m",
) -> Signal | None:
    """Newest closed bar that qualifies. Forming bar is never considered."""
    ts, o, h, l, c, close_ts = take_closed(ts, o, h, l, c, close_ts, now_ms)
    n = len(c)
    if n < 80:
        return None
    labels = latest_labels(o, h, l, c)
    hist, dot, _ = squeeze(h.tolist(), l.tolist(), c.tolist())
    for i in range(n - 1, 79, -1):
        sig = _signal_at(
            symbol, i, ts, o, h, l, c, close_ts, labels, hist, dot, interval=interval
        )
        if sig is not None:
            return sig
    return None


def window_fresh(entry_ts: int, now_ms: int, grace_ms: int = PLACE_GRACE_MS) -> bool:
    """True only at the start of the Prediction window."""
    lag = int(now_ms) - int(entry_ts)
    return 0 <= lag <= int(grace_ms)


def resolve_window(side: str, open_px: float, close_px: float) -> tuple[bool, str]:
    """Up wins iff close > open; Down wins iff close < open. Tie is a loss."""
    bet = str(side).upper()
    if close_px == open_px:
        return False, "tie"
    if bet == "UP":
        won = close_px > open_px
        return won, "up_win" if won else "up_lose"
    won = close_px < open_px
    return won, "down_win" if won else "down_lose"


def settle_pnl(
    won: bool,
    stake: float,
    *,
    filled: bool = True,
    limit_px: float | None = None,
    fee_bps: float | None = None,
) -> float:
    if not filled:
        return 0.0
    s = float(stake)
    if not won:
        return -s
    if limit_px and float(limit_px) > 0:
        fee = float(fee_bps or 0) / 10_000.0
        return s * ((1.0 - fee) / float(limit_px) - 1.0)
    return s * FEE_WIN_FRAC


def target_limit_price(
    fee_bps: float,
    *,
    profit_mult: float = WIN_PROFIT_MULT,
    decimals: int = 2,
) -> float:
    """Highest BUY price such that a win pays at least `profit_mult` × stake.

    shares = (1 − fee) / price, profit = shares − 1. Floor to tick so profit
    is not below the target.
    """
    fee = max(0.0, float(fee_bps) / 10_000.0)
    raw = (1.0 - fee) / (1.0 + float(profit_mult))
    places = max(0, int(decimals))
    step = 10.0 ** (-places)
    floored = math.floor(raw / step + 1e-12) * step
    return round(max(step, floored), places)


def order_is_filled(status: str | None) -> bool:
    return str(status or "").upper() in ("FILLED", "PARTIALLY_FILLED")


def order_is_open(status: str | None) -> bool:
    return str(status or "").upper() in ("PENDING", "SUBMITTED")


def pick_first(signals: list[Signal | None]) -> Signal | None:
    for sig in signals:
        if sig is not None:
            return sig
    return None


@dataclass(frozen=True)
class Bankroll:
    """Current recovery target and cash already lost in the streak."""

    target: float
    streak_loss: float = 0.0


def fresh_bankroll(base: float = TARGET_USDT) -> Bankroll:
    return Bankroll(target=float(base), streak_loss=0.0)


def bankroll_from_loss(lost: float, base: float = TARGET_USDT) -> Bankroll:
    """Target a win that fills the hole then leaves `base` (default 2 USDT)."""
    hole = max(0.0, float(lost))
    return Bankroll(target=hole + float(base), streak_loss=hole)


def stake_from_target(
    target: float, *, profit_mult: float = WIN_PROFIT_MULT
) -> float:
    """Stake so a win at LIMIT (≥ profit_mult × stake) covers `target`."""
    mult = max(1e-9, float(profit_mult))
    return round(max(0.01, float(target) / mult), 4)


def after_recovery(
    won: bool,
    bank: Bankroll,
    *,
    filled: bool = True,
    stake: float,
    base: float = TARGET_USDT,
) -> Bankroll:
    """Unfilled keeps the bank. Win resets. Loss: next target = hole + base."""
    if not filled:
        return bank
    if won:
        return fresh_bankroll(base)
    lost = float(bank.streak_loss) + float(stake)
    return bankroll_from_loss(lost, base)


def stake_usdt(double_step: int, base: float = STAKE_USDT) -> float:
    """Legacy 1, 2, 4, 8, … used by the streak backtest."""
    n = max(0, int(double_step))
    return float(base) * (2 ** n)


def after_result(won: bool, double_step: int, *, filled: bool = True) -> int:
    """Legacy doubling. Unfilled keeps the same stake."""
    if not filled:
        return int(double_step)
    if won:
        return 0
    return int(double_step) + 1


def cannot_afford(stake: float, available: float) -> bool:
    return float(stake) > float(available) + 1e-9
