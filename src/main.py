import logging
import threading
import time
from datetime import datetime, timezone

import uvicorn

from src.bitget_client import BitgetClientError, fetch_candles, fetch_contract_spec, fetch_futures_balance, has_credentials
from src.bot_state import (
    clear_stale_signal_statuses,
    update_account_balance,
    update_symbol_status,
)
from src.ichimoku_positions import get_managed_symbols, restore_tracked_positions
from src.candles import get_closed_candles, get_last_closed_candle
from src.config import (
    GRANULARITY,
    ICHIMOKU_MIN_CANDLES,
    ICHIMOKU_PARTIAL_TP_RATIO,
    ICHIMOKU_SL_TICKS,
    INTERVAL_MINUTES,
    LOG_DIR,
    OFI_SYMBOL,
    ORDER_SIZE_USDT,
    PROFIT_TARGET_PCT,
    TRADING_ENABLED,
    WEB_PORT,
    LEVERAGE,
    order_notional_usdt,
)
from src.database import init_db
from src.ichimoku import get_ichimoku_snapshot
from src.ichimoku_signals import detect_ichimoku_signal
from src.ichimoku_trading import evaluate_ichimoku_trade
from src.market_universe import get_volume_ranked, refresh_volume_rank, set_scan_progress
from src.orderflow import start_orderflow_ws_loop
from src.profit_target import check_profit_target, refresh_account_profit_info
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


def run_analysis_for_symbol(
    symbol: str,
    *,
    scan_only: bool = False,
    scan_rank: int = 0,
) -> tuple[str, str | None] | None:
    candles = fetch_candles(symbol=symbol, granularity=GRANULARITY)
    closed_candles = get_closed_candles(candles)
    if len(closed_candles) < 1:
        raise BitgetClientError("Not enough closed candle data returned from Bitget")

    if len(closed_candles) < ICHIMOKU_MIN_CANDLES:
        raise BitgetClientError(
            f"Need at least {ICHIMOKU_MIN_CANDLES} closed candles for Ichimoku, got {len(closed_candles)}"
        )

    last_closed = get_last_closed_candle(candles)
    color = candle_color(last_closed)
    snap = get_ichimoku_snapshot(closed_candles)
    spec = fetch_contract_spec(symbol)
    tick_size = 10 ** (-spec.price_place)
    signal = detect_ichimoku_signal(snap, tick_size)
    signal_side = signal.side or None

    if scan_only and not signal_side:
        logging.info("  #%d %s — no signal", scan_rank, symbol)
        return None

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    logging.info("%s | %s %s", now_str, symbol, GRANULARITY)
    if snap.ready:
        logging.info(
            "  Ichimoku: trend=%s kumo=%s (%s) price_vs_kumo=%s",
            snap.ichimoku_trend,
            snap.kumo_color,
            "rising" if snap.kumo_rising else "falling" if snap.kumo_falling else "flat",
            snap.price_vs_kumo,
        )
        logging.info(
            "  Tenkan=%.4f Kijun=%.4f SenkouA=%.4f SenkouB=%.4f",
            snap.tenkan,
            snap.kijun,
            snap.senkou_a,
            snap.senkou_b,
        )
        logging.info(
            "  Chikou: bullish=%s bearish=%s | Signal: %s trigger=%s",
            snap.chikou_bullish,
            snap.chikou_bearish,
            signal.side or "none",
            signal.trigger or "none",
        )
    else:
        logging.warning("  [%s] Ichimoku not ready", symbol)

    logging.info(
        "  Last closed candle: %s | O=%.4f H=%.4f L=%.4f C=%.4f | ts=%s",
        color.upper(),
        last_closed.open,
        last_closed.high,
        last_closed.low,
        last_closed.close,
        format_timestamp_ms(last_closed.timestamp),
    )

    chikou_ok = snap.chikou_bullish if snap.ichimoku_trend == "bullish" else snap.chikou_bearish if snap.ichimoku_trend == "bearish" else False

    update_symbol_status(
        symbol,
        trend=snap.ichimoku_trend,
        candle_color=color,
        last_close=last_closed.close,
        ichimoku_trend=snap.ichimoku_trend,
        price_vs_kumo=snap.price_vs_kumo,
        kumo_color=snap.kumo_color,
        kijun=snap.kijun,
        tenkan=snap.tenkan,
        senkou_a=snap.senkou_a,
        senkou_b=snap.senkou_b,
        chikou_ok=chikou_ok,
        ichimoku_signal=signal.side or "",
        ichimoku_trigger=signal.trigger or "",
        last_updated=now_str,
    )

    try:
        evaluate_ichimoku_trade(symbol, snap, signal)
    except BitgetClientError as exc:
        logging.error("  [%s] Trading failed: %s", symbol, exc)

    return (signal_side, signal.trigger)


def run_cycle() -> None:
    ranked = refresh_volume_rank()
    if not ranked:
        logging.warning("Volume rank empty — could not fetch Bitget tickers")
        return

    managed_symbols = get_managed_symbols()
    clear_stale_signal_statuses(set(managed_symbols))
    pnl_symbols = managed_symbols or [ranked[0][0]]

    if check_profit_target(pnl_symbols):
        log_futures_balance_once(pnl_symbols[0])
        return

    scanned: set[str] = set(managed_symbols)

    for symbol in managed_symbols:
        try:
            run_analysis_for_symbol(symbol)
        except BitgetClientError as exc:
            logging.error("[%s] Position management failed: %s", symbol, exc)

    signal_symbol = ""
    checked = 0
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

    set_scan_progress(checked, signal_symbol)
    if not signal_symbol and not managed_symbols:
        logging.info("Scan complete: checked %d coins, no Ichimoku signal", checked)

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
    logging.info("Bitget Ichimoku M15 Bot started")
    logging.info("Dashboard: http://localhost:%d", WEB_PORT)
    logging.info("Scan mode: volume rank until first Ichimoku signal (%d pairs)", len(ranked))
    logging.info("Timeframe: %s (interval %dm)", GRANULARITY, INTERVAL_MINUTES)
    if TRADING_ENABLED:
        logging.info(
            "Trading: LIVE Ichimoku M15 — margin %.0f USDT @ %dx (notional ~%.0f USDT)",
            ORDER_SIZE_USDT,
            LEVERAGE,
            order_notional_usdt(),
        )
        logging.info("  Entry=market | SL=Kijun±%d ticks | TP1=1R (%.0f%%) | TP2=Kijun cross",
                     ICHIMOKU_SL_TICKS, ICHIMOKU_PARTIAL_TP_RATIO * 100)
    else:
        logging.info("Trading: DISABLED — analysis and dashboard only")
    if PROFIT_TARGET_PCT > 0 and TRADING_ENABLED:
        logging.info("Profit target: %.2f%% unrealized PnL / equity", PROFIT_TARGET_PCT)

    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    orderflow_thread = threading.Thread(
        target=start_orderflow_ws_loop,
        daemon=True,
        name="orderflow-ws",
    )
    orderflow_thread.start()
    logging.info("Order flow WebSocket started for %s (1m display only)", OFI_SYMBOL)

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
