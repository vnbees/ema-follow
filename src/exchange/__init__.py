"""Multi-exchange facade for RSI bot."""

from functools import lru_cache

from src.exchange import binance as binance_mod
from src.exchange import bitget as bitget_mod
from src.exchange.protocol import ExchangeClient
from src.exchange.sizing import format_size, notional_to_size
from src.exchange.types import (
    Candle,
    ContractSpec,
    ExchangeClientError,
    FuturesAccountBalance,
    PendingOrder,
    Position,
)

BitgetClientError = ExchangeClientError


def _active_exchange() -> str:
    from src.config import EXCHANGE

    return EXCHANGE


def exchange_display_name() -> str:
    from src.config import EXCHANGE_DISPLAY_NAME

    return EXCHANGE_DISPLAY_NAME


@lru_cache(maxsize=1)
def get_client() -> ExchangeClient:
    if _active_exchange() == "binance":
        return binance_mod.BinanceExchange()
    if _active_exchange() == "bitget":
        return bitget_mod.BitgetExchange()
    raise ExchangeClientError(f"Unsupported EXCHANGE: {_active_exchange()}")


def _client() -> ExchangeClient:
    return get_client()


def has_credentials() -> bool:
    from src.config import (
        BINANCE_API_KEY,
        BINANCE_SECRET_KEY,
        BITGET_API_KEY,
        BITGET_PASSPHRASE,
        BITGET_SECRET_KEY,
        EXCHANGE,
    )

    if EXCHANGE == "binance":
        return bool(BINANCE_API_KEY and BINANCE_SECRET_KEY)
    return bool(BITGET_API_KEY and BITGET_SECRET_KEY and BITGET_PASSPHRASE)


def fetch_candles(symbol: str, **kwargs):
    granularity = kwargs.get("granularity")
    limit = kwargs.get("limit")
    if granularity is None or limit is None:
        from src.config import CANDLE_LIMIT, GRANULARITY

        granularity = granularity or GRANULARITY
        limit = limit or CANDLE_LIMIT
    return _client().fetch_candles(symbol, granularity=granularity, limit=limit)


def fetch_top_futures_by_volume(limit: int | None = None, **kwargs) -> list[tuple[str, float]]:
    _ = kwargs
    return _client().fetch_top_futures_by_volume(limit=limit)


def fetch_contract_spec(symbol: str, **kwargs):
    _ = kwargs
    return _client().fetch_contract_spec(symbol)


def fetch_futures_balance(symbol: str, **kwargs):
    _ = kwargs
    return _client().fetch_futures_balance(symbol)


def fetch_symbol_positions(symbol: str, **kwargs):
    _ = kwargs
    return _client().fetch_symbol_positions(symbol)


def fetch_all_open_positions(**kwargs):
    _ = kwargs
    return _client().fetch_all_open_positions()


def fetch_side_mark_price(symbol: str, **kwargs) -> float:
    _ = kwargs
    return _client().fetch_side_mark_price(symbol)


def fetch_side_unrealized_pnl(symbol: str, hold_side: str, **kwargs) -> float:
    _ = kwargs
    return _client().fetch_side_unrealized_pnl(symbol, hold_side)


def fetch_total_unrealized_pnl(symbols: list[str], **kwargs) -> tuple[float, int]:
    _ = kwargs
    return _client().fetch_total_unrealized_pnl(symbols)


def fetch_pending_orders(symbol: str, **kwargs):
    _ = kwargs
    return _client().fetch_pending_orders(symbol)


def fetch_order_detail(symbol: str, order_id: str, **kwargs) -> dict:
    _ = kwargs
    return _client().fetch_order_detail(symbol, order_id)


def configure_symbol_trading(symbol: str) -> None:
    _client().configure_symbol_trading(symbol)


def place_market_order(
    symbol: str,
    side: str,
    size: str,
    **kwargs,
) -> dict:
    return _client().place_market_order(
        symbol,
        side,
        size,
        hold_side=kwargs.get("hold_side"),
        trade_side=kwargs.get("trade_side"),
        reduce_only=kwargs.get("reduce_only", False),
    )


def close_position_side(symbol: str, hold_side: str, size: str, **kwargs) -> dict:
    _ = kwargs
    return _client().close_position_side(symbol, hold_side, size)


def transfer_futures_to_spot(asset: str, amount: float, **kwargs) -> dict:
    _ = kwargs
    return _client().transfer_futures_to_spot(asset, amount)


def fetch_spot_balance(asset: str = "USDT", **kwargs) -> float:
    _ = kwargs
    return _client().fetch_spot_balance(asset)


__all__ = [
    "BitgetClientError",
    "Candle",
    "ContractSpec",
    "ExchangeClientError",
    "exchange_display_name",
    "FuturesAccountBalance",
    "PendingOrder",
    "Position",
    "close_position_side",
    "configure_symbol_trading",
    "fetch_all_open_positions",
    "fetch_candles",
    "fetch_contract_spec",
    "fetch_futures_balance",
    "fetch_pending_orders",
    "fetch_order_detail",
    "fetch_side_mark_price",
    "fetch_side_unrealized_pnl",
    "fetch_spot_balance",
    "fetch_symbol_positions",
    "fetch_top_futures_by_volume",
    "fetch_total_unrealized_pnl",
    "format_size",
    "get_client",
    "has_credentials",
    "notional_to_size",
    "place_market_order",
    "transfer_futures_to_spot",
]
