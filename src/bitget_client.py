import json
import math
import time
import uuid
from dataclasses import dataclass

import requests

from src.bitget_auth import build_auth_headers, build_query_string
from src.exchange.symbols import is_scan_symbol
from src.config import (
    ACCOUNT_ENDPOINT,
    BITGET_API_BASE,
    BITGET_API_KEY,
    BITGET_PASSPHRASE,
    BITGET_SECRET_KEY,
    CANCEL_ORDER_ENDPOINT,
    CANDLE_LIMIT,
    CANDLES_ENDPOINT,
    CLOSE_POSITIONS_ENDPOINT,
    CONTRACTS_ENDPOINT,
    GRANULARITY,
    LEVERAGE,
    MARGIN_COIN,
    MARGIN_MODE,
    ORDER_DETAIL_ENDPOINT,
    ORDER_SIZE_USDT,
    PENDING_ORDERS_ENDPOINT,
    PLACE_ORDER_ENDPOINT,
    PRODUCT_TYPE,
    PRODUCT_TYPE_API,
    SET_LEVERAGE_ENDPOINT,
    SET_MARGIN_MODE_ENDPOINT,
    SET_POSITION_MODE_ENDPOINT,
    SINGLE_POSITION_ENDPOINT,
    ALL_POSITIONS_ENDPOINT,
    SYMBOL,
    TICKERS_ENDPOINT,
)


@dataclass
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class FuturesAccountBalance:
    margin_coin: str
    available: float
    account_equity: float
    usdt_equity: float


@dataclass
class ContractSpec:
    symbol: str
    volume_place: int
    price_place: int
    min_trade_num: float
    min_trade_usdt: float
    size_multiplier: float


@dataclass
class Position:
    symbol: str
    side: str | None
    size: float
    avg_price: float


@dataclass
class PendingOrder:
    order_id: str
    client_oid: str
    side: str
    price: float
    size: float


class BitgetClientError(Exception):
    pass


def has_credentials() -> bool:
    return bool(BITGET_API_KEY and BITGET_SECRET_KEY and BITGET_PASSPHRASE)


def _ensure_credentials() -> None:
    if not has_credentials():
        raise BitgetClientError("Missing Bitget API credentials")


def _check_response(payload: dict) -> dict:
    if payload.get("code") != "00000":
        raise BitgetClientError(
            f"Bitget API error: code={payload.get('code')} msg={payload.get('msg')}"
        )
    return payload.get("data") or {}


def _parse_api_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        return f"code={payload.get('code')} msg={payload.get('msg')}"
    except ValueError:
        return response.text[:200]


