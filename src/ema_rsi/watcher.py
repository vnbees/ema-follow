from __future__ import annotations

import logging
import threading
import time

from src.ema_rsi import store
from src.ema_rsi.config import WATCHER_INTERVAL_SEC
from src.ema_rsi.trading import finalize_close, position_confirmed_flat
from src.exchange import ExchangeClientError, fetch_order_detail
from src.notify import notify_error

_stop = threading.Event()
_thread: threading.Thread | None = None


def _parse_fill_price(detail: dict | None) -> float:
    if not detail:
        return 0.0
    for key in ("avgPrice", "priceAvg", "ap", "sp", "stopPrice", "actualPrice", "triggerPrice"):
        raw = detail.get(key)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def _order_filled(detail: dict | None) -> bool:
    if not detail:
        return False
    status = str(detail.get("status") or detail.get("state") or "").lower()
    # Algo STOP/TP can sit in "triggered" while the position is still open.
    if status in {"triggered", "new", "working", "pending"}:
        return False
    if status in {"filled", "partially_filled", "partial-fill"}:
        return True
    if status in {"finished", "executed"}:
        try:
            px = float(detail.get("avgPrice") or detail.get("actualPrice") or 0)
        except (TypeError, ValueError):
            px = 0.0
        return px > 0 or bool(detail.get("actualOrderId"))
    return False


def infer_close_reason(trade, close_price: float) -> str:
    sl = float(trade["sl"] or 0)
    tp = float(trade["tp"] or 0)
    if close_price <= 0:
        return store.REASON_HIT_SL
    if abs(close_price - tp) <= abs(close_price - sl):
        return store.REASON_HIT_TP
    return store.REASON_HIT_SL


def _reason_from_orders(trade) -> tuple[str, float] | None:
    symbol = str(trade["symbol"])
    sl_id = str(trade["sl_order_id"] or "")
    tp_id = str(trade["tp_order_id"] or "")
    for reason, order_id in (
        (store.REASON_HIT_SL, sl_id),
        (store.REASON_HIT_TP, tp_id),
    ):
        if not order_id:
            continue
        try:
            detail = fetch_order_detail(symbol, order_id)
        except ExchangeClientError as exc:
            logging.debug("  [%s] Order %s lookup skipped: %s", symbol, order_id, exc)
            continue
        if _order_filled(detail):
            px = _parse_fill_price(detail)
            return reason, px
    return None


def on_order_update(detail: dict | None) -> None:
    if not detail or not _order_filled(detail):
        return
    order_id = str(detail.get("orderId") or "")
    client_oid = str(detail.get("clientOid") or "")
    trade = store.find_open_by_order_id(order_id) if order_id else None
    if trade is None and client_oid:
        trade = store.find_open_by_client_oid(client_oid)
    if trade is None:
        return
    symbol = str(trade["symbol"])
    side = str(trade["side"])
    if not position_confirmed_flat(symbol, side):
        logging.debug(
            "  [%s] Skip order fill close — %s position still open on exchange",
            symbol,
            side,
        )
        return
    close_price = _parse_fill_price(detail)
    sl_id = str(trade["sl_order_id"] or "")
    tp_id = str(trade["tp_order_id"] or "")
    if order_id == sl_id or client_oid.lower().startswith("ersl"):
        reason = store.REASON_HIT_SL
        leftover = tp_id
    elif order_id == tp_id or client_oid.lower().startswith("ertp"):
        reason = store.REASON_HIT_TP
        leftover = sl_id
    else:
        return
    if close_price <= 0:
        close_price = float(trade["sl"] if reason == store.REASON_HIT_SL else trade["tp"])
    finalize_close(trade, reason=reason, close_price=close_price, leftover_order_id=leftover)


def on_position_flat(symbol: str, side: str) -> None:
    trade = store.get_open_trade_for_symbol(symbol)
    if trade is None or str(trade["side"]).lower() != side.lower():
        return
    if not position_confirmed_flat(symbol, side):
        return
    looked = _reason_from_orders(trade)
    if looked is not None:
        reason, px = looked
        if px <= 0:
            px = float(trade["sl"] if reason == store.REASON_HIT_SL else trade["tp"])
        leftover = (
            str(trade["tp_order_id"] or "")
            if reason == store.REASON_HIT_SL
            else str(trade["sl_order_id"] or "")
        )
        finalize_close(trade, reason=reason, close_price=px, leftover_order_id=leftover)
        return
    logging.debug(
        "  [%s] Position flat but no filled SL/TP order — skip infer close",
        symbol,
    )


def reconcile_open_trades() -> None:
    for trade in store.get_open_trades():
        symbol = str(trade["symbol"])
        side = str(trade["side"])
        if not position_confirmed_flat(symbol, side):
            continue
        on_position_flat(symbol, side)


def _loop() -> None:
    while not _stop.wait(WATCHER_INTERVAL_SEC):
        try:
            reconcile_open_trades()
        except Exception as exc:  # noqa: BLE001
            logging.error("EMA-RSI watcher cycle failed: %s", exc)
            notify_error("EMA-RSI watcher failed", str(exc))


def start_watcher() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="ema-rsi-watcher", daemon=True)
    _thread.start()
    logging.info("EMA-RSI close watcher started (%.1fs)", WATCHER_INTERVAL_SEC)


def stop_watcher() -> None:
    _stop.set()
