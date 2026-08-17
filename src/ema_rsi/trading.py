from __future__ import annotations

import logging
import threading

from src.bot_state import is_trading_enabled
from src.ema_rsi.config import LEVERAGE, MARGIN_MIN_USDT, MARGIN_PCT, MAX_OPEN, RR
from src.ema_rsi.signals import EntrySignal, levels_from_entry
from src.ema_rsi import store
from src.exchange import (
    ExchangeClientError,
    close_position_side,
    configure_symbol_trading,
    fetch_all_open_positions,
    fetch_contract_spec,
    fetch_futures_balance,
    fetch_order_detail,
    fetch_symbol_positions,
    has_credentials,
    notional_to_size,
    place_algo_close_order,
    place_market_order,
    cancel_order,
)
from src.exchange.sizing import format_price, format_size
from src.notify import notify_ema_rsi_close, notify_ema_rsi_open, notify_error
from src.exchange.fills import resolve_order_fill

_open_lock = threading.Lock()


def compute_margin_usdt(equity: float) -> float:
    if equity <= 0:
        return MARGIN_MIN_USDT
    return max(MARGIN_MIN_USDT, equity * MARGIN_PCT / 100)


def live_account_balance(symbol: str):
    """REST /fapi/v2/account equity for sizing. Skip WS/disk cache (often stale)."""
    from src.config import EXCHANGE

    if EXCHANGE != "binance":
        return fetch_futures_balance(symbol)

    from src.exchange import binance as binance_mod

    if binance_mod.is_optional_rest_blocked():
        raise ExchangeClientError(
            "REST blocked "
            f"({binance_mod.optional_rest_blocked_sec():.0f}s) — skip entry sizing"
        )
    balance = binance_mod.fetch_futures_balance_rest(symbol)
    try:
        from src.exchange.binance_ws.cache import CACHE
        from src.exchange.binance_ws.persist import save_account_snapshot

        CACHE.set_balance(balance)
        save_account_snapshot()
    except Exception:  # noqa: BLE001
        pass
    return balance


def realized_pnl(side: str, entry: float, close_price: float, size: float) -> float:
    if side == "long":
        return (close_price - entry) * size
    return (entry - close_price) * size


def _exchange_occupied_symbols() -> set[str]:
    occupied: set[str] = set()
    try:
        positions = fetch_all_open_positions()
    except ExchangeClientError as exc:
        logging.warning("EMA-RSI occupied-symbol fetch failed: %s", exc)
        notify_error("EMA-RSI occupied symbols", str(exc))
        return occupied
    for pos in positions:
        if pos.size > 0 and pos.symbol:
            occupied.add(pos.symbol.upper())
    return occupied


def occupied_symbols() -> set[str]:
    occupied = {row["symbol"].upper() for row in store.get_open_trades()}
    occupied.update(_exchange_occupied_symbols())
    return occupied


def can_open_symbol(symbol: str, occupied: set[str] | None = None) -> bool:
    if store.count_open() >= MAX_OPEN:
        return False
    if store.get_open_trade_for_symbol(symbol) is not None:
        return False
    busy = occupied if occupied is not None else occupied_symbols()
    return symbol.upper() not in busy


def _cancel_quiet(symbol: str, order_id: str) -> None:
    if not order_id:
        return
    try:
        cancel_order(symbol, order_id)
    except ExchangeClientError as exc:
        logging.info("  [%s] Cancel order %s skipped: %s", symbol, order_id, exc)


def _place_protective(symbol: str, side: str, sl: float, tp: float, trade_id: int) -> tuple[str, str]:
    spec = fetch_contract_spec(symbol)
    sl_px = format_price(sl, spec)
    tp_px = format_price(tp, spec)
    sl_res = place_algo_close_order(
        symbol,
        hold_side=side,
        order_type="STOP_MARKET",
        stop_price=sl_px,
        client_oid=f"ersl{trade_id}",
    )
    tp_res = place_algo_close_order(
        symbol,
        hold_side=side,
        order_type="TAKE_PROFIT_MARKET",
        stop_price=tp_px,
        client_oid=f"ertp{trade_id}",
    )
    return str(sl_res.get("orderId") or ""), str(tp_res.get("orderId") or "")


