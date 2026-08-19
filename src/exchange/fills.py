from __future__ import annotations

import logging
import time

from src.exchange import ExchangeClientError, fetch_order_detail as exchange_fetch_order_detail
from src.exchange import fetch_side_mark_price
from src.notify import notify_error

_FILL_WS_ATTEMPTS = 5
_FILL_WS_DELAY_SEC = 0.1
_FILL_REST_ATTEMPTS = 1


def parse_fill_price(detail: dict) -> float | None:
    raw = (
        detail.get("priceAvg")
        or detail.get("price_avg")
        or detail.get("averagePrice")
        or detail.get("avgPrice")
    )
    if raw is None or raw == "":
        return None
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def commission_usdt_from_detail(detail: dict | None) -> float:
    if not detail:
        return 0.0
    try:
        fee = float(detail.get("commission") or 0)
    except (TypeError, ValueError):
        return 0.0
    return abs(fee) if fee else 0.0


def resolve_order_commission(order_id: str) -> float:
    """USDT commission accumulated on UDS ORDER_TRADE_UPDATE for this order."""
    oid = str(order_id or "")
    if not oid:
        return 0.0
    last = 0.0
    for attempt in range(_FILL_WS_ATTEMPTS):
        try:
            from src.exchange.binance_ws import get_order_detail_from_ws

            cached = get_order_detail_from_ws(oid)
        except Exception:  # noqa: BLE001
            cached = None
        last = commission_usdt_from_detail(cached)
        if last > 0:
            return last
        if attempt < _FILL_WS_ATTEMPTS - 1:
            time.sleep(_FILL_WS_DELAY_SEC)
    return last


def _fill_from_ws(order_id: str) -> float | None:
    try:
        from src.exchange.binance_ws import get_order_detail_from_ws

        cached = get_order_detail_from_ws(order_id)
    except Exception:  # noqa: BLE001
        return None
    if not cached:
        return None
    return parse_fill_price(cached)


def _optional_rest_blocked() -> bool:
    try:
        from src.exchange.binance import is_optional_rest_blocked

        return bool(is_optional_rest_blocked())
    except Exception:  # noqa: BLE001
        return False


def _uds_connected() -> bool:
    try:
        from src.exchange.binance_ws.cache import CACHE
        from src.exchange.binance_ws.manager import is_ws_enabled

        return bool(is_ws_enabled() and CACHE.user_connected)
    except Exception:  # noqa: BLE001
        return False


def resolve_order_fill(
    symbol: str,
    order_result: dict,
    *,
    fallback_price: float,
) -> float:
    """Resolve average fill price from order response, UDS, or mark — REST last."""
    parsed = parse_fill_price(order_result)
    if parsed is not None:
        return parsed

    order_id = str(
        order_result.get("orderId")
        or order_result.get("order_id")
        or "",
    )
    if not order_id:
        if fallback_price > 0:
            logging.warning(
                "  [%s] No orderId for fill resolution — using fallback %.6f",
                symbol,
                fallback_price,
            )
            notify_error(
                f"order fill {symbol}",
                f"No orderId — using fallback {fallback_price:.6f}",
            )
            return fallback_price
        return 0.0

    for attempt in range(_FILL_WS_ATTEMPTS):
        ws_fill = _fill_from_ws(order_id)
        if ws_fill is not None:
            return ws_fill
        if attempt < _FILL_WS_ATTEMPTS - 1:
            time.sleep(_FILL_WS_DELAY_SEC)

    rest_blocked = _optional_rest_blocked()
    uds_up = _uds_connected()
    if rest_blocked:
        logging.info(
            "  [%s] Skip REST fill poll for order %s — ban/resume cooldown",
            symbol,
            order_id,
        )
    elif uds_up:
        logging.debug(
            "  [%s] Skip REST fill poll for order %s — UDS connected, using mark fallback",
            symbol,
            order_id,
        )
    else:
        for attempt in range(_FILL_REST_ATTEMPTS):
            try:
                detail = exchange_fetch_order_detail(symbol, order_id)
                parsed = parse_fill_price(detail)
                if parsed is not None:
                    return parsed
                state = (detail.get("state") or detail.get("status") or "").lower()
                if state in {"filled", "partially_filled", "partial-fill"}:
                    break
            except ExchangeClientError as exc:
                logging.warning(
                    "  [%s] Fill poll %d failed for order %s: %s",
                    symbol,
                    attempt + 1,
                    order_id,
                    exc,
                )
                notify_error(
                    f"order fill {symbol}",
                    f"Fill poll failed for order {order_id}: {exc}",
                )

    try:
        mark = fetch_side_mark_price(symbol)
        if mark > 0:
            logging.warning(
                "  [%s] Order %s fill unknown — using mark fallback %.6f",
                symbol,
                order_id,
                mark,
            )
            return mark
    except ExchangeClientError:
        pass

    if fallback_price > 0:
        logging.warning(
            "  [%s] Order %s fill unknown — using price fallback %.6f",
            symbol,
            order_id,
            fallback_price,
        )
        notify_error(
            f"order fill {symbol}",
            f"Order {order_id} fill unknown — using price fallback {fallback_price:.6f}",
        )
        return fallback_price
    return 0.0
