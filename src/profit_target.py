import logging
from datetime import datetime, timezone

from src import database as db
from src.bitget_client import (
    BitgetClientError,
    fetch_futures_balance,
    fetch_total_unrealized_pnl,
    has_credentials,
)
from src.bot_state import get_account_balance, update_account_balance
from src.config import PROFIT_TARGET_PCT, TRADING_ENABLED
from src.trading import liquidate_all_and_reset


def _unrealized_pnl(equity: float, unrealized_usdt: float) -> tuple[float, float]:
    if equity <= 0:
        return unrealized_usdt, 0.0
    return unrealized_usdt, unrealized_usdt / equity * 100


def refresh_account_profit_info(
    symbols: list[str],
    available: float,
    equity: float,
    margin_coin: str,
    last_updated: str,
) -> None:
    baseline = db.get_baseline_equity()
    unrealized_usdt = 0.0
    if symbols and has_credentials():
        try:
            unrealized_usdt, _ = fetch_total_unrealized_pnl(symbols)
        except BitgetClientError:
            unrealized_usdt = 0.0
    pnl_usdt, pnl_pct = _unrealized_pnl(equity, unrealized_usdt)
    update_account_balance(
        available,
        equity,
        margin_coin,
        last_updated,
        baseline_equity=baseline,
        pnl_usdt=pnl_usdt,
        pnl_pct=pnl_pct,
        profit_target_pct=PROFIT_TARGET_PCT,
        baseline_updated_at=db.get_baseline_updated_at(),
    )


def _record_profit_take_and_reset(
    symbols: list[str],
    baseline: float,
    equity_before: float,
    unrealized_usdt: float,
    unrealized_pct: float,
    trigger_type: str,
    balance_available: float,
    margin_coin: str,
    now_str: str,
) -> float | None:
    try:
        new_equity = liquidate_all_and_reset(symbols)
    except BitgetClientError as exc:
        logging.error("  Liquidation failed: %s", exc)
        return None

    db.insert_profit_take(
        baseline_before=baseline,
        equity_after=new_equity,
        pnl_usdt=unrealized_usdt,
        pnl_pct=unrealized_pct,
        trigger_type=trigger_type,
    )
    db.set_baseline_equity(new_equity)
    logging.info(
        "  Profit take (%s): mốc reset %.2f USDT (unrealized %+.2f USDT, %+.2f%%)",
        trigger_type,
        new_equity,
        unrealized_usdt,
        unrealized_pct,
    )

    try:
        post_balance = fetch_futures_balance(symbols[0])
        refresh_account_profit_info(
            symbols,
            post_balance.available,
            post_balance.account_equity,
            post_balance.margin_coin,
            now_str,
        )
    except BitgetClientError:
        refresh_account_profit_info(
            symbols,
            balance_available,
            new_equity,
            margin_coin,
            now_str,
        )
    return new_equity


def check_profit_target(symbols: list[str]) -> bool:
    """Return True if unrealized PnL >= target % of equity and cycle should skip trading."""
    if not TRADING_ENABLED:
        return False
    if not has_credentials() or not symbols:
        return False
    if PROFIT_TARGET_PCT <= 0:
        return False

    try:
        balance = fetch_futures_balance(symbols[0])
        unrealized_usdt, open_count = fetch_total_unrealized_pnl(symbols)
    except BitgetClientError as exc:
        logging.warning("  Profit target check failed: %s", exc)
        return False

    equity = balance.account_equity
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    unrealized_usdt, unrealized_pct = _unrealized_pnl(equity, unrealized_usdt)

    if db.init_baseline_if_missing(equity):
        logging.info("  Session baseline set: %.2f USDT", equity)

    refresh_account_profit_info(
        symbols,
        balance.available,
        equity,
        balance.margin_coin,
        now_str,
    )

    if open_count == 0 or unrealized_usdt <= 0:
        return False

    if unrealized_pct < PROFIT_TARGET_PCT - 1e-9:
        return False

    baseline = db.get_baseline_equity() or equity
    logging.info(
        "  Profit target hit: unrealized %+.2f USDT (%+.2f%% of equity %.2f) >= %.2f%% — liquidating all",
        unrealized_usdt,
        unrealized_pct,
        equity,
        PROFIT_TARGET_PCT,
    )

    if _record_profit_take_and_reset(
        symbols,
        baseline,
        equity,
        unrealized_usdt,
        unrealized_pct,
        "target",
        balance.available,
        balance.margin_coin,
        now_str,
    ) is None:
        return False

    logging.info("  Skipping trading this cycle after profit take")
    return True


def trigger_manual_profit_take() -> dict:
    """Liquidate all, reset baseline, log to calendar — same as auto target hit."""
    if not TRADING_ENABLED:
        return {"ok": False, "error": "trading disabled (TRADING_ENABLED=false)"}
    symbols = db.get_symbols()
    if not symbols:
        return {"ok": False, "error": "watchlist empty"}
    if not has_credentials():
        return {"ok": False, "error": "missing API credentials"}

    try:
        balance = fetch_futures_balance(symbols[0])
        unrealized_usdt, _ = fetch_total_unrealized_pnl(symbols)
    except BitgetClientError as exc:
        return {"ok": False, "error": str(exc)}

    equity = balance.account_equity
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    db.init_baseline_if_missing(equity)
    baseline = db.get_baseline_equity()
    if baseline is None or baseline <= 0:
        return {"ok": False, "error": "invalid baseline"}

    unrealized_usdt, unrealized_pct = _unrealized_pnl(equity, unrealized_usdt)
    logging.info(
        "  Manual profit take triggered (equity=%.2f, unrealized=%+.2f USDT, %+.2f%%)",
        equity,
        unrealized_usdt,
        unrealized_pct,
    )

    new_equity = _record_profit_take_and_reset(
        symbols,
        baseline,
        equity,
        unrealized_usdt,
        unrealized_pct,
        "manual",
        balance.available,
        balance.margin_coin,
        now_str,
    )
    if new_equity is None:
        return {"ok": False, "error": "liquidation failed"}

    return {
        "ok": True,
        "baseline_before": baseline,
        "equity_after": new_equity,
        "pnl_usdt": unrealized_usdt,
        "pnl_pct": unrealized_pct,
    }


def reset_baseline_to_current_equity() -> float | None:
    """Set baseline to current account equity without liquidating."""
    symbols = db.get_symbols()
    equity: float | None = None
    available = 0.0
    margin_coin = "USDT"

    account = get_account_balance()
    if account.equity > 0:
        equity = account.equity
        available = account.available
        margin_coin = account.margin_coin
    elif symbols and has_credentials():
        try:
            balance = fetch_futures_balance(symbols[0])
            equity = balance.account_equity
            available = balance.available
            margin_coin = balance.margin_coin
        except BitgetClientError:
            return None

    if equity is None or equity <= 0:
        return None

    db.set_baseline_equity(equity)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    refresh_account_profit_info(
        symbols,
        available,
        equity,
        margin_coin,
        now_str,
    )
    logging.info("  Baseline manually reset to %.2f USDT (no liquidation)", equity)
    return equity
