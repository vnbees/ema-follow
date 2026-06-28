import logging
import threading
import time
from datetime import datetime, timezone

import uvicorn

from src.bitget_client import BitgetClientError, fetch_candles, fetch_futures_balance, has_credentials
from src.bot_state import clear_stale_signal_statuses, update_account_balance
from src.candles import get_closed_candles, get_last_closed_candle
from src.config import (
    CANDLE_LIMIT,
    GRANULARITY,
    INTERVAL_MINUTES,
    LOG_DIR,
    MAX_OPEN_SYMBOLS,
    ORDER_MARGIN_MIN_USDT,
    ORDER_MARGIN_PCT,
    PAIR_PROFIT_TARGET_PCT,
    PROFIT_TARGET_PCT,
    RSI_MIN_CANDLES,
    RSI_PERIOD,
    TRADING_ENABLED,
    WEB_PORT,
    LEVERAGE,
)
from src.database import init_db
from src.market_universe import refresh_volume_rank, set_scan_progress
from src.profit_target import check_profit_target, refresh_account_profit_info
from src.rsi import get_rsi_snapshot
from src.rsi_signals import detect_entry_signal
from src.rsi_trading import can_open_new_symbol, evaluate_rsi_trade
from src.rsi_positions import get_managed_symbols, get_open_position_count, restore_tracked_positions
from src.trend import candle_color
from src.web.app import app as web_app


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / "bot.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def seconds_until_next_interval(interval_minutes: int = INTERVAL_MINUTES) -> float:
    now = datetime.now(timezone.utc)
    interval_seconds = interval_minutes * 60
    elapsed = (now.minute % interval_minutes) * 60 + now.second
    remaining = interval_seconds - elapsed
    if remaining <= 0:
        remaining = interval_seconds
    return float(remaining)


def format_timestamp_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log_futures_balance_once(symbol: str) -> None:
    if not has_credentials():
        logging.info(
            "  Futures balance: skipped (set BITGET_API_KEY, SECRET, PASSPHRASE in .env)"
        )
        return

    try:
        balance = fetch_futures_balance(symbol)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        logging.info(
            "  Futures balance: available=%.2f %s | equity=%.2f %s | usdt_equity=%.2f",
            balance.available,
            balance.margin_coin,
            balance.account_equity,
            balance.margin_coin,
            balance.usdt_equity,
        )
        update_account_balance(
            balance.available,
            balance.account_equity,
            balance.margin_coin,
            now_str,
        )
        refresh_account_profit_info(
            get_managed_symbols() or ["BTCUSDT"],
            balance.available,
            balance.account_equity,
            balance.margin_coin,
            now_str,
        )
    except BitgetClientError as exc:
        logging.warning("  Futures balance: failed to fetch (%s)", exc)


def _fetch_rsi_snapshot(symbol: str):
    candles = fetch_candles(symbol=symbol, granularity=GRANULARITY, limit=CANDLE_LIMIT)
    closed = get_closed_candles(candles, interval_minutes=INTERVAL_MINUTES)
    if len(closed) < RSI_MIN_CANDLES:
        raise BitgetClientError(
            f"Need at least {RSI_MIN_CANDLES} closed {GRANULARITY} candles, got {len(closed)}"
        )
    snap = get_rsi_snapshot(closed)
    last_candle = get_last_closed_candle(candles, interval_minutes=INTERVAL_MINUTES)
    return snap, last_candle


def run_analysis_for_symbol(
    symbol: str,
    *,
    scan_only: bool = False,
    scan_rank: int = 0,
) -> tuple[str, str | None] | None:
    snap, last_candle = _fetch_rsi_snapshot(symbol)
    signal = detect_entry_signal(snap)
    signal_side = signal.side

    if scan_only and not signal_side:
        logging.info(
            "  #%d %s — no signal (RSI=%.2f, prev=%.2f)",
            scan_rank,
            symbol,
            snap.rsi if snap.ready else 0.0,
            snap.prev_rsi if snap.ready else 0.0,
        )
        return None

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    color = candle_color(last_candle)

    logging.info("%s | %s %s", now_str, symbol, GRANULARITY)
    logging.info(
        "  RSI(%d): %.2f (prev=%.2f) | cross↑25=%s cross↑75=%s cross↓75=%s cross↓25=%s | signal=%s",
        RSI_PERIOD,
        snap.rsi if snap.ready else 0.0,
        snap.prev_rsi if snap.ready else 0.0,
        snap.cross_up_25,
        snap.cross_up_75,
        snap.cross_down_75,
        snap.cross_down_25,
        signal_side or "none",
    )
    logging.info(
        "  Last candle: %s | O=%.4f H=%.4f L=%.4f C=%.4f | ts=%s",
        color.upper(),
        last_candle.open,
        last_candle.high,
        last_candle.low,
        last_candle.close,
        format_timestamp_ms(last_candle.timestamp),
    )

    try:
        evaluate_rsi_trade(symbol, snap, signal)
    except BitgetClientError as exc:
        logging.error("  [%s] Trading failed: %s", symbol, exc)

    if signal_side:
        return (signal_side, signal.entry_trigger)
    return None


