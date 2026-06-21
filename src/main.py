import logging
import threading
import time
from datetime import datetime, timezone

import uvicorn

from src.bitget_client import BitgetClientError, fetch_candles, fetch_futures_balance, has_credentials
from src.bot_state import update_status
from src.candles import get_closed_candles, get_last_closed_candle
from src.config import (
    EMA_PERIODS,
    GRANULARITY,
    INTERVAL_MINUTES,
    LOG_DIR,
    WEB_PORT,
)
from src.database import get_symbol, init_db
from src.indicators import compute_emas
from src.trading import ensure_symbol_configured, evaluate_and_trade
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


def log_futures_balance(symbol: str) -> None:
    if not has_credentials():
        logging.info(
            "  Futures balance: skipped (set BITGET_API_KEY, SECRET, PASSPHRASE in .env)"
        )
        return

    try:
        balance = fetch_futures_balance(symbol)
        logging.info(
            "  Futures balance: available=%.2f %s | equity=%.2f %s | usdt_equity=%.2f",
            balance.available,
            balance.margin_coin,
            balance.account_equity,
            balance.margin_coin,
            balance.usdt_equity,
        )
        update_status(
            balance_available=balance.available,
            balance_equity=balance.account_equity,
        )
    except BitgetClientError as exc:
        logging.warning("  Futures balance: failed to fetch (%s)", exc)


def run_analysis() -> None:
    symbol = get_symbol()
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
    trend = detect_trend(emas[20], emas[50], emas[100], emas[200])
    color = candle_color(last_closed)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    logging.info("%s | %s %s", now_str, symbol, GRANULARITY)
    logging.info(
        "  EMA20=%.4f  EMA50=%.4f  EMA100=%.4f  EMA200=%.4f",
        emas[20],
        emas[50],
        emas[100],
        emas[200],
    )
    logging.info("  Trend: %s", trend)
    logging.info(
        "  Last closed candle: %s | O=%.4f H=%.4f L=%.4f C=%.4f | ts=%s",
        color.upper(),
        last_closed.open,
        last_closed.high,
        last_closed.low,
        last_closed.close,
        format_timestamp_ms(last_closed.timestamp),
    )

    update_status(
        symbol=symbol,
        trend=trend,
        candle_color=color,
        ema20=emas[20],
        ema50=emas[50],
        ema100=emas[100],
        ema200=emas[200],
        last_close=last_closed.close,
        last_updated=now_str,
    )

    log_futures_balance(symbol)

    try:
        evaluate_and_trade(symbol, trend, color, last_closed.close)
    except BitgetClientError as exc:
        logging.error("  Trading failed: %s", exc)


def start_web_server() -> None:
    config = uvicorn.Config(web_app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def main() -> None:
    setup_logging()
    init_db()
    symbol = get_symbol()
    logging.info("Bitget EMA Trend Bot Phase 2 started")
    logging.info("Dashboard: http://localhost:%d", WEB_PORT)

    if has_credentials():
        try:
            ensure_symbol_configured(symbol)
        except BitgetClientError as exc:
            logging.warning("Initial trading config failed: %s", exc)

    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    while True:
        try:
            run_analysis()
        except BitgetClientError as exc:
            logging.error("Analysis failed: %s", exc)

        sleep_seconds = seconds_until_next_interval()
        logging.info("Sleeping %.0f seconds until next %dm boundary...", sleep_seconds, INTERVAL_MINUTES)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
