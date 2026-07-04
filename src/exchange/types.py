from dataclasses import dataclass


class ExchangeClientError(Exception):
    pass


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
    total_maint_margin: float = 0.0
    total_initial_margin: float = 0.0

    @property
    def maint_margin_pct(self) -> float:
        if self.account_equity <= 0:
            return 0.0
        return self.total_maint_margin / self.account_equity * 100

    @property
    def initial_margin_pct(self) -> float:
        if self.account_equity <= 0:
            return 0.0
        return self.total_initial_margin / self.account_equity * 100


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
