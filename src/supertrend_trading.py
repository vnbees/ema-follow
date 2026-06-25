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
from src.supertrend import SuperTrendSnapshot
from src.supertrend_signals import SuperTrendSignal, should_exit
from src.trading import (
    _get_state,
    _record_market_entry,
    ensure_symbol_configured,
    sync_state,
)


@dataclass
class SuperTrendTradeState:
    trade_id: int | None = None
    entry_trigger: str = ""


_st_states: dict[str, SuperTrendTradeState] = {}


def can_open_new_position() -> bool:
    return len(db.get_open_supertrend_trades()) < MAX_OPEN_POSITIONS


def load_st_state_from_db(row) -> SuperTrendTradeState:
    state = SuperTrendTradeState(
        trade_id=int(row["id"]),
        entry_trigger=str(row["entry_trigger"] or ""),
    )
    _st_states[row["symbol"]] = state
    return state


def _get_st_state(symbol: str) -> SuperTrendTradeState:
    if symbol not in _st_states:
        row = db.get_open_supertrend_trade(symbol)
        if row:
            return load_st_state_from_db(row)
        _st_states[symbol] = SuperTrendTradeState()
    return _st_states[symbol]


def clear_st_state(symbol: str) -> None:
    _st_states.pop(symbol, None)


def _close_full(symbol: str, hold_side: str, close_reason: str) -> None:
    close_positions(symbol, hold_side=hold_side)
    db.close_supertrend_trade(symbol, close_reason=close_reason)
    clear_st_state(symbol)


def evaluate_supertrend_trade(
    symbol: str,
    snap_5m: SuperTrendSnapshot,
    snap_1h: SuperTrendSnapshot,
    signal: SuperTrendSignal,
) -> None:
    if not has_credentials():
        logging.warning("  [%s] SuperTrend trading skipped: missing API credentials", symbol)
        return

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if not TRADING_ENABLED:
        try:
            position, pending, avg_entry = sync_state(symbol)
            update_symbol_status(
                symbol,
                position_side=position.side,
                position_size=position.size,
                avg_entry=avg_entry,
                st_trend_5m=snap_5m.trend,
                st_trend_1h=snap_1h.trend,
                st_flip_5m=snap_5m.flipped,
                st_signal=signal.side or "",
                st_supertrend_5m=snap_5m.supertrend_value,
                st_supertrend_1h=snap_1h.supertrend_value,
                is_tracked=db.get_open_supertrend_trade(symbol) is not None,
                on_exchange=position.size > 0,
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

    if not snap_5m.ready or not snap_1h.ready:
        logging.info("  [%s] SuperTrend not ready — skip trading", symbol)
        return

    ensure_symbol_configured(symbol)
    position, pending, avg_entry = sync_state(symbol)
    trade_state = _get_state(symbol)
    on_exchange = position.size > 0 and bool(position.side)

    update_symbol_status(
        symbol,
        position_side=position.side,
        position_size=position.size,
        avg_entry=avg_entry,
        st_trend_5m=snap_5m.trend,
        st_trend_1h=snap_1h.trend,
        st_flip_5m=snap_5m.flipped,
        st_signal=signal.side or "",
        st_supertrend_5m=snap_5m.supertrend_value,
        st_supertrend_1h=snap_1h.supertrend_value,
        is_tracked=True,
        on_exchange=on_exchange,
        pending_orders=[
            {"order_id": o.order_id, "side": o.side, "price": o.price, "size": o.size}
            for o in pending
        ],
        margin_mode=MARGIN_MODE,
        leverage=LEVERAGE,
        last_updated=now_str,
    )

    if position.size > 0 and position.side:
        exit_now, reason = should_exit(position.side, snap_5m.trend, snap_1h.trend)
        logging.info(
            "  [%s] Managing %s | 5m=%s 1h=%s | size=%.4f",
            symbol,
            position.side.upper(),
            snap_5m.trend,
            snap_1h.trend,
            position.size,
        )
        if exit_now:
            logging.info("  [%s] Exit %s — %s", symbol, position.side, reason)
            _close_full(symbol, position.side, reason)
        return

    if position.size > 0:
        return

    if not signal.side:
        logging.info(
            "  [%s] SuperTrend: no entry (%s)",
            symbol,
            ", ".join(signal.reasons) if signal.reasons else "no signal",
        )
        return

    if not can_open_new_position():
        open_count = len(db.get_open_supertrend_trades())
        logging.info(
            "  [%s] Max open positions reached (%d/%d) — skip entry",
            symbol,
            open_count,
            MAX_OPEN_POSITIONS,
        )
        return

    spec = fetch_contract_spec(symbol)
    price = snap_5m.close
    size_str = notional_to_size(order_notional_usdt(), price, spec)

    if signal.side == "long":
        logging.info(
            "  [%s] SuperTrend LONG: 5m flip %s + 1h %s | price=%.4f",
            symbol,
            snap_5m.trend,
            snap_1h.trend,
            price,
        )
        result = place_market_order(symbol, "buy", size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        fill = _record_market_entry(
            symbol, "long", order_id, client_oid, size_str, price, trade_state.open_cycle_id,
        )
        db.insert_supertrend_trade(
            symbol=symbol,
            side="long",
            entry_price=fill,
            trend_5m_entry=snap_5m.trend,
            trend_1h_entry=snap_1h.trend,
            entry_trigger=signal.entry_trigger or "5m_flip",
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
            "  [%s] SuperTrend SHORT: 5m flip %s + 1h %s | price=%.4f",
            symbol,
            snap_5m.trend,
            snap_1h.trend,
            price,
        )
        result = place_market_order(symbol, "sell", size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        fill = _record_market_entry(
            symbol, "short", order_id, client_oid, size_str, price, trade_state.open_cycle_id,
        )
        db.insert_supertrend_trade(
            symbol=symbol,
            side="short",
            entry_price=fill,
            trend_5m_entry=snap_5m.trend,
            trend_1h_entry=snap_1h.trend,
            entry_trigger=signal.entry_trigger or "5m_flip",
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
