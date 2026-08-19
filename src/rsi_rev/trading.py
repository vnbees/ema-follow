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
from src.notify import notify_error, notify_rsi_rev_close, notify_rsi_rev_open
from src.rsi_rev import store
from src.rsi_rev.config import (
    BALANCE_CACHE_MAX_AGE_SEC,
    LEVERAGE,
    MARGIN_MIN_USDT,
    MARGIN_PCT,
    MAX_OPEN,
    MIN_TP_ROOM_PCT,
)
from src.rsi_rev.signals import (
    ZONE_LABELS,
    EntryTrigger,
    entry_has_tp_room,
    tp_remaining_pct,
)

_open_lock = threading.Lock()
_close_lock = threading.Lock()


def compute_margin_usdt(equity: float) -> float:
    return max(0.0, equity * MARGIN_PCT / 100)


def realized_pnl(side: str, entry: float, close_price: float, size: float) -> float:
    if side == "long":
        return (close_price - entry) * size
    return (entry - close_price) * size


def net_realized_pnl(
    gross: float,
    fee_open_usdt: float = 0.0,
    fee_close_usdt: float = 0.0,
) -> float:
    return float(gross) - abs(float(fee_open_usdt or 0)) - abs(float(fee_close_usdt or 0))


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
        if (
            cached is None
            and age is not None
            and age <= BALANCE_CACHE_MAX_AGE_SEC
        ):
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
                logging.warning(
                    "  [%s] Using stale WS equity — REST blocked",
                    symbol,
                )
                return stale
        except Exception:  # noqa: BLE001
            pass
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


def try_open(symbol: str, trigger: EntryTrigger) -> str:
    """Open a lot. Returns opened | cap_skip | room_skip | skipped | error | disabled."""
    if not is_trading_enabled() or not has_credentials():
        return "disabled"

    with _open_lock:
        if MAX_OPEN > 0 and store.count_open() >= MAX_OPEN:
            logging.info(
                "  [%s] Skip RSI-rev — max open %d reached",
                symbol,
                MAX_OPEN,
            )
            store.record_skip(symbol, "max_open")
            return "cap_skip"
        if store.has_open_lot(symbol, trigger.anchor_ts, trigger.side):
            return "skipped"
        if not entry_has_tp_room(trigger.side, trigger.entry, trigger.tp):
            remain = tp_remaining_pct(trigger.side, trigger.entry, trigger.tp)
            logging.info(
                "  [%s] Skip RSI-rev %s — TP room %.3f%% < min %.3f%% "
                "(entry=%.6f tp=%.6f)",
                symbol,
                trigger.side.upper(),
                remain * 100,
                MIN_TP_ROOM_PCT * 100,
                trigger.entry,
                trigger.tp,
            )
            store.record_skip(symbol, "tp_room")
            return "room_skip"

        try:
            configure_symbol_trading(symbol)
            balance = live_account_balance(symbol)
            equity = float(balance.account_equity or 0)
            available = float(balance.available or 0)
            if equity <= 0:
                logging.warning("  [%s] Skip RSI-rev — equity=%.4f", symbol, equity)
                store.record_skip(symbol, "no_equity")
                return "cap_skip"
            margin = compute_margin_usdt(equity)
            if margin < MARGIN_MIN_USDT:
                logging.info(
                    "  [%s] Skip RSI-rev — margin %.4f below min %.2f",
                    symbol,
                    margin,
                    MARGIN_MIN_USDT,
                )
                store.record_skip(symbol, "margin_too_small")
                return "cap_skip"
            if available < margin:
                logging.info(
                    "  [%s] Skip RSI-rev — available %.2f < margin %.2f (cap_skip)",
                    symbol,
                    available,
                    margin,
                )
                store.record_skip(symbol, "cap_skip")
                return "cap_skip"

            spec = fetch_contract_spec(symbol)
            notional = margin * LEVERAGE
            size_str = notional_to_size(notional, trigger.entry, spec)
            size = float(size_str)
            if size <= 0:
                logging.warning("  [%s] Skip RSI-rev — size rounded to 0", symbol)
                store.record_skip(symbol, "size_zero")
                return "cap_skip"

            result = place_market_order(
                symbol,
                "",
                size_str,
                hold_side=trigger.side,
                trade_side="open",
            )
            order_id = _real_order_id(result)
            if not order_id:
                logging.warning(
                    "  [%s] RSI-rev open skipped persist — missing orderId (%r)",
                    symbol,
                    type(result).__name__,
                )
                notify_error(
                    f"RSI-rev open {symbol}",
                    "Exchange order returned no orderId — lot not saved",
                )
                return "error"
            fill = resolve_order_fill(symbol, result, fallback_price=trigger.entry)
            if fill <= 0:
                logging.warning("  [%s] RSI-rev open skipped persist — fill=%.6f", symbol, fill)
                return "error"
            fee_open = resolve_order_commission(order_id)
            lot_id = store.insert_lot(
                symbol=symbol,
                side=trigger.side,
                zone=trigger.zone,
                anchor_ts=trigger.anchor_ts,
                anchor_price=trigger.anchor_price,
                anchor_rsi=trigger.anchor_rsi,
                entry=fill,
                tp=trigger.tp,
                size=size,
                margin_usdt=margin,
                notional_usdt=notional,
                entry_order_id=order_id,
                client_oid=str(result.get("clientOid") or result.get("client_oid") or ""),
                signal_ts=trigger.signal_ts,
                fee_open_usdt=fee_open,
            )
            zone_label = ZONE_LABELS.get(trigger.zone, trigger.zone)
            notify_rsi_rev_open(
                symbol,
                trigger.side,
                zone=zone_label,
                anchor=trigger.anchor_price,
                entry=fill,
                tp=trigger.tp,
                size=size,
                margin_usdt=margin,
            )
            logging.info(
                "  [%s] RSI-rev %s opened id=%s zone=%s entry=%.6f anchor=%.6f TP=%.6f "
                "size=%s margin=%.2f",
                symbol,
                trigger.side.upper(),
                lot_id,
                trigger.zone,
                fill,
                trigger.anchor_price,
                trigger.tp,
                size_str,
                margin,
            )
            return "opened"
        except ExchangeClientError as exc:
            logging.warning("  [%s] RSI-rev open failed: %s", symbol, exc)
            notify_error(f"RSI-rev open {symbol}", str(exc))
            return "error"


