"""Shut the old fade/Donchian books, one symbol per call, no burst REST."""

from __future__ import annotations

import logging

from src.exchange import ExchangeClientError, close_position_side, fetch_contract_spec
from src.exchange.sizing import format_size
from src.trend4h.config import SYMBOLS

_queue: list[str] | None = None
_done = False


def finished() -> bool:
    global _done
    if _done:
        return True
    try:
        from src import database as db

        if db.get_setting_bool("trend4h_retired", False):
            if not _done:
                logging.info("trend4h retire skipped — already done")
            _done = True
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _mark_done() -> None:
    global _done
    _done = True
    try:
        from src import database as db

        db.set_setting_bool("trend4h_retired", True)
    except Exception:  # noqa: BLE001
        pass
    logging.info("trend4h retire done — old fade/Donchian books stopped")


def _trend4h_qty(symbol: str, side: str) -> float:
    """Do not flatten the live 4h book if retire runs again after a restart."""
    try:
        from src.trend4h import store as tstore

        row = tstore.open_row(symbol)
    except Exception:  # noqa: BLE001
        return 0.0
    if row is None or str(row["side"]) != side:
        return 0.0
    return abs(float(row["qty"] or 0))


def _held(symbol: str, side: str) -> float:
    try:
        from src.exchange.binance_ws.cache import CACHE

        with CACHE.lock:
            pack = CACHE.positions_by_symbol.get(symbol.upper()) or {}
            pos = pack.get(side)
        if pos is None:
            return 0.0
        return abs(float(getattr(pos, "size", 0) or 0))
    except Exception:  # noqa: BLE001
        return 0.0


def _cancel_resting(symbol: str) -> None:
    from src.exchange.binance import cancel_all_open_orders, is_rate_limited, optional_rest_blocked_sec

    if is_rate_limited() or optional_rest_blocked_sec() > 0:
        raise ExchangeClientError("rate limited — postpone retire")
    cancel_all_open_orders(symbol)


def _close_held(symbol: str) -> bool:
    """Market-close leftover hedge-mode size. True if an order was sent."""
    sent = False
    spec = fetch_contract_spec(symbol)
    for side in ("long", "short"):
        qty = _held(symbol, side) - _trend4h_qty(symbol, side)
        if qty <= 1e-12:
            continue
        size = format_size(qty, spec)
        close_position_side(symbol, side, size)
        logging.info("trend4h retire %s close leftover %s %s", symbol, side, size)
        sent = True
        break  # at most one order this call
    return sent


def _mark_old_books(symbol: str) -> None:
    try:
        from src.smc import revert_store as revert

        for order in revert.working_orders(symbol):
            oid = int(order["id"])
            status = str(order["status"])
            if status == "pending":
                revert.mark_cancelled(oid, "trend4h_retire")
            elif status == "open":
                revert.mark_closed(oid, close_px=float(order.get("fill_px") or 0), fee_close=0.0, pnl=0.0, reason="trend4h_retire")
    except Exception as exc:  # noqa: BLE001
        logging.debug("trend4h retire revert db %s: %s", symbol, exc)
    try:
        from src.donchian import store as dstore
        from src.donchian.trading import close_lot

        for lot in dstore.get_open_lots(symbol):
            mark = _held(symbol, str(lot["side"]))
            px = float(lot["entry_px"] or 0)
            try:
                from src.exchange.binance_ws import get_mark_from_ws

                m = get_mark_from_ws(symbol)
                if m:
                    px = float(m)
            except Exception:  # noqa: BLE001
                pass
            if mark <= 1e-12:
                close_lot(lot, reason="trend4h_retire", close_price=px)
            else:
                close_lot(lot, reason="trend4h_retire", close_price=px)
    except Exception as exc:  # noqa: BLE001
        logging.debug("trend4h retire donchian %s: %s", symbol, exc)


def step() -> bool:
    """Advance one symbol. Returns True when every coin has been retired."""
    global _queue, _done
    if finished():
        return True
    if _queue is None:
        extra = []
        try:
            from src.donchian import store as dstore

            extra.extend(str(r["symbol"]) for r in dstore.get_open_lots())
        except Exception:  # noqa: BLE001
            pass
        try:
            from src.smc import revert_store as revert

            extra.extend(str(r["symbol"]) for r in revert.working_orders())
        except Exception:  # noqa: BLE001
            pass
        seen = list(dict.fromkeys([*SYMBOLS, *[s.upper() for s in extra]]))
        _queue = seen
        logging.info("trend4h retire %d symbols (cancel limits + flatten old books)", len(_queue))
    if not _queue:
        _mark_done()
        return True
    symbol = _queue[0]
    try:
        _cancel_resting(symbol)
        if _close_held(symbol):
            return False
        _mark_old_books(symbol)
        _queue.pop(0)
    except ExchangeClientError as exc:
        logging.warning("trend4h retire %s wait: %s", symbol, exc)
        return False
    if not _queue:
        _mark_done()
        return True
    return False
