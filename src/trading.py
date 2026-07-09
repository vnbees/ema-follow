import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src import database as db
from src.bitget_client import (
    BitgetClientError,
    Position,
    cancel_all_pending_limits,
    close_positions,
    fetch_contract_spec,
    fetch_futures_balance,
    fetch_order_detail,
    fetch_pending_orders,
    fetch_position,
    has_credentials as bitget_has_credentials,
    notional_to_size,
    place_market_order,
)
from src.exchange import (
    ExchangeClientError,
    configure_symbol_trading,
    fetch_order_detail as exchange_fetch_order_detail,
    fetch_side_mark_price,
)
from src.bot_state import update_symbol_status
from src.config import LEVERAGE, MARGIN_MODE, ORDER_SIZE_USDT, TRADING_ENABLED
from src.database import get_symbols, update_entry_by_order_id


@dataclass
class SymbolTradingState:
    open_cycle_id: int | None = None
    open_cycle_side: str | None = None
    last_position_size: float = 0.0
    last_position_side: str | None = None
    configured: bool = False


_states: dict[str, SymbolTradingState] = {}


def _get_state(symbol: str) -> SymbolTradingState:
    if symbol not in _states:
        _states[symbol] = SymbolTradingState()
    return _states[symbol]


def ensure_symbol_configured(symbol: str) -> None:
    state = _get_state(symbol)
    if state.configured:
        return
    configure_symbol_trading(symbol)
    state.configured = True
    logging.info("  Trading config: symbol=%s margin=%s leverage=%dx", symbol, MARGIN_MODE, LEVERAGE)


def configure_all_symbols(symbols: list[str]) -> None:
    for symbol in symbols:
        try:
            ensure_symbol_configured(symbol)
        except BitgetClientError as exc:
            logging.warning("  [%s] Trading config failed: %s", symbol, exc)


def on_symbol_added(symbol: str) -> None:
    _get_state(symbol)
    if TRADING_ENABLED:
        try:
            ensure_symbol_configured(symbol)
        except BitgetClientError as exc:
            logging.warning("  [%s] Trading config failed on add: %s", symbol, exc)


def on_symbol_removed(symbol: str) -> None:
    _states.pop(symbol, None)


def reset_symbol_state(symbol: str) -> None:
    state = _get_state(symbol)
    state.open_cycle_id = None
    state.open_cycle_side = None
    state.last_position_size = 0.0
    state.last_position_side = None


def _close_open_cycles_for_symbol(symbol: str, balance_equity: float) -> None:
    while True:
        open_cycle = db.get_open_trade_cycle_for_symbol(symbol)
        if not open_cycle:
            break
        cycle_id = int(open_cycle["id"])
        db.archive_filled_entries(symbol, cycle_id=cycle_id)
        db.close_trade_cycle(cycle_id, balance_equity)


def liquidate_symbol(symbol: str) -> None:
    try:
        cancel_all_pending_limits(symbol)
    except BitgetClientError as exc:
        logging.warning("  [%s] Cancel pending limits failed: %s", symbol, exc)

    db.cancel_pending_entries(symbol)

    try:
        position = fetch_position(symbol)
        if position.size > 0 and position.side:
            logging.info("  [%s] Closing %s position (profit target)", symbol, position.side)
            close_positions(symbol, hold_side=position.side)
    except BitgetClientError as exc:
        logging.warning("  [%s] Close position failed: %s", symbol, exc)

    try:
        balance = fetch_futures_balance(symbol)
        equity = balance.account_equity
    except BitgetClientError:
        equity = 0.0

    db.archive_filled_entries(symbol, side="long")
    db.archive_filled_entries(symbol, side="short")
    _close_open_cycles_for_symbol(symbol, equity)
    reset_symbol_state(symbol)

    update_symbol_status(
        symbol,
        position_side=None,
        position_size=0.0,
        avg_entry=None,
        pending_orders=[],
    )


