import logging
from datetime import datetime, timezone

from src import database as db
from src.bitget_client import (
    BitgetClientError,
    cancel_all_pending_limits,
    close_positions,
    configure_symbol_trading,
    fetch_contract_spec,
    fetch_futures_balance,
    fetch_order_detail,
    fetch_pending_orders,
    fetch_position,
    format_price,
    has_credentials,
    notional_to_size,
    place_limit_order,
)
from src.bot_state import update_status
from src.config import LEVERAGE, MARGIN_MODE, ORDER_SIZE_USDT
from src.database import update_entry_by_order_id

_open_cycle_id: int | None = None
_open_cycle_side: str | None = None
_last_position_size: float = 0.0
_last_position_side: str | None = None
_configured_symbol: str | None = None


def ensure_symbol_configured(symbol: str) -> None:
    global _configured_symbol
    if _configured_symbol == symbol:
        return
    configure_symbol_trading(symbol)
    _configured_symbol = symbol
    logging.info("  Trading config: symbol=%s margin=%s leverage=%dx", symbol, MARGIN_MODE, LEVERAGE)


def _avg_for_side(symbol: str, side: str, position_avg: float) -> float | None:
    avg = db.compute_avg_entry_price(symbol, side)
    if avg is not None:
        return avg
    if position_avg > 0:
        return position_avg
    return None


def _resolve_pending_entry(symbol: str, order_id: str, pending_ids: set[str]) -> None:
    if order_id in pending_ids:
        return
    try:
        detail = fetch_order_detail(symbol, order_id)
        state = (detail.get("state") or detail.get("status") or "").lower()
        if state in {"filled", "partially_filled", "partial-fill"}:
            update_entry_by_order_id(order_id, "filled", filled=True)
        elif state in {"cancelled", "canceled", "rejected"}:
            update_entry_by_order_id(order_id, "cancelled")
        else:
            logging.warning("  Order %s unknown state '%s', marking cancelled", order_id, state)
            update_entry_by_order_id(order_id, "cancelled")
    except BitgetClientError as exc:
        logging.warning("  Could not resolve order %s status: %s", order_id, exc)
        update_entry_by_order_id(order_id, "cancelled")


def sync_state(symbol: str) -> tuple:
    global _open_cycle_id, _open_cycle_side, _last_position_size, _last_position_side

    position = fetch_position(symbol)
    pending = fetch_pending_orders(symbol)
    pending_ids = {order.order_id for order in pending}

    for row in db.get_pending_entries(symbol):
        order_id = row["order_id"]
        if order_id:
            _resolve_pending_entry(symbol, order_id, pending_ids)

    balance = fetch_futures_balance(symbol)
    prev_size = _last_position_size
    curr_size = position.size
    curr_side = position.side

    if prev_size <= 0 and curr_size > 0 and curr_side:
        cycle_id = db.create_trade_cycle(symbol, curr_side, balance.account_equity)
        _open_cycle_id = cycle_id
        _open_cycle_side = curr_side
        logging.info(
            "  Cycle opened: id=%s side=%s balance_at_open=%.2f USDT",
            cycle_id,
            curr_side,
            balance.account_equity,
        )

    if prev_size > 0 and curr_size <= 0 and _open_cycle_id is not None:
        db.close_trade_cycle(_open_cycle_id, balance.account_equity)
        cycle = db.get_all_trade_cycles(limit=1)[0]
        logging.info(
            "  Cycle closed: id=%s pnl=%.2f USDT (%.2f%%)",
            cycle["id"],
            float(cycle["pnl_usdt"] or 0),
            float(cycle["pnl_pct"] or 0),
        )
        _open_cycle_id = None
        _open_cycle_side = None

    _last_position_size = curr_size
    _last_position_side = curr_side

    avg_entry = None
    if position.side:
        avg_entry = _avg_for_side(symbol, position.side, position.avg_price)

    return position, pending, balance, avg_entry


