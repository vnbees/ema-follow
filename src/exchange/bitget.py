"""Adapter wrapping the existing Bitget client."""

from src import bitget_client as bg
from src.exchange.types import ExchangeClientError


class BitgetExchange:
    def has_credentials(self) -> bool:
        return bg.has_credentials()

    def fetch_candles(self, symbol: str, *, granularity: str, limit: int) -> list:
        return bg.fetch_candles(symbol=symbol, granularity=granularity, limit=limit)

    def fetch_top_futures_by_volume(self, limit: int | None = None) -> list[tuple[str, float]]:
        return bg.fetch_top_futures_by_volume(limit=limit)

    def fetch_contract_spec(self, symbol: str):
        return bg.fetch_contract_spec(symbol)

    def fetch_futures_balance(self, symbol: str):
        return bg.fetch_futures_balance(symbol)

    def fetch_symbol_positions(self, symbol: str) -> dict[str, bg.Position]:
        return bg.fetch_symbol_positions(symbol)

    def fetch_all_open_positions(self) -> list[bg.Position]:
        return bg.fetch_all_open_positions()

    def fetch_side_mark_price(self, symbol: str) -> float:
        return bg.fetch_side_mark_price(symbol)

    def fetch_side_unrealized_pnl(self, symbol: str, hold_side: str) -> float:
        return bg.fetch_side_unrealized_pnl(symbol, hold_side)

    def fetch_total_unrealized_pnl(self, symbols: list[str]) -> tuple[float, int]:
        return bg.fetch_total_unrealized_pnl(symbols)

    def fetch_pending_orders(self, symbol: str) -> list:
        return bg.fetch_pending_orders(symbol)

    def fetch_order_detail(self, symbol: str, order_id: str) -> dict:
        return bg.fetch_order_detail(symbol, order_id)

    def configure_symbol_trading(self, symbol: str) -> None:
        bg.configure_symbol_trading(symbol)

    def place_market_order(
        self,
        symbol: str,
        side: str,
        size: str,
        *,
        hold_side: str | None = None,
        trade_side: str | None = None,
        reduce_only: bool = False,
    ) -> dict:
        return bg.place_market_order(
            symbol,
            side,
            size,
            hold_side=hold_side,
            trade_side=trade_side,
            reduce_only=reduce_only,
        )

    def close_position_side(self, symbol: str, hold_side: str, size: str) -> dict:
        return bg.close_position_side(symbol, hold_side, size)

    def transfer_futures_to_spot(self, asset: str, amount: float) -> dict:
        try:
            return bg.transfer_futures_to_spot(asset, amount)
        except bg.BitgetClientError as exc:
            raise ExchangeClientError(str(exc)) from exc

    def fetch_spot_balance(self, asset: str = "USDT") -> float:
        try:
            return bg.fetch_spot_balance(asset)
        except bg.BitgetClientError as exc:
            raise ExchangeClientError(str(exc)) from exc


def _wrap_error(exc: Exception) -> ExchangeClientError:
    if isinstance(exc, bg.BitgetClientError):
        return ExchangeClientError(str(exc))
    return ExchangeClientError(str(exc))
