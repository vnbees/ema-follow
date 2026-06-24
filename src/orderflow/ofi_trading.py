import logging
import time
from threading import Lock

from src.bitget_client import BitgetClientError, close_positions, fetch_contract_spec, has_credentials, notional_to_size, place_market_order
from src.config import OFI_HISTORY_CANDLES, OFI_SYMBOL, ORDER_SIZE_USDT, TRADING_ENABLED
from src.orderflow.aggregator import get_snapshot
from src.orderflow.ofi_state import get_live_state
from src.orderflow.orderbook import get_book_pressure
from src.orderflow.trading_signals import evaluate_ofi_entry, evaluate_ofi_exit
from src.trading import ensure_symbol_configured, sync_state

_lock = Lock()
_last_entry_period_ms: int | None = None


def _record_entry_period(period_ms: int) -> None:
    global _last_entry_period_ms
    _last_entry_period_ms = period_ms


def _already_entered_this_candle(period_ms: int) -> bool:
    return _last_entry_period_ms == period_ms and period_ms > 0


def evaluate_ofi_trading() -> None:
    if not TRADING_ENABLED:
        return
    if not has_credentials():
        return

    symbol = OFI_SYMBOL
    live = get_live_state()
    snapshot = get_snapshot(symbol)
    book = get_book_pressure()
    stats_ready = snapshot.history_candles >= OFI_HISTORY_CANDLES

    try:
        ensure_symbol_configured(symbol)
        position, _, _ = sync_state(symbol)
    except BitgetClientError as exc:
        logging.warning("  [OFI %s] Sync failed: %s", symbol, exc)
        return

    if position.size > 0 and position.side:
        should_exit, reason = evaluate_ofi_exit(snapshot, book, position.side)
        if should_exit:
            logging.info(
                "  [OFI %s] Exit %s: %s (delta=%.2f vel=%.2f imb=%.1f%%)",
                symbol,
                position.side,
                reason,
                snapshot.current_delta,
                snapshot.delta_velocity,
                snapshot.forming_imbalance_pct,
            )
            try:
                close_positions(symbol, hold_side=position.side)
            except BitgetClientError as exc:
                logging.error("  [OFI %s] Close failed: %s", symbol, exc)
        return

    if _already_entered_this_candle(snapshot.current_period_ms):
        return

    decision = evaluate_ofi_entry(snapshot, book, stats_ready=stats_ready)
    if decision.side is None:
        return

    if position.size > 0:
        return

    try:
        spec = fetch_contract_spec(symbol)
        price = book.mid_price or live.last_close
        if price <= 0:
            logging.info("  [OFI %s] Entry skipped: no price", symbol)
            return
        size_str = notional_to_size(ORDER_SIZE_USDT, price, spec)
        order_side = "buy" if decision.side == "long" else "sell"
        logging.info(
            "  [OFI %s] %s entry (%s): imb=%.1f%% book=%.1f%% delta=%.2f age=%.1fs",
            symbol,
            decision.side.upper(),
            decision.reason,
            snapshot.forming_imbalance_pct,
            book.book_pressure_pct,
            snapshot.current_delta,
            snapshot.candle_age_sec,
        )
        result = place_market_order(symbol, order_side, size_str)
        order_id = str(result.get("orderId", ""))
        logging.info("  [OFI %s] Market %s placed: order_id=%s size=%s", symbol, order_side, order_id, size_str)
        _record_entry_period(snapshot.current_period_ms)
    except BitgetClientError as exc:
        logging.error("  [OFI %s] Entry failed: %s", symbol, exc)


def ofi_trading_loop() -> None:
    while True:
        try:
            with _lock:
                evaluate_ofi_trading()
        except Exception as exc:
            logging.warning("OFI trading loop error: %s", exc)
        time.sleep(1.0)
