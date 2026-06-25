import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from src import database as db
from src.bitget_client import (
    BitgetClientError,
    close_positions,
    fetch_contract_spec,
    has_credentials,
    notional_to_size,
    place_market_order,
)
from src.bot_state import update_symbol_status
from src.config import (
    LEVERAGE,
    MARGIN_MODE,
    MAX_OPEN_POSITIONS,
    TRADING_ENABLED,
    order_notional_usdt,
)
from src.rsi import RsiSnapshot
from src.rsi_signals import RsiSignal, detect_dca_signal, should_exit
from src.trading import (
    _get_state,
    _record_market_entry,
    ensure_symbol_configured,
    sync_state,
)


@dataclass
class RsiTradeState:
    trade_id: int | None = None
    entry_trigger: str = ""


_rsi_states: dict[str, RsiTradeState] = {}


def can_open_new_position() -> bool:
    return len(db.get_open_rsi_trades()) < MAX_OPEN_POSITIONS


def load_rsi_state_from_db(row) -> RsiTradeState:
    state = RsiTradeState(
        trade_id=int(row["id"]),
        entry_trigger=str(row["entry_trigger"] or ""),
    )
    _rsi_states[row["symbol"]] = state
    return state


def clear_rsi_state(symbol: str) -> None:
    _rsi_states.pop(symbol, None)


def _close_full(symbol: str, hold_side: str, close_reason: str) -> None:
    close_positions(symbol, hold_side=hold_side)
    db.close_rsi_trade(symbol, close_reason=close_reason)
    clear_rsi_state(symbol)


def _update_status_from_snap(
    symbol: str,
    snap: RsiSnapshot,
    signal: RsiSignal,
    *,
    position_side: str | None,
    position_size: float,
    avg_entry: float | None,
    on_exchange: bool,
    is_tracked: bool,
    pending_orders: list[dict],
    now_str: str,
) -> None:
    update_symbol_status(
        symbol,
        position_side=position_side,
        position_size=position_size,
        avg_entry=avg_entry,
        rsi_value=snap.rsi,
        rsi_prev=snap.prev_rsi,
        rsi_signal=signal.side or "",
        rsi_cross_up_25=snap.cross_up_25,
        rsi_cross_up_75=snap.cross_up_75,
        rsi_cross_down_75=snap.cross_down_75,
        rsi_cross_down_25=snap.cross_down_25,
        is_tracked=is_tracked,
        on_exchange=on_exchange,
        pending_orders=pending_orders,
        margin_mode=MARGIN_MODE,
        leverage=LEVERAGE,
        last_updated=now_str,
    )


