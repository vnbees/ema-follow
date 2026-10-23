"""Market open/close for the 4h book. One position per coin."""

from __future__ import annotations

import logging
import time

from src.bot_state import is_trading_enabled
from src.exchange import (
    ExchangeClientError,
    close_position_side,
    configure_symbol_trading,
    fetch_contract_spec,
    has_credentials,
    notional_to_size,
    place_market_order,
)
from src.exchange.fills import commission_usdt_from_detail, parse_fill_price, resolve_order_commission, resolve_order_fill
from src.exchange.sizing import format_size
from src.trend4h.config import LEVERAGE, MARGIN_USDT, TAKER_FEE
from src.trend4h import store

_configured: set[str] = set()


def _mark(symbol: str) -> float:
    try:
        from src.exchange.binance_ws import get_mark_from_ws

        mark = get_mark_from_ws(symbol)
        if mark is not None and mark > 0:
            return float(mark)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _ensure(symbol: str) -> None:
    if symbol in _configured:
        return
    configure_symbol_trading(symbol)
    _configured.add(symbol)


def _pnl(side: str, entry: float, exit_px: float, qty: float, fee_open: float, fee_close: float) -> float:
    gross = (exit_px - entry) * qty if side == "long" else (entry - exit_px) * qty
    return gross - fee_open - fee_close


def open_side(symbol: str, side: str, bar_ts: int) -> dict | None:
    if not is_trading_enabled() or not has_credentials():
        return None
    if store.open_row(symbol):
        return None
    _ensure(symbol)
    spec = fetch_contract_spec(symbol)
    px = _mark(symbol)
    if px <= 0:
        logging.warning("trend4h skip %s — no mark", symbol)
        return None
    notional = MARGIN_USDT * LEVERAGE
    qty_txt = notional_to_size(notional, px, spec)
    qty = float(qty_txt)
    if qty <= 0:
        logging.warning("trend4h skip %s — size 0", symbol)
        return None
    result = place_market_order(symbol, "", qty_txt, hold_side=side, trade_side="open")
    oid = str(result.get("orderId") or "")
    if not oid:
        logging.warning("trend4h open %s no orderId", symbol)
        return None
    fill = resolve_order_fill(symbol, result, fallback_price=px)
    if fill <= 0:
        fill = parse_fill_price(result) or px
    fee = resolve_order_commission(oid)
    if fee <= 0:
        fee = fill * qty * TAKER_FEE
    now_ms = int(time.time() * 1000)
    row = store.insert_open(
        symbol=symbol,
        side=side,
        qty=qty,
        entry_px=fill,
        fee_open=fee,
        opened_ts=now_ms,
        bar_ts=bar_ts,
        entry_order_id=oid,
    )
    logging.info("trend4h open %s %s qty %s @ %.6f bar %s", symbol, side, qty_txt, fill, bar_ts)
    return row


def close_side(row: dict, reason: str) -> dict | None:
    if not is_trading_enabled() or not has_credentials():
        return None
    symbol = str(row["symbol"])
    side = str(row["side"])
    spec = fetch_contract_spec(symbol)
    size = format_size(float(row["qty"]), spec)
    try:
        result = close_position_side(symbol, side, size)
    except ExchangeClientError as exc:
        logging.warning("trend4h close %s failed: %s", symbol, exc)
        return None
    oid = str(result.get("orderId") or "")
    px = _mark(symbol) or float(row["entry_px"])
    fill = resolve_order_fill(symbol, result, fallback_price=px) if oid else px
    if fill <= 0:
        fill = parse_fill_price(result) or px
    fee = resolve_order_commission(oid) if oid else 0.0
    if fee <= 0:
        fee = commission_usdt_from_detail(result)
    if fee <= 0:
        fee = fill * float(row["qty"]) * TAKER_FEE
    pnl = _pnl(side, float(row["entry_px"]), fill, float(row["qty"]), float(row["fee_open"] or 0), fee)
    now_ms = int(time.time() * 1000)
    store.close_open(
        int(row["id"]),
        exit_px=fill,
        fee_close=fee,
        pnl=pnl,
        reason=reason,
        closed_ts=now_ms,
        close_order_id=oid,
    )
    logging.info("trend4h close %s %s %s pnl %.4f", symbol, side, reason, pnl)
    return {"pnl": pnl, "exit_px": fill, "side": side, "symbol": symbol, "reason": reason}