def finalize_close(
    trade,
    *,
    reason: str,
    close_price: float,
    leftover_order_id: str | None = None,
) -> bool:
    trade_id = int(trade["id"])
    symbol = str(trade["symbol"])
    side = str(trade["side"])
    entry = float(trade["entry"])
    sl = float(trade["sl"])
    tp = float(trade["tp"])
    size = float(trade["size"] or 0)
    if reason != store.REASON_INVALID_SL and not position_confirmed_flat(symbol, side):
        logging.warning(
            "  [%s] Skip close %s — %s position still open on exchange",
            symbol,
            reason,
            side.upper(),
        )
        return False
    pnl = realized_pnl(side, entry, close_price, size)
    if leftover_order_id is None:
        leftover_order_id = (
            str(trade["tp_order_id"] or "")
            if reason == store.REASON_HIT_SL
            else str(trade["sl_order_id"] or "")
        )
        if reason == store.REASON_INVALID_SL:
            leftover_order_id = ""
            for oid in (trade["sl_order_id"], trade["tp_order_id"]):
                _cancel_quiet(symbol, str(oid or ""))
        else:
            _cancel_quiet(symbol, leftover_order_id)
    elif leftover_order_id:
        _cancel_quiet(symbol, leftover_order_id)

    if not store.close_trade(
        trade_id, close_price=close_price, close_reason=reason, pnl_usdt=pnl
    ):
        return False
    notify_ema_rsi_close(
        symbol,
        side,
        reason=reason,
        entry=entry,
        sl=sl,
        tp=tp,
        close_price=close_price,
        pnl_usdt=pnl,
    )
    logging.info(
        "  [%s] EMA-RSI %s closed %s @ %.6f pnl=%.4f",
        symbol,
        side.upper(),
        reason,
        close_price,
        pnl,
    )
    return True


