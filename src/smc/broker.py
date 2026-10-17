"""Place and manage reversed SMC limits on the shared futures book.

The sweep signal is unchanged. The order is the opposite side: the wick is the
take-profit and the midpoint is the stop. Entry is a stop at the swing extreme,
so it rests until price trades that level. One pending or open trade per coin.
Each new order uses 2% of equity as margin. Take-profit and stop prices stay put.
A signal whose take-profit does not cover the round-trip fee is not placed.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from src.bot_state import get_account_balance, is_trading_enabled
from src.donchian.config import FIXED_SCAN_SYMBOLS, INTERVAL, LEVERAGE
from src.exchange import ExchangeClientError, configure_symbol_trading, fetch_contract_spec, has_credentials, notional_to_size
from src.exchange.fills import commission_usdt_from_detail, parse_fill_price
from src.exchange.sizing import format_price, format_size
from src.smc import store
from src.smc.signals import BAR_MS, Setup, expired, flipped, setup_on_last_bar, stop_hit

# Margin per order, as a fraction of equity. Notional is margin times leverage.
MARGIN_PCT = 0.02
# Stop-market entry is taker. The take-profit limit is maker.
TAKER_FEE_RATE = 0.0005
MAKER_FEE_RATE = 0.0002
# Close a filled trade at the end of the 35–41 minute stable window.
MAX_HOLD_SEC = 41 * 60
_configured: set[str] = set()
_saw_size: set[int] = set()
_TERMINAL = frozenset({"filled", "canceled", "cancelled", "expired", "rejected"})
_STOP_FILLED = frozenset({"filled", "triggered", "finished"})
_SHORT_FILL_WAIT_SEC = 3.0
_short_fill_since: dict[int, float] = {}
_bracket_locks: dict[int, threading.Lock] = {}
_bracket_locks_guard = threading.Lock()


def _bracket_lock(trade_id: int) -> threading.Lock:
    with _bracket_locks_guard:
        lock = _bracket_locks.get(trade_id)
        if lock is None:
            lock = threading.Lock()
            _bracket_locks[trade_id] = lock
        return lock


def _mark(symbol: str) -> float:
    try:
        from src.exchange.binance_ws import get_mark_from_ws

        mark = get_mark_from_ws(symbol)
        if mark is not None and mark > 0:
            return float(mark)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _closed_candles(symbol: str) -> list:
    from src.exchange.binance_ws.cache import CACHE

    raw = CACHE.get_candles(symbol.upper(), INTERVAL, 400) or []
    now_ms = int(time.time() * 1000)
    return [c for c in raw if int(c.timestamp) + BAR_MS <= now_ms]


def _ws_detail(order_id: str) -> dict | None:
    try:
        from src.exchange.binance_ws import get_order_detail_from_ws

        return get_order_detail_from_ws(order_id)
    except Exception:  # noqa: BLE001
        return None


def _ensure(symbol: str) -> None:
    sym = symbol.upper()
    if sym in _configured:
        return
    configure_symbol_trading(sym)
    _configured.add(sym)


def _epoch_ms(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def entry_block_reason(signal_ts: int, rows) -> str | None:
    """Skip a signal the backtest would not take.

    busy: this coin already had an order during the signal candle.
    cooldown: a fill happened on this candle or the previous one, so the
    backtest still treats the coin as in a trade.
    """
    start = int(signal_ts)
    end = start + BAR_MS
    for row in rows:
        reason = row["close_reason"] if "close_reason" in row.keys() else None
        if reason in {"busy", "cooldown", "through", "small"}:
            continue
        created = _epoch_ms(row["created_at"])
        closed = _epoch_ms(row["closed_at"])
        if created is not None and created < end and (closed is None or closed > start):
            return "busy"
        opened = _epoch_ms(row["opened_at"])
        if opened is None:
            continue
        fill_bar = opened - (opened % BAR_MS)
        if start == fill_bar or start == fill_bar + BAR_MS:
            return "cooldown"
    return None


def round_trip_fee_usdt(entry: float, target: float, notional: float) -> float:
    """Taker fee to open plus maker fee to close at the take-profit price."""
    if entry <= 0 or notional <= 0:
        return 0.0
    close_notional = float(notional) * float(target) / float(entry)
    return float(notional) * TAKER_FEE_RATE + close_notional * MAKER_FEE_RATE


def order_notional(entry: float, stop: float, target: float, equity: float) -> float | None:
    """Position notional for a fixed 2% margin.

    `stop` is unused. Size does not depend on stop distance.
    Returns None when the take-profit does not cover the round-trip fee.
    """
    del stop
    if entry <= 0 or equity <= 0:
        return None
    notional = float(equity) * MARGIN_PCT * LEVERAGE
    reward = abs(float(target) - float(entry)) / float(entry) * notional
    if reward < round_trip_fee_usdt(entry, target, notional):
        return None
    return notional


def book_qty(ordered: float, seen: float) -> float | None:
    """Size to open once the fill covers the order.

    A stop market can print several trades in one moment. The first trade's
    quantity is not the position.
    """
    if seen <= 0:
        return None
    if ordered <= 0 or seen + 1e-9 >= ordered:
        return seen
    return None


def _pnl(side: str, entry: float, close: float, qty: float, fee_open: float, fee_close: float) -> float:
    gross = (close - entry) * qty if side == "long" else (entry - close) * qty
    return gross - fee_open - fee_close


def _open_filled(row, *, fill_px: float, qty: float, fee_open: float) -> None:
    if qty <= 0:
        return
    if fee_open <= 0:
        fee_open = qty * fill_px * 0.0005
    if not store.mark_open(int(row["id"]), fill_px=fill_px, qty=qty, fee_open=fee_open):
        return
    fresh = store.get_trade(int(row["id"]))
    if fresh is None:
        return
    _place_brackets(fresh)
    _notify_fill(fresh)


def _place_entry(trade_id: int, symbol: str, side: str, entry: float, margin: float) -> None:
    from src.exchange.binance import place_market_order, place_stop_open_order

    spec = fetch_contract_spec(symbol)
    px = format_price(entry, spec)
    qty_str = notional_to_size(margin * LEVERAGE, float(px), spec)
    if float(qty_str) <= 0:
        store.mark_cancelled(trade_id, "size")
        return
    _ensure(symbol)
    try:
        result = place_stop_open_order(symbol, side, qty_str, px)
    except ExchangeClientError as exc:
        if "-2021" not in str(exc):
            logging.warning("SMC stop entry %s %s failed: %s", symbol, side, exc)
            return
        result = place_market_order(symbol, "", qty_str, hold_side=side, trade_side="open")
        logging.info("SMC stop %s %s already through %s — market entry", symbol, side, px)
    oid = str(result.get("orderId") or "")
    if not oid:
        return
    store.set_entry_order(trade_id, oid, float(qty_str))
    logging.info("SMC pending %s %s stop %s qty %s id %s", symbol, side, px, qty_str, oid)


def _notify_fill(row) -> None:
    from src.notify import notify_smc_fill

    notify_smc_fill(
        str(row["symbol"]),
        str(row["side"]),
        entry=float(row["fill_px"] or row["entry_px"]),
        stop=float(row["stop_px"]),
        tp=float(row["tp_px"]),
        qty=float(row["qty"] or 0),
        margin_usdt=float(row["margin_usdt"] or 0),
    )


def _notify_close(row, *, close_px: float, pnl: float, reason: str) -> None:
    from src.notify import notify_smc_close

    notify_smc_close(
        str(row["symbol"]),
        str(row["side"]),
        reason=reason,
        entry=float(row["fill_px"] or row["entry_px"]),
        stop=float(row["stop_px"]),
        tp=float(row["tp_px"]),
        close_px=close_px,
        pnl_usdt=pnl,
        qty=float(row["qty"] or 0),
        margin_usdt=float(row["margin_usdt"] or 0),
        opened_at=str(row["opened_at"] or ""),
    )


def _place_brackets(row) -> None:
    """Limit at the wick, stop-market at the midpoint.

    The fill stream and the 2s loop can both arrive before the order id is saved.
    One lock per trade makes the second caller read the saved id and skip.
    """
    from src.exchange.binance import place_algo_close_order, place_limit_close_order

    trade_id = int(row["id"])
    with _bracket_lock(trade_id):
        fresh = store.get_trade(trade_id) or row
        symbol = str(fresh["symbol"])
        side = str(fresh["side"])
        qty = float(fresh["qty"] or 0)
        if qty <= 0:
            return
        spec = fetch_contract_spec(symbol)
        _ensure(symbol)
        if not str(fresh["stop_order_id"] or ""):
            stop_px = format_price(float(fresh["stop_px"]), spec)
            try:
                result = place_algo_close_order(
                    symbol,
                    hold_side=side,
                    order_type="STOP_MARKET",
                    stop_price=stop_px,
                )
            except ExchangeClientError as exc:
                logging.warning("SMC stop %s failed: %s", symbol, exc)
            else:
                oid = str(result.get("orderId") or "")
                if oid:
                    store.set_stop_order(trade_id, oid)
                    logging.info("SMC open %s %s stop %s id %s", symbol, side, stop_px, oid)
        fresh = store.get_trade(trade_id) or fresh
        if not str(fresh["tp_order_id"] or ""):
            tp_px = format_price(float(fresh["tp_px"]), spec)
            size = format_size(qty, spec)
            try:
                result = place_limit_close_order(symbol, side, size, tp_px)
            except ExchangeClientError as exc:
                logging.warning("SMC TP %s failed: %s", symbol, exc)
            else:
                oid = str(result.get("orderId") or "")
                if oid:
                    store.set_tp_order(trade_id, oid)
                    logging.info("SMC open %s %s TP %s id %s", symbol, side, tp_px, oid)


def _cancel_order(symbol: str, order_id: str) -> dict | None:
    from src.exchange.binance import cancel_limit_order_detail

    if not order_id:
        return None
    try:
        return cancel_limit_order_detail(symbol, order_id)
    except ExchangeClientError as exc:
        logging.warning("SMC cancel %s %s failed: %s", symbol, order_id, exc)
        return None


def _cancel_protective(row) -> None:
    symbol = str(row["symbol"])
    _cancel_order(symbol, str(row["tp_order_id"] or ""))
    stop_id = str(row["stop_order_id"] or "")
    if not stop_id:
        return
    from src.exchange.binance import cancel_order

    try:
        cancel_order(symbol, stop_id)
    except ExchangeClientError as exc:
        logging.warning("SMC cancel stop %s failed: %s", symbol, exc)


def _finish_open(row, *, close_px: float, fee_close: float, reason: str) -> None:
    _cancel_protective(row)
    entry = float(row["fill_px"] or row["entry_px"])
    qty = float(row["qty"] or 0)
    fee_open = float(row["fee_open"] or 0)
    pnl = _pnl(str(row["side"]), entry, close_px, qty, fee_open, fee_close)
    if not store.mark_closed(int(row["id"]), close_px=close_px, fee_close=fee_close, pnl=pnl, reason=reason):
        return
    logging.info("SMC close %s %s %s pnl %.2f", row["symbol"], row["side"], reason, pnl)
    _notify_close(row, close_px=close_px, pnl=pnl, reason=reason)


def _market_close(row, *, reason: str, fallback: float) -> None:
    from src.exchange.binance import close_position_side
    from src.exchange.fills import resolve_order_fill, resolve_order_commission

    symbol = str(row["symbol"])
    side = str(row["side"])
    _cancel_protective(row)
    spec = fetch_contract_spec(symbol)
    size = format_size(float(row["qty"] or 0), spec)
    try:
        result = close_position_side(symbol, side, size)
    except ExchangeClientError as exc:
        logging.warning("SMC %s %s failed: %s", reason, symbol, exc)
        return
    fill = resolve_order_fill(symbol, result, fallback_price=fallback)
    oid = str(result.get("orderId") or "")
    fee = resolve_order_commission(oid) if oid else 0.0
    _finish_open(row, close_px=fill, fee_close=fee, reason=reason)


def _market_stop(row) -> None:
    _market_close(row, reason="stop", fallback=float(row["stop_px"]))


def _apply_detail(row, detail: dict) -> None:
    status = str(detail.get("status") or detail.get("state") or "").lower()
    if status not in _TERMINAL and status not in _STOP_FILLED:
        return
    oid = str(detail.get("orderId") or "")
    filled = 0.0
    try:
        filled = float(detail.get("executedQty") or 0)
    except (TypeError, ValueError):
        filled = 0.0
    fee = commission_usdt_from_detail(_ws_detail(oid) or detail)
    if str(row["status"]) == "pending" and oid == str(row["entry_order_id"] or ""):
        if status in _STOP_FILLED and status != "triggered":
            qty = book_qty(float(row["qty"] or 0), filled)
            if qty is None:
                return
            px = parse_fill_price(detail) or float(row["entry_px"])
            _open_filled(row, fill_px=px, qty=qty, fee_open=fee)
        elif status in _STOP_FILLED:
            return
        elif status in _TERMINAL:
            store.mark_cancelled(int(row["id"]), status or "cancel")
        return
    if str(row["status"]) == "open" and oid == str(row["tp_order_id"] or "") and status == "filled":
        px = parse_fill_price(detail) or float(row["tp_px"])
        _finish_open(row, close_px=px, fee_close=fee, reason="tp")


def on_order_update(detail: dict | None) -> None:
    if not detail:
        return
    row = store.find_by_order(str(detail.get("orderId") or ""))
    if row is not None:
        _apply_detail(row, detail)


def _poll_fills() -> None:
    for row in store.active_rows():
        for key in ("entry_order_id", "tp_order_id"):
            oid = str(row[key] or "")
            detail = _ws_detail(oid) if oid else None
            if detail and str(detail.get("status") or "").lower() in _TERMINAL:
                _apply_detail(row, detail)
                break


def _scan(now_ms: int) -> None:
    if not is_trading_enabled() or not has_credentials():
        return
    account = get_account_balance()
    equity = float(account.equity or 0)
    free = float(account.available or 0)
    if equity <= 0:
        return
    for symbol in FIXED_SCAN_SYMBOLS:
        candles = _closed_candles(symbol)
        if len(candles) < 80:
            continue
        setup = setup_on_last_bar(
            [int(c.timestamp) for c in candles],
            [float(c.open) for c in candles],
            [float(c.high) for c in candles],
            [float(c.low) for c in candles],
            [float(c.close) for c in candles],
        )
        if setup is None or store.has_signal(symbol, setup.signal_ts):
            continue
        if expired(setup.signal_ts, now_ms):
            continue
        order = flipped(setup)
        if stop_hit(order.side, order.stop, float(candles[-1].close)):
            skipped = store.insert_pending(
                symbol=symbol,
                side=order.side,
                signal_ts=order.signal_ts,
                entry=order.entry,
                stop=order.stop,
                target=order.target,
                margin=0.0,
            )
            store.mark_cancelled(skipped, "through")
            logging.info("SMC skip %s through signal %s", symbol, order.signal_ts)
            continue
        block = entry_block_reason(order.signal_ts, store.rows_for_symbol(symbol))
        if block is None and store.active_for(symbol) is not None:
            block = "busy"
        if block is not None:
            skipped = store.insert_pending(
                symbol=symbol,
                side=order.side,
                signal_ts=order.signal_ts,
                entry=order.entry,
                stop=order.stop,
                target=order.target,
                margin=0.0,
            )
            store.mark_cancelled(skipped, block)
            logging.info("SMC skip %s %s signal %s", symbol, block, order.signal_ts)
            continue
        notional = order_notional(order.entry, order.stop, order.target, equity)
        if notional is None:
            skipped = store.insert_pending(
                symbol=symbol,
                side=order.side,
                signal_ts=order.signal_ts,
                entry=order.entry,
                stop=order.stop,
                target=order.target,
                margin=0.0,
            )
            store.mark_cancelled(skipped, "small")
            logging.info("SMC skip %s small-tp signal %s", symbol, order.signal_ts)
            continue
        margin = notional / LEVERAGE
        if free < margin:
            continue
        trade_id = store.insert_pending(
            symbol=symbol,
            side=order.side,
            signal_ts=order.signal_ts,
            entry=order.entry,
            stop=order.stop,
            target=order.target,
            margin=margin,
        )
        logging.info(
            "SMC size %s notional %.2f margin %.2f",
            symbol,
            notional,
            margin,
        )
        try:
            _place_entry(trade_id, symbol, order.side, order.entry, margin)
        except ExchangeClientError as exc:
            logging.warning("SMC place %s failed: %s", symbol, exc)
            store.mark_cancelled(trade_id, "place")
            continue
        fresh = store.get_trade(trade_id)
        if fresh is not None and str(fresh["status"]) == "pending" and fresh["entry_order_id"]:
            free -= margin


def _opened_age_sec(row, now_sec: float | None = None) -> float | None:
    raw = str(row["opened_at"] or "")
    if not raw:
        return None
    try:
        opened = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    now = time.time() if now_sec is None else now_sec
    return now - opened.timestamp()


def hold_limit_reached(opened_at: str, now_sec: float, limit_sec: float = MAX_HOLD_SEC) -> bool:
    """True once a filled trade has been open for the stable-window limit."""
    age = _opened_age_sec({"opened_at": opened_at}, now_sec)
    return age is not None and age >= limit_sec


def _held_size(symbol: str, side: str) -> float | None:
    from src.exchange.binance_ws.cache import CACHE

    sides = CACHE.get_symbol_positions(symbol)
    if sides is None:
        return None
    pos = sides.get(side)
    if pos is None:
        return None
    return float(pos.size or 0)


def _guard(row, now_ms: int) -> None:
    symbol = str(row["symbol"])
    side = str(row["side"])
    stop = float(row["stop_px"])
    mark = _mark(symbol)
    candles = _closed_candles(symbol)
    wick = 0.0
    if candles:
        last = candles[-1]
        wick = float(last.low) if side == "long" else float(last.high)
        if int(last.timestamp) <= int(row["signal_ts"]):
            wick = 0.0
    hit = (mark > 0 and stop_hit(side, stop, mark)) or (wick > 0 and stop_hit(side, stop, wick))
    if str(row["status"]) == "pending":
        held = _held_size(symbol, side)
        trade_id = int(row["id"])
        if held is not None and held > 1e-12:
            ordered = float(row["qty"] or 0)
            qty = book_qty(ordered, held)
            if qty is None:
                started = _short_fill_since.get(trade_id)
                if started is None or time.time() - started < _SHORT_FILL_WAIT_SEC:
                    _short_fill_since.setdefault(trade_id, time.time())
                    return
                qty = held
            _short_fill_since.pop(trade_id, None)
            _open_filled(row, fill_px=float(row["entry_px"]), qty=qty, fee_open=0.0)
            return
        _short_fill_since.pop(trade_id, None)
        if hit or expired(int(row["signal_ts"]), now_ms):
            from src.exchange.binance import cancel_order

            oid = str(row["entry_order_id"] or "")
            try:
                cancel_order(symbol, oid)
            except ExchangeClientError as exc:
                logging.warning("SMC cancel stop entry %s failed: %s", symbol, exc)
            _cancel_order(symbol, oid)
            reason = "stop" if hit else "expire"
            store.mark_cancelled(int(row["id"]), reason)
            logging.info("SMC cancel pending %s %s", symbol, reason)
        return
    fresh = store.get_trade(int(row["id"])) or row
    now_sec = now_ms / 1000.0
    if hold_limit_reached(str(fresh["opened_at"] or ""), now_sec):
        held = _held_size(symbol, side)
        if held is not None and held > 1e-12:
            logging.info("SMC time stop %s %s after %.0fs", symbol, side, MAX_HOLD_SEC)
            _market_close(fresh, reason="time", fallback=float(_mark(symbol) or fresh["fill_px"] or fresh["entry_px"]))
            return
    if not str(fresh["stop_order_id"] or "") or not str(fresh["tp_order_id"] or ""):
        _place_brackets(fresh)
        fresh = store.get_trade(int(row["id"])) or fresh
    if str(fresh["stop_order_id"] or ""):
        trade_id = int(fresh["id"])
        held = _held_size(symbol, side)
        age = _opened_age_sec(fresh) or 0.0
        if held is not None and held > 1e-12:
            _saw_size.add(trade_id)
        elif held is not None and held <= 1e-12 and (trade_id in _saw_size or age >= 120):
            qty = float(fresh["qty"] or 0)
            stop_px = float(fresh["stop_px"])
            _saw_size.discard(trade_id)
            _finish_open(fresh, close_px=stop_px, fee_close=qty * stop_px * 0.0005, reason="stop")
        return
    if hit:
        _market_stop(fresh)


def manage(now_ms: int | None = None) -> None:
    """Watcher tick: fills, stops, expiry, then new signals."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    if not has_credentials():
        return
    _poll_fills()
    for row in store.active_rows():
        _guard(row, now)
    _scan(now)


def describe(setup: Setup) -> str:
    return f"{setup.side} entry {setup.entry:.6g} stop {setup.stop:.6g} tp {setup.target:.6g}"
