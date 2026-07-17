import logging
import re
import threading
import time
import uuid
from typing import Any

import requests

from src.config import (
    BINANCE_API_BASE,
    BINANCE_API_KEY,
    BINANCE_SECRET_KEY,
    BINANCE_SPOT_API_BASE,
    CANDLE_LIMIT,
    GRANULARITY,
    LEVERAGE,
    MARGIN_COIN,
    MARGIN_MODE,
    SYMBOL,
)
from src.exchange.binance_auth import auth_headers, signed_params
from src.exchange.symbols import is_scan_symbol
from src.exchange.sizing import format_size, notional_to_size
from src.exchange.types import (
    Candle,
    ContractSpec,
    ExchangeClientError,
    FuturesAccountBalance,
    PendingOrder,
    Position,
)

_SPEC_CACHE: dict[str, ContractSpec] = {}
_EXCHANGE_INFO_CACHE: dict | None = None


def has_credentials() -> bool:
    return bool(BINANCE_API_KEY and BINANCE_SECRET_KEY)


def _ensure_credentials() -> None:
    if not has_credentials():
        raise ExchangeClientError("Missing Binance API credentials")


def _parse_api_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        return f"code={payload.get('code')} msg={payload.get('msg')}"
    except ValueError:
        return response.text[:200]


class RateLimitError(ExchangeClientError):
    """HTTP 429/418 from Binance — must NOT be retried."""


# Cooldown window: while active, all REST calls fail fast without hitting Binance.
_rate_limit_lock = threading.Lock()
_rate_limited_until_ms = 0.0


def _now_ms() -> float:
    return time.time() * 1000


def _set_rate_limited_until(until_ms: float) -> None:
    global _rate_limited_until_ms
    with _rate_limit_lock:
        if until_ms > _rate_limited_until_ms:
            _rate_limited_until_ms = until_ms
            logging.warning(
                "Binance rate limited — pausing REST calls for %.0fs",
                max(0.0, until_ms - _now_ms()) / 1000,
            )


def _check_rate_limit_pause() -> None:
    with _rate_limit_lock:
        until_ms = _rate_limited_until_ms
    if _now_ms() < until_ms:
        remaining = (until_ms - _now_ms()) / 1000
        raise RateLimitError(
            f"Rate-limit cooldown active — {remaining:.0f}s remaining, request skipped"
        )


def _handle_rate_limit_response(response: requests.Response) -> None:
    """Register cooldown for HTTP 429/418 and raise RateLimitError."""
    if response.status_code not in (429, 418):
        return
    detail = _parse_api_error(response)
    until_ms = 0.0
    match = re.search(r"banned until (\d{13})", detail)
    if match:
        until_ms = float(match.group(1))
    else:
        try:
            retry_after_sec = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry_after_sec = 0.0
        if retry_after_sec > 0:
            until_ms = _now_ms() + retry_after_sec * 1000
    if until_ms <= _now_ms():
        # No usable hint: back off for a full minute (weight window).
        until_ms = _now_ms() + 60_000
    _set_rate_limited_until(until_ms)
    raise RateLimitError(f"HTTP {response.status_code}: {detail}")