def run_cycle() -> None:
    ranked = refresh_volume_rank()
    if not ranked:
        logging.warning("Volume rank empty — could not fetch Bitget tickers")
        return

    managed_symbols = get_managed_symbols()
    cycle_symbols = sorted(managed_symbols)
    clear_stale_signal_statuses(set(cycle_symbols))
    pnl_symbols = cycle_symbols or [ranked[0][0]]

    if check_profit_target(pnl_symbols):
        log_futures_balance_once(pnl_symbols[0])
        return

    scanned: set[str] = set(cycle_symbols)

    for symbol in cycle_symbols:
        try:
            run_analysis_for_symbol(symbol)
        except BitgetClientError as exc:
            logging.error("[%s] Position management failed: %s", symbol, exc)

    signal_symbol = ""
    checked = 0
    if can_open_new_symbol():
        for rank, (symbol, _volume) in enumerate(ranked, 1):
            if symbol in scanned:
                continue
            checked += 1
            try:
                result = run_analysis_for_symbol(symbol, scan_only=True, scan_rank=rank)
            except BitgetClientError as exc:
                logging.error("[%s] Scan failed: %s", symbol, exc)
                continue
            if result:
                signal_side, signal_trigger = result
                signal_symbol = symbol
                logging.info(
                    "Scan stopped at #%d %s — %s signal (%s)",
                    rank,
                    symbol,
                    signal_side.upper(),
                    signal_trigger or "unknown",
                )
                break
    else:
        logging.info(
            "Max open symbols reached (%d/%d) — skip scan for new entries",
            get_open_position_count(),
            MAX_OPEN_SYMBOLS,
        )

    set_scan_progress(checked, signal_symbol)
    if not signal_symbol and not cycle_symbols:
        logging.info(
            "Scan complete: checked %d/%d coins, no RSI entry",
            checked,
            len(ranked),
        )

    log_futures_balance_once(pnl_symbols[0])


def start_web_server() -> None:
    config = uvicorn.Config(web_app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def main() -> None:
    setup_logging()
    init_db()
    restore_tracked_positions()
    ranked = refresh_volume_rank()
    logging.info("Bitget RSI Bot started")
    logging.info("Dashboard: http://localhost:%d", WEB_PORT)
    logging.info(
        "Scan mode: TP %.1f%% mỗi cycle (đóng only) | cross 25/75: TP+reopen/stack | max %d symbols",
        PAIR_PROFIT_TARGET_PCT,
        MAX_OPEN_SYMBOLS,
    )
    logging.info("Cycle: %dm | RSI period: %d", INTERVAL_MINUTES, RSI_PERIOD)
    if TRADING_ENABLED:
        logging.info(
            "Trading: LIVE hedged RSI — cycle TP %.1f%% | cross 25/75 pair+stack | margin/leg max(%.0f USDT, %.1f%% equity) @ %dx",
            PAIR_PROFIT_TARGET_PCT,
            ORDER_MARGIN_MIN_USDT,
            ORDER_MARGIN_PCT,
            LEVERAGE,
        )
        logging.info("  Max open symbols: %d (hedge mode)", MAX_OPEN_SYMBOLS)
    else:
        logging.info("Trading: DISABLED — analysis and dashboard only")
    if PROFIT_TARGET_PCT > 0 and TRADING_ENABLED:
        logging.info("Profit target: %.2f%% unrealized PnL / equity", PROFIT_TARGET_PCT)

    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    while True:
        try:
            run_cycle()
        except Exception as exc:
            logging.error("Cycle failed: %s", exc)

        sleep_seconds = seconds_until_next_interval()
        logging.info("Sleeping %.0f seconds until next %dm boundary...", sleep_seconds, INTERVAL_MINUTES)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
