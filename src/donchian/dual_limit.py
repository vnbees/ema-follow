"""Dual limit entry: every Config A signal places a post-only LONG and a post-only SHORT.

Each side becomes its own lot once filled and takes profit at its own live band
(long = upper, short = lower) through the normal watcher. Unfilled orders are
cancelled after ENTRY_TTL_BARS closed bars. No lot cap; MAX_COINS caps distinct
coins holding lots or pending entries.

Rate limit: fills arrive over the user stream. REST is only used to place,
cancel on expiry, one bookTicker read per signal, and one GET /order when a
cancel reports the order as already gone.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from src.bot_state import is_trading_enabled
from src.exchange import (
    ExchangeClientError,
    configure_symbol_trading,
    fetch_contract_spec,
    has_credentials,
    notional_to_size,
)
from src.exchange.fills import commission_usdt_from_detail, parse_fill_price
from src.exchange.types import ContractSpec
from src.notify import notify_error
from src.signal_publish import publish_open
from src.donchian import store
from src.donchian.config import (
    ENTRY_TTL_BARS,
    INTERVAL,
    LEVERAGE,
    MAKER_FEE_RATE,
    MARGIN_CAP_PCT,
    MARGIN_MIN_USDT,
    MARGIN_PCT,
    MAX_COINS,
    SIZE_BY_RR,
    mark_untradable,
)

ENTRY_DUAL_LIMIT = "dual_limit"
SIDES = ("long", "short")

_TERMINAL = frozenset({"filled", "canceled", "cancelled", "expired", "rejected"})
_configured: set[str] = set()


def interval_ms() -> int:
    minutes = int(INTERVAL.rstrip("m").rstrip("h")) * (60 if INTERVAL.endswith("h") else 1)
    return minutes * 60 * 1000


@dataclass
class DualSignal:
    symbol: str
    trend: str
    trend_ts: int | None
    entry_ts: int
    close: float
    upper: float
    lower: float
    size_mult: float
    body_atr: float | None = None
    pot_rr: float | None = None
    atr: float | None = None
    why: str = ""


def side_bands(side: str, upper: float, lower: float) -> tuple[float, float]:
    """(tp band, opposite band) for one side."""
    return (upper, lower) if side == "long" else (lower, upper)


def side_pot_rr(side: str, entry: float, upper: float, lower: float) -> float:
    tp, opp = side_bands(side, upper, lower)
    dist_sl = abs(entry - opp)
    return abs(tp - entry) / dist_sl if dist_sl > 1e-12 else 0.0


def coin_slot_ok(symbol: str, active: set[str], max_coins: int = MAX_COINS) -> bool:
    """A coin already in use can stack more lots. A new coin needs a free slot."""
    if max_coins <= 0:
        return True
    return symbol.upper() in active or len(active) < max_coins


def expire_at_ms(entry_ts: int, ttl_bars: int = ENTRY_TTL_BARS) -> int:
    """Signal bar opens at entry_ts. The order lives through ttl_bars more closed bars."""
    return int(entry_ts) + (1 + ttl_bars) * interval_ms()


def margin_for(equity: float, size_mult: float) -> float:
    mult = min(2.0, max(0.5, float(size_mult))) if SIZE_BY_RR else 1.0
    return min(max(0.0, equity * MARGIN_PCT * mult), equity * MARGIN_CAP_PCT)


def maker_prices(bid: float, ask: float, price_place: int) -> tuple[float, float]:
    """Long rests at bid, short at ask. Never let them meet when quotes collapse to one price."""
    tick = 10 ** (-price_place) if price_place > 0 else 1.0
    factor = 10 ** price_place
    long_px = math.floor(bid * factor + 1e-9) / factor
    short_px = math.ceil(ask * factor - 1e-9) / factor
    if short_px <= long_px:
        short_px = long_px + tick
    return long_px, short_px


def _quotes(symbol: str) -> tuple[float, float] | None:
    from src.exchange.binance import fetch_book_ticker

    book = fetch_book_ticker(symbol)
    if book is not None:
        return book
    from src.donchian.trading import _get_mark

    mark = _get_mark(symbol)
    if mark <= 0:
        return None
    return mark, mark


def _ensure_configured(symbol: str) -> None:
    sym = symbol.upper()
    if sym in _configured:
        return
    configure_symbol_trading(sym)
    _configured.add(sym)


def _place_side(
    sig: DualSignal,
    side: str,
    price: float,
    *,
    margin: float,
    size_mult: float,
    spec: ContractSpec,
    pair_key: str,
    expire_ms: int,
) -> bool:
    from src.exchange.binance import place_limit_open_order
    from src.exchange.sizing import format_price

    symbol = sig.symbol.upper()
    result = None
    for attempt in range(2):
        px_str = format_price(price, spec)
        size_str = notional_to_size(margin * LEVERAGE, float(px_str), spec)
        try:
            result = place_limit_open_order(symbol, side, size_str, px_str, post_only=True)
            break
        except ExchangeClientError as exc:
            if "-5022" in str(exc) and attempt == 0:
                quotes = _quotes(symbol)
                if quotes is None:
                    logging.info("  [%s] %s post-only rejected, no fresh quote", symbol, side.upper())
                    return False
                long_px, short_px = maker_prices(quotes[0], quotes[1], spec.price_place)
                price = long_px if side == "long" else short_px
                continue
            logging.warning("  [%s] Dual limit %s @ %s failed: %s", symbol, side.upper(), px_str, exc)
            if "-5022" not in str(exc):
                notify_error(f"Donchian dual limit {symbol}", str(exc), cooldown_sec=120)
            return False
    if not result or not result.get("orderId"):
        logging.warning("  [%s] Dual limit %s returned no orderId", symbol, side.upper())
        return False

    tp_band, opp_band = side_bands(side, sig.upper, sig.lower)
    entry_id = store.insert_entry_order(
        pair_key=pair_key,
        symbol=symbol,
        side=side,
        order_id=str(result["orderId"]),
        client_oid=str(result.get("clientOid") or ""),
        price=float(px_str),
        size=float(size_str),
        margin_usdt=margin,
        tp_band=tp_band,
        expire_ms=expire_ms,
        size_mult=size_mult,
        body_atr=sig.body_atr,
        pot_rr=side_pot_rr(side, sig.close, sig.upper, sig.lower),
        opp_band=opp_band,
        trend=sig.trend,
        trend_ts=sig.trend_ts,
        entry_ts=sig.entry_ts,
        entry_atr=sig.atr,
        why=sig.why,
    )
    logging.info(
        "  [%s] Dual limit %s placed id=%d order=%s @ %s size=%s margin=%.2f",
        symbol, side.upper(), entry_id, result["orderId"], px_str, size_str, margin,
    )
    _check_ws_detail(str(result["orderId"]))
    return True


def place_pair(sig: DualSignal, *, reserved_usdt: float = 0.0) -> tuple[str, float]:
    """Place both sides. Returns (status, margin committed).

    status: placed | coin_cap | cap_skip | rate_limited | error | disabled
    """
    if not is_trading_enabled() or not has_credentials():
        return "disabled", 0.0
    symbol = sig.symbol.upper()
    try:
        from src.exchange import binance as binance_mod

        if binance_mod.is_rate_limited():
            logging.info(
                "  [%s] Dual limit skipped — REST cooldown %.0fs",
                symbol, binance_mod.rate_limit_remaining_sec(),
            )
            return "rate_limited", 0.0
    except Exception:  # noqa: BLE001
        pass

    active = store.active_coins()
    if not coin_slot_ok(symbol, active):
        logging.info("  [%s] Dual limit skipped — %d/%d coins in use", symbol, len(active), MAX_COINS)
        store.record_skip(symbol, "max_coins")
        return "coin_cap", 0.0

    from src.donchian.trading import live_account_balance

    try:
        _ensure_configured(symbol)
        balance = live_account_balance(symbol)
        equity = float(balance.account_equity or 0)
        available = float(balance.available or 0) - max(0.0, reserved_usdt)
        if equity <= 0:
            store.record_skip(symbol, "no_equity")
            return "cap_skip", 0.0
        margin = margin_for(equity, sig.size_mult)
        mult = min(2.0, max(0.5, float(sig.size_mult))) if SIZE_BY_RR else 1.0
        if margin < MARGIN_MIN_USDT:
            logging.info("  [%s] Dual limit skipped — margin %.4f below min %.2f", symbol, margin, MARGIN_MIN_USDT)
            store.record_skip(symbol, "margin_too_small")
            return "cap_skip", 0.0
        if available < 2 * margin:
            logging.info(
                "  [%s] Dual limit skipped — available %.2f < 2 × margin %.2f", symbol, available, margin,
            )
            store.record_skip(symbol, "cap_skip")
            return "cap_skip", 0.0
        spec = fetch_contract_spec(symbol)
        quotes = _quotes(symbol)
        if quotes is None:
            store.record_skip(symbol, "no_price")
            return "error", 0.0
    except ExchangeClientError as exc:
        logging.warning("  [%s] Dual limit prepare failed: %s", symbol, exc)
        if "Contract spec not found" in str(exc):
            mark_untradable(symbol)
        return "error", 0.0

    long_px, short_px = maker_prices(quotes[0], quotes[1], spec.price_place)
    pair_key = f"{symbol}:{int(sig.entry_ts)}"
    expire_ms = expire_at_ms(sig.entry_ts)
    placed = 0
    for side, px in (("long", long_px), ("short", short_px)):
        if _place_side(
            sig,
            side,
            px,
            margin=margin,
            size_mult=mult,
            spec=spec,
            pair_key=pair_key,
            expire_ms=expire_ms,
        ):
            placed += 1
    if not placed:
        return "error", 0.0
    return "placed", margin * placed


def finalize_entry(row, *, status: str, filled_qty: float, avg_px: float, fee: float) -> int | None:
    """Close out a pending entry once. A fill (full or partial) becomes a lot. Returns lot id."""
    entry_id = int(row["id"])
    symbol = str(row["symbol"])
    side = str(row["side"])
    size = float(row["size"] or 0)
    qty = min(max(0.0, float(filled_qty or 0)), size) if size > 0 else max(0.0, float(filled_qty or 0))
    final = store.ENTRY_FILLED if status == "filled" else store.ENTRY_CANCELED
    px = float(avg_px or 0) if avg_px and float(avg_px) > 0 else float(row["price"])
    if not store.claim_entry(entry_id, status=final, filled_qty=qty, avg_px=px if qty > 0 else None):
        return None
    if qty <= 0:
        logging.info("  [%s] Dual limit %s entry %d %s unfilled", symbol, side.upper(), entry_id, status)
        return None

    notional = qty * px
    margin = notional / LEVERAGE if LEVERAGE > 0 else float(row["margin_usdt"] or 0)
    fee_open = float(fee or 0) if fee and float(fee) > 0 else notional * MAKER_FEE_RATE
    lot_id = store.insert_lot(
        symbol=symbol,
        side=side,
        trend=str(row["trend"] or side),
        trend_ts=row["trend_ts"],
        entry_ts=row["entry_ts"],
        entry_px=px,
        tp_band=float(row["tp_band"]),
        size=qty,
        margin_usdt=margin,
        notional_usdt=notional,
        entry_order_id=str(row["order_id"]),
        fee_open_usdt=fee_open,
        body_atr=row["body_atr"],
        pot_rr=row["pot_rr"],
        size_mult=row["size_mult"],
        opp_band=row["opp_band"],
        entry_rule=ENTRY_DUAL_LIMIT,
        entry_atr=row["entry_atr"],
    )
    store.set_entry_lot(entry_id, lot_id)
    partial = "" if final == store.ENTRY_FILLED else f" (khớp một phần {qty:g}/{size:g})"
    logging.info(
        "  [%s] Dual limit %s filled → lot %d entry=%.6f size=%g margin=%.2f%s",
        symbol, side.upper(), lot_id, px, qty, margin, partial,
    )
    try:
        from src.donchian.trading import _notify_open

        why = str(row["why"] or "")
        _notify_open(
            symbol,
            side,
            trend=str(row["trend"] or side),
            entry=px,
            tp_band=float(row["tp_band"]),
            size=qty,
            margin_usdt=margin,
            opp_band=row["opp_band"],
            body_atr=row["body_atr"],
            pot_rr=row["pot_rr"],
            size_mult=row["size_mult"],
            why=(why + partial) if partial else why,
            entry_rule=ENTRY_DUAL_LIMIT,
        )
    except Exception as exc:  # noqa: BLE001
        logging.debug("Dual limit notify skipped: %s", exc)
    opened = store.get_lot(lot_id)
    publish_open(
        lot_id=lot_id,
        symbol=symbol,
        side=side,
        trend=str(row["trend"] or side),
        entry=px,
        tp=float(row["tp_band"]),
        size_mult=row["size_mult"],
        opened_at=str(opened["opened_at"]) if opened else None,
    )
    return lot_id


def on_entry_order_update(detail: dict | None) -> None:
    """UDS ORDER_TRADE_UPDATE hook. Acts only on final states of our pending entries."""
    if not detail:
        return
    oid = str(detail.get("orderId") or "")
    status = str(detail.get("status") or detail.get("state") or "").lower()
    if not oid or status not in _TERMINAL:
        return
    row = store.find_pending_entry_by_order(oid)
    if row is None:
        return
    _finalize_from_detail(row, detail)


def _finalize_from_detail(row, detail: dict) -> int | None:
    status = str(detail.get("status") or detail.get("state") or "").lower()
    try:
        filled = float(detail.get("executedQty") or 0)
    except (TypeError, ValueError):
        filled = 0.0
    if status == "filled" and filled <= 0:
        filled = float(row["size"] or 0)
    avg = parse_fill_price(detail) or float(row["price"])
    fee = commission_usdt_from_detail(_ws_detail(str(row["order_id"])) or detail)
    return finalize_entry(row, status=status, filled_qty=filled, avg_px=avg, fee=fee)


def _ws_detail(order_id: str) -> dict | None:
    try:
        from src.exchange.binance_ws import get_order_detail_from_ws

        return get_order_detail_from_ws(order_id)
    except Exception:  # noqa: BLE001
        return None


def _check_ws_detail(order_id: str) -> None:
    detail = _ws_detail(order_id)
    if detail and str(detail.get("status") or "").lower() in _TERMINAL:
        on_entry_order_update(detail)


def poll_ws_fills() -> None:
    """Watcher tick: catch fills that landed before the DB row existed. Cache only, no REST."""
    for row in store.get_pending_entries():
        detail = _ws_detail(str(row["order_id"]))
        if detail and str(detail.get("status") or "").lower() in _TERMINAL:
            _finalize_from_detail(row, detail)


def expire_pending(now_ms: int) -> int:
    """Cancel entries past their bar budget. Returns how many reached a final state."""
    from src.exchange.binance import RateLimitError, cancel_limit_order_detail, fetch_order_full

    done = 0
    for row in store.get_pending_entries():
        if now_ms < int(row["expire_ms"] or 0):
            continue
        symbol = str(row["symbol"])
        oid = str(row["order_id"])
        detail = _ws_detail(oid)
        if detail and str(detail.get("status") or "").lower() in _TERMINAL:
            _finalize_from_detail(row, detail)
            done += 1
            continue
        try:
            result = cancel_limit_order_detail(symbol, oid)
            if result is None:
                detail = _ws_detail(oid)
                if detail and str(detail.get("status") or "").lower() in _TERMINAL:
                    result = detail
                else:
                    result = fetch_order_full(symbol, oid)
        except RateLimitError as exc:
            logging.info("  [%s] Dual limit expiry deferred: %s", symbol, exc)
            continue
        except ExchangeClientError as exc:
            logging.warning("  [%s] Dual limit cancel %s failed: %s", symbol, oid, exc)
            continue
        status = str(result.get("status") or "").lower()
        if status not in _TERMINAL:
            continue
        _finalize_from_detail(row, result)
        done += 1
    return done