def open_signal(symbol: str, signal: EntrySignal) -> int | None:
    if signal.skip_reason:
        logging.info("  [%s] Skip EMA-RSI %s — %s", symbol, signal.side, signal.skip_reason)
        return None
    if not is_trading_enabled() or not has_credentials():
        return None

    with _open_lock:
        if not can_open_symbol(symbol):
            logging.info("  [%s] Skip EMA-RSI — symbol occupied or max %d open", symbol, MAX_OPEN)
            return None
        if not store.mark_signal_seen(symbol, signal.signal_ts):
            logging.info("  [%s] Skip EMA-RSI — signal candle already processed", symbol)
            return None

        try:
            configure_symbol_trading(symbol)
            balance = live_account_balance(symbol)
            equity = float(balance.account_equity or 0)
            if equity <= 0:
                logging.warning("  [%s] Skip EMA-RSI — REST equity=%.4f", symbol, equity)
                notify_error(
                    f"EMA-RSI equity {symbol}",
                    f"REST equity={equity:.4f} — skip entry",
                )
                return None
            margin = compute_margin_usdt(equity)
            spec = fetch_contract_spec(symbol)
            notional = margin * LEVERAGE
            size_str = notional_to_size(notional, signal.entry, spec)
            size = float(size_str)
            if size <= 0:
                logging.warning("  [%s] Skip EMA-RSI — size rounded to 0", symbol)
                return None
            logging.info(
                "  [%s] REST equity=%.2f available=%.2f → margin=%.2f USDT (%.1f%%) "
                "notional=%.2f @ %dx size=%s",
                symbol,
                equity,
                float(balance.available or 0),
                margin,
                MARGIN_PCT,
                notional,
                LEVERAGE,
                size_str,
            )

            result = place_market_order(
                symbol,
                "",
                size_str,
                hold_side=signal.side,
                trade_side="open",
            )
            fill = resolve_order_fill(symbol, result, fallback_price=signal.entry)
            sized = levels_from_entry(signal.side, fill, signal.sl, RR)
            if sized is None:
                logging.warning(
                    "  [%s] Fill %.6f vs SL %.6f invalid — flatten",
                    symbol,
                    fill,
                    signal.sl,
                )
                try:
                    close_position_side(symbol, signal.side, format_size(size, spec))
                except ExchangeClientError as exc:
                    logging.warning("  [%s] Flatten after invalid SL failed: %s", symbol, exc)
                    notify_error(
                        f"EMA-RSI flatten {symbol}",
                        f"Invalid SL flatten failed: {exc}",
                    )
                trade_id = store.insert_trade(
                    symbol=symbol,
                    side=signal.side,
                    entry=fill,
                    sl=signal.sl,
                    tp=0.0,
                    r=0.0,
                    size=size,
                    margin_usdt=margin,
                    entry_order_id=str(result.get("orderId") or ""),
                    sl_order_id="",
                    tp_order_id="",
                    client_oid=str(result.get("clientOid") or ""),
                    zone_start_ts=signal.zone_start_ts,
                    signal_ts=signal.signal_ts,
                    status="closed",
                    close_reason=store.REASON_INVALID_SL,
                    close_price=fill,
                    pnl_usdt=0.0,
                    close_notified=True,
                )
                notify_ema_rsi_close(
                    symbol,
                    signal.side,
                    reason=store.REASON_INVALID_SL,
                    entry=fill,
                    sl=signal.sl,
                    tp=0.0,
                    close_price=fill,
                    pnl_usdt=0.0,
                )
                return trade_id

            r, tp = sized
            tick = 10 ** (-spec.price_place) if spec.price_place >= 0 else 0.0
            if tick > 0 and r < tick:
                logging.info("  [%s] Skip EMA-RSI after fill — R below 1 tick", symbol)
                try:
                    close_position_side(symbol, signal.side, format_size(size, spec))
                except ExchangeClientError as exc:
                    logging.warning("  [%s] Flatten tiny-R failed: %s", symbol, exc)
                    notify_error(
                        f"EMA-RSI flatten {symbol}",
                        f"Tiny-R flatten failed: {exc}",
                    )
                return None

            trade_id = store.insert_trade(
                symbol=symbol,
                side=signal.side,
                entry=fill,
                sl=signal.sl,
                tp=tp,
                r=r,
                size=size,
                margin_usdt=margin,
                entry_order_id=str(result.get("orderId") or ""),
                sl_order_id="",
                tp_order_id="",
                client_oid=str(result.get("clientOid") or ""),
                zone_start_ts=signal.zone_start_ts,
                signal_ts=signal.signal_ts,
            )
            try:
                sl_id, tp_id = _place_protective(symbol, signal.side, signal.sl, tp, trade_id)
            except ExchangeClientError as exc:
                logging.error("  [%s] Protective SL/TP failed: %s — flattening", symbol, exc)
                flatten_err = ""
                try:
                    close_position_side(symbol, signal.side, format_size(size, spec))
                except ExchangeClientError as close_exc:
                    flatten_err = f" | flatten failed: {close_exc}"
                    logging.warning("  [%s] Flatten after algo fail: %s", symbol, close_exc)
                notify_error(
                    f"EMA-RSI SL/TP {symbol}",
                    f"Protective SL/TP failed: {exc}{flatten_err}",
                )
                store.close_trade(
                    trade_id,
                    close_price=fill,
                    close_reason=store.REASON_INVALID_SL,
                    pnl_usdt=0.0,
                )
                return None

            store.update_algo_ids(trade_id, sl_order_id=sl_id, tp_order_id=tp_id)
            if store.mark_open_notified(trade_id):
                notify_ema_rsi_open(
                    symbol,
                    signal.side,
                    entry=fill,
                    sl=signal.sl,
                    tp=tp,
                    r=r,
                    rr=RR,
                    margin_usdt=margin,
                )
            logging.info(
                "  [%s] EMA-RSI %s opened entry=%.6f SL=%.6f TP=%.6f size=%s margin=%.2f",
                symbol,
                signal.side.upper(),
                fill,
                signal.sl,
                tp,
                size_str,
                margin,
            )
            return trade_id
        except ExchangeClientError as exc:
            logging.warning("  [%s] EMA-RSI open failed: %s", symbol, exc)
            notify_error(f"EMA-RSI open {symbol}", str(exc))
            return None


def symbol_side_flat(symbol: str, side: str) -> bool:
    try:
        positions = fetch_symbol_positions(symbol)
    except ExchangeClientError:
        return False
    pos = positions.get(side.lower())
    return pos is None or pos.size <= 0


def position_confirmed_flat(symbol: str, side: str) -> bool:
    """Require REST positionRisk when available; never trust stale WS-only flat."""
    from src.config import EXCHANGE

    if EXCHANGE != "binance":
        return symbol_side_flat(symbol, side)

    from src.exchange import binance as binance_mod

    if not binance_mod.is_optional_rest_blocked():
        try:
            positions = binance_mod.fetch_symbol_positions_rest(symbol, priority="critical")
            pos = positions.get(side.lower())
            return pos is None or pos.size <= 0
        except ExchangeClientError as exc:
            logging.debug("  [%s] REST flat check failed: %s", symbol, exc)

    try:
        from src.exchange.binance_ws import get_symbol_positions_from_ws

        cached = get_symbol_positions_from_ws(symbol)
        if cached is None:
            return False
        pos = cached.get(side.lower())
        return pos is None or pos.size <= 0
    except Exception:  # noqa: BLE001
        return False


