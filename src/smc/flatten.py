"""Cancel Donchian dual-limit orders and close those lots once, before SMC trades."""

from __future__ import annotations

import logging

from src.bot_state import is_trading_enabled
from src.database import get_setting, set_setting
from src.donchian import store as donchian_store
from src.exchange import ExchangeClientError, has_credentials

FLAG = "smc_donchian_flattened"


def _cancel_entry(row) -> bool:
    from src.donchian.dual_limit import on_entry_order_update
    from src.exchange.binance import cancel_limit_order_detail, fetch_order_full

    symbol = str(row["symbol"])
    oid = str(row["order_id"])
    result = None
    try:
        result = cancel_limit_order_detail(symbol, oid)
    except ExchangeClientError as exc:
        logging.warning("SMC flatten cancel entry %s %s failed: %s", symbol, oid, exc)
    if result is None:
        try:
            result = fetch_order_full(symbol, oid)
        except ExchangeClientError as exc:
            logging.warning("SMC flatten order lookup %s %s failed: %s", symbol, oid, exc)
            result = None
    if result is None:
        logging.warning("SMC flatten could not confirm cancel of %s %s", symbol, oid)
        return False
    status = str(result.get("status") or "").lower()
    if status == "filled":
        on_entry_order_update(result)
        return True
    if status not in ("canceled", "cancelled", "expired", "rejected"):
        logging.warning("SMC flatten entry %s %s still %s", symbol, oid, status or "unknown")
        return False
    donchian_store.claim_entry(int(row["id"]), status="cancelled", filled_qty=0.0, avg_px=None)
    return True


def _close_lot(lot) -> bool:
    from src.donchian.trading import _get_mark, close_lot
    from src.exchange.binance import cancel_limit_order_detail

    oid = str(lot["tp_limit_order_id"] or "")
    if oid:
        try:
            cancel_limit_order_detail(str(lot["symbol"]), oid)
        except ExchangeClientError as exc:
            logging.warning("SMC flatten cancel TP %s failed: %s", lot["id"], exc)
    mark = _get_mark(str(lot["symbol"]))
    price = mark if mark > 0 else float(lot["entry_px"] or 0)
    return close_lot(lot, reason="SMC_SWITCH", close_price=price)


def flatten_donchian_once() -> bool:
    """Close the Donchian book. Returns True when nothing open remains."""
    if get_setting(FLAG, "") == "1":
        return True
    if not has_credentials() or not is_trading_enabled():
        logging.warning("SMC flatten waiting — trading disabled or API keys missing")
        return False
    pending = donchian_store.get_pending_entries()
    logging.info("SMC switch: cancelling %d Donchian entry order(s)", len(pending))
    for row in pending:
        if not _cancel_entry(row):
            return False
    lots = donchian_store.get_open_lots()
    logging.info("SMC switch: closing %d Donchian lot(s)", len(lots))
    for lot in lots:
        if not _close_lot(lot):
            logging.warning("SMC switch: lot %s still open — will retry", lot["id"])
            return False
    left_lots = donchian_store.get_open_lots()
    left_orders = donchian_store.get_pending_entries()
    if left_lots or left_orders:
        logging.warning(
            "SMC switch incomplete — open lots=%d pending=%d",
            len(left_lots),
            len(left_orders),
        )
        return False
    set_setting(FLAG, "1")
    logging.info("SMC switch: Donchian book is flat")
    return True