def _finalize_open_cycle(symbol: str, side: str | None = None) -> None:
    global _open_cycle_id, _open_cycle_side
    if _open_cycle_id is None:
        open_cycle = db.get_open_trade_cycle_for_symbol(symbol)
        if open_cycle:
            _open_cycle_id = int(open_cycle["id"])
        else:
            return
    balance = fetch_futures_balance(symbol)
    db.close_trade_cycle(_open_cycle_id, balance.account_equity)
    cycle = db.get_all_trade_cycles(limit=1)[0]
    logging.info(
        "  Cycle closed (flip): id=%s pnl=%.2f USDT (%.2f%%)",
        cycle["id"],
        float(cycle["pnl_usdt"] or 0),
        float(cycle["pnl_pct"] or 0),
    )
    _open_cycle_id = None
    _open_cycle_side = None


def evaluate_and_trade(
    symbol: str,
    trend: str,
    candle: str,
    last_close: float,
) -> None:
    global _last_position_size, _last_position_side

    if not has_credentials():
        logging.warning("  Trading skipped: missing API credentials")
        return

    ensure_symbol_configured(symbol)
    position, pending, balance, avg_entry = sync_state(symbol)

    update_status(
        symbol=symbol,
        position_side=position.side,
        position_size=position.size,
        avg_entry=avg_entry,
        pending_orders=[
            {"order_id": o.order_id, "side": o.side, "price": o.price, "size": o.size}
            for o in pending
        ],
        margin_mode=MARGIN_MODE,
        leverage=LEVERAGE,
        balance_available=balance.available,
        balance_equity=balance.account_equity,
        last_updated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )

    if trend == "sideway":
        logging.info("  Trading: sideway — no action")
        return

    spec = fetch_contract_spec(symbol)
    price_str = format_price(last_close, spec)
    size_str = notional_to_size(ORDER_SIZE_USDT, last_close, spec)

    if trend == "uptrend" and candle == "red":
        if position.side == "short" and position.size > 0:
            logging.info("  Closing short position before long entry")
            close_positions(symbol, hold_side="short")
            _finalize_open_cycle(symbol)
            position = fetch_position(symbol)
            _last_position_size = position.size
            _last_position_side = position.side

        avg_long = _avg_for_side(symbol, "long", position.avg_price if position.side == "long" else 0)
        if position.side == "long" and avg_long is not None and last_close > avg_long:
            logging.info(
                "  Long entry skipped: close %.4f > avg %.4f (already profitable)",
                last_close,
                avg_long,
            )
            return

        logging.info("  Cancelling pending limits and placing long limit @ %s", price_str)
        cancel_all_pending_limits(symbol)
        for row in db.get_pending_entries(symbol):
            db.update_entry_status(int(row["id"]), "cancelled")

        result = place_limit_order(symbol, "buy", price_str, size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        db.insert_entry(symbol, "long", order_id, client_oid, last_close, float(size_str))
        logging.info("  Placed limit buy: order_id=%s size=%s price=%s", order_id, size_str, price_str)
        return

    if trend == "downtrend" and candle == "green":
        if position.side == "long" and position.size > 0:
            logging.info("  Closing long position before short entry")
            close_positions(symbol, hold_side="long")
            _finalize_open_cycle(symbol)
            position = fetch_position(symbol)
            _last_position_size = position.size
            _last_position_side = position.side

        avg_short = _avg_for_side(symbol, "short", position.avg_price if position.side == "short" else 0)
        if position.side == "short" and avg_short is not None and last_close < avg_short:
            logging.info(
                "  Short entry skipped: close %.4f < avg %.4f (already profitable)",
                last_close,
                avg_short,
            )
            return

        logging.info("  Cancelling pending limits and placing short limit @ %s", price_str)
        cancel_all_pending_limits(symbol)
        for row in db.get_pending_entries(symbol):
            db.update_entry_status(int(row["id"]), "cancelled")

        result = place_limit_order(symbol, "sell", price_str, size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        db.insert_entry(symbol, "short", order_id, client_oid, last_close, float(size_str))
        logging.info("  Placed limit sell: order_id=%s size=%s price=%s", order_id, size_str, price_str)
        return

    logging.info("  Trading: no entry signal (trend=%s candle=%s)", trend, candle)


def on_symbol_changed(symbol: str) -> None:
    global _configured_symbol, _open_cycle_id, _open_cycle_side, _last_position_size, _last_position_side
    _configured_symbol = None
    _open_cycle_id = None
    _open_cycle_side = None
    _last_position_size = 0.0
    _last_position_side = None
    ensure_symbol_configured(symbol)
