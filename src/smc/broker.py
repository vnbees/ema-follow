"""Place and manage SMC limits on the shared futures book.

One pending or open trade per coin. Each new order uses 10% of account equity
and leverage 10. Entry and take-profit are limits. The stop is a market close.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from src.bot_state import get_account_balance, is_trading_enabled
from src.donchian.config import FIXED_SCAN_SYMBOLS, INTERVAL, LEVERAGE
from src.exchange import ExchangeClientError, configure_symbol_trading, fetch_contract_spec, has_credentials, notional_to_size
from src.exchange.fills import commission_usdt_from_detail, parse_fill_price
from src.exchange.sizing import format_price, format_size
from src.smc import store
from src.smc.signals import BAR_MS, Setup, expired, setup_on_last_bar, stop_hit

MARGIN_PCT = 0.10
_configured: set[str] = set()
_saw_size: set[int] = set()
_TERMINAL = frozenset({"filled", "canceled", "cancelled", "expired", "rejected"})


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
        if reason in {"busy", "cooldown"}:
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


def _pnl(side: str, entry: float, close: float, qty: float, fee_open: float, fee_close: float) -> float:
    gross = (close - entry) * qty if side == "long" else (entry - close) * qty
    return gross - fee_open - fee_close


def _place_entry(trade_id: int, symbol: str, side: str, entry: float, margin: float) -> None:
    from src.exchange.binance import place_limit_open_order

    spec = fetch_contract_spec(symbol)
    px = format_price(entry, spec)
    qty_str = notional_to_size(margin * LEVERAGE, float(px), spec)
    if float(qty_str) <= 0:
        store.mark_cancelled(trade_id, "size")
        return
    _ensure(symbol)
    try:
        result = place_limit_open_order(symbol, side, qty_str, px, post_only=True)
    except ExchangeClientError as exc:
        if "-5022" not in str(exc):
            logging.warning("SMC entry %s %s failed: %s", symbol, side, exc)
            return
        result = place_limit_open_order(symbol, side, qty_str, px, post_only=False)
    oid = str(result.get("orderId") or "")
    if not oid:
        return
    store.set_entry_order(trade_id, oid, float(qty_str))
    logging.info("SMC pending %s %s limit %s qty %s id %s", symbol, side, px, qty_str, oid)


def _place_brackets(row) -> None:
    """Rest the backtest pair: limit at the midpoint, stop-market at the wick."""
    from src.exchange.binance import place_algo_close_order, place_limit_close_order

    symbol = str(row["symbol"])
    side = str(row["side"])
    qty = float(row["qty"] or 0)
    if qty <= 0:
        return
    spec = fetch_contract_spec(symbol)
    _ensure(symbol)
    if not str(row["stop_order_id"] or ""):
        stop_px = format_price(float(row["stop_px"]), spec)
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
                store.set_stop_order(int(row["id"]), oid)
                logging.info("SMC open %s %s stop %s id %s", symbol, side, stop_px, oid)
    if not str(row["tp_order_id"] or ""):
        tp_px = format_price(float(row["tp_px"]), spec)
        size = format_size(qty, spec)
        try:
            result = place_limit_close_order(symbol, side, size, tp_px)
        except ExchangeClientError as exc:
            logging.warning("SMC TP %s failed: %s", symbol, exc)
        else:
            oid = str(result.get("orderId") or "")
            if oid:
                store.set_tp_order(int(row["id"]), oid)
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
    store.mark_closed(int(row["id"]), close_px=close_px, fee_close=fee_close, pnl=pnl, reason=reason)
    logging.info("SMC close %s %s %s pnl %.2f", row["symbol"], row["side"], reason, pnl)


def _market_stop(row) -> None:
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
        logging.warning("SMC stop %s failed: %s", symbol, exc)
        return
    fill = resolve_order_fill(symbol, result, fallback_price=float(row["stop_px"]))
    oid = str(result.get("orderId") or "")
    fee = resolve_order_commission(oid) if oid else 0.0
    _finish_open(row, close_px=fill, fee_close=fee, reason="stop")


def _apply_detail(row, detail: dict) -> None:
    status = str(detail.get("status") or detail.get("state") or "").lower()
    if status not in _TERMINAL:
        return
    oid = str(detail.get("orderId") or "")
    filled = 0.0
    try:
        filled = float(detail.get("executedQty") or 0)
    except (TypeError, ValueError):
        filled = 0.0
    fee = commission_usdt_from_detail(_ws_detail(oid) or detail)
    if str(row["status"]) == "pending" and oid == str(row["entry_order_id"] or ""):
        if status == "filled" and filled > 0:
            px = parse_fill_price(detail) or float(row["entry_px"])
            if store.mark_open(int(row["id"]), fill_px=px, qty=filled, fee_open=fee):
                fresh = store.get_trade(int(row["id"]))
                if fresh is not None:
                    _place_brackets(fresh)
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
    margin = equity * MARGIN_PCT
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
        block = entry_block_reason(setup.signal_ts, store.rows_for_symbol(symbol))
        if block is None and store.active_for(symbol) is not None:
            block = "busy"
        if block is not None:
            skipped = store.insert_pending(
                symbol=symbol,
                side=setup.side,
                signal_ts=setup.signal_ts,
                entry=setup.entry,
                stop=setup.stop,
                target=setup.target,
                margin=0.0,
            )
            store.mark_cancelled(skipped, block)
            logging.info("SMC skip %s %s signal %s", symbol, block, setup.signal_ts)
            continue
        if free < margin:
            break
        trade_id = store.insert_pending(
            symbol=symbol,
            side=setup.side,
            signal_ts=setup.signal_ts,
            entry=setup.entry,
            stop=setup.stop,
            target=setup.target,
            margin=margin,
        )
        try:
            _place_entry(trade_id, symbol, setup.side, setup.entry, margin)
        except ExchangeClientError as exc:
            logging.warning("SMC place %s failed: %s", symbol, exc)
            store.mark_cancelled(trade_id, "place")
            continue
        fresh = store.get_trade(trade_id)
        if fresh is not None and str(fresh["status"]) == "pending" and fresh["entry_order_id"]:
            free -= margin


def _opened_age_sec(row) -> float | None:
    raw = str(row["opened_at"] or "")
    if not raw:
        return None
    try:
        opened = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    return time.time() - opened.timestamp()


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
        if hit or expired(int(row["signal_ts"]), now_ms):
            _cancel_order(symbol, str(row["entry_order_id"] or ""))
            reason = "stop" if hit else "expire"
            store.mark_cancelled(int(row["id"]), reason)
            logging.info("SMC cancel pending %s %s", symbol, reason)
        return
    fresh = store.get_trade(int(row["id"])) or row
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