def _algo_order_active(detail: dict | None) -> bool:
    if not detail:
        return False
    status = str(detail.get("status") or detail.get("state") or "").lower()
    return status in {"new", "working", "pending", "triggered"}


def _open_algo_ids_by_client(symbol: str) -> dict[str, str]:
    """Map clientAlgoId (lower) -> algoId for open conditional orders."""
    from src.config import EXCHANGE

    if EXCHANGE != "binance":
        return {}
    try:
        from src.exchange import binance as binance_mod

        rows = binance_mod.fetch_open_algo_orders(symbol)
    except ExchangeClientError:
        return {}
    out: dict[str, str] = {}
    for row in rows:
        client = str(row.get("clientAlgoId") or "").lower()
        algo_id = str(row.get("algoId") or "")
        if client and algo_id:
            out[client] = algo_id
    return out


def ensure_protective_orders(trade) -> None:
    """Re-place or adopt SL/TP algo orders cancelled, missing, or lost from DB."""
    if not is_trading_enabled() or not has_credentials():
        return
    symbol = str(trade["symbol"])
    side = str(trade["side"])
    sl = float(trade["sl"])
    tp = float(trade["tp"])
    trade_id = int(trade["id"])
    sl_id = str(trade["sl_order_id"] or "")
    tp_id = str(trade["tp_order_id"] or "")
    sl_client = f"ersl{trade_id}"
    tp_client = f"ertp{trade_id}"
    on_exchange = _open_algo_ids_by_client(symbol)
    new_sl = sl_id or on_exchange.get(sl_client, "")
    new_tp = tp_id or on_exchange.get(tp_client, "")
    spec = fetch_contract_spec(symbol)

    if new_sl:
        try:
            if not _algo_order_active(fetch_order_detail(symbol, new_sl)):
                new_sl = on_exchange.get(sl_client, "")
        except ExchangeClientError:
            new_sl = on_exchange.get(sl_client, "")
    if new_tp:
        try:
            if not _algo_order_active(fetch_order_detail(symbol, new_tp)):
                new_tp = on_exchange.get(tp_client, "")
        except ExchangeClientError:
            new_tp = on_exchange.get(tp_client, "")

    try:
        if not new_sl:
            sl_res = place_algo_close_order(
                symbol,
                hold_side=side,
                order_type="STOP_MARKET",
                stop_price=format_price(sl, spec),
                client_oid=sl_client,
            )
            new_sl = str(sl_res.get("orderId") or "")
        if not new_tp:
            tp_res = place_algo_close_order(
                symbol,
                hold_side=side,
                order_type="TAKE_PROFIT_MARKET",
                stop_price=format_price(tp, spec),
                client_oid=tp_client,
            )
            new_tp = str(tp_res.get("orderId") or "")
        if new_sl != sl_id or new_tp != tp_id:
            store.update_algo_ids(trade_id, sl_order_id=new_sl, tp_order_id=new_tp)
            logging.info(
                "  [%s] Restored protective orders SL=%s TP=%s",
                symbol,
                new_sl,
                new_tp,
            )
    except ExchangeClientError as exc:
        logging.warning("  [%s] Restore SL/TP failed: %s", symbol, exc)
        notify_error(f"EMA-RSI restore SL/TP {symbol}", str(exc))


def reconcile_protective_orders() -> None:
    """Ensure every open trade has SL/TP on exchange (DB ids may be missing)."""
    for trade in store.get_open_trades():
        ensure_protective_orders(trade)


def reconcile_orphan_positions() -> None:
    """Re-adopt exchange positions that were wrongly marked closed in DB."""
    try:
        positions = fetch_all_open_positions()
    except ExchangeClientError as exc:
        logging.debug("EMA-RSI orphan reconcile skipped: %s", exc)
        return
    for pos in positions:
        if pos.size <= 0 or not pos.symbol or not pos.side:
            continue
        symbol = pos.symbol.upper()
        side = str(pos.side).lower()
        if store.get_open_trade_for_symbol(symbol) is not None:
            continue
        closed = store.get_latest_closed_trade(symbol, side)
        if closed is None:
            continue
        trade_id = int(closed["id"])
        if not store.reopen_trade(trade_id):
            continue
        trade = store.get_trade(trade_id)
        if trade is None:
            continue
        logging.warning(
            "  [%s] Re-adopted orphan %s — DB was %s but exchange still open",
            symbol,
            side.upper(),
            closed["close_reason"],
        )
        notify_error(
            f"EMA-RSI orphan {symbol}",
            f"Re-adopted {side.upper()} — DB was {closed['close_reason']} but exchange still open",
        )
        ensure_protective_orders(trade)