def _public_get(path: str, params: dict[str, str], max_retries: int = 3) -> Any:
    url = f"{BINANCE_API_BASE}{path}"
    last_error: Exception | None = None
    for attempt in range(max_retries):
        _check_rate_limit_pause()
        try:
            response = requests.get(url, params=params, timeout=10)
            _handle_rate_limit_response(response)
            if not response.ok:
                raise ExchangeClientError(f"HTTP {response.status_code}: {_parse_api_error(response)}")
            return response.json()
        except RateLimitError:
            raise
        except (requests.RequestException, ExchangeClientError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise ExchangeClientError(f"GET {path} failed after {max_retries} attempts: {last_error}")


def _private_request(
    method: str,
    path: str,
    params: dict[str, str | int | float | bool],
    max_retries: int = 3,
) -> Any:
    _ensure_credentials()
    url = f"{BINANCE_API_BASE}{path}"
    headers = auth_headers(BINANCE_API_KEY)
    last_error: Exception | None = None
    for attempt in range(max_retries):
        _check_rate_limit_pause()
        # Re-sign each attempt so timestamp stays inside recvWindow after backoff sleeps.
        signed = signed_params(BINANCE_API_KEY, BINANCE_SECRET_KEY, params)
        try:
            if method == "GET":
                response = requests.get(url, params=signed, headers=headers, timeout=10)
            else:
                response = requests.post(url, params=signed, headers=headers, timeout=10)
            _handle_rate_limit_response(response)
            if not response.ok:
                raise ExchangeClientError(f"HTTP {response.status_code}: {_parse_api_error(response)}")
            if not response.text:
                return {}
            return response.json()
        except RateLimitError:
            raise
        except (requests.RequestException, ExchangeClientError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise ExchangeClientError(f"{method} {path} failed after {max_retries} attempts: {last_error}")


def _private_get(path: str, params: dict[str, str | int | float | bool], max_retries: int = 3) -> Any:
    return _private_request("GET", path, params, max_retries=max_retries)


def _private_post(path: str, params: dict[str, str | int | float | bool], max_retries: int = 3) -> Any:
    return _private_request("POST", path, params, max_retries=max_retries)


def _spot_private_request(
    method: str,
    path: str,
    params: dict[str, str | int | float | bool],
    max_retries: int = 3,
) -> Any:
    _ensure_credentials()
    url = f"{BINANCE_SPOT_API_BASE}{path}"
    headers = auth_headers(BINANCE_API_KEY)
    last_error: Exception | None = None
    for attempt in range(max_retries):
        _check_rate_limit_pause()
        signed = signed_params(BINANCE_API_KEY, BINANCE_SECRET_KEY, params)
        try:
            if method == "GET":
                response = requests.get(url, params=signed, headers=headers, timeout=10)
            else:
                response = requests.post(url, params=signed, headers=headers, timeout=10)
            _handle_rate_limit_response(response)
            if not response.ok:
                raise ExchangeClientError(f"HTTP {response.status_code}: {_parse_api_error(response)}")
            if not response.text:
                return {}
            return response.json()
        except RateLimitError:
            raise
        except (requests.RequestException, ExchangeClientError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise ExchangeClientError(f"{method} {path} failed after {max_retries} attempts: {last_error}")


def transfer_futures_to_spot(asset: str, amount: float) -> dict:
    """Transfer from USDT-M futures wallet to spot (UMFUTURE_MAIN)."""
    if amount <= 0:
        raise ExchangeClientError("Transfer amount must be positive")
    result = _spot_private_request(
        "POST",
        "/sapi/v1/asset/transfer",
        {
            "type": "UMFUTURE_MAIN",
            "asset": asset.upper(),
            "amount": amount,
        },
    )
    return {
        "tranId": str(result.get("tranId", "")),
        "clientOid": str(result.get("clientTranId", "") or result.get("tranId", "")),
        "raw": result,
    }


def fetch_spot_balance(asset: str = "USDT") -> float:
    asset = asset.upper()
    rows = _spot_private_request(
        "POST",
        "/sapi/v3/asset/getUserAsset",
        {"asset": asset},
    )
    if isinstance(rows, list):
        for row in rows:
            if str(row.get("asset", "")).upper() == asset:
                free = float(row.get("free") or 0)
                locked = float(row.get("locked") or 0)
                return free + locked
        return 0.0
    if isinstance(rows, dict):
        free = float(rows.get("free") or 0)
        locked = float(rows.get("locked") or 0)
        return free + locked
    return 0.0


def _decimals_from_step(step: str) -> int:
    if not step or "e" in step.lower():
        return 8
    if "." not in step:
        return 0
    trimmed = step.rstrip("0")
    if trimmed.endswith("."):
        return 0
    return len(trimmed.split(".")[1])


def _load_exchange_info() -> dict:
    global _EXCHANGE_INFO_CACHE
    if _EXCHANGE_INFO_CACHE is None:
        _EXCHANGE_INFO_CACHE = _public_get("/fapi/v1/exchangeInfo", {})
    return _EXCHANGE_INFO_CACHE


def _parse_contract_spec(symbol: str, info: dict) -> ContractSpec:
    for item in info.get("symbols", []):
        if item.get("symbol") != symbol:
            continue
        if item.get("contractType") != "PERPETUAL":
            continue
        if item.get("status") != "TRADING":
            continue
        lot_step = "0.001"
        min_qty = 0.0
        min_notional = 5.0
        tick_size = "0.01"
        for filt in item.get("filters", []):
            ftype = filt.get("filterType")
            if ftype == "LOT_SIZE":
                lot_step = str(filt.get("stepSize", lot_step))
                min_qty = float(filt.get("minQty", 0))
            elif ftype == "MIN_NOTIONAL":
                min_notional = float(filt.get("notional", min_notional))
            elif ftype == "PRICE_FILTER":
                tick_size = str(filt.get("tickSize", tick_size))
        volume_place = _decimals_from_step(lot_step)
        price_place = _decimals_from_step(tick_size)
        return ContractSpec(
            symbol=symbol,
            volume_place=volume_place,
            price_place=price_place,
            min_trade_num=min_qty,
            min_trade_usdt=min_notional,
            size_multiplier=float(lot_step),
        )
    raise ExchangeClientError(f"Contract spec not found for {symbol}")


def fetch_contract_spec(symbol: str) -> ContractSpec:
    symbol = symbol.upper()
    if symbol not in _SPEC_CACHE:
        _SPEC_CACHE[symbol] = _parse_contract_spec(symbol, _load_exchange_info())
    return _SPEC_CACHE[symbol]


def fetch_candles(
    symbol: str = SYMBOL,
    *,
    granularity: str = GRANULARITY,
    limit: int = CANDLE_LIMIT,
    max_retries: int = 3,
) -> list[Candle]:
    data = _public_get(
        "/fapi/v1/klines",
        {
            "symbol": symbol.upper(),
            "interval": granularity,
            "limit": str(limit),
        },
        max_retries=max_retries,
    )
    candles = [
        Candle(
            timestamp=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in data
    ]
    candles.sort(key=lambda c: c.timestamp)
    return candles


def fetch_top_futures_by_volume(limit: int | None = None) -> list[tuple[str, float]]:
    info = _load_exchange_info()
    trading_perps = {
        item["symbol"]
        for item in info.get("symbols", [])
        if item.get("contractType") == "PERPETUAL"
        and item.get("status") == "TRADING"
        and item.get("quoteAsset") == "USDT"
        and item.get("marginAsset") == "USDT"
        and is_scan_symbol(str(item.get("symbol", "")))
    }
    tickers = _public_get("/fapi/v1/ticker/24hr", {})
    ranked: list[tuple[str, float]] = []
    for item in tickers:
        symbol = str(item.get("symbol", "")).upper()
        if symbol not in trading_perps:
            continue
        try:
            volume = float(item.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            volume = 0.0
        if volume <= 0:
            continue
        ranked.append((symbol, volume))
    ranked.sort(key=lambda row: row[1], reverse=True)
    if limit is None:
        return ranked
    return ranked[:limit]


def fetch_futures_balance(symbol: str = SYMBOL) -> FuturesAccountBalance:
    _ = symbol
    data = _private_get("/fapi/v2/account", {})
    available = float(data.get("availableBalance", 0))
    equity = float(data.get("totalMarginBalance", data.get("totalWalletBalance", 0)))
    maint = float(data.get("totalMaintMargin", 0) or 0)
    initial = float(data.get("totalInitialMargin", 0) or 0)
    return FuturesAccountBalance(
        margin_coin=MARGIN_COIN,
        available=available,
        account_equity=equity,
        usdt_equity=equity,
        total_maint_margin=maint,
        total_initial_margin=initial,
    )


def _empty_position(symbol: str) -> Position:
    return Position(symbol=symbol, side=None, size=0.0, avg_price=0.0)


def _parse_position_row(symbol: str, row: dict) -> Position | None:
    position_side = str(row.get("positionSide", "")).upper()
    if position_side not in ("LONG", "SHORT"):
        return None
    size = abs(float(row.get("positionAmt", 0) or 0))
    if size <= 0:
        return None
    side = position_side.lower()
    return Position(
        symbol=symbol,
        side=side,
        size=size,
        avg_price=float(row.get("entryPrice", 0) or 0),
    )


def fetch_symbol_positions(symbol: str) -> dict[str, Position]:
    symbol = symbol.upper()
    rows = _private_get("/fapi/v2/positionRisk", {"symbol": symbol})
    result = {
        "long": _empty_position(symbol),
        "short": _empty_position(symbol),
    }
    if not isinstance(rows, list):
        return result
    for row in rows:
        pos = _parse_position_row(symbol, row)
        if pos and pos.side in result:
            result[pos.side] = pos
    return result


def fetch_all_open_positions() -> list[Position]:
    rows = _private_get("/fapi/v2/positionRisk", {})
    positions: list[Position] = []
    if not isinstance(rows, list):
        return positions
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        pos = _parse_position_row(symbol, row)
        if pos:
            positions.append(pos)
    return positions


def fetch_side_mark_price(symbol: str) -> float:
    data = _public_get("/fapi/v1/premiumIndex", {"symbol": symbol.upper()})
    if isinstance(data, list):
        data = data[0] if data else {}
    return float(data.get("markPrice", 0) or 0)


def fetch_side_unrealized_pnl(symbol: str, hold_side: str) -> float:
    symbol = symbol.upper()
    hold_side = hold_side.lower()
    rows = _private_get("/fapi/v2/positionRisk", {"symbol": symbol})
    if not isinstance(rows, list):
        return 0.0
    for row in rows:
        pos_side = str(row.get("positionSide", "")).lower()
        if pos_side != hold_side:
            continue
        size = abs(float(row.get("positionAmt", 0) or 0))
        if size <= 0:
            return 0.0
        pnl = float(row.get("unRealizedProfit", 0) or 0)
        if pnl != 0:
            return pnl
        mark = float(row.get("markPrice", 0) or 0)
        entry = float(row.get("entryPrice", 0) or 0)
        if mark <= 0 or entry <= 0:
            return 0.0
        if hold_side == "long":
            return (mark - entry) * size
        return (entry - mark) * size
    return 0.0


def fetch_total_unrealized_pnl(symbols: list[str]) -> tuple[float, int]:
    total = 0.0
    open_count = 0
    for symbol in symbols:
        try:
            positions = fetch_symbol_positions(symbol)
            for hold_side in ("long", "short"):
                if positions[hold_side].size <= 0:
                    continue
                total += fetch_side_unrealized_pnl(symbol, hold_side)
                open_count += 1
        except ExchangeClientError:
            continue
    return total, open_count


def fetch_order_detail(symbol: str, order_id: str) -> dict:
    data = _private_get(
        "/fapi/v1/order",
        {"symbol": symbol.upper(), "orderId": order_id},
    )
    status = str(data.get("status", "")).lower()
    avg_price = data.get("avgPrice")
    return {
        "orderId": str(data.get("orderId", order_id)),
        "status": status,
        "state": status,
        "avgPrice": avg_price,
        "priceAvg": avg_price,
    }


def fetch_pending_orders(symbol: str) -> list[PendingOrder]:
    rows = _private_get("/fapi/v1/openOrders", {"symbol": symbol.upper()})
    if not isinstance(rows, list):
        return []
    orders: list[PendingOrder] = []
    for item in rows:
        if str(item.get("type", "")).upper() != "LIMIT":
            continue
        orders.append(
            PendingOrder(
                order_id=str(item.get("orderId", "")),
                client_oid=str(item.get("clientOrderId", "")),
                side=str(item.get("side", "")).lower(),
                price=float(item.get("price", 0)),
                size=float(item.get("origQty", 0)),
            )
        )
    return orders


def _ignore_config_error(exc: ExchangeClientError, *codes: str) -> None:
    msg = str(exc)
    for code in codes:
        if code in msg:
            return
    lower = msg.lower()
    if "no need to change" in lower:
        return
    raise exc


def set_dual_side_position() -> None:
    try:
        _private_post("/fapi/v1/positionSide/dual", {"dualSidePosition": "true"})
    except ExchangeClientError as exc:
        _ignore_config_error(exc, "-4059", "-4061")


def set_margin_type(symbol: str) -> None:
    margin_type = "CROSSED" if MARGIN_MODE.lower() in ("crossed", "cross") else "ISOLATED"
    try:
        _private_post(
            "/fapi/v1/marginType",
            {"symbol": symbol.upper(), "marginType": margin_type},
        )
    except ExchangeClientError as exc:
        _ignore_config_error(exc, "-4046", "-4047")


def set_leverage(symbol: str, leverage: int = LEVERAGE) -> None:
    try:
        _private_post(
            "/fapi/v1/leverage",
            {"symbol": symbol.upper(), "leverage": leverage},
        )
    except ExchangeClientError as exc:
        _ignore_config_error(exc, "-4028")


def configure_symbol_trading(symbol: str) -> None:
    set_dual_side_position()
    set_margin_type(symbol)
    set_leverage(symbol)


def market_order_params(hold_side: str, trade_side: str) -> tuple[str, str]:
    """Return (side, positionSide) for Binance hedge mode."""
    hold_side = hold_side.lower()
    trade_side = trade_side.lower()
    if hold_side == "long":
        return ("BUY", "LONG") if trade_side == "open" else ("SELL", "LONG")
    return ("SELL", "SHORT") if trade_side == "open" else ("BUY", "SHORT")


def place_market_order(
    symbol: str,
    side: str,
    size: str,
    *,
    hold_side: str | None = None,
    trade_side: str | None = None,
    reduce_only: bool = False,
) -> dict:
    client_oid = f"bot_{uuid.uuid4().hex[:16]}"
    params: dict[str, str | int | float | bool] = {
        "symbol": symbol.upper(),
        "type": "MARKET",
        "quantity": size,
        "newClientOrderId": client_oid,
    }
    if hold_side and trade_side:
        order_side, position_side = market_order_params(hold_side, trade_side)
        params["side"] = order_side
        params["positionSide"] = position_side
        # Hedge mode: close via side + positionSide; reduceOnly is one-way only.
    else:
        params["side"] = side.upper()
        if reduce_only:
            params["reduceOnly"] = "true"
    result = _private_post("/fapi/v1/order", params)
    return {
        "orderId": str(result.get("orderId", "")),
        "clientOid": str(result.get("clientOrderId", client_oid)),
        "avgPrice": result.get("avgPrice"),
        "status": str(result.get("status", "")).lower(),
    }


def close_position_side(symbol: str, hold_side: str, size: str) -> dict:
    return place_market_order(
        symbol,
        "",
        size,
        hold_side=hold_side,
        trade_side="close",
        reduce_only=True,
    )


class BinanceExchange:
    has_credentials = staticmethod(has_credentials)
    fetch_candles = staticmethod(fetch_candles)
    fetch_top_futures_by_volume = staticmethod(fetch_top_futures_by_volume)
    fetch_contract_spec = staticmethod(fetch_contract_spec)
    fetch_futures_balance = staticmethod(fetch_futures_balance)
    fetch_symbol_positions = staticmethod(fetch_symbol_positions)
    fetch_all_open_positions = staticmethod(fetch_all_open_positions)
    fetch_side_mark_price = staticmethod(fetch_side_mark_price)
    fetch_side_unrealized_pnl = staticmethod(fetch_side_unrealized_pnl)
    fetch_total_unrealized_pnl = staticmethod(fetch_total_unrealized_pnl)
    fetch_pending_orders = staticmethod(fetch_pending_orders)
    fetch_order_detail = staticmethod(fetch_order_detail)
    configure_symbol_trading = staticmethod(configure_symbol_trading)
    place_market_order = staticmethod(place_market_order)
    close_position_side = staticmethod(close_position_side)
    transfer_futures_to_spot = staticmethod(transfer_futures_to_spot)
    fetch_spot_balance = staticmethod(fetch_spot_balance)