def _private_get(path: str, params: dict[str, str], max_retries: int = 3) -> dict | list:
    _ensure_credentials()
    query_string = build_query_string(params)
    url = f"{BITGET_API_BASE}{path}?{query_string}"
    headers = build_auth_headers("GET", path, query_string)

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if not response.ok:
                raise BitgetClientError(f"HTTP {response.status_code}: {_parse_api_error(response)}")
            return _check_response(response.json())
        except (requests.RequestException, BitgetClientError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise BitgetClientError(f"GET {path} failed after {max_retries} attempts: {last_error}")


def _private_post(path: str, body: dict, max_retries: int = 3) -> dict:
    _ensure_credentials()
    body_str = json.dumps(body)
    headers = build_auth_headers("POST", path, "", body_str)
    url = f"{BITGET_API_BASE}{path}"

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, data=body_str, timeout=10)
            if not response.ok:
                raise BitgetClientError(f"HTTP {response.status_code}: {_parse_api_error(response)}")
            data = _check_response(response.json())
            return data if isinstance(data, dict) else {"result": data}
        except (requests.RequestException, BitgetClientError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise BitgetClientError(f"POST {path} failed after {max_retries} attempts: {last_error}")


def _public_get(path: str, params: dict[str, str], max_retries: int = 3) -> dict | list:
    url = f"{BITGET_API_BASE}{path}"
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return _check_response(response.json())
        except (requests.RequestException, BitgetClientError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise BitgetClientError(f"GET {path} failed after {max_retries} attempts: {last_error}")


def _parse_candle(raw: list) -> Candle:
    return Candle(
        timestamp=int(raw[0]),
        open=float(raw[1]),
        high=float(raw[2]),
        low=float(raw[3]),
        close=float(raw[4]),
        volume=float(raw[5]),
    )


def _parse_balance(data: dict) -> FuturesAccountBalance:
    equity = float(data.get("accountEquity", 0) or 0)
    maint = float(
        data.get("unionMm")
        or data.get("maintMargin")
        or data.get("crossedMaintMargin")
        or 0
    )
    initial = float(
        data.get("unionIm")
        or data.get("initialMargin")
        or data.get("crossedInitialMargin")
        or 0
    )
    return FuturesAccountBalance(
        margin_coin=data.get("marginCoin", MARGIN_COIN),
        available=float(data.get("available", 0)),
        account_equity=equity,
        usdt_equity=float(data.get("usdtEquity", 0)),
        total_maint_margin=maint,
        total_initial_margin=initial,
    )


def _round_down(value: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.floor(value * factor) / factor


def fetch_top_futures_by_volume(
    limit: int | None = None,
    product_type: str = PRODUCT_TYPE,
) -> list[tuple[str, float]]:
    """Return perpetual USDT-M symbols sorted by 24h usdtVolume descending."""
    data = _public_get(
        TICKERS_ENDPOINT,
        {"productType": PRODUCT_TYPE_API if product_type == PRODUCT_TYPE else product_type},
    )
    if not isinstance(data, list):
        raise BitgetClientError("Invalid tickers response")

    ranked: list[tuple[str, float]] = []
    for item in data:
        symbol = str(item.get("symbol", "")).upper()
        if not is_scan_symbol(symbol):
            continue
        if item.get("deliveryTime"):
            continue
        try:
            volume = float(item.get("usdtVolume") or item.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            volume = 0.0
        if volume <= 0:
            continue
        ranked.append((symbol, volume))

    ranked.sort(key=lambda row: row[1], reverse=True)
    if limit is None:
        return ranked
    return ranked[:limit]


def fetch_candles(
    symbol: str = SYMBOL,
    product_type: str = PRODUCT_TYPE,
    granularity: str = GRANULARITY,
    limit: int = CANDLE_LIMIT,
    max_retries: int = 3,
) -> list[Candle]:
    data = _public_get(
        CANDLES_ENDPOINT,
        {
            "symbol": symbol,
            "productType": product_type,
            "granularity": granularity,
            "limit": str(limit),
        },
        max_retries=max_retries,
    )
    candles = [_parse_candle(row) for row in data]
    candles.sort(key=lambda c: c.timestamp)
    return candles


def fetch_futures_balance(
    symbol: str = SYMBOL,
    product_type: str = PRODUCT_TYPE,
    margin_coin: str = MARGIN_COIN,
    max_retries: int = 3,
) -> FuturesAccountBalance:
    data = _private_get(
        ACCOUNT_ENDPOINT,
        {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
        },
        max_retries=max_retries,
    )
    if not isinstance(data, dict):
        raise BitgetClientError("Empty account data returned from Bitget")
    return _parse_balance(data)


def fetch_contract_spec(
    symbol: str,
    product_type: str = PRODUCT_TYPE,
) -> ContractSpec:
    data = _public_get(CONTRACTS_ENDPOINT, {"productType": product_type})
    if not isinstance(data, list):
        raise BitgetClientError("Invalid contracts response")

    for item in data:
        if item.get("symbol") == symbol:
            return ContractSpec(
                symbol=symbol,
                volume_place=int(item.get("volumePlace", 4)),
                price_place=int(item.get("pricePlace", 4)),
                min_trade_num=float(item.get("minTradeNum", 0)),
                min_trade_usdt=float(item.get("minTradeUSDT", 5)),
                size_multiplier=float(item.get("sizeMultiplier", 1)),
            )
    raise BitgetClientError(f"Contract spec not found for {symbol}")


def _round_up(value: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.ceil(value * factor - 1e-12) / factor


def notional_to_size(notional_usdt: float, price: float, spec: ContractSpec) -> str:
    target_usdt = max(notional_usdt, spec.min_trade_usdt)
    raw_size = target_usdt / price
    size = _round_up(raw_size, spec.volume_place)
    if size < spec.min_trade_num:
        size = spec.min_trade_num
    step = 10 ** (-spec.volume_place)
    while size * price < spec.min_trade_usdt - 1e-9:
        size = _round_up(size + step, spec.volume_place)
    if spec.volume_place == 0:
        return str(int(size))
    formatted = f"{size:.{spec.volume_place}f}".rstrip("0").rstrip(".")
    return formatted or str(spec.min_trade_num)


def format_price(price: float, spec: ContractSpec) -> str:
    rounded = round(price, spec.price_place)
    return f"{rounded:.{spec.price_place}f}"


def set_leverage(
    symbol: str,
    leverage: int = LEVERAGE,
    product_type: str = PRODUCT_TYPE_API,
    margin_coin: str = MARGIN_COIN,
) -> None:
    _private_post(
        SET_LEVERAGE_ENDPOINT,
        {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
            "leverage": str(leverage),
        },
    )


def set_margin_mode(
    symbol: str,
    margin_mode: str = MARGIN_MODE,
    product_type: str = PRODUCT_TYPE_API,
    margin_coin: str = MARGIN_COIN,
) -> None:
    try:
        _private_post(
            SET_MARGIN_MODE_ENDPOINT,
            {
                "symbol": symbol,
                "productType": product_type,
                "marginCoin": margin_coin,
                "marginMode": margin_mode,
            },
        )
    except BitgetClientError as exc:
        if "margin mode" in str(exc).lower() or "40774" in str(exc):
            return
        raise


def set_position_mode(product_type: str = PRODUCT_TYPE_API) -> None:
    try:
        _private_post(
            SET_POSITION_MODE_ENDPOINT,
            {
                "productType": product_type,
                "posMode": "hedge_mode",
            },
        )
    except BitgetClientError as exc:
        if "40774" in str(exc) or "position mode" in str(exc).lower():
            return
        raise


def configure_symbol_trading(symbol: str) -> None:
    set_position_mode()
    set_margin_mode(symbol)
    set_leverage(symbol)


def _parse_position_row(symbol: str, data: dict) -> Position:
    total = abs(float(data.get("total", 0) or 0))
    if total <= 0:
        return Position(symbol=symbol, side=None, size=0.0, avg_price=0.0)

    hold_side = (data.get("holdSide") or "").lower()
    side = "long" if hold_side == "long" else "short" if hold_side == "short" else None
    if side is None:
        side = "long" if float(data.get("total", 0)) > 0 else "short"

    return Position(
        symbol=symbol,
        side=side,
        size=total,
        avg_price=float(data.get("openPriceAvg") or data.get("averageOpenPrice") or 0),
    )


def _empty_position(symbol: str) -> Position:
    return Position(symbol=symbol, side=None, size=0.0, avg_price=0.0)


def _position_rows_from_response(data) -> list[dict]:
    if not data:
        return []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def fetch_symbol_positions(
    symbol: str,
    product_type: str = PRODUCT_TYPE_API,
    margin_coin: str = MARGIN_COIN,
) -> dict[str, Position]:
    data = _private_get(
        SINGLE_POSITION_ENDPOINT,
        {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
        },
    )
    result = {
        "long": _empty_position(symbol),
        "short": _empty_position(symbol),
    }
    for row in _position_rows_from_response(data):
        pos = _parse_position_row(symbol, row)
        if pos.side in result and pos.size > 0:
            result[pos.side] = pos
    return result


def fetch_side_mark_price(
    symbol: str,
    product_type: str = PRODUCT_TYPE_API,
    margin_coin: str = MARGIN_COIN,
) -> float:
    data = _private_get(
        SINGLE_POSITION_ENDPOINT,
        {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
        },
    )
    for row in _position_rows_from_response(data):
        mark = float(row.get("markPrice") or 0)
        if mark > 0:
            return mark
    return 0.0


def fetch_position(
    symbol: str,
    product_type: str = PRODUCT_TYPE_API,
    margin_coin: str = MARGIN_COIN,
) -> Position:
    positions = fetch_symbol_positions(symbol, product_type, margin_coin)
    long_pos = positions["long"]
    short_pos = positions["short"]
    if long_pos.size >= short_pos.size and long_pos.size > 0:
        return long_pos
    if short_pos.size > 0:
        return short_pos
    return _empty_position(symbol)


def fetch_all_open_positions(
    product_type: str = PRODUCT_TYPE_API,
    margin_coin: str = MARGIN_COIN,
) -> list[Position]:
    data = _private_get(
        ALL_POSITIONS_ENDPOINT,
        {
            "productType": product_type,
            "marginCoin": margin_coin,
        },
    )
    if not data:
        return []
    if not isinstance(data, list):
        data = [data]

    positions: list[Position] = []
    for row in data:
        symbol = str(row.get("symbol", "")).upper()
        if not symbol:
            continue
        pos = _parse_position_row(symbol, row)
        if pos.size > 0 and pos.side:
            positions.append(pos)
    return positions


def _parse_unrealized_pnl(data: dict, side: str | None, size: float, avg_price: float) -> float:
    unrealized = float(data.get("unrealizedPL") or data.get("unrealizedPnl") or 0)
    if unrealized != 0:
        return unrealized
    mark = float(data.get("markPrice") or 0)
    if mark <= 0 or size <= 0 or not side or avg_price <= 0:
        return 0.0
    if side == "long":
        return (mark - avg_price) * size
    return (avg_price - mark) * size


def fetch_symbol_unrealized_pnl(symbol: str) -> tuple[float, float]:
    """Return (unrealized USDT, mark price) for an open position, or (0, 0) if flat."""
    data = _private_get(
        SINGLE_POSITION_ENDPOINT,
        {
            "symbol": symbol,
            "productType": PRODUCT_TYPE_API,
            "marginCoin": MARGIN_COIN,
        },
    )
    if not data:
        return 0.0, 0.0
    if isinstance(data, list):
        if not data:
            return 0.0, 0.0
        data = data[0]
    size = abs(float(data.get("total", 0) or 0))
    if size <= 0:
        return 0.0, 0.0
    hold_side = (data.get("holdSide") or "").lower()
    side = "long" if hold_side == "long" else "short" if hold_side == "short" else None
    if side is None:
        side = "long" if float(data.get("total", 0)) > 0 else "short"
    avg_price = float(data.get("openPriceAvg") or data.get("averageOpenPrice") or 0)
    mark = float(data.get("markPrice") or 0)
    return _parse_unrealized_pnl(data, side, size, avg_price), mark


def fetch_side_unrealized_pnl(symbol: str, hold_side: str) -> float:
    positions = fetch_symbol_positions(symbol)
    pos = positions.get(hold_side)
    if pos is None or pos.size <= 0:
        return 0.0
    data = _private_get(
        SINGLE_POSITION_ENDPOINT,
        {
            "symbol": symbol,
            "productType": PRODUCT_TYPE_API,
            "marginCoin": MARGIN_COIN,
        },
    )
    for row in _position_rows_from_response(data):
        row_side = (row.get("holdSide") or "").lower()
        if row_side != hold_side:
            continue
        size = abs(float(row.get("total", 0) or 0))
        avg_price = float(row.get("openPriceAvg") or row.get("averageOpenPrice") or 0)
        return _parse_unrealized_pnl(row, hold_side, size, avg_price)
    return 0.0


def fetch_total_unrealized_pnl(symbols: list[str]) -> tuple[float, int]:
    """Return (sum unrealized USDT, number of open legs)."""
    total = 0.0
    open_count = 0
    for symbol in symbols:
        try:
            positions = fetch_symbol_positions(symbol)
            for hold_side in ("long", "short"):
                pos = positions[hold_side]
                if pos.size <= 0:
                    continue
                total += fetch_side_unrealized_pnl(symbol, hold_side)
                open_count += 1
        except BitgetClientError:
            continue
    return total, open_count


def fetch_pending_orders(
    symbol: str,
    product_type: str = PRODUCT_TYPE_API,
) -> list[PendingOrder]:
    data = _private_get(
        PENDING_ORDERS_ENDPOINT,
        {
            "symbol": symbol,
            "productType": product_type,
        },
    )
    if not data:
        return []
    if not isinstance(data, list):
        return []

    orders: list[PendingOrder] = []
    for item in data:
        if item.get("orderType") != "limit":
            continue
        orders.append(
            PendingOrder(
                order_id=str(item.get("orderId", "")),
                client_oid=str(item.get("clientOid", "")),
                side=str(item.get("side", "")),
                price=float(item.get("price", 0)),
                size=float(item.get("size", 0)),
            )
        )
    return orders


def fetch_order_detail(
    symbol: str,
    order_id: str,
    product_type: str = PRODUCT_TYPE_API,
) -> dict:
    return _private_get(
        ORDER_DETAIL_ENDPOINT,
        {
            "symbol": symbol,
            "orderId": order_id,
            "productType": product_type,
        },
    )  # type: ignore[return-value]


def cancel_order(
    symbol: str,
    order_id: str,
    product_type: str = PRODUCT_TYPE_API,
    margin_coin: str = MARGIN_COIN,
) -> None:
    _private_post(
        CANCEL_ORDER_ENDPOINT,
        {
            "symbol": symbol,
            "productType": product_type,
            "marginCoin": margin_coin,
            "orderId": order_id,
        },
    )


def cancel_all_pending_limits(symbol: str, product_type: str = PRODUCT_TYPE_API) -> None:
    pending = fetch_pending_orders(symbol, product_type)
    for order in pending:
        try:
            cancel_order(symbol, order.order_id, product_type)
        except BitgetClientError:
            continue


def place_limit_order(
    symbol: str,
    side: str,
    price: float,
    size: str,
    product_type: str = PRODUCT_TYPE_API,
    margin_mode: str = MARGIN_MODE,
    margin_coin: str = MARGIN_COIN,
) -> dict:
    client_oid = f"bot_{uuid.uuid4().hex[:16]}"
    return _private_post(
        PLACE_ORDER_ENDPOINT,
        {
            "symbol": symbol,
            "productType": product_type,
            "marginMode": margin_mode,
            "marginCoin": margin_coin,
            "size": size,
            "price": price if isinstance(price, str) else str(price),
            "side": side,
            "orderType": "limit",
            "force": "gtc",
            "clientOid": client_oid,
        },
    )


def _market_order_side(hold_side: str, trade_side: str) -> str:
    """Bitget hedge mode: side is position direction, tradeSide is open/close.

    Open/close long  -> side=buy
    Open/close short -> side=sell
    """
    _ = trade_side
    return "buy" if hold_side == "long" else "sell"


def place_market_order(
    symbol: str,
    side: str,
    size: str,
    product_type: str = PRODUCT_TYPE_API,
    margin_mode: str = MARGIN_MODE,
    margin_coin: str = MARGIN_COIN,
    *,
    hold_side: str | None = None,
    trade_side: str | None = None,
    reduce_only: bool = False,
) -> dict:
    client_oid = f"bot_{uuid.uuid4().hex[:16]}"
    order_side = side
    if hold_side and trade_side:
        order_side = _market_order_side(hold_side, trade_side)
    body: dict[str, str] = {
        "symbol": symbol,
        "productType": product_type,
        "marginMode": margin_mode,
        "marginCoin": margin_coin,
        "size": size,
        "side": order_side,
        "orderType": "market",
        "force": "ioc",
        "clientOid": client_oid,
    }
    if hold_side:
        body["holdSide"] = hold_side
    if trade_side:
        body["tradeSide"] = trade_side
    # reduceOnly is one-way mode only; hedge uses holdSide + tradeSide=close
    if reduce_only and not (hold_side and trade_side):
        body["reduceOnly"] = "YES"
    return _private_post(PLACE_ORDER_ENDPOINT, body)


def close_position_side(
    symbol: str,
    hold_side: str,
    size: str,
    product_type: str = PRODUCT_TYPE_API,
    margin_mode: str = MARGIN_MODE,
    margin_coin: str = MARGIN_COIN,
) -> dict:
    return place_market_order(
        symbol,
        _market_order_side(hold_side, "close"),
        size,
        product_type=product_type,
        margin_mode=margin_mode,
        margin_coin=margin_coin,
        hold_side=hold_side,
        trade_side="close",
        reduce_only=True,
    )


def format_size(size: float, spec: ContractSpec) -> str:
    rounded = round(size, spec.volume_place)
    if rounded < spec.min_trade_num:
        rounded = spec.min_trade_num
    if spec.volume_place == 0:
        return str(int(rounded))
    formatted = f"{rounded:.{spec.volume_place}f}".rstrip("0").rstrip(".")
    return formatted or str(spec.min_trade_num)


def close_positions(
    symbol: str,
    product_type: str = PRODUCT_TYPE_API,
    hold_side: str | None = None,
) -> None:
    body: dict[str, str] = {
        "symbol": symbol,
        "productType": product_type,
    }
    if hold_side:
        body["holdSide"] = hold_side
    _private_post(CLOSE_POSITIONS_ENDPOINT, body)


def transfer_futures_to_spot(asset: str, amount: float) -> dict:
    """Transfer from USDT futures to spot wallet."""
    if amount <= 0:
        raise BitgetClientError("Transfer amount must be positive")
    client_oid = f"spot_{uuid.uuid4().hex[:16]}"
    result = _private_post(
        "/api/v2/spot/wallet/transfer",
        {
            "fromType": "usdt_futures",
            "toType": "spot",
            "amount": str(amount),
            "coin": asset.upper(),
            "clientOid": client_oid,
        },
    )
    return {
        "tranId": str(result.get("transferId") or result.get("orderId") or ""),
        "clientOid": str(result.get("clientOid") or client_oid),
        "raw": result,
    }


def fetch_spot_balance(asset: str = "USDT") -> float:
    asset = asset.upper()
    data = _private_get("/api/v2/spot/account/assets", {"coin": asset})
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    total = 0.0
    for row in rows:
        if str(row.get("coin") or row.get("coinName") or "").upper() != asset:
            continue
        available = float(row.get("available") or row.get("availableAmount") or 0)
        frozen = float(row.get("frozen") or row.get("locked") or 0)
        total += available + frozen
    return total

