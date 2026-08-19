from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from src.notify import notify_error
from src.rsi_rev import store
from src.rsi_rev.config import BE_AFTER_HOURS, MAX_AGE_DAYS, WATCHER_INTERVAL_SEC
from src.rsi_rev.signals import exit_reason_for_mark, lot_age_hours
from src.rsi_rev.trading import close_lot

_stop = threading.Event()
_thread: threading.Thread | None = None


def _parse_opened_epoch(opened_at: str) -> float:
    raw = (opened_at or "").strip()
    if not raw:
        return 0.0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _mark_for(symbol: str) -> float:
    try:
        from src.exchange.binance_ws import get_mark_from_ws

        mark = get_mark_from_ws(symbol)
        if mark is not None and mark > 0:
            return float(mark)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def check_open_lots(*, now_epoch: float | None = None) -> None:
    now = time.time() if now_epoch is None else now_epoch
    for lot in store.get_open_lots():
        symbol = str(lot["symbol"])
        side = str(lot["side"])
        mark = _mark_for(symbol)
        if mark <= 0:
            continue
        opened = _parse_opened_epoch(str(lot["opened_at"] or ""))
        if opened <= 0:
            continue
        reason = exit_reason_for_mark(
            side=side,
            mark=mark,
            entry=float(lot["entry"]),
            tp=float(lot["tp"]),
            age_hours=lot_age_hours(opened, now),
            be_after_hours=BE_AFTER_HOURS,
            max_age_days=MAX_AGE_DAYS,
        )
        if reason is None:
            continue
        close_lot(lot, reason=reason, close_price=mark)


def on_order_update(detail: dict | None) -> None:
    """Fill confirmation only — watcher owns lot exits. Stacked lots must not flatten a side."""
    return


def on_position_flat(symbol: str, side: str) -> None:
    """No-op: hedge net-flat is not a per-lot close when lots are stacked."""
    return


def reconcile_open_lots() -> None:
    """Log DB vs exchange size mismatch; REST only when not blocked. Do not flatten a side."""
    try:
        from src.config import EXCHANGE
        from src.exchange import binance as binance_mod

        if EXCHANGE == "binance" and (
            binance_mod.is_boot_rest_quiet() or binance_mod.is_optional_rest_blocked()
        ):
            return
    except Exception:  # noqa: BLE001
        return

    from src.exchange import ExchangeClientError, fetch_symbol_positions

    by_symbol: dict[str, dict[str, float]] = {}
    for lot in store.get_open_lots():
        symbol = str(lot["symbol"]).upper()
        side = str(lot["side"]).lower()
        by_symbol.setdefault(symbol, {"long": 0.0, "short": 0.0})
        by_symbol[symbol][side] += float(lot["size"] or 0)

    for symbol, sides in by_symbol.items():
        try:
            positions = fetch_symbol_positions(symbol)
        except ExchangeClientError as exc:
            logging.debug("  [%s] RSI-rev position reconcile skipped: %s", symbol, exc)
            continue
        for side, db_size in sides.items():
            if db_size <= 0:
                continue
            pos = positions.get(side)
            ex_size = float(pos.size) if pos is not None else 0.0
            if abs(ex_size - db_size) > max(1e-8, db_size * 0.02):
                logging.warning(
                    "  [%s] RSI-rev %s size mismatch DB=%.6f exchange=%.6f",
                    symbol,
                    side.upper(),
                    db_size,
                    ex_size,
                )


def _loop() -> None:
    while not _stop.wait(WATCHER_INTERVAL_SEC):
        try:
            check_open_lots()
        except Exception as exc:  # noqa: BLE001
            logging.error("RSI-rev watcher cycle failed: %s", exc, extra={"skip_discord": True})
            notify_error("RSI-rev watcher failed", str(exc))


def start_watcher() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="rsi-rev-watcher", daemon=True)
    _thread.start()
    logging.info("RSI-rev close watcher started (%.1fs)", WATCHER_INTERVAL_SEC)


def stop_watcher() -> None:
    _stop.set()
