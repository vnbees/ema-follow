"""Limit open and market flatten for the 5m-fade book only."""

from __future__ import annotations

import logging
import sqlite3
import time

from src.bot_state import is_trading_enabled
from src.exchange import (
    ExchangeClientError,
    close_position_side,
    configure_symbol_trading,
    fetch_contract_spec,
    has_credentials,
    notional_to_size,
)
from src.exchange.binance import (
    cancel_limit_order_detail,
    place_limit_close_order,
    place_limit_open_order,
)
from src.exchange.fills import commission_usdt_from_detail, parse_fill_price
from src.exchange.sizing import format_price, format_size
from src.fade5m import store
from src.fade5m.config import LEVERAGE, MAKER_FEE, MARGIN_USDT, TAKER_FEE
from src.fade5m.engine import tp_price

_configured: set[str] = set()


def _ensure(symbol: str) -> None:
    if symbol in _configured:
        return
    configure_symbol_trading(symbol)
    _configured.add(symbol)


def _ws_detail(order_id: str) -> dict | None:
    try:
        from src.exchange.binance_ws import get_order_detail_from_ws

        return get_order_detail_from_ws(order_id)
    except Exception:  # noqa: BLE001
        return None


def place_limit(
    symbol: str,
    side: str,
    px: float,
    bar_ts: int,
    bucket_ts: int,
    *,
    extra_qty: float = 0.0,
) -> dict | None:
    if not is_trading_enabled() or not has_credentials():
        return None
    if store.has_bar(symbol, bar_ts):
        return None
    _ensure(symbol)
    spec = fetch_contract_spec(symbol)
    base = float(notional_to_size(MARGIN_USDT * LEVERAGE, px, spec))
    qty_txt = format_size(base + max(0.0, float(extra_qty)), spec)
    if float(qty_txt) <= 0:
        logging.warning("fade5m skip %s — size 0", symbol)
        return None
    price_txt = format_price(px, spec)
    try:
        result = place_limit_open_order(symbol, side, qty_txt, price_txt, post_only=False)
    except ExchangeClientError as exc:
        logging.warning("fade5m limit %s %s failed: %s", symbol, side, exc)
        return None
    oid = str(result.get("orderId") or "")
    if not oid:
        logging.warning("fade5m limit %s %s no orderId", symbol, side)
        return None
    try:
        row = store.insert_pending(
            symbol=symbol,
            side=side,
            qty=float(qty_txt),
            limit_px=float(price_txt),
            bar_ts=bar_ts,
            bucket_ts=bucket_ts,
            entry_order_id=oid,
        )
    except sqlite3.IntegrityError:
        logging.warning("fade5m duplicate %s bar %s oid %s", symbol, bar_ts, oid)
        return None
    logging.info(
        "fade5m limit %s %s qty %s @ %s bar %s oid %s extra=%.8f",
        symbol,
        side,
        qty_txt,
        price_txt,
        bar_ts,
        oid,
        extra_qty,
    )
    return row


def realize_pending(row: dict) -> bool:
    """Promote pending → open if the limit filled. True if the row left pending."""
    oid = str(row.get("entry_order_id") or "")
    detail = _ws_detail(oid) if oid else None
    status = str((detail or {}).get("status") or "").lower()
    if status != "filled":
        return False
    try:
        filled = float(detail.get("executedQty") or 0)
    except (TypeError, ValueError):
        filled = 0.0
    if filled <= 0:
        filled = float(row["qty"])
    px = parse_fill_price(detail) or float(row["limit_px"])
    fee = commission_usdt_from_detail(detail)
    if fee <= 0:
        fee = px * filled * TAKER_FEE
    store.mark_open(
        int(row["id"]),
        entry_px=px,
        qty=filled,
        fee_open=fee,
        opened_ts=int(time.time() * 1000),
    )
    logging.info("fade5m filled %s %s qty %s @ %.6f", row["symbol"], row["side"], filled, px)
    return True


def _tp_text(side: str, entry: float, atr: float, spec, qty: float) -> str | None:
    raw = tp_price(side, entry, atr)
    if raw <= 0:
        return None
    price = format_price(raw, spec)
    tick = 10 ** (-spec.price_place)
    for _ in range(8):
        px = float(price)
        if px <= 0:
            return None
        gross = (px - entry) * qty if side == "long" else (entry - px) * qty
        net = gross - entry * qty * TAKER_FEE - abs(px) * qty * MAKER_FEE
        if net > 0:
            return price
        bumped = px + tick if side == "long" else px - tick
        if bumped <= 0:
            return None
        price = format_price(bumped, spec)
    return None


def place_tp(row: dict, atr: float) -> bool | None:
    """Hang a reduce-only 1 ATR TP. True placed, False REST failed, None skip."""
    if str(row.get("tp_order_id") or ""):
        return True
    if not is_trading_enabled() or not has_credentials():
        return None
    symbol = str(row["symbol"])
    side = str(row["side"])
    entry = float(row.get("entry_px") or row["limit_px"])
    qty = float(row["qty"])
    if atr <= 0 or entry <= 0 or qty <= 0:
        return None
    spec = fetch_contract_spec(symbol)
    price = _tp_text(side, entry, atr, spec, qty)
    if not price:
        logging.info("fade5m skip TP %s %s — no net after fees atr %.6f", symbol, side, atr)
        return None
    size = format_size(qty, spec)
    try:
        result = place_limit_close_order(symbol, side, size, price)
    except ExchangeClientError as exc:
        logging.warning("fade5m TP %s %s failed: %s", symbol, side, exc)
        return False
    oid = str(result.get("orderId") or "")
    if not oid:
        return False
    store.set_tp(int(row["id"]), tp_px=float(price), tp_order_id=oid, atr=float(atr))
    logging.info("fade5m TP %s %s qty %s @ %s atr %.6f oid %s", symbol, side, size, price, atr, oid)
    return True


