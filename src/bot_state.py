import threading
from dataclasses import dataclass
from typing import Any

from src.config import TRADING_ENABLED


@dataclass
class AccountBalance:
    available: float = 0.0
    equity: float = 0.0
    margin_coin: str = "USDT"
    last_updated: str = ""
    baseline_equity: float | None = None
    maint_margin_pct: float | None = None
    initial_margin_pct: float | None = None


_lock = threading.Lock()
_account = AccountBalance()
_last_cycle_at: str = ""
_trading_enabled: bool = TRADING_ENABLED


def update_account_balance(
    available: float,
    equity: float,
    margin_coin: str = "USDT",
    last_updated: str = "",
    *,
    baseline_equity: float | None = None,
    maint_margin_pct: float | None = None,
    initial_margin_pct: float | None = None,
    **_ignored: Any,
) -> None:
    with _lock:
        _account.available = available
        _account.equity = equity
        _account.margin_coin = margin_coin
        if last_updated:
            _account.last_updated = last_updated
        if baseline_equity is not None:
            _account.baseline_equity = baseline_equity
        if maint_margin_pct is not None:
            _account.maint_margin_pct = maint_margin_pct
        if initial_margin_pct is not None:
            _account.initial_margin_pct = initial_margin_pct


def set_last_cycle_at(ts: str) -> None:
    with _lock:
        global _last_cycle_at
        _last_cycle_at = ts


def get_last_cycle_at() -> str:
    with _lock:
        return _last_cycle_at


def set_trading_enabled(enabled: bool) -> None:
    from src import database as db

    with _lock:
        global _trading_enabled
        _trading_enabled = enabled
    db.set_setting_bool("trading_enabled", enabled)


def is_trading_enabled() -> bool:
    from src import database as db

    try:
        return db.get_setting_bool("trading_enabled", TRADING_ENABLED)
    except Exception:
        with _lock:
            return _trading_enabled


def get_account_balance() -> AccountBalance:
    with _lock:
        return AccountBalance(
            available=_account.available,
            equity=_account.equity,
            margin_coin=_account.margin_coin,
            last_updated=_account.last_updated,
            baseline_equity=_account.baseline_equity,
            maint_margin_pct=_account.maint_margin_pct,
            initial_margin_pct=_account.initial_margin_pct,
        )
