import logging
import threading
import time
from datetime import datetime, timezone

import uvicorn

from src.bitget_client import BitgetClientError, fetch_candles, fetch_futures_balance, has_credentials
from src.bot_state import update_account_balance, update_symbol_status
from src.candles import get_closed_candles, get_last_closed_candle
from src.config import (
    EMA_PERIODS,
    GRANULARITY,
    INTERVAL_MINUTES,
    LOG_DIR,
    OFI_SYMBOL,
    PROFIT_TARGET_PCT,
    SAR_AF,
    SAR_MAX_AF,
    TRADING_ENABLED,
    WEB_PORT,
)
from src.database import get_symbols, init_db
from src.indicators import compute_emas, compute_parabolic_sar
from src.orderflow import start_orderflow_ws_loop
from src.profit_target import check_profit_target, refresh_account_profit_info
from src.sar import detect_sar_flip, sar_position
from src.trading import configure_all_symbols, evaluate_and_trade
from src.trend import candle_color, detect_trend
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
            get_symbols(),
            balance.available,
            balance.account_equity,
            balance.margin_coin,
            now_str,
        )
    except BitgetClientError as exc:
        logging.warning("  Futures balance: failed to fetch (%s)", exc)


def run_analysis_for_symbol(symbol: str) -> None:
    candles = fetch_candles(symbol=symbol)
    closed_candles = get_closed_candles(candles)
    if len(closed_candles) < 1:
        raise BitgetClientError("Not enough closed candle data returned from Bitget")

    last_closed = get_last_closed_candle(candles)
    closes = [c.close for c in closed_candles]

    max_period = max(EMA_PERIODS)
    if len(closes) < max_period:
        raise BitgetClientError(
            f"Need at least {max_period} closed candles for EMA{max_period}, got {len(closes)}"
        )

    emas = compute_emas(closes, EMA_PERIODS)
    trend = detect_trend(emas[34], emas[89], emas[144], emas[200])
    color = candle_color(last_closed)
    sar_values = compute_parabolic_sar(closed_candles, SAR_AF, SAR_MAX_AF)
    sar_signal: str | None = None
    curr_sar: float | None = None
    sar_pos = ""

    if len(closed_candles) >= 2:
        prev_candle = closed_candles[-2]
        prev_sar = sar_values[-2]
        curr_sar_val = sar_values[-1]
        if prev_sar is not None and curr_sar_val is not None:
            curr_sar = curr_sar_val
            sar_pos = sar_position(curr_sar_val, last_closed)
            sar_signal = detect_sar_flip(prev_candle, last_closed, prev_sar, curr_sar_val)
        else:
            logging.warning("  [%s] SAR not ready — skipping trade signals", symbol)
    else:
        logging.warning("  [%s] Not enough candles for SAR flip detection", symbol)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    logging.info("%s | %s %s", now_str, symbol, GRANULARITY)
    logging.info(
        "  EMA34=%.4f  EMA89=%.4f  EMA144=%.4f  EMA200=%.4f",
        emas[34],
        emas[89],
        emas[144],
        emas[200],
    )
    logging.info("  Trend: %s", trend)
    if curr_sar is not None:
        logging.info(
            "  SAR=%.4f position=%s signal=%s",
            curr_sar,
            sar_pos,
            sar_signal or "none",
        )
    logging.info(
        "  Last closed candle: %s | O=%.4f H=%.4f L=%.4f C=%.4f | ts=%s",
        color.upper(),
        last_closed.open,
        last_closed.high,
        last_closed.low,
        last_closed.close,
        format_timestamp_ms(last_closed.timestamp),
    )

    update_symbol_status(
        symbol,
        trend=trend,
        candle_color=color,
        ema34=emas[34],
        ema89=emas[89],
        ema144=emas[144],
        ema200=emas[200],
        last_close=last_closed.close,
        sar_value=curr_sar or 0.0,
        sar_position=sar_pos,
        sar_signal=sar_signal or "",
        last_updated=now_str,
    )

    try:
        evaluate_and_trade(symbol, trend, sar_signal, last_closed.close)
    except BitgetClientError as exc:
        logging.error("  [%s] Trading failed: %s", symbol, exc)


def run_cycle() -> None:
    symbols = get_symbols()
    if not symbols:
        logging.warning("No symbols in watchlist — add coins via dashboard")
        return

    if check_profit_target(symbols):
        log_futures_balance_once(symbols[0])
        return

    for symbol in symbols:
        try:
            run_analysis_for_symbol(symbol)
        except BitgetClientError as exc:
            logging.error("[%s] Analysis failed: %s", symbol, exc)

    log_futures_balance_once(symbols[0])


def start_web_server() -> None:
    config = uvicorn.Config(web_app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def main() -> None:
    setup_logging()
    init_db()
    symbols = get_symbols()
    logging.info("Bitget EMA Trend Bot Phase 2 started")
    logging.info("Dashboard: http://localhost:%d", WEB_PORT)
    logging.info("Watchlist: %s", ", ".join(symbols) if symbols else "(empty)")
    if TRADING_ENABLED:
        logging.info("Trading: ENABLED (EMA + SAR market orders)")
    else:
        logging.info("Trading: DISABLED — analysis and dashboard only")
    if PROFIT_TARGET_PCT > 0 and TRADING_ENABLED:
        logging.info("Profit target: %.2f%% unrealized PnL / equity", PROFIT_TARGET_PCT)

    if TRADING_ENABLED and has_credentials() and symbols:
        configure_all_symbols(symbols)

    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    orderflow_thread = threading.Thread(
        target=start_orderflow_ws_loop,
        daemon=True,
        name="orderflow-ws",
    )
    orderflow_thread.start()
    logging.info("Order flow WebSocket started for %s (1m, realtime)", OFI_SYMBOL)

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