def liquidate_all_and_reset(symbols: list[str]) -> float:
    for symbol in symbols:
        try:
            liquidate_symbol(symbol)
        except BitgetClientError as exc:
            logging.error("  [%s] Liquidation failed: %s", symbol, exc)

    if not symbols:
        return 0.0
    balance = fetch_futures_balance(symbols[0])
    return balance.account_equity


_FILL_POLL_ATTEMPTS = 3
_FILL_POLL_DELAY_SEC = 0.15


def _parse_fill_price(detail: dict) -> float | None:
    raw = (
        detail.get("priceAvg")
        or detail.get("price_avg")
        or detail.get("averagePrice")
        or detail.get("avgPrice")
    )
    if raw is None or raw == "":
        return None
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def resolve_order_fill(
    symbol: str,
    order_result: dict,
    *,
    fallback_price: float,
) -> float:
    """Resolve average fill price from order response or order detail API."""
    parsed = _parse_fill_price(order_result)
    if parsed is not None:
        return parsed

    order_id = str(
        order_result.get("orderId")
        or order_result.get("order_id")
        or "",
    )
    if not order_id:
        if fallback_price > 0:
            logging.warning(
                "  [%s] No orderId for fill resolution — using fallback %.6f",
                symbol,
                fallback_price,
            )
            return fallback_price
        return 0.0

    for attempt in range(_FILL_POLL_ATTEMPTS):
        try:
            detail = exchange_fetch_order_detail(symbol, order_id)
            parsed = _parse_fill_price(detail)
            if parsed is not None:
                return parsed
            state = (detail.get("state") or detail.get("status") or "").lower()
            if state in {"filled", "partially_filled", "partial-fill"}:
                break
        except ExchangeClientError as exc:
            logging.warning(
                "  [%s] Fill poll %d failed for order %s: %s",
                symbol,
                attempt + 1,
                order_id,
                exc,
            )
        if attempt < _FILL_POLL_ATTEMPTS - 1:
            time.sleep(_FILL_POLL_DELAY_SEC)

    try:
        mark = fetch_side_mark_price(symbol)
        if mark > 0:
            logging.warning(
                "  [%s] Order %s fill unknown — using mark fallback %.6f",
                symbol,
                order_id,
                mark,
            )
            return mark
    except ExchangeClientError:
        pass

    if fallback_price > 0:
        logging.warning(
            "  [%s] Order %s fill unknown — using price fallback %.6f",
            symbol,
            order_id,
            fallback_price,
        )
        return fallback_price
    return 0.0


def _resolve_avg_entry(
    symbol: str,
    side: str,
    position: Position,
    cycle_id: int | None = None,
    *,
    log: bool = False,
) -> float | None:
    db_avg = db.compute_avg_entry_price(symbol, side, cycle_id=cycle_id)

    bitget_avg: float | None = None
    if position.size > 0 and position.side == side and position.avg_price > 0:
        bitget_avg = position.avg_price

    if bitget_avg is not None:
        result = bitget_avg
        using = "bitget"
    elif db_avg is not None:
        result = db_avg
        using = "db"
    else:
        result = None
        using = "none"

    if log and (bitget_avg is not None or db_avg is not None or position.size > 0):
        bitget_str = f"{bitget_avg:.4f}" if bitget_avg is not None else "—"
        db_str = f"{db_avg:.4f}" if db_avg is not None else "—"
        logging.info(
            "  [%s] Avg entry (%s): bitget=%s db=%s using=%s",
            symbol,
            side,
            bitget_str,
            db_str,
            using,
        )

    return result


