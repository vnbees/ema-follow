"""Donchian close watcher — runs every WATCHER_INTERVAL_SEC.

TP matches backtest: long when price touches the *current* Donchian upper,
short when price touches the *current* lower. Live difference: check every
WATCHER_INTERVAL_SEC on mark + forming-candle high/low — do not wait for 15m close.
"""

from __future__ import annotations

import logging
import threading

from src.notify import notify_error
from src.donchian import store
from src.donchian.config import CANDLE_LIMIT, DONCHIAN_PERIOD, INTERVAL, WATCHER_INTERVAL_SEC
from src.donchian.signals import rolling_channel
from src.donchian.trading import close_lot, reconcile_flat_lots

_stop = threading.Event()
_thread: threading.Thread | None = None


def _mark_for(symbol: str) -> float:
    try:
        from src.exchange.binance_ws import get_mark_from_ws

        mark = get_mark_from_ws(symbol)
        if mark is not None and mark > 0:
            return float(mark)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _last_candle_ts(symbol: str) -> int | None:
    try:
        from src.exchange.binance_ws.cache import CACHE

        raw = CACHE.get_candles(symbol.upper(), INTERVAL, CANDLE_LIMIT)
        if not raw:
            return None
        return int(raw[-1].timestamp)
    except Exception:  # noqa: BLE001
        return None


def _live_channel(symbol: str) -> tuple[float, float, float, float] | None:
    """Return (upper, lower, last_high, last_low) including the forming 15m bar."""
    try:
        from src.exchange.binance_ws.cache import CACHE

        raw = CACHE.get_candles(symbol.upper(), INTERVAL, CANDLE_LIMIT)
        if not raw or len(raw) < DONCHIAN_PERIOD:
            return None
        highs = [float(c.high) for c in raw]
        lows = [float(c.low) for c in raw]
        ch = rolling_channel(highs, lows, DONCHIAN_PERIOD)
        if ch is None:
            return None
        upper, lower = ch
        return upper, lower, highs[-1], lows[-1]
    except Exception:  # noqa: BLE001
        return None


def _tp_hit(side: str, *, upper: float, lower: float, high: float, low: float, mark: float) -> bool:
    """Same as backtest hi/lo vs current band; mark fills in if kline extreme lags."""
    bar_high = max(high, mark) if mark > 0 else high
    bar_low = min(low, mark) if mark > 0 else low
    if side == "long":
        return bar_high >= upper
    return bar_low <= lower


def check_open_lots() -> None:
    reconcile_flat_lots()
    lots = store.get_open_lots()
    if lots:
        try:
            from src.exchange.binance_ws import watch_symbols

            watch_symbols([str(lot["symbol"]) for lot in lots])
        except Exception:  # noqa: BLE001
            pass
    for lot in lots:
        symbol = str(lot["symbol"])
        side = str(lot["side"])
        mark = _mark_for(symbol)
        live = _live_channel(symbol)
        if live is None:
            tp_band = float(lot["tp_band"])
            if mark <= 0:
                continue
            hit = (side == "long" and mark >= tp_band) or (side == "short" and mark <= tp_band)
            close_px = mark
        else:
            upper, lower, high, low = live
            last_bar_ts = _last_candle_ts(symbol)
            entry_ts = lot["entry_ts"]
            # Backtest không TP nến vào lệnh — chỉ nến sau.
            if entry_ts is not None and last_bar_ts is not None and int(last_bar_ts) <= int(entry_ts):
                continue
            hit = _tp_hit(side, upper=upper, lower=lower, high=high, low=low, mark=mark)
            close_px = mark if mark > 0 else (upper if side == "long" else lower)
        if not hit:
            continue
        close_lot(lot, reason=store.REASON_TP, close_price=close_px)


def _loop() -> None:
    while not _stop.wait(WATCHER_INTERVAL_SEC):
        try:
            check_open_lots()
        except Exception as exc:  # noqa: BLE001
            logging.error("Donchian watcher cycle failed: %s", exc, extra={"skip_discord": True})
            notify_error("Donchian watcher failed", str(exc))


def start_watcher() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="donchian-watcher", daemon=True)
    _thread.start()
    logging.info("Donchian close watcher started (%.1fs) — live Donchian band", WATCHER_INTERVAL_SEC)


def stop_watcher() -> None:
    _stop.set()
