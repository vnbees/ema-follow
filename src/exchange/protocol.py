from typing import Protocol

from src.exchange.types import (
    Candle,
    ContractSpec,
    FuturesAccountBalance,
    PendingOrder,
    Position,
)


class ExchangeClient(Protocol):
    def has_credentials(self) -> bool: ...

    def fetch_candles(
        self,
        symbol: str,
        *,
        granularity: str,
        limit: int,
    ) -> list[Candle]: ...

    def fetch_top_futures_by_volume(self, limit: int | None = None) -> list[tuple[str, float]]: ...

    def fetch_contract_spec(self, symbol: str) -> ContractSpec: ...

    def fetch_futures_balance(self, symbol: str) -> FuturesAccountBalance: ...

    def fetch_symbol_positions(self, symbol: str) -> dict[str, Position]: ...

    def fetch_all_open_positions(self) -> list[Position]: ...

    def fetch_side_mark_price(self, symbol: str) -> float: ...

    def fetch_side_unrealized_pnl(self, symbol: str, hold_side: str) -> float: ...

    def fetch_total_unrealized_pnl(self, symbols: list[str]) -> tuple[float, int]: ...

    def fetch_pending_orders(self, symbol: str) -> list[PendingOrder]: ...

    def fetch_order_detail(self, symbol: str, order_id: str) -> dict: ...

    def configure_symbol_trading(self, symbol: str) -> None: ...

    def place_market_order(
        self,
        symbol: str,
        side: str,
        size: str,
        *,
        hold_side: str | None = None,
        trade_side: str | None = None,
        reduce_only: bool = False,
    ) -> dict: ...

    def close_position_side(self, symbol: str, hold_side: str, size: str) -> dict: ...

    def transfer_futures_to_spot(self, asset: str, amount: float) -> dict: ...

    def fetch_spot_balance(self, asset: str = "USDT") -> float: ...