def close_lot(lot, *, reason: str, close_price: float) -> bool:
    lot_id = int(lot["id"])
    symbol = str(lot["symbol"])
    side = str(lot["side"])
    entry = float(lot["entry"])
    tp = float(lot["tp"])
    size = float(lot["size"] or 0)
    anchor = float(lot["anchor_price"])
    zone = str(lot["zone"])
    opened_at = str(lot["opened_at"] or "")

    with _close_lock:
        current = store.get_lot(lot_id)
        if current is None or str(current["status"]) != "open":
            return False
        if not is_trading_enabled() or not has_credentials():
            return False
        try:
            spec = fetch_contract_spec(symbol)
            size_str = format_size(size, spec)
            result = close_position_side(symbol, side, size_str)
            fill = resolve_order_fill(symbol, result, fallback_price=close_price)
            close_oid = _real_order_id(result)
            fee_close = resolve_order_commission(close_oid) if close_oid else 0.0
        except ExchangeClientError as exc:
            logging.warning(
                "  [%s] RSI-rev close lot %s failed: %s",
                symbol,
                lot_id,
                exc,
            )
            notify_error(f"RSI-rev close {symbol}", str(exc))
            return False

        try:
            fee_open = float(current["fee_open_usdt"] or 0)
        except (KeyError, TypeError):
            fee_open = 0.0
        gross = realized_pnl(side, entry, fill, size)
        pnl = net_realized_pnl(gross, fee_open, fee_close)
        if not store.close_lot(
            lot_id,
            close_price=fill,
            close_reason=reason,
            pnl_usdt=pnl,
            pnl_gross_usdt=gross,
            fee_close_usdt=fee_close,
            close_order_id=close_oid,
        ):
            return False
        zone_label = ZONE_LABELS.get(zone, zone)
        reason_label = store.REASON_LABELS.get(reason, reason)
        notify_rsi_rev_close(
            symbol,
            side,
            reason=reason_label,
            zone=zone_label,
            anchor=anchor,
            entry=entry,
            tp=tp,
            close_price=fill,
            pnl_usdt=pnl,
            opened_at=opened_at,
        )
        logging.info(
            "  [%s] RSI-rev %s lot %s closed %s @ %.6f pnl=%.4f (gross=%.4f fee=%.4f)",
            symbol,
            side.upper(),
            lot_id,
            reason,
            fill,
            pnl,
            gross,
            fee_open + fee_close,
        )
        return True
