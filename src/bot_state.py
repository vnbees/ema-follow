import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BotStatus:
    symbol: str = ""
    trend: str = ""
    candle_color: str = ""
    ema34: float = 0.0
    ema89: float = 0.0
    ema144: float = 0.0
    ema200: float = 0.0
    last_close: float = 0.0
    sar_value: float = 0.0
    sar_position: str = ""
    sar_signal: str = ""
    st_trend_5m: str = ""
    st_trend_1h: str = ""
    st_flip_5m: bool = False
    st_signal: str = ""
    st_supertrend_5m: float = 0.0
    st_supertrend_1h: float = 0.0
    rsi_value: float = 0.0
    rsi_prev: float = 0.0
    rsi_signal: str = ""
    rsi_cross_up_25: bool = False
    rsi_cross_up_75: bool = False
    rsi_cross_down_75: bool = False
    rsi_cross_down_25: bool = False
    is_tracked: bool = False
    on_exchange: bool = False
    ofi_buy_volume: float = 0.0
    ofi_sell_volume: float = 0.0
    ofi_volume_delta: float = 0.0
    ofi_delta_spike: float = 0.0
    ofi_bias: str = ""
    position_side: str | None = None
    position_size: float = 0.0
    avg_entry: float | None = None
    pending_orders: list[dict[str, Any]] = field(default_factory=list)
    margin_mode: str = "crossed"
    leverage: int = 5
    last_updated: str = ""


@dataclass
class AccountBalance:
    available: float = 0.0
    equity: float = 0.0
    margin_coin: str = "USDT"
    last_updated: str = ""
    baseline_equity: float | None = None
    pnl_usdt: float | None = None
    pnl_pct: float | None = None
    profit_target_pct: float = 0.0
    baseline_updated_at: str = ""
    maint_margin_pct: float | None = None
    initial_margin_pct: float | None = None
    margin_guard_tier: str = ""
    margin_guard_scenario: str = ""
    suggest_deposit_usdt: float | None = None
    effective_tp_pct: float | None = None


_lock = threading.Lock()
_statuses: dict[str, BotStatus] = {}
_account = AccountBalance()


def _copy_status(status: BotStatus) -> BotStatus:
    return BotStatus(
        symbol=status.symbol,
        trend=status.trend,
        candle_color=status.candle_color,
        ema34=status.ema34,
        ema89=status.ema89,
        ema144=status.ema144,
        ema200=status.ema200,
        last_close=status.last_close,
        sar_value=status.sar_value,
        sar_position=status.sar_position,
        sar_signal=status.sar_signal,
        st_trend_5m=status.st_trend_5m,
        st_trend_1h=status.st_trend_1h,
        st_flip_5m=status.st_flip_5m,
        st_signal=status.st_signal,
        st_supertrend_5m=status.st_supertrend_5m,
        st_supertrend_1h=status.st_supertrend_1h,
        rsi_value=status.rsi_value,
        rsi_prev=status.rsi_prev,
        rsi_signal=status.rsi_signal,
        rsi_cross_up_25=status.rsi_cross_up_25,
        rsi_cross_up_75=status.rsi_cross_up_75,
        rsi_cross_down_75=status.rsi_cross_down_75,
        rsi_cross_down_25=status.rsi_cross_down_25,
        is_tracked=status.is_tracked,
        on_exchange=status.on_exchange,
        ofi_buy_volume=status.ofi_buy_volume,
        ofi_sell_volume=status.ofi_sell_volume,
        ofi_volume_delta=status.ofi_volume_delta,
        ofi_delta_spike=status.ofi_delta_spike,
        ofi_bias=status.ofi_bias,
        position_side=status.position_side,
        position_size=status.position_size,
        avg_entry=status.avg_entry,
        pending_orders=list(status.pending_orders),
        margin_mode=status.margin_mode,
        leverage=status.leverage,
        last_updated=status.last_updated,
    )


def update_symbol_status(symbol: str, **kwargs: Any) -> None:
    with _lock:
        if symbol not in _statuses:
            _statuses[symbol] = BotStatus(symbol=symbol)
        for key, value in kwargs.items():
            if hasattr(_statuses[symbol], key):
                setattr(_statuses[symbol], key, value)


