from __future__ import annotations

import logging
import threading
import time

from src.bot_state import is_trading_enabled
from src.exchange import (
    ExchangeClientError,
    close_position_side,
    configure_symbol_trading,
    fetch_contract_spec,
    fetch_futures_balance,
    has_credentials,
    notional_to_size,
    place_market_order,
)
from src.exchange.fills import resolve_order_commission, resolve_order_fill
from src.exchange.sizing import format_size
from src.notify import notify_error
from src.donchian import store
from src.donchian.config import (
    BALANCE_CACHE_MAX_AGE_SEC,
    LEVERAGE,
    MARGIN_MIN_USDT,
    MARGIN_PCT,
    MAX_OPEN,
    mark_untradable,
)

_open_lock = threading.Lock()
_close_lock = threading.Lock()


def realized_pnl(side: str, entry: float, close_price: float, size: float) -> float:
    if side == "long":
        return (close_price - entry) * size
    return (entry - close_price) * size


def live_account_balance(symbol: str):
    """WS account cache if fresh; REST /fapi/v2/account only when stale and not blocked."""
    from src.config import EXCHANGE

    if EXCHANGE != "binance":
        return fetch_futures_balance(symbol)

    from src.exchange import binance as binance_mod

    try:
        from src.exchange.binance_ws import get_balance_from_ws
        from src.exchange.binance_ws.cache import CACHE

        cached = get_balance_from_ws()
        if cached is not None and float(cached.account_equity or 0) > 0:
            return cached
        age = None
        if CACHE.account_updated_at > 0:
            age = time.monotonic() - CACHE.account_updated_at
        if cached is None and age is not None and age <= BALANCE_CACHE_MAX_AGE_SEC:
            stale = CACHE.get_balance()
            if stale is not None and float(stale.account_equity or 0) > 0:
                return stale
    except Exception:  # noqa: BLE001
        pass

    if binance_mod.is_optional_rest_blocked():
        try:
            from src.exchange.binance_ws.cache import CACHE

            stale = CACHE.get_balance()
            if stale is not None and float(stale.account_equity or 0) > 0:
                logging.warning("  [%s] Using stale WS equity — REST blocked", symbol)
                return stale
        except Exception:  # noqa: BLE001
            pass
        raise ExchangeClientError(
            f"REST blocked ({binance_mod.optional_rest_blocked_sec():.0f}s) — skip entry sizing"
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


def _real_order_id(result) -> str:
    if not isinstance(result, dict):
        return ""
    raw = result.get("orderId") or result.get("order_id") or ""
    if isinstance(raw, bool) or raw is None:
        return ""
    if isinstance(raw, (int, float)):
        return str(int(raw)) if raw else ""
    text = str(raw).strip()
    if not text or text.startswith("<"):
        return ""
    return text


def _notify_open(symbol: str, side: str, *, trend: str, entry: float, tp_band: float, size: float, margin_usdt: float) -> None:
    try:
        from src.notify import discord_configured, _send_discord, _fmt_px
        if not discord_configured():
            return
        title = f"{symbol.upper()} {side.upper()} mở — trend {trend.upper()}"
        body = (
            f"entry={_fmt_px(entry)}\n"
            f"target band={_fmt_px(tp_band)}\n"
            f"size={size:g}  margin={margin_usdt:.2f} USDT"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_open failed: %s", exc)


def _notify_close(symbol: str, side: str, *, reason: str, entry: float, tp_band: float, close_price: float, pnl_usdt: float, opened_at: str = "") -> None:
    try:
        from src.notify import discord_configured, _send_discord, _fmt_px, _hold_label
        if not discord_configured():
            return
        reason_label = store.REASON_LABELS.get(reason, reason)
        title = f"{symbol.upper()} {side.upper()} đóng — {reason_label}"
        body = (
            f"entry={_fmt_px(entry)}  target band={_fmt_px(tp_band)}\n"
            f"đóng={_fmt_px(close_price)}\n"
            f"pnl={pnl_usdt:+.2f} USDT (đã trừ phí)\n"
            f"giữ {_hold_label(opened_at)}"
        )
        _send_discord(title, body)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_close failed: %s", exc)


def open_lot(
    symbol: str,
    *,
    side: str,
    trend: str,
    trend_ts: int | None,
    entry_ts: int | None,
    tp_band: float,
) -> str:
    """Open a Donchian lot. Returns opened | cap_skip | error | disabled."""
    if not is_trading_enabled() or not has_credentials():
        return "disabled"

    with _open_lock:
        if store.count_open() >= MAX_OPEN:
            logging.info("  [%s] Skip Donchian — max open %d reached", symbol, MAX_OPEN)
            store.record_skip(symbol, "max_open")
            return "cap_skip"

        if store.has_open_lot_for_symbol(symbol):
            logging.debug("  [%s] Skip Donchian — already has open lot", symbol)
            return "cap_skip"

        try:
            configure_symbol_trading(symbol)
            balance = live_account_balance(symbol)
            equity = float(balance.account_equity or 0)
            available = float(balance.available or 0)
            if equity <= 0:
                logging.warning("  [%s] Skip Donchian — equity=%.4f", symbol, equity)
                store.record_skip(symbol, "no_equity")
                return "cap_skip"

            margin = max(0.0, equity * MARGIN_PCT)
            if margin < MARGIN_MIN_USDT:
                logging.info("  [%s] Skip Donchian — margin %.4f below min %.2f", symbol, margin, MARGIN_MIN_USDT)
                store.record_skip(symbol, "margin_too_small")
                return "cap_skip"
            if available < margin:
                logging.info("  [%s] Skip Donchian — available %.2f < margin %.2f", symbol, available, margin)
                store.record_skip(symbol, "cap_skip")
                return "cap_skip"

            spec = fetch_contract_spec(symbol)
            notional = margin * LEVERAGE
            mark_px = _get_mark(symbol)
            size_ref = mark_px if mark_px > 0 else 0.0
            if size_ref <= 0:
                store.record_skip(symbol, "no_price")
                return "cap_skip"

            size_str = notional_to_size(notional, size_ref, spec)
            size = float(size_str)
            if size <= 0:
                logging.warning("  [%s] Skip Donchian — size rounded to 0", symbol)
                store.record_skip(symbol, "size_zero")
                return "cap_skip"

            result = place_market_order(symbol, "", size_str, hold_side=side, trade_side="open")
            order_id = _real_order_id(result)
            if not order_id:
                logging.warning("  [%s] Donchian open skipped persist — missing orderId", symbol)
                notify_error(f"Donchian open {symbol}", "Exchange order returned no orderId")
                return "error"

            fill = resolve_order_fill(symbol, result, fallback_price=size_ref)
            if fill <= 0:
                logging.warning("  [%s] Donchian open skipped persist — fill=%.6f", symbol, fill)
                return "error"

            fee_open = resolve_order_commission(order_id)
            lot_id = store.insert_lot(
                symbol=symbol,
                side=side,
                trend=trend,
                trend_ts=trend_ts,
                entry_ts=entry_ts,
                entry_px=fill,
                tp_band=tp_band,
                size=size,
                margin_usdt=margin,
                notional_usdt=notional,
                entry_order_id=order_id,
                fee_open_usdt=fee_open,
            )
            _notify_open(symbol, side, trend=trend, entry=fill, tp_band=tp_band, size=size, margin_usdt=margin)
            logging.info(
                "  [%s] Donchian %s opened id=%d trend=%s entry=%.6f tp_band=%.6f size=%s margin=%.2f",
                symbol, side.upper(), lot_id, trend, fill, tp_band, size_str, margin,
            )
            return "opened"
        except ExchangeClientError as exc:
            logging.warning("  [%s] Donchian open failed: %s", symbol, exc)
            if "Contract spec not found" not in str(exc):
                notify_error(f"Donchian open {symbol}", str(exc))
            else:
                mark_untradable(symbol)
            return "error"
        except Exception as exc:  # noqa: BLE001
            logging.warning("  [%s] Donchian open unexpected error: %s", symbol, exc)
            notify_error(f"Donchian open {symbol}", str(exc))
            return "error"


def _held_size(symbol: str, side: str) -> float | None:
    """WS hedge size for side, or None if unknown."""
    try:
        from src.exchange.binance_ws import get_symbol_positions_from_ws

        sides = get_symbol_positions_from_ws(symbol.upper())
        if sides is None:
            return None
        pos = sides.get(side.lower())
        if pos is None:
            return 0.0
        return abs(float(pos.size or 0))
    except Exception:  # noqa: BLE001
        return None


def _already_flat_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "-2022" in msg or "reduceonly" in msg or "reduce only" in msg


def _finalize_close(lot, *, reason: str, fill: float, fee_close: float, close_oid: str, opened_at: str) -> bool:
    lot_id = int(lot["id"])
    symbol = str(lot["symbol"])
    side = str(lot["side"])
    entry = float(lot["entry_px"])
    tp_band = float(lot["tp_band"])
    size = float(lot["size"] or 0)
    current = store.get_lot(lot_id)
    if current is None or str(current["status"]) != "open":
        return False
    fee_open = float(current["fee_open_usdt"] or 0)
    gross = realized_pnl(side, entry, fill, size)
    pnl = gross - abs(fee_open) - abs(fee_close)
    if not store.close_lot(
        lot_id,
        close_px=fill,
        close_reason=reason,
        pnl_usdt=pnl,
        fee_close_usdt=fee_close,
        close_order_id=close_oid,
    ):
        return False
    _notify_close(symbol, side, reason=reason, entry=entry, tp_band=tp_band, close_price=fill, pnl_usdt=pnl, opened_at=opened_at)
    logging.info(
        "  [%s] Donchian %s lot %d closed %s @ %.6f pnl=%.4f fee=%.4f",
        symbol, side.upper(), lot_id, reason, fill, pnl, fee_open + fee_close,
    )
    return True


def reconcile_flat_lots() -> None:
    """If DB says open but exchange size is 0, close the lot (ghost after failed persist)."""
    for lot in store.get_open_lots():
        symbol = str(lot["symbol"])
        side = str(lot["side"])
        held = _held_size(symbol, side)
        if held is None or held > 1e-12:
            continue
        mark = _get_mark(symbol)
        fill = mark if mark > 0 else float(lot["entry_px"])
        logging.warning("  [%s] lot %s flat on exchange — closing in DB", symbol, lot["id"])
        _finalize_close(lot, reason=store.REASON_TP, fill=fill, fee_close=0.0, close_oid="", opened_at=str(lot["opened_at"] or ""))


def close_lot(lot, *, reason: str, close_price: float) -> bool:
    lot_id = int(lot["id"])
    symbol = str(lot["symbol"])
    side = str(lot["side"])
    size = float(lot["size"] or 0)
    opened_at = str(lot["opened_at"] or "")

    with _close_lock:
        current = store.get_lot(lot_id)
        if current is None or str(current["status"]) != "open":
            return False
        if not is_trading_enabled() or not has_credentials():
            return False

        held = _held_size(symbol, side)
        if held is not None and held <= 1e-12:
            return _finalize_close(
                current, reason=reason, fill=close_price, fee_close=0.0, close_oid="", opened_at=opened_at
            )

        try:
            spec = fetch_contract_spec(symbol)
            close_qty = held if held and held > 0 else size
            size_str = format_size(close_qty, spec)
            result = close_position_side(symbol, side, size_str)
            fill = resolve_order_fill(symbol, result, fallback_price=close_price)
            close_oid = _real_order_id(result)
            fee_close = resolve_order_commission(close_oid) if close_oid else 0.0
        except ExchangeClientError as exc:
            if _already_flat_error(exc):
                logging.info("  [%s] ReduceOnly rejected — treating lot %d as closed", symbol, lot_id)
                return _finalize_close(
                    current, reason=reason, fill=close_price, fee_close=0.0, close_oid="", opened_at=opened_at
                )
            logging.warning("  [%s] Donchian close lot %d failed: %s", symbol, lot_id, exc)
            notify_error(f"Donchian close {symbol}", str(exc), cooldown_sec=120)
            return False

        return _finalize_close(
            current, reason=reason, fill=fill, fee_close=fee_close, close_oid=close_oid, opened_at=opened_at
        )


def _get_mark(symbol: str) -> float:
    try:
        from src.exchange.binance_ws import get_mark_from_ws
        mark = get_mark_from_ws(symbol)
        if mark is not None and mark > 0:
            return float(mark)
    except Exception:  # noqa: BLE001
        pass
    return 0.0
