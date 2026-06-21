import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BotStatus:
    symbol: str = "SUIUSDT"
    trend: str = ""
    candle_color: str = ""
    ema20: float = 0.0
    ema50: float = 0.0
    ema100: float = 0.0
    ema200: float = 0.0
    last_close: float = 0.0
    position_side: str | None = None
    position_size: float = 0.0
    avg_entry: float | None = None
    pending_orders: list[dict[str, Any]] = field(default_factory=list)
    margin_mode: str = "crossed"
    leverage: int = 5
    balance_available: float = 0.0
    balance_equity: float = 0.0
    last_updated: str = ""


_lock = threading.Lock()
_status = BotStatus()


def update_status(**kwargs: Any) -> None:
    with _lock:
        for key, value in kwargs.items():
            if hasattr(_status, key):
                setattr(_status, key, value)


def get_status() -> BotStatus:
    with _lock:
        return BotStatus(
            symbol=_status.symbol,
            trend=_status.trend,
            candle_color=_status.candle_color,
            ema20=_status.ema20,
            ema50=_status.ema50,
            ema100=_status.ema100,
            ema200=_status.ema200,
            last_close=_status.last_close,
            position_side=_status.position_side,
            position_size=_status.position_size,
            avg_entry=_status.avg_entry,
            pending_orders=list(_status.pending_orders),
            margin_mode=_status.margin_mode,
            leverage=_status.leverage,
            balance_available=_status.balance_available,
            balance_equity=_status.balance_equity,
            last_updated=_status.last_updated,
        )