def _resolve_pending_entry(symbol: str, order_id: str, pending_ids: set[str]) -> None:
    if order_id in pending_ids:
        return
    try:
        detail = fetch_order_detail(symbol, order_id)
        state = (detail.get("state") or detail.get("status") or "").lower()
        if state in {"filled", "partially_filled", "partial-fill"}:
            fill_price = _parse_fill_price(detail)
            update_entry_by_order_id(order_id, "filled", filled=True, fill_price=fill_price)
        elif state in {"cancelled", "canceled", "rejected"}:
            update_entry_by_order_id(order_id, "cancelled")
        else:
            logging.warning("  [%s] Order %s unknown state '%s', marking cancelled", symbol, order_id, state)
            update_entry_by_order_id(order_id, "cancelled")
    except BitgetClientError as exc:
        logging.warning("  [%s] Could not resolve order %s status: %s", symbol, order_id, exc)
        update_entry_by_order_id(order_id, "cancelled")


def sync_state(symbol: str) -> tuple:
    state = _get_state(symbol)
    position = fetch_position(symbol)
    pending = fetch_pending_orders(symbol)
    pending_ids = {order.order_id for order in pending}

    for row in db.get_pending_entries(symbol):
        order_id = row["order_id"]
        if order_id:
            _resolve_pending_entry(symbol, order_id, pending_ids)

    balance = fetch_futures_balance(symbol)
    prev_size = state.last_position_size
    curr_size = position.size
    curr_side = position.side

    if curr_size > 0 and state.open_cycle_id is None:
        open_cycle = db.get_open_trade_cycle_for_symbol(symbol)
        if open_cycle:
            state.open_cycle_id = int(open_cycle["id"])
            state.open_cycle_side = open_cycle["side"]

    if prev_size <= 0 and curr_size > 0 and curr_side:
        cycle_id = db.create_trade_cycle(symbol, curr_side, balance.account_equity)
        state.open_cycle_id = cycle_id
        state.open_cycle_side = curr_side
        db.assign_entries_to_cycle(symbol, curr_side, cycle_id)
        logging.info(
            "  [%s] Cycle opened: id=%s side=%s balance_at_open=%.2f USDT",
            symbol,
            cycle_id,
            curr_side,
            balance.account_equity,
        )

    if prev_size > 0 and curr_size <= 0 and state.open_cycle_id is not None:
        closed_cycle_id = state.open_cycle_id
        closed_side = state.open_cycle_side
        db.archive_filled_entries(symbol, cycle_id=closed_cycle_id)
        db.close_trade_cycle(closed_cycle_id, balance.account_equity)
        cycle = db.get_all_trade_cycles(limit=1)[0]
        logging.info(
            "  [%s] Cycle closed: id=%s side=%s pnl=%.2f USDT (%.2f%%)",
            symbol,
            cycle["id"],
            closed_side or "?",
            float(cycle["pnl_usdt"] or 0),
            float(cycle["pnl_pct"] or 0),
        )
        state.open_cycle_id = None
        state.open_cycle_side = None

    state.last_position_size = curr_size
    state.last_position_side = curr_side

    avg_entry = None
    if position.side and position.size > 0:
        avg_entry = _resolve_avg_entry(
            symbol,
            position.side,
            position,
            cycle_id=state.open_cycle_id,
            log=True,
        )

    return position, pending, avg_entry


def _finalize_open_cycle(symbol: str) -> None:
    state = _get_state(symbol)
    if state.open_cycle_id is None:
        open_cycle = db.get_open_trade_cycle_for_symbol(symbol)
        if open_cycle:
            state.open_cycle_id = int(open_cycle["id"])
            state.open_cycle_side = open_cycle["side"]
        else:
            return
    closed_cycle_id = state.open_cycle_id
    closed_side = state.open_cycle_side
    db.archive_filled_entries(symbol, cycle_id=closed_cycle_id)
    balance = fetch_futures_balance(symbol)
    db.close_trade_cycle(closed_cycle_id, balance.account_equity)
    cycle = db.get_all_trade_cycles(limit=1)[0]
    logging.info(
        "  [%s] Cycle closed (flip): id=%s side=%s pnl=%.2f USDT (%.2f%%)",
        symbol,
        cycle["id"],
        closed_side or "?",
        float(cycle["pnl_usdt"] or 0),
        float(cycle["pnl_pct"] or 0),
    )
    state.open_cycle_id = None
    state.open_cycle_side = None


