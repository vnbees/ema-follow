"""Realtime take-profit watcher.

Reads mark prices from the Binance WebSocket cache (markPrice@1s) every
``REALTIME_TP_INTERVAL_SEC`` and closes lot legs the moment they touch the TP
threshold, instead of waiting for the 5-minute cycle. The 5m cycle TP scan
stays active as the safety net when the WS feed is stale.

Double-close safety: every close goes through
``rsi_trading._take_profit_lots_side_batched`` under ``TP_CLOSE_LOCK`` — the
same lock the 5m cycle uses — and that path re-reads lot statuses from the DB
before placing any order.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from src import database as db
from src.config import (
    EXCHANGE,
    BINANCE_WS_ENABLED,
    REALTIME_TP_ENABLED,
    REALTIME_TP_INTERVAL_SEC,
)
from src.exchange import ExchangeClientError, has_credentials
from src.rsi import RsiSnapshot
from src.rsi_signals import should_take_profit


_lock = threading.Lock()
_thread: threading.Thread | None = None
_status: dict = {
    "enabled": False,
    "running": False,
    "paused_reason": None,
    "last_check_at": None,
    "last_close_at": None,
    "closes": 0,
    "be_closes": 0,
}


def _set_status(**fields) -> None:
    with _lock:
        _status.update(fields)


def get_realtime_tp_status() -> dict:
    with _lock:
        return dict(_status)


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _open_sides_by_symbol() -> dict[str, set[str]]:
    """Symbols with open lot legs -> sides that have at least one open leg."""
    result: dict[str, set[str]] = {}
    for lot in db.get_all_open_pair_lots():
        symbol = str(lot["symbol"]).upper()
        sides = result.setdefault(symbol, set())
        if lot["long_status"] == "open" and float(lot["long_size"] or 0) > 0:
            sides.add("long")
        if lot["short_status"] == "open" and float(lot["short_size"] or 0) > 0:
            sides.add("short")
    return result


def _side_has_tp_candidate(symbol: str, side: str, mark: float, tp_pct: float) -> bool:
    for lot in db.get_open_pair_lots(symbol):
        status_key = "long_status" if side == "long" else "short_status"
        if lot[status_key] != "open":
            continue
        entry = float(lot["long_entry"] if side == "long" else lot["short_entry"])
        size = float(lot["long_size"] if side == "long" else lot["short_size"])
        if size <= 0 or entry <= 0:
            continue
        if should_take_profit(side, entry, mark, target_pct=tp_pct):
            return True
    return False


def _run_once() -> None:
    from src.exchange import binance as binance_mod
    from src.exchange.binance_ws import get_mark_from_ws
    from src.margin_guard import effective_tp_pct
    from src.rsi_trading import (
        AlreadyFlatError,
        TP_CLOSE_LOCK,
        _is_already_flat_error,
        _scan_breakeven_closes,
        _scan_take_profits_locked,
        _trading_enabled,
        arm_breakeven_lots_for_symbol,
        side_has_breakeven_candidate,
    )

    if not _trading_enabled() or not has_credentials():
        _set_status(paused_reason="trading disabled")
        return
    if binance_mod.is_rate_limited():
        _set_status(paused_reason="rate-limit cooldown")
        return

    tp_pct = effective_tp_pct()
    open_sides = _open_sides_by_symbol()
    if not open_sides:
        _set_status(paused_reason=None, last_check_at=_now_str())
        return

    ws_ok = False
    for symbol, sides in open_sides.items():
        mark = get_mark_from_ws(symbol)
        if mark is None or mark <= 0:
            continue
        ws_ok = True

        # Arm sticky BE for old underwater lots (DB-only, no exchange).
        arm_breakeven_lots_for_symbol(symbol, mark)

        hit_sides = [
            side
            for side in ("long", "short")
            if side in sides and _side_has_tp_candidate(symbol, side, mark, tp_pct)
        ]
        be_sides = [
            side
            for side in ("long", "short")
            if side in sides and side_has_breakeven_candidate(symbol, side, mark)
        ]
        if not hit_sides and not be_sides:
            continue

        snap = RsiSnapshot(ready=False, rsi=0.0, close=mark)
        took = False
        be_took = False
        with TP_CLOSE_LOCK:
            # Locked scan re-reads DB + exchange, so a concurrent 5m cycle
            # close is detected and nothing is closed twice.
            try:
                if hit_sides:
                    took = _scan_take_profits_locked(
                        symbol,
                        mark,
                        snap,
                        trigger="realtime",
                        reopen_pair=False,
                        tp_target_pct=tp_pct,
                    )
                if be_sides or hit_sides:
                    # Re-check BE after TP (lot may still be open / newly at BE).
                    be_took = _scan_breakeven_closes(symbol, mark)
            except ExchangeClientError as exc:
                if isinstance(exc, AlreadyFlatError) or _is_already_flat_error(exc):
                    logging.info("  [%s] Realtime TP/BE already flat: %s", symbol, exc)
                else:
                    logging.warning("  [%s] Realtime TP/BE failed: %s", symbol, exc)
                continue
        if took:
            with _lock:
                _status["closes"] += 1
                _status["last_close_at"] = _now_str()
            logging.info("  [%s] Realtime TP closed side(s) %s", symbol, hit_sides)
        if be_took:
            with _lock:
                _status["be_closes"] += 1
                _status["last_close_at"] = _now_str()
            logging.info("  [%s] Realtime BE closed side(s) %s", symbol, be_sides)

    _set_status(
        paused_reason=None if ws_ok else "ws stale — 5m cycle is the fallback",
        last_check_at=_now_str(),
    )


def _loop() -> None:
    interval = max(0.5, REALTIME_TP_INTERVAL_SEC)
    _set_status(running=True)
    while True:
        time.sleep(interval)
        try:
            _run_once()
        except Exception as exc:  # noqa: BLE001 — watcher must never die
            logging.warning("Realtime TP watcher error: %s", exc)
            time.sleep(interval)


def start_realtime_tp() -> bool:
    """Start the watcher thread. Returns True when started."""
    global _thread

    enabled = (
        REALTIME_TP_ENABLED
        and EXCHANGE == "binance"
        and BINANCE_WS_ENABLED
        and has_credentials()
    )
    _set_status(enabled=enabled)
    if not enabled:
        logging.info(
            "Realtime TP watcher disabled (REALTIME_TP_ENABLED=%s, exchange=%s, ws=%s)",
            REALTIME_TP_ENABLED,
            EXCHANGE,
            BINANCE_WS_ENABLED,
        )
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
    thread = threading.Thread(target=_loop, name="realtime-tp", daemon=True)
    with _lock:
        _thread = thread
    thread.start()
    logging.info(
        "Realtime TP watcher started (interval=%.1fs, WS markPrice@1s)",
        max(0.5, REALTIME_TP_INTERVAL_SEC),
    )
    return True