def realize_tp(row: dict) -> float | None:
    """Close the row if the TP limit filled. Returns pnl, or None if still open."""
    oid = str(row.get("tp_order_id") or "")
    if not oid:
        return None
    detail = _ws_detail(oid)
    status = str((detail or {}).get("status") or "").lower()
    if status != "filled":
        return None
    try:
        filled = float(detail.get("executedQty") or 0)
    except (TypeError, ValueError):
        filled = 0.0
    qty = filled if filled > 0 else float(row["qty"])
    px = parse_fill_price(detail) or float(row.get("tp_px") or 0)
    fee = commission_usdt_from_detail(detail)
    entry = float(row.get("entry_px") or row["limit_px"])
    fee_open = float(row.get("fee_open") or 0)
    if fee <= 0:
        fee = px * qty * MAKER_FEE
    side = str(row["side"])
    pnl = (px - entry) * qty if side == "long" else (entry - px) * qty
    pnl -= fee_open + fee
    store.mark_closed(
        int(row["id"]),
        exit_px=px,
        fee_close=fee,
        pnl=pnl,
        reason="tp",
        closed_ts=int(time.time() * 1000),
        close_order_id=oid,
    )
    logging.info("fade5m TP fill %s %s qty %s @ %.6f pnl %.4f", row["symbol"], side, qty, px, pnl)
    return pnl


def cancel_tp(row: dict) -> float | None:
    """Cancel a resting TP. Returns pnl if it filled on cancel, else None."""
    oid = str(row.get("tp_order_id") or "")
    if not oid:
        return None
    symbol = str(row["symbol"])
    try:
        result = cancel_limit_order_detail(symbol, oid)
    except ExchangeClientError as exc:
        logging.warning("fade5m cancel TP %s %s: %s", symbol, oid, exc)
        store.clear_tp_order(int(row["id"]))
        return None
    if result and str(result.get("status") or "").lower() == "filled":
        try:
            filled = float(result.get("executedQty") or 0)
        except (TypeError, ValueError):
            filled = 0.0
        qty = filled if filled > 0 else float(row["qty"])
        px = parse_fill_price(result) or float(row.get("tp_px") or 0)
        fee = commission_usdt_from_detail(result)
        entry = float(row.get("entry_px") or row["limit_px"])
        fee_open = float(row.get("fee_open") or 0)
        if fee <= 0:
            fee = px * qty * MAKER_FEE
        side = str(row["side"])
        pnl = (px - entry) * qty if side == "long" else (entry - px) * qty
        pnl -= fee_open + fee
        store.mark_closed(
            int(row["id"]),
            exit_px=px,
            fee_close=fee,
            pnl=pnl,
            reason="tp",
            closed_ts=int(time.time() * 1000),
            close_order_id=oid,
        )
        logging.info("fade5m TP filled-on-cancel %s %s pnl %.4f", symbol, side, pnl)
        return pnl
    store.clear_tp_order(int(row["id"]))
    return None


def cancel_pending(row: dict, reason: str) -> None:
    oid = str(row.get("entry_order_id") or "")
    symbol = str(row["symbol"])
    if oid:
        try:
            result = cancel_limit_order_detail(symbol, oid)
        except ExchangeClientError as exc:
            logging.warning("fade5m cancel %s %s: %s", symbol, oid, exc)
            return
        if result and str(result.get("status") or "").lower() == "filled":
            try:
                filled = float(result.get("executedQty") or 0)
            except (TypeError, ValueError):
                filled = 0.0
            if filled > 0:
                px = parse_fill_price(result) or float(row["limit_px"])
                fee = commission_usdt_from_detail(result)
                if fee <= 0:
                    fee = px * filled * TAKER_FEE
                store.mark_open(
                    int(row["id"]),
                    entry_px=px,
                    qty=filled,
                    fee_open=fee,
                    opened_ts=int(time.time() * 1000),
                )
                return
    store.mark_cancelled(int(row["id"]), reason, int(time.time() * 1000))


def close_open(row: dict, reason: str) -> dict | None:
    if not is_trading_enabled() or not has_credentials():
        return None
    symbol = str(row["symbol"])
    side = str(row["side"])
    spec = fetch_contract_spec(symbol)
    size = format_size(float(row["qty"]), spec)
    try:
        result = close_position_side(symbol, side, size)
    except ExchangeClientError as exc:
        logging.warning("fade5m close %s %s failed: %s", symbol, side, exc)
        return None
    oid = str(result.get("orderId") or "")
    px = parse_fill_price(result) or float(row.get("entry_px") or row["limit_px"])
    fee = commission_usdt_from_detail(result)
    qty = float(row["qty"])
    entry = float(row.get("entry_px") or row["limit_px"])
    fee_open = float(row.get("fee_open") or 0)
    if fee <= 0:
        fee = px * qty * TAKER_FEE
    pnl = (px - entry) * qty if side == "long" else (entry - px) * qty
    pnl -= fee_open + fee
    now_ms = int(time.time() * 1000)
    store.mark_closed(
        int(row["id"]),
        exit_px=px,
        fee_close=fee,
        pnl=pnl,
        reason=reason,
        closed_ts=now_ms,
        close_order_id=oid,
    )
    logging.info("fade5m close %s %s %s pnl %.4f", symbol, side, reason, pnl)
    return {"pnl": pnl, "exit_px": px, "side": side, "symbol": symbol}
