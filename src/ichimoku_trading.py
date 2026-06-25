import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from src import database as db
from src.bitget_client import (
    BitgetClientError,
    close_positions,
    fetch_contract_spec,
    format_size,
    has_credentials,
    notional_to_size,
    place_market_order,
)
from src.bot_state import update_symbol_status
from src.config import (
    ICHIMOKU_PARTIAL_TP_RATIO,
    ICHIMOKU_SL_TICKS,
    LEVERAGE,
    MARGIN_MODE,
    TRADING_ENABLED,
    order_notional_usdt,
)
from src.ichimoku import IchimokuSnapshot
from src.ichimoku_signals import IchimokuSignal
from src.trading import (
    _get_state,
    _record_market_entry,
    ensure_symbol_configured,
    sync_state,
)


@dataclass
class IchimokuTradeState:
    trade_id: int | None = None
    entry_price: float = 0.0
    stop_price: float = 0.0
    risk_distance: float = 0.0
    tp1_price: float = 0.0
    partial_taken: bool = False
    trigger_type: str = ""


_ichi_states: dict[str, IchimokuTradeState] = {}


def load_ichi_state_from_db(row) -> IchimokuTradeState:
    state = IchimokuTradeState(
        trade_id=int(row["id"]),
        entry_price=float(row["entry_price"]),
        stop_price=float(row["stop_price"]),
        risk_distance=float(row["risk_distance"]),
        tp1_price=float(row["tp1_price"]),
        partial_taken=bool(row["partial_taken"]),
        trigger_type=str(row["trigger_type"] or ""),
    )
    _ichi_states[row["symbol"]] = state
    return state


def _get_ichi_state(symbol: str) -> IchimokuTradeState:
    if symbol not in _ichi_states:
        row = db.get_open_ichimoku_trade(symbol)
        if row:
            return load_ichi_state_from_db(row)
        _ichi_states[symbol] = IchimokuTradeState()
    return _ichi_states[symbol]


def clear_ichi_state(symbol: str) -> None:
    _ichi_states.pop(symbol, None)


def _persist_ichi_state(symbol: str, ichi: IchimokuTradeState, position_size: float | None = None) -> None:
    db.update_ichimoku_trade(
        symbol,
        entry_price=ichi.entry_price,
        stop_price=ichi.stop_price,
        risk_distance=ichi.risk_distance,
        tp1_price=ichi.tp1_price,
        partial_taken=ichi.partial_taken,
        position_size=position_size,
    )


def _tick_size(spec) -> float:
    return 10 ** (-spec.price_place)


def _kijun_stop(snap: IchimokuSnapshot, side: str, tick_size: float) -> float:
    offset = ICHIMOKU_SL_TICKS * tick_size
    if side == "long":
        return snap.kijun - offset
    return snap.kijun + offset


def _tp1_price(entry: float, risk: float, side: str) -> float:
    if side == "long":
        return entry + risk
    return entry - risk


def _close_full(symbol: str, hold_side: str, close_reason: str) -> None:
    close_positions(symbol, hold_side=hold_side)
    db.close_ichimoku_trade(symbol, close_reason=close_reason)
    clear_ichi_state(symbol)


def _partial_close(symbol: str, position_side: str, size: float, spec) -> bool:
    close_side = "sell" if position_side == "long" else "buy"
    size_str = format_size(size, spec)
    if float(size_str) < spec.min_trade_num:
        return False
    place_market_order(symbol, close_side, size_str, reduce_only=True)
    return True


def _update_trade_status(
    symbol: str,
    ichi: IchimokuTradeState,
    position,
    avg_entry: float | None,
    snap: IchimokuSnapshot,
    signal: IchimokuSignal,
    now_str: str,
    *,
    on_exchange: bool,
) -> None:
    update_symbol_status(
        symbol,
        position_side=position.side,
        position_size=position.size,
        avg_entry=avg_entry,
        ichimoku_signal=signal.side or "",
        ichimoku_trigger=signal.trigger or ichi.trigger_type,
        ichimoku_trend=snap.ichimoku_trend,
        kumo_color=snap.kumo_color,
        kijun=snap.kijun,
        stop_loss=ichi.stop_price,
        tp1_price=ichi.tp1_price,
        partial_taken=ichi.partial_taken,
        is_tracked=True,
        on_exchange=on_exchange,
        margin_mode=MARGIN_MODE,
        leverage=LEVERAGE,
        last_updated=now_str,
    )