def _open_new_position(
    symbol: str,
    signal: RsiSignal,
    snap: RsiSnapshot,
    trade_state,
) -> None:
    spec = fetch_contract_spec(symbol)
    price = snap.close
    size_str = notional_to_size(order_notional_usdt(), price, spec)

    if signal.side == "long":
        logging.info(
            "  [%s] RSI LONG: cross up 25 | RSI=%.2f | price=%.4f",
            symbol,
            snap.rsi,
            price,
        )
        result = place_market_order(symbol, "buy", size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        fill = _record_market_entry(
            symbol, "long", order_id, client_oid, size_str, price, trade_state.open_cycle_id,
        )
        db.insert_rsi_trade(
            symbol=symbol,
            side="long",
            entry_price=fill,
            rsi_entry=snap.rsi,
            entry_trigger=signal.entry_trigger or "rsi_cross_25",
            position_size=float(size_str),
        )
        logging.info(
            "  [%s] Placed market buy: margin=%.0f USDT @ %dx size=%s fill=%.4f",
            symbol,
            order_notional_usdt() / LEVERAGE,
            LEVERAGE,
            size_str,
            fill,
        )
        return

    if signal.side == "short":
        logging.info(
            "  [%s] RSI SHORT: cross down 75 | RSI=%.2f | price=%.4f",
            symbol,
            snap.rsi,
            price,
        )
        result = place_market_order(symbol, "sell", size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        fill = _record_market_entry(
            symbol, "short", order_id, client_oid, size_str, price, trade_state.open_cycle_id,
        )
        db.insert_rsi_trade(
            symbol=symbol,
            side="short",
            entry_price=fill,
            rsi_entry=snap.rsi,
            entry_trigger=signal.entry_trigger or "rsi_cross_75",
            position_size=float(size_str),
        )
        logging.info(
            "  [%s] Placed market sell: margin=%.0f USDT @ %dx size=%s fill=%.4f",
            symbol,
            order_notional_usdt() / LEVERAGE,
            LEVERAGE,
            size_str,
            fill,
        )


def _add_to_position(
    symbol: str,
    side: str,
    snap: RsiSnapshot,
    signal: RsiSignal,
    trade_state,
) -> None:
    row = db.get_open_rsi_trade(symbol)
    prev_dca = int(row["dca_count"]) if row and "dca_count" in row.keys() else 0
    dca_num = prev_dca + 1

    spec = fetch_contract_spec(symbol)
    price = snap.close
    size_str = notional_to_size(order_notional_usdt(), price, spec)

    if side == "long":
        logging.info(
            "  [%s] RSI DCA LONG #%d: cross up 25 | RSI=%.2f | price=%.4f",
            symbol,
            dca_num,
            snap.rsi,
            price,
        )
        result = place_market_order(symbol, "buy", size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        _record_market_entry(
            symbol, "long", order_id, client_oid, size_str, price, trade_state.open_cycle_id,
        )
    else:
        logging.info(
            "  [%s] RSI DCA SHORT #%d: cross down 75 | RSI=%.2f | price=%.4f",
            symbol,
            dca_num,
            snap.rsi,
            price,
        )
        result = place_market_order(symbol, "sell", size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        _record_market_entry(
            symbol, "short", order_id, client_oid, size_str, price, trade_state.open_cycle_id,
        )

    position, _, avg_entry = sync_state(symbol)
    db.update_rsi_trade(
        symbol,
        entry_price=avg_entry or price,
        position_size=position.size,
        rsi_entry=snap.rsi,
        entry_trigger=signal.entry_trigger,
        dca_count=dca_num,
    )
    logging.info(
        "  [%s] DCA #%d placed: margin=%.0f USDT @ %dx size=%s | total=%.4f",
        symbol,
        dca_num,
        order_notional_usdt() / LEVERAGE,
        LEVERAGE,
        size_str,
        position.size,
    )


def evaluate_rsi_trade(
    symbol: str,
    snap: RsiSnapshot,
    signal: RsiSignal,
) -> None:
    if not has_credentials():
        logging.warning("  [%s] RSI trading skipped: missing API credentials", symbol)
        return

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if not TRADING_ENABLED:
        try:
            position, pending, avg_entry = sync_state(symbol)
            _update_status_from_snap(
                symbol,
                snap,
                signal,
                position_side=position.side,
                position_size=position.size,
                avg_entry=avg_entry,
                on_exchange=position.size > 0,
                is_tracked=db.get_open_rsi_trade(symbol) is not None,
                pending_orders=[
                    {"order_id": o.order_id, "side": o.side, "price": o.price, "size": o.size}
                    for o in pending
                ],
                now_str=now_str,
            )
        except BitgetClientError as exc:
            logging.warning("  [%s] Position sync failed: %s", symbol, exc)
        logging.info("  [%s] Trading disabled (TRADING_ENABLED=false) — no orders", symbol)
        return

    if not snap.ready:
        logging.info("  [%s] RSI not ready — skip trading", symbol)
        return

    ensure_symbol_configured(symbol)
    position, pending, avg_entry = sync_state(symbol)
    trade_state = _get_state(symbol)
    on_exchange = position.size > 0 and bool(position.side)
    pending_orders = [
        {"order_id": o.order_id, "side": o.side, "price": o.price, "size": o.size}
        for o in pending
    ]

    display_signal = signal
    if position.size > 0 and position.side:
        dca_signal = detect_dca_signal(position.side, snap)
        if dca_signal:
            display_signal = dca_signal

    _update_status_from_snap(
        symbol,
        snap,
        display_signal,
        position_side=position.side,
        position_size=position.size,
        avg_entry=avg_entry,
        on_exchange=on_exchange,
        is_tracked=True,
        pending_orders=pending_orders,
        now_str=now_str,
    )

    if position.size > 0 and position.side:
        exit_now, reason = should_exit(position.side, snap)
        logging.info(
            "  [%s] Managing %s | RSI=%.2f (prev=%.2f) | size=%.4f",
            symbol,
            position.side.upper(),
            snap.rsi,
            snap.prev_rsi,
            position.size,
        )
        if exit_now:
            logging.info("  [%s] Exit %s — %s", symbol, position.side, reason)
            _close_full(symbol, position.side, reason)
            return

        dca_signal = detect_dca_signal(position.side, snap)
        if dca_signal and dca_signal.side:
            _add_to_position(symbol, position.side, snap, dca_signal, trade_state)
        return

    if position.size > 0:
        return

    if not signal.side:
        logging.info(
            "  [%s] RSI: no entry (%s)",
            symbol,
            ", ".join(signal.reasons) if signal.reasons else "no signal",
        )
        return

    if not can_open_new_position():
        open_count = len(db.get_open_rsi_trades())
        logging.info(
            "  [%s] Max open positions reached (%d/%d) — skip entry",
            symbol,
            open_count,
            MAX_OPEN_POSITIONS,
        )
        return

    _open_new_position(symbol, signal, snap, trade_state)