def _record_market_entry(
    symbol: str,
    side: str,
    order_id: str,
    client_oid: str,
    size_str: str,
    fallback_price: float,
    cycle_id: int | None,
    *,
    order_result: dict | None = None,
) -> float:
    base = dict(order_result or {})
    if order_id:
        base.setdefault("orderId", order_id)
    fill_price = resolve_order_fill(symbol, base, fallback_price=fallback_price)
    filled = False
    if order_id:
        try:
            detail = exchange_fetch_order_detail(symbol, order_id)
            state = (detail.get("state") or detail.get("status") or "").lower()
            if state in {"filled", "partially_filled", "partial-fill", "partially_filled"}:
                filled = True
        except ExchangeClientError as exc:
            logging.warning("  [%s] Could not resolve market fill for %s: %s", symbol, order_id, exc)
            filled = fill_price > 0

    db.insert_entry(
        symbol,
        side,
        order_id,
        client_oid,
        fill_price,
        float(size_str),
        status="filled" if filled else "pending",
        cycle_id=cycle_id,
    )
    if filled and order_id:
        update_entry_by_order_id(order_id, "filled", filled=True, fill_price=fill_price)

    return fill_price


def evaluate_and_trade(
    symbol: str,
    trend: str,
    sar_signal: str | None,
    last_close: float,
) -> None:
    if not bitget_has_credentials():
        logging.warning("  [%s] Trading skipped: missing API credentials", symbol)
        return

    if not TRADING_ENABLED:
        try:
            position, pending, avg_entry = sync_state(symbol)
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            update_symbol_status(
                symbol,
                position_side=position.side,
                position_size=position.size,
                avg_entry=avg_entry,
                pending_orders=[
                    {"order_id": o.order_id, "side": o.side, "price": o.price, "size": o.size}
                    for o in pending
                ],
                margin_mode=MARGIN_MODE,
                leverage=LEVERAGE,
                last_updated=now_str,
            )
        except BitgetClientError as exc:
            logging.warning("  [%s] Position sync failed: %s", symbol, exc)
        logging.info("  [%s] Trading disabled (TRADING_ENABLED=false) — no orders", symbol)
        return

    ensure_symbol_configured(symbol)
    position, pending, avg_entry = sync_state(symbol)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    update_symbol_status(
        symbol,
        position_side=position.side,
        position_size=position.size,
        avg_entry=avg_entry,
        pending_orders=[
            {"order_id": o.order_id, "side": o.side, "price": o.price, "size": o.size}
            for o in pending
        ],
        margin_mode=MARGIN_MODE,
        leverage=LEVERAGE,
        last_updated=now_str,
    )

    if trend == "sideway":
        logging.info("  [%s] Trading: sideway — no action", symbol)
        return

    state = _get_state(symbol)

    if sar_signal == "bearish_flip" and position.side == "long" and position.size > 0:
        avg_long = _resolve_avg_entry(
            symbol,
            "long",
            position,
            cycle_id=state.open_cycle_id,
        )
        if avg_long is not None and last_close > avg_long:
            logging.info(
                "  [%s] SAR take profit: closing long (close %.4f > avg %.4f)",
                symbol,
                last_close,
                avg_long,
            )
            close_positions(symbol, hold_side="long")
            _finalize_open_cycle(symbol)
            position = fetch_position(symbol)
            state.last_position_size = position.size
            state.last_position_side = position.side
        elif avg_long is not None:
            logging.info(
                "  [%s] SAR bearish flip but long not profitable (close %.4f <= avg %.4f) — holding",
                symbol,
                last_close,
                avg_long,
            )

    if sar_signal == "bullish_flip" and position.side == "short" and position.size > 0:
        avg_short = _resolve_avg_entry(
            symbol,
            "short",
            position,
            cycle_id=state.open_cycle_id,
        )
        if avg_short is not None and last_close < avg_short:
            logging.info(
                "  [%s] SAR take profit: closing short (close %.4f < avg %.4f)",
                symbol,
                last_close,
                avg_short,
            )
            close_positions(symbol, hold_side="short")
            _finalize_open_cycle(symbol)
            position = fetch_position(symbol)
            state.last_position_size = position.size
            state.last_position_side = position.side
        elif avg_short is not None:
            logging.info(
                "  [%s] SAR bullish flip but short not profitable (close %.4f >= avg %.4f) — holding",
                symbol,
                last_close,
                avg_short,
            )

    spec = fetch_contract_spec(symbol)
    size_str = notional_to_size(ORDER_SIZE_USDT, last_close, spec)

    if trend == "uptrend" and sar_signal == "bullish_flip":
        if position.side == "short" and position.size > 0:
            logging.info("  [%s] Closing short position before long entry", symbol)
            close_positions(symbol, hold_side="short")
            _finalize_open_cycle(symbol)
            position = fetch_position(symbol)
            state.last_position_size = position.size
            state.last_position_side = position.side

        avg_long = _resolve_avg_entry(
            symbol,
            "long",
            position,
            cycle_id=state.open_cycle_id,
            log=position.side == "long" and position.size > 0,
        )
        if position.side == "long" and avg_long is not None and last_close > avg_long:
            logging.info(
                "  [%s] Long entry skipped: close %.4f > avg %.4f (already profitable)",
                symbol,
                last_close,
                avg_long,
            )
            return

        logging.info("  [%s] Cancelling pending limits and placing long market size=%s", symbol, size_str)
        cancel_all_pending_limits(symbol)
        for row in db.get_pending_entries(symbol):
            db.update_entry_status(int(row["id"]), "cancelled")

        result = place_market_order(symbol, "buy", size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        fill_price = _record_market_entry(
            symbol, "long", order_id, client_oid, size_str, last_close, state.open_cycle_id,
        )
        logging.info(
            "  [%s] Placed market buy: order_id=%s size=%s fill=%.4f",
            symbol, order_id, size_str, fill_price,
        )
        return

    if trend == "downtrend" and sar_signal == "bearish_flip":
        if position.side == "long" and position.size > 0:
            logging.info("  [%s] Closing long position before short entry", symbol)
            close_positions(symbol, hold_side="long")
            _finalize_open_cycle(symbol)
            position = fetch_position(symbol)
            state.last_position_size = position.size
            state.last_position_side = position.side

        avg_short = _resolve_avg_entry(
            symbol,
            "short",
            position,
            cycle_id=state.open_cycle_id,
            log=position.side == "short" and position.size > 0,
        )
        if position.side == "short" and avg_short is not None and last_close < avg_short:
            logging.info(
                "  [%s] Short entry skipped: close %.4f < avg %.4f (already profitable)",
                symbol,
                last_close,
                avg_short,
            )
            return

        logging.info("  [%s] Cancelling pending limits and placing short market size=%s", symbol, size_str)
        cancel_all_pending_limits(symbol)
        for row in db.get_pending_entries(symbol):
            db.update_entry_status(int(row["id"]), "cancelled")

        result = place_market_order(symbol, "sell", size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        fill_price = _record_market_entry(
            symbol, "short", order_id, client_oid, size_str, last_close, state.open_cycle_id,
        )
        logging.info(
            "  [%s] Placed market sell: order_id=%s size=%s fill=%.4f",
            symbol, order_id, size_str, fill_price,
        )
        return

    logging.info(
        "  [%s] Trading: no entry signal (trend=%s sar=%s)",
        symbol,
        trend,
        sar_signal or "none",
    )
