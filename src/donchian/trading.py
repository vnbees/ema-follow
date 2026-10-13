from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

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
from src.exchange.fills import commission_usdt_from_detail, parse_fill_price
from src.exchange.sizing import format_price, format_size
from src.notify import notify_error
from src.signal_publish import publish_close, publish_open
from src.donchian import store
from src.donchian.config import (
    BALANCE_CACHE_MAX_AGE_SEC,
    LEVERAGE,
    MARGIN_CAP_PCT,
    MARGIN_MIN_USDT,
    MARGIN_PCT,
    MAX_BODY_ATR,
    MAX_OPEN,
    MIN_BODY_ATR,
    MIN_POT_RR,
    SIZE_BY_RR,
    STALE_OPP_HOURS,
    mark_untradable,
)
from src.donchian.favorable_add import ENTRY_FAVORABLE_ADD
from src.donchian.stale_opposite import ENTRY_STALE_OPPOSITE, ENTRY_STANDARD, qualifies_stale_opposite

_open_lock = threading.Lock()
_close_lock = threading.Lock()
_tp_retry_after: dict[int, tuple[float, float]] = {}
_TP_PLACE_BACKOFF_SEC = 60.0
_partial_lock = threading.Lock()
_partial_tp_orders: set[str] = set()


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


def _notify_open(
    symbol: str,
    side: str,
    *,
    trend: str,
    entry: float,
    tp_band: float,
    size: float,
    margin_usdt: float,
    equity: float = 0.0,
    opp_band: float | None = None,
    body_atr: float | None = None,
    pot_rr: float | None = None,
    size_mult: float | None = None,
    why: str = "",
    entry_rule: str = ENTRY_STANDARD,
) -> None:
    try:
        from src.notify import discord_configured, _send_discord, _fmt_px
        if not discord_configured():
            return
        rule = entry_rule == ENTRY_STALE_OPPOSITE
        add = entry_rule == ENTRY_FAVORABLE_ADD
        title = f"{symbol.upper()} {side.upper()} mở — trend {trend.upper()}"
        if rule:
            title = f"{symbol.upper()} {side.upper()} mở — quy tắc ngược"
        elif add:
            title = f"{symbol.upper()} {side.upper()} mở — lệnh 2"
        elif entry_rule == "dual_limit":
            title = f"{symbol.upper()} {side.upper()} khớp limit — vào 2 chiều (trend {trend.upper()})"
        lines: list[str] = []
        if rule:
            lines.append(
                f"quy tắc mới: vào ngược chiều vì lệnh cùng coin đang lỗ sau ≥ {STALE_OPP_HOURS:g} giờ"
            )
        if why:
            lines.append(f"lý do: {why}")
        if body_atr is not None and pot_rr is not None:
            lines.append(
                f"lọc: body_atr={body_atr:.2f} (ok {MIN_BODY_ATR:g}–{MAX_BODY_ATR:g}) · "
                f"pot_rr={pot_rr:.2f} (≥{MIN_POT_RR:g})"
            )
        if size_mult is not None and not add:
            base_pct = MARGIN_PCT * 100.0
            lines.append(
                f"size: size_mult={size_mult:.2f}× · margin_pct={base_pct:.2f}% "
                f"→ margin={margin_usdt:.2f} USDT"
                + (f" (equity≈{equity:.0f})" if equity > 0 else "")
            )
        elif add:
            lines.append(f"size: margin={margin_usdt:.2f} USDT (copy lệnh 1)")
        lines.append(f"entry={_fmt_px(entry)}")
        opp_part = f" · opp band={_fmt_px(opp_band)}" if opp_band is not None else ""
        lines.append(f"target band={_fmt_px(tp_band)}{opp_part}")
        lines.append(f"size={size:g}")
        _send_discord(title, "\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logging.warning("Discord notify_open failed: %s", exc)


def _notify_close(
    symbol: str,
    side: str,
    *,
    reason: str,
    entry: float,
    tp_band: float,
    close_price: float,
    pnl_usdt: float,
    opened_at: str = "",
    size_mult: float | None = None,
    entry_rule: str = ENTRY_STANDARD,
) -> None:
    try:
        from src.notify import discord_configured, _send_discord, _fmt_px, _hold_label
        if not discord_configured():
            return
        reason_label = store.REASON_LABELS.get(reason, reason)
        rule = entry_rule == ENTRY_STALE_OPPOSITE
        title = f"{symbol.upper()} {side.upper()} đóng — {reason_label}"
        if rule:
            title = f"{symbol.upper()} {side.upper()} đóng — {reason_label} · quy tắc ngược"
        body = (
            f"entry={_fmt_px(entry)}  target band={_fmt_px(tp_band)}\n"
            f"đóng={_fmt_px(close_price)}\n"
            f"pnl={pnl_usdt:+.2f} USDT (đã trừ phí)\n"
            f"giữ {_hold_label(opened_at)}"
        )
        if size_mult is not None:
            body += f"\nsize_mult={float(size_mult):.2f}×"
        if rule:
            body += "\nquy tắc mới: lệnh này được vào ngược chiều (lệnh cũ cùng coin đang lỗ ≥ 6 giờ)"
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
    size_mult: float = 1.0,
    opp_band: float | None = None,
    body_atr: float | None = None,
    pot_rr: float | None = None,
    why: str = "",
    entry_atr: float | None = None,
    add_on: bool = False,
    margin_fixed: float | None = None,
) -> str:
    """Open a Donchian lot. Returns opened | cap_skip | error | disabled."""
    if not is_trading_enabled() or not has_credentials():
        return "disabled"

    with _open_lock:
        open_rows = store.get_open_lots(symbol)
        entry_rule = ENTRY_FAVORABLE_ADD if add_on else ENTRY_STANDARD
        if open_rows and not add_on:
            if any(str(row["side"]).lower() == side.lower() for row in open_rows):
                logging.info("  [%s] Skip Donchian — already open %s", symbol, side)
                return "hold_wait"
            mark_px = _get_mark(symbol)
            if STALE_OPP_HOURS > 0 and qualifies_stale_opposite(
                open_rows,
                side,
                mark_px,
                datetime.now(timezone.utc),
                min_hours=STALE_OPP_HOURS,
            ):
                entry_rule = ENTRY_STALE_OPPOSITE
                logging.info(
                    "  [%s] Stale-opposite %s allowed — open lot losing ≥ %.0fh",
                    symbol,
                    side,
                    STALE_OPP_HOURS,
                )
            else:
                logging.info("  [%s] Skip opposite %s — open lot not stale-red", symbol, side)
                return "hold_wait"

        if store.count_open() >= MAX_OPEN:
            logging.info("  [%s] Skip Donchian — max open %d reached", symbol, MAX_OPEN)
            store.record_skip(symbol, "max_open")
            return "hold_wait" if entry_rule == ENTRY_STALE_OPPOSITE else "cap_skip"

        try:
            configure_symbol_trading(symbol)
            balance = live_account_balance(symbol)
            equity = float(balance.account_equity or 0)
            available = float(balance.available or 0)
            if equity <= 0:
                logging.warning("  [%s] Skip Donchian — equity=%.4f", symbol, equity)
                store.record_skip(symbol, "no_equity")
                return "cap_skip"

            mult = 1.0
            if add_on and margin_fixed is not None:
                margin = float(margin_fixed)
                mult = float(size_mult)
            else:
                if SIZE_BY_RR:
                    mult = min(2.0, max(0.5, float(size_mult)))
                margin = max(0.0, equity * MARGIN_PCT * mult)
                margin = min(margin, equity * MARGIN_CAP_PCT)
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
                body_atr=body_atr,
                pot_rr=pot_rr,
                size_mult=mult,
                opp_band=opp_band,
                entry_rule=entry_rule,
                entry_atr=entry_atr,
            )
            _notify_open(
                symbol,
                side,
                trend=trend,
                entry=fill,
                tp_band=tp_band,
                size=size,
                margin_usdt=margin,
                equity=equity,
                opp_band=opp_band,
                body_atr=body_atr,
                pot_rr=pot_rr,
                size_mult=mult,
                why=why,
                entry_rule=entry_rule,
            )
            opened_row = store.get_lot(lot_id)
            publish_open(
                lot_id=lot_id,
                symbol=symbol,
                side=side,
                trend=trend,
                entry=fill,
                tp=tp_band,
                size_mult=mult,
                opened_at=str(opened_row["opened_at"]) if opened_row else None,
            )
            logging.info(
                "  [%s] Donchian %s opened id=%d trend=%s entry=%.6f tp_band=%.6f "
                "size=%s margin=%.2f size_mult=%.2f body_atr=%s pot_rr=%s",
                symbol,
                side.upper(),
                lot_id,
                trend,
                fill,
                tp_band,
                size_str,
                margin,
                mult,
                f"{body_atr:.2f}" if body_atr is not None else "-",
                f"{pot_rr:.2f}" if pot_rr is not None else "-",
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


def _tp_held_size(symbol: str, side: str) -> float | None:
    """Position size the shared TP must cover: WS hedge size, else the sum of open DB lots on that side."""
    held = _held_size(symbol, side)
    if held is not None:
        return held
    total = sum(
        float(row["size"] or 0)
        for row in store.get_open_lots(symbol)
        if str(row["side"]).lower() == side.lower()
    )
    return total if total > 0 else None


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
    size_mult_meta = None
    try:
        raw_sm = lot["size_mult"]
        if raw_sm is not None:
            size_mult_meta = float(raw_sm)
    except (KeyError, IndexError, TypeError):
        pass
    entry_rule = ENTRY_STANDARD
    try:
        if "entry_rule" in current.keys() and current["entry_rule"]:
            entry_rule = str(current["entry_rule"])
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    _notify_close(
        symbol,
        side,
        reason=reason,
        entry=entry,
        tp_band=tp_band,
        close_price=fill,
        pnl_usdt=pnl,
        opened_at=opened_at,
        size_mult=size_mult_meta,
        entry_rule=entry_rule,
    )
    margin_meta = None
    try:
        raw_m = lot["margin_usdt"]
        if raw_m is not None:
            margin_meta = float(raw_m)
    except (KeyError, IndexError, TypeError):
        pass
    publish_close(
        lot_id=lot_id,
        close_px=fill,
        pnl_usdt=pnl,
        margin_usdt=margin_meta,
        close_reason=reason,
    )
    logging.info(
        "  [%s] Donchian %s lot %d closed %s @ %.6f pnl=%.4f fee=%.4f",
        symbol, side.upper(), lot_id, reason, fill, pnl, fee_open + fee_close,
    )
    return True


def _tp_prices_match(old_px: float | None, new_px: float, price_place: int) -> bool:
    if old_px is None:
        return False
    tick = 10 ** (-price_place) if price_place > 0 else 1e-8
    return abs(float(old_px) - float(new_px)) <= tick / 2


def _row_text(row, key: str) -> str:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return ""
    return str(value or "")


def _is_partial_tp(order_id: str) -> bool:
    with _partial_lock:
        return order_id in _partial_tp_orders


def _mark_partial_tp(order_id: str, partial: bool) -> None:
    with _partial_lock:
        if partial:
            _partial_tp_orders.add(order_id)
        else:
            _partial_tp_orders.discard(order_id)


def _limit_fill_price(detail: dict, fallback: float) -> float:
    parsed = parse_fill_price(detail)
    if parsed is not None:
        return parsed
    return fallback if fallback > 0 else 0.0


def _fee_from_order(order_id: str, detail: dict) -> float:
    try:
        from src.exchange.binance_ws import get_order_detail_from_ws

        cached = get_order_detail_from_ws(order_id)
    except Exception:  # noqa: BLE001
        cached = None
    fee = commission_usdt_from_detail(cached)
    if fee > 0:
        return fee
    return commission_usdt_from_detail(detail)


def _close_limit_side(hold_side: str) -> str:
    return "sell" if str(hold_side).lower() == "long" else "buy"


def _matching_close_limit(symbol: str, side: str, px: float, price_place: int, *, rest: bool):
    """A resting close limit at this price. REST only after a place was rejected.

    Hedge mode: a short entry limit is also a SELL, so positionSide must match and
    our own pending entry orders are never adopted as TP.
    """
    from src.exchange.binance import fetch_open_limit_orders_rest, fetch_pending_orders
    from src.exchange.types import PendingOrder

    want = _close_limit_side(side)
    entry_ids = store.pending_entry_order_ids()
    orders: list[PendingOrder]
    if rest:
        orders = fetch_open_limit_orders_rest(symbol)
    else:
        orders = fetch_pending_orders(symbol)
    for order in orders:
        if str(order.side).lower() != want or not order.order_id:
            continue
        if order.order_id in entry_ids:
            continue
        pos_side = str(getattr(order, "position_side", "") or "").lower()
        if pos_side and pos_side not in (side.lower(), "both"):
            continue
        if _tp_prices_match(order.price, px, price_place):
            return order
    return None


def _remember_tp(lot_id: int, order_id: str, px: float, *, adopted: bool, qty: float | None = None) -> None:
    store.set_tp_limit(lot_id, order_id, px, qty)
    _tp_retry_after.pop(lot_id, None)
    if adopted:
        logging.info("  TP limit adopted order %s @ %s", order_id, px)


_TP_DEAD = frozenset({"filled", "canceled", "cancelled", "expired", "rejected"})


def _tp_order_dead(order_id: str) -> bool:
    """UDS saw this TP order end. Unknown stays alive (no REST)."""
    try:
        from src.exchange.binance_ws import get_order_detail_from_ws

        detail = get_order_detail_from_ws(order_id)
    except Exception:  # noqa: BLE001
        return False
    if not detail:
        return False
    return str(detail.get("status") or "").lower() in _TP_DEAD


def _row_float_or_none(row, key: str) -> float | None:
    try:
        raw = row[key]
    except (KeyError, IndexError):
        return None
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def sync_tp_limit(lot, target_px: float) -> None:
    """Park a reduce-side GTC limit at the live Donchian band. Replace only when the price changes."""
    if target_px <= 0:
        return
    lot_id = int(lot["id"])
    with _close_lock:
        current = store.get_lot(lot_id)
        if current is None or str(current["status"]) != "open":
            return
        if not is_trading_enabled() or not has_credentials():
            return
        symbol = str(current["symbol"])
        side = str(current["side"])
        try:
            spec = fetch_contract_spec(symbol)
        except ExchangeClientError as exc:
            logging.warning("  [%s] TP limit spec failed: %s", symbol, exc)
            return
        px_str = format_price(target_px, spec)
        try:
            px = float(px_str)
        except ValueError:
            return
        if px <= 0:
            return
        retry = _tp_retry_after.get(lot_id)
        if retry is not None and time.monotonic() < retry[0] and _tp_prices_match(retry[1], px, spec.price_place):
            return
        old_id = _row_text(current, "tp_limit_order_id")
        old_px_raw = None
        try:
            old_px_raw = current["tp_limit_px"]
        except (KeyError, IndexError):
            old_px_raw = None
        old_px = float(old_px_raw) if old_px_raw not in (None, "") else None
        if old_id and not _is_partial_tp(old_id) and _tp_order_dead(old_id):
            store.clear_tp_limit(lot_id)
            old_id = ""
        held = _tp_held_size(symbol, side)
        step = 10 ** (-spec.volume_place) if spec.volume_place > 0 else 1.0
        if old_id and _tp_prices_match(old_px, px, spec.price_place):
            # Same band, but another lot on this side may have grown the position.
            old_qty = _row_float_or_none(current, "tp_limit_qty")
            if old_qty is None:
                old_qty = float(current["size"] or 0)
            if held is None or held <= old_qty + step / 2:
                return
            if _is_partial_tp(old_id):
                return
            logging.info("  [%s] TP limit %s resize %g → %g", symbol, side.upper(), old_qty, held)
        if old_id and _is_partial_tp(old_id):
            return

        from src.exchange.binance import cancel_limit_order, place_limit_close_order

        siblings = [
            int(row["id"]) for row in store.find_open_lots_by_tp_order(old_id) if int(row["id"]) != lot_id
        ] if old_id else []
        if old_id:
            try:
                cancel_limit_order(symbol, old_id)
            except ExchangeClientError as exc:
                logging.warning("  [%s] TP limit cancel %s failed: %s", symbol, old_id, exc)
                return
            _mark_partial_tp(old_id, False)
            store.clear_tp_limit(lot_id)
            current = store.get_lot(lot_id)
            if current is None or str(current["status"]) != "open":
                return

        if held is not None and held <= 1e-12:
            return
        qty = held if held and held > 0 else float(current["size"] or 0)
        if qty <= 0:
            return
        size_str = format_size(qty, spec)
        resting = _matching_close_limit(symbol, side, px, spec.price_place, rest=False)
        if resting is not None:
            if held is None or float(resting.size or 0) + step / 2 >= held:
                _remember_tp(lot_id, resting.order_id, px, adopted=True, qty=float(resting.size or 0) or None)
                return
            try:
                cancel_limit_order(symbol, resting.order_id)
            except ExchangeClientError as exc:
                logging.warning("  [%s] TP limit cancel short %s failed: %s", symbol, resting.order_id, exc)
                return
            store.clear_tp_limit_by_order(resting.order_id)
        try:
            result = place_limit_close_order(symbol, side, size_str, px_str)
        except ExchangeClientError as exc:
            adopted = None
            if _already_flat_error(exc) or "-4116" in str(exc):
                adopted = _matching_close_limit(symbol, side, px, spec.price_place, rest=True)
            if adopted is not None:
                _remember_tp(lot_id, adopted.order_id, px, adopted=True, qty=float(adopted.size or 0) or None)
                return
            _tp_retry_after[lot_id] = (time.monotonic() + _TP_PLACE_BACKOFF_SEC, px)
            logging.warning("  [%s] TP limit place @ %s failed: %s", symbol, px_str, exc)
            notify_error(f"Donchian TP limit {symbol}", str(exc), cooldown_sec=120)
            return
        oid = _real_order_id(result)
        status = str(result.get("status") or "").lower()
        if status == "filled" and oid:
            fill = _limit_fill_price(result, px)
            logging.info("  [%s] TP limit %s filled immediately @ %s", symbol, side.upper(), px_str)
            _finalize_close(
                current,
                reason=store.REASON_TP,
                fill=fill if fill > 0 else px,
                fee_close=_fee_from_order(oid, result),
                close_oid=oid,
                opened_at=str(current["opened_at"] or ""),
            )
            return
        if not oid:
            logging.warning("  [%s] TP limit place returned no orderId", symbol)
            return
        store.set_tp_limit(lot_id, oid, px, float(size_str))
        for sibling_id in siblings:
            sibling = store.get_lot(sibling_id)
            if sibling is not None and str(sibling["status"]) == "open":
                store.set_tp_limit(sibling_id, oid, px, float(size_str))
        logging.info(
            "  [%s] TP limit %s @ %s%s",
            symbol,
            side.upper(),
            px_str,
            f" (was {old_px})" if old_px is not None else "",
        )


def on_tp_limit_order_update(detail: dict | None) -> None:
    """Close the lot when its resting TP limit fills. Clear the id if the order dies unfilled."""
    if not detail:
        return
    oid = str(detail.get("orderId") or "")
    if not oid:
        return
    status = str(detail.get("status") or detail.get("state") or "").lower()
    if status == "partially_filled":
        _mark_partial_tp(oid, True)
        return
    lots = store.find_open_lots_by_tp_order(oid)
    if not lots:
        return
    lot = lots[0]
    if status == "filled":
        _mark_partial_tp(oid, False)
        fill = _limit_fill_price(detail, float(lot["tp_limit_px"] or 0))
        if fill <= 0:
            fill = float(lot["entry_px"] or 0)
        with _close_lock:
            targets = [
                cur for cur in (store.get_lot(int(row["id"])) for row in lots)
                if cur is not None and str(cur["status"]) == "open"
            ]
            if not targets:
                return
            try:
                filled_qty = float(detail.get("executedQty") or 0)
            except (TypeError, ValueError):
                filled_qty = 0.0
            if filled_qty > 0:
                # Position-level TP: the fill also flattened same-side lots that had no TP id yet.
                taken = {int(cur["id"]) for cur in targets}
                others = [
                    row for row in store.get_open_lots(str(lot["symbol"]))
                    if str(row["side"]).lower() == str(lot["side"]).lower() and int(row["id"]) not in taken
                ]
                total = sum(float(row["size"] or 0) for row in targets + others)
                if others and filled_qty >= total * 0.999:
                    targets.extend(others)
                # An undersized shared TP only flattened part of the position: close what it covered.
                covered, remaining = [], filled_qty * 1.001
                for cur in sorted(targets, key=lambda r: int(r["id"])):
                    size = float(cur["size"] or 0)
                    if size <= remaining:
                        covered.append(cur)
                        remaining -= size
                    else:
                        store.clear_tp_limit(int(cur["id"]))
                if len(covered) < len(targets):
                    logging.warning(
                        "  [%s] TP %s filled %g < lots %g — closing %d/%d lots, rest re-arm TP",
                        lot["symbol"], oid, filled_qty,
                        sum(float(c["size"] or 0) for c in targets), len(covered), len(targets),
                    )
                targets = covered
                if not targets:
                    return
            fee_total = _fee_from_order(oid, detail)
            size_total = sum(float(cur["size"] or 0) for cur in targets) or 1.0
            for current in targets:
                share = float(current["size"] or 0) / size_total
                _finalize_close(
                    current,
                    reason=store.REASON_TP,
                    fill=fill,
                    fee_close=fee_total * share,
                    close_oid=oid,
                    opened_at=str(current["opened_at"] or ""),
                )
        return
    if status in {"canceled", "cancelled", "expired", "rejected"}:
        _mark_partial_tp(oid, False)
        store.clear_tp_limit_by_order(oid)


def reconcile_flat_lots() -> None:
    """If DB says open but exchange size is 0, close the lot (ghost after failed persist)."""
    for lot in store.get_open_lots():
        symbol = str(lot["symbol"])
        side = str(lot["side"])
        held = _held_size(symbol, side)
        if held is None:
            continue
        if held > 1e-12:
            continue
        # WS says flat — confirm via REST (or lenient WS fallback) before closing DB.
        try:
            from src.exchange.binance import RateLimitError, fetch_symbol_positions

            rest_sides = fetch_symbol_positions(symbol)
            rest_pos = rest_sides.get(side.lower())
            rest_held = abs(float(rest_pos.size or 0)) if rest_pos else 0.0
            if rest_held > 1e-12:
                continue
        except RateLimitError:
            continue
        except Exception:  # noqa: BLE001
            continue
        mark = _get_mark(symbol)
        fill = mark if mark > 0 else float(lot["entry_px"])
        oid = _row_text(lot, "tp_limit_order_id")
        if oid:
            try:
                from src.exchange.binance_ws import get_order_detail_from_ws

                cached = get_order_detail_from_ws(oid) or {}
                parsed = parse_fill_price(cached)
                if parsed is not None:
                    fill = parsed
            except Exception:  # noqa: BLE001
                pass
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