def evaluate_ichimoku_trade(
    symbol: str,
    snap: IchimokuSnapshot,
    signal: IchimokuSignal,
) -> None:
    if not has_credentials():
        logging.warning("  [%s] Ichimoku trading skipped: missing API credentials", symbol)
        return

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    if not TRADING_ENABLED:
        try:
            position, pending, avg_entry = sync_state(symbol)
            ichi = _get_ichi_state(symbol)
            update_symbol_status(
                symbol,
                position_side=position.side,
                position_size=position.size,
                avg_entry=avg_entry,
                stop_loss=ichi.stop_price,
                tp1_price=ichi.tp1_price,
                partial_taken=ichi.partial_taken,
                is_tracked=db.get_open_ichimoku_trade(symbol) is not None,
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

    if not snap.ready:
        logging.info("  [%s] Ichimoku not ready — skip trading", symbol)
        return

    ensure_symbol_configured(symbol)
    position, pending, avg_entry = sync_state(symbol)
    trade_state = _get_state(symbol)
    ichi = _get_ichi_state(symbol)
    spec = fetch_contract_spec(symbol)
    tick = _tick_size(spec)
    on_exchange = position.size > 0 and bool(position.side)

    _update_trade_status(
        symbol, ichi, position, avg_entry, snap, signal, now_str, on_exchange=on_exchange,
    )

    if position.size > 0 and position.side:
        side = position.side
        if ichi.entry_price <= 0 or ichi.stop_price <= 0:
            ichi.entry_price = avg_entry or position.avg_price or snap.close
            ichi.stop_price = _kijun_stop(snap, side, tick)
            ichi.risk_distance = abs(ichi.entry_price - ichi.stop_price)
            ichi.tp1_price = _tp1_price(ichi.entry_price, ichi.risk_distance, side)
            if ichi.trade_id is None:
                ichi.trade_id = db.insert_ichimoku_trade(
                    symbol=symbol,
                    side=side,
                    entry_price=ichi.entry_price,
                    stop_price=ichi.stop_price,
                    risk_distance=ichi.risk_distance,
                    tp1_price=ichi.tp1_price,
                    trigger_type=ichi.trigger_type or signal.trigger or "recovered",
                    position_size=position.size,
                )
            else:
                _persist_ichi_state(symbol, ichi, position.size)
        entry = ichi.entry_price or avg_entry or position.avg_price
        stop = ichi.stop_price
        risk = ichi.risk_distance

        logging.info(
            "  [%s] Managing %s | entry=%.4f SL=%.4f TP1=%.4f TP2=Kijun | size=%.4f partial=%s",
            symbol,
            side.upper(),
            entry,
            stop,
            ichi.tp1_price,
            position.size,
            "done" if ichi.partial_taken else "pending",
        )

        if side == "long":
            if stop > 0 and snap.low <= stop:
                logging.info("  [%s] Long stop hit (low %.4f <= stop %.4f)", symbol, snap.low, stop)
                _close_full(symbol, "long", "stop_loss")
                return
            if snap.close < snap.kijun:
                logging.info("  [%s] Long Kijun exit (close %.4f < kijun %.4f)", symbol, snap.close, snap.kijun)
                _close_full(symbol, "long", "kijun_exit")
                return
            if not ichi.partial_taken and risk > 0 and snap.close >= entry + risk:
                partial_size = position.size * ICHIMOKU_PARTIAL_TP_RATIO
                if _partial_close(symbol, "long", partial_size, spec):
                    ichi.partial_taken = True
                    _persist_ichi_state(symbol, ichi, position.size * (1 - ICHIMOKU_PARTIAL_TP_RATIO))
                    logging.info("  [%s] Long partial TP at 1R (50%% closed)", symbol)
                else:
                    logging.info("  [%s] Long 1R hit but partial size below minimum — holding", symbol)
            return

        if side == "short":
            if stop > 0 and snap.high >= stop:
                logging.info("  [%s] Short stop hit (high %.4f >= stop %.4f)", symbol, snap.high, stop)
                _close_full(symbol, "short", "stop_loss")
                return
            if snap.close > snap.kijun:
                logging.info("  [%s] Short Kijun exit (close %.4f > kijun %.4f)", symbol, snap.close, snap.kijun)
                _close_full(symbol, "short", "kijun_exit")
                return
            if not ichi.partial_taken and risk > 0 and snap.close <= entry - risk:
                partial_size = position.size * ICHIMOKU_PARTIAL_TP_RATIO
                if _partial_close(symbol, "short", partial_size, spec):
                    ichi.partial_taken = True
                    _persist_ichi_state(symbol, ichi, position.size * (1 - ICHIMOKU_PARTIAL_TP_RATIO))
                    logging.info("  [%s] Short partial TP at 1R (50%% closed)", symbol)
                else:
                    logging.info("  [%s] Short 1R hit but partial size below minimum — holding", symbol)
        return

    if position.size > 0:
        return

    if not signal.side or not signal.trigger:
        logging.info(
            "  [%s] Ichimoku: no entry (%s)",
            symbol,
            ", ".join(signal.reasons) if signal.reasons else "no signal",
        )
        return

    size_str = notional_to_size(order_notional_usdt(), snap.close, spec)
    stop_price = _kijun_stop(snap, signal.side, tick)
    risk_distance = abs(snap.close - stop_price)
    tp1 = _tp1_price(snap.close, risk_distance, signal.side)

    if signal.side == "long":
        logging.info(
            "  [%s] Ichimoku LONG (%s): entry~%.4f | SL=%.4f | TP1=%.4f (1R, %.0f%%) | TP2=Kijun trail",
            symbol,
            signal.trigger,
            snap.close,
            stop_price,
            tp1,
            ICHIMOKU_PARTIAL_TP_RATIO * 100,
        )
        result = place_market_order(symbol, "buy", size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        fill = _record_market_entry(
            symbol, "long", order_id, client_oid, size_str, snap.close, trade_state.open_cycle_id,
        )
        ichi.entry_price = fill
        ichi.stop_price = stop_price
        ichi.risk_distance = risk_distance
        ichi.tp1_price = tp1
        ichi.partial_taken = False
        ichi.trigger_type = signal.trigger or ""
        ichi.trade_id = db.insert_ichimoku_trade(
            symbol=symbol,
            side="long",
            entry_price=fill,
            stop_price=stop_price,
            risk_distance=risk_distance,
            tp1_price=tp1,
            trigger_type=signal.trigger or "",
            position_size=float(size_str),
        )
        logging.info(
            "  [%s] Placed market buy: margin=%.0f USDT @ %dx (notional ~%.0f) size=%s order_id=%s fill=%.4f",
            symbol,
            order_notional_usdt() / LEVERAGE,
            LEVERAGE,
            order_notional_usdt(),
            size_str,
            order_id,
            fill,
        )
        return

    if signal.side == "short":
        logging.info(
            "  [%s] Ichimoku SHORT (%s): entry~%.4f | SL=%.4f | TP1=%.4f (1R, %.0f%%) | TP2=Kijun trail",
            symbol,
            signal.trigger,
            snap.close,
            stop_price,
            tp1,
            ICHIMOKU_PARTIAL_TP_RATIO * 100,
        )
        result = place_market_order(symbol, "sell", size_str)
        order_id = str(result.get("orderId", ""))
        client_oid = str(result.get("clientOid", ""))
        fill = _record_market_entry(
            symbol, "short", order_id, client_oid, size_str, snap.close, trade_state.open_cycle_id,
        )
        ichi.entry_price = fill
        ichi.stop_price = stop_price
        ichi.risk_distance = risk_distance
        ichi.tp1_price = tp1
        ichi.partial_taken = False
        ichi.trigger_type = signal.trigger or ""
        ichi.trade_id = db.insert_ichimoku_trade(
            symbol=symbol,
            side="short",
            entry_price=fill,
            stop_price=stop_price,
            risk_distance=risk_distance,
            tp1_price=tp1,
            trigger_type=signal.trigger or "",
            position_size=float(size_str),
        )
        logging.info(
            "  [%s] Placed market sell: margin=%.0f USDT @ %dx (notional ~%.0f) size=%s order_id=%s fill=%.4f",
            symbol,
            order_notional_usdt() / LEVERAGE,
            LEVERAGE,
            order_notional_usdt(),
            size_str,
            order_id,
            fill,
        )