def update_account_balance(
    available: float,
    equity: float,
    margin_coin: str = "USDT",
    last_updated: str = "",
    *,
    baseline_equity: float | None = None,
    pnl_usdt: float | None = None,
    pnl_pct: float | None = None,
    profit_target_pct: float = 0.0,
    baseline_updated_at: str = "",
    maint_margin_pct: float | None = None,
    initial_margin_pct: float | None = None,
    margin_guard_tier: str | None = None,
    margin_guard_scenario: str | None = None,
    suggest_deposit_usdt: float | None = None,
    effective_tp_pct: float | None = None,
) -> None:
    with _lock:
        _account.available = available
        _account.equity = equity
        _account.margin_coin = margin_coin
        if last_updated:
            _account.last_updated = last_updated
        if baseline_equity is not None:
            _account.baseline_equity = baseline_equity
        if pnl_usdt is not None:
            _account.pnl_usdt = pnl_usdt
        if pnl_pct is not None:
            _account.pnl_pct = pnl_pct
        if profit_target_pct > 0:
            _account.profit_target_pct = profit_target_pct
        if baseline_updated_at:
            _account.baseline_updated_at = baseline_updated_at
        if maint_margin_pct is not None:
            _account.maint_margin_pct = maint_margin_pct
        if initial_margin_pct is not None:
            _account.initial_margin_pct = initial_margin_pct
        if margin_guard_tier is not None:
            _account.margin_guard_tier = margin_guard_tier
        if margin_guard_scenario is not None:
            _account.margin_guard_scenario = margin_guard_scenario
        if suggest_deposit_usdt is not None:
            _account.suggest_deposit_usdt = suggest_deposit_usdt
        if effective_tp_pct is not None:
            _account.effective_tp_pct = effective_tp_pct


def remove_symbol_status(symbol: str) -> None:
    with _lock:
        _statuses.pop(symbol, None)


def get_all_statuses() -> dict[str, BotStatus]:
    with _lock:
        return {sym: _copy_status(st) for sym, st in _statuses.items()}


def get_open_position_symbols() -> list[str]:
    from src import database as db

    return [row["symbol"] for row in db.get_open_rsi_trades()]


def clear_stale_signal_statuses(managed_symbols: set[str]) -> None:
    """Remove in-memory statuses for symbols that are not actively managed."""
    with _lock:
        to_remove = [sym for sym in _statuses if sym not in managed_symbols]
        for sym in to_remove:
            del _statuses[sym]


def get_account_balance() -> AccountBalance:
    with _lock:
        return AccountBalance(
            available=_account.available,
            equity=_account.equity,
            margin_coin=_account.margin_coin,
            last_updated=_account.last_updated,
            baseline_equity=_account.baseline_equity,
            pnl_usdt=_account.pnl_usdt,
            pnl_pct=_account.pnl_pct,
            profit_target_pct=_account.profit_target_pct,
            baseline_updated_at=_account.baseline_updated_at,
            maint_margin_pct=_account.maint_margin_pct,
            initial_margin_pct=_account.initial_margin_pct,
            margin_guard_tier=_account.margin_guard_tier,
            margin_guard_scenario=_account.margin_guard_scenario,
            suggest_deposit_usdt=_account.suggest_deposit_usdt,
            effective_tp_pct=_account.effective_tp_pct,
        )


def get_status() -> BotStatus | None:
    with _lock:
        if not _statuses:
            return None
        first = next(iter(_statuses.values()))
        return _copy_status(first)


def status_to_dict(status: BotStatus) -> dict[str, Any]:
    return {
        "symbol": status.symbol,
        "trend": status.trend,
        "candle_color": status.candle_color,
        "ema34": status.ema34,
        "ema89": status.ema89,
        "ema144": status.ema144,
        "ema200": status.ema200,
        "last_close": status.last_close,
        "sar_value": status.sar_value,
        "sar_position": status.sar_position,
        "sar_signal": status.sar_signal,
        "st_trend_5m": status.st_trend_5m,
        "st_trend_1h": status.st_trend_1h,
        "st_flip_5m": status.st_flip_5m,
        "st_signal": status.st_signal,
        "st_supertrend_5m": status.st_supertrend_5m,
        "st_supertrend_1h": status.st_supertrend_1h,
        "rsi_value": status.rsi_value,
        "rsi_prev": status.rsi_prev,
        "rsi_signal": status.rsi_signal,
        "rsi_cross_up_25": status.rsi_cross_up_25,
        "rsi_cross_up_75": status.rsi_cross_up_75,
        "rsi_cross_down_75": status.rsi_cross_down_75,
        "rsi_cross_down_25": status.rsi_cross_down_25,
        "is_tracked": status.is_tracked,
        "on_exchange": status.on_exchange,
        "ofi_buy_volume": status.ofi_buy_volume,
        "ofi_sell_volume": status.ofi_sell_volume,
        "ofi_volume_delta": status.ofi_volume_delta,
        "ofi_delta_spike": status.ofi_delta_spike,
        "ofi_bias": status.ofi_bias,
        "position_side": status.position_side,
        "position_size": status.position_size,
        "avg_entry": status.avg_entry,
        "pending_orders": status.pending_orders,
        "margin_mode": status.margin_mode,
        "leverage": status.leverage,
        "last_updated": status.last_updated,
    }
