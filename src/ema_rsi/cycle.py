from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import uvicorn

from src.bot_state import is_trading_enabled, set_last_cycle_at, update_account_balance
from src.candles import get_closed_candles
from src.config import (
    DEFAULT_SYMBOL,
    EXCHANGE_DISPLAY_NAME,
    GRANULARITY,
    INTERVAL_MINUTES,
    LOG_DIR,
    WEB_PORT,
    BINANCE_CLEAR_RATE_LIMIT,
    BINANCE_VOLUME_RANK_REST_SEC,
)
from src.database import init_db, insert_equity_snapshot
from src.ema_rsi.config import (
    CANDLE_LIMIT,
    EMA_PERIOD,
    ENTRIES_PER_CYCLE,
    MAX_OPEN,
    MARGIN_PCT,
    RR,
    SCAN_LIMIT,
)
from src.ema_rsi.candles import confirm_entry_signal, fetch_scan_candles
from src.ema_rsi.signals import detect_entry
from src.ema_rsi import store
from src.ema_rsi.trading import can_open_symbol, occupied_symbols, open_signal, reconcile_orphan_positions, reconcile_protective_orders
from src.ema_rsi.watcher import reconcile_open_trades, start_watcher
from src.exchange import ExchangeClientError, fetch_futures_balance, has_credentials
from src.exchange.symbols import is_tradeable_symbol
from src.market_universe import refresh_volume_rank
from src.notify import notify_error
from src.web.app import app as web_app


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / "ema_rsi.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )


def seconds_until_next_interval(interval_minutes: int = INTERVAL_MINUTES) -> float:
    now = datetime.now(timezone.utc)
    interval_seconds = interval_minutes * 60
    elapsed = (now.minute % interval_minutes) * 60 + now.second
    remaining = interval_seconds - elapsed
    if remaining <= 0:
        remaining = interval_seconds
    return float(remaining)


def start_web_server() -> None:
    config = uvicorn.Config(web_app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def _sync_watched(open_symbols: list[str], ranked: list[tuple[str, float]]) -> None:
    try:
        from src.config import EXCHANGE
        from src.exchange.binance_ws import is_ws_enabled, set_watched_symbols

        if EXCHANGE != "binance" or not is_ws_enabled():
            return
        top = [sym for sym, _vol in ranked[:SCAN_LIMIT]]
        set_watched_symbols(list(dict.fromkeys([*open_symbols, *top])))
    except Exception as exc:  # noqa: BLE001
        logging.debug("EMA-RSI WS watch sync skipped: %s", exc)


def _log_balance() -> None:
    if not has_credentials():
        return
    try:
        bal = fetch_futures_balance(DEFAULT_SYMBOL)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        update_account_balance(
            available=bal.available,
            equity=bal.account_equity,
            margin_coin=bal.margin_coin,
            last_updated=now_str,
            maint_margin_pct=bal.maint_margin_pct,
            initial_margin_pct=bal.initial_margin_pct,
        )
        insert_equity_snapshot(
            bal.account_equity,
            bal.available,
            maint_margin_pct=bal.maint_margin_pct,
        )
        logging.info(
            "Futures equity=%.2f %s | maint=%.2f%% | open=%d/%d",
            bal.account_equity,
            bal.margin_coin,
            bal.maint_margin_pct,
            store.count_open(),
            MAX_OPEN,
        )
    except ExchangeClientError as exc:
        logging.debug("EMA-RSI balance log skipped: %s", exc)


def _wait_binance_ws_ready() -> None:
    """Wait for miniTicker seed + kline WS before first volume rank (avoid REST burst)."""
    for _ in range(80):
        time.sleep(0.5)
        try:
            from src.exchange.binance_ws.cache import CACHE

            if CACHE.mini_ticker_seeded:
                break
        except Exception:  # noqa: BLE001
            break
    else:
        logging.warning(
            "Binance miniTicker not seeded after 40s — volume rank may defer to next cycle"
        )
        notify_error(
            "Binance miniTicker",
            "miniTicker not seeded after 40s — volume rank may defer to next cycle",
        )

    for _ in range(60):
        time.sleep(0.5)
        try:
            from src.exchange.binance_ws.cache import CACHE

            if CACHE.kline_connected:
                return
        except Exception:  # noqa: BLE001
            return
    logging.warning(
        "Binance kline WS not connected after 30s — entries will REST-confirm candles"
    )
    notify_error(
        "Binance kline WS",
        "kline WS not connected after 30s — entries will REST-confirm candles",
    )


def _maybe_clear_stale_rate_limit() -> None:
    if not BINANCE_CLEAR_RATE_LIMIT:
        return
    try:
        from src.config import EXCHANGE
        from src.exchange import binance as binance_mod

        if EXCHANGE != "binance":
            return
        if binance_mod.clear_rate_limit_cooldown():
            logging.warning(
                "Binance REST cooldown cleared on boot (BINANCE_CLEAR_RATE_LIMIT)"
            )
    except Exception as exc:  # noqa: BLE001
        logging.debug("Binance cooldown clear skipped: %s", exc)


_first_cycle = True


def run_cycle() -> None:
    global _first_cycle
    ranked = refresh_volume_rank(max_age_sec=BINANCE_VOLUME_RANK_REST_SEC)
    open_rows = store.get_open_trades()
    open_symbols = [str(row["symbol"]) for row in open_rows]
    _sync_watched(open_symbols, ranked)
    if _first_cycle:
        logging.info(
            "First cycle — skip REST orphan/protective reconcile (WS-only boot)"
        )
        _first_cycle = False
    else:
        reconcile_orphan_positions()
        reconcile_protective_orders()
    reconcile_open_trades()

    occupied = occupied_symbols()
    opened = 0
    checked = 0
    if is_trading_enabled() and has_credentials() and store.count_open() < MAX_OPEN:
        for symbol, _vol in ranked:
            if opened >= ENTRIES_PER_CYCLE:
                break
            if checked >= SCAN_LIMIT:
                break
            if not is_tradeable_symbol(symbol):
                continue
            if not can_open_symbol(symbol, occupied):
                continue
            checked += 1
            try:
                raw = fetch_scan_candles(symbol)
            except ExchangeClientError as exc:
                logging.debug("  [%s] scan candles skipped: %s", symbol, exc)
                continue
            closed = get_closed_candles(raw)
            if len(closed) < EMA_PERIOD + 2:
                continue
            signal = detect_entry(closed)
            if signal is None:
                continue
            if signal.skip_reason:
                logging.info(
                    "  [%s] EMA-RSI %s skip %s (entry=%.6f SL=%.6f)",
                    symbol,
                    signal.side,
                    signal.skip_reason,
                    signal.entry,
                    signal.sl,
                )
                store.mark_signal_seen(symbol, signal.signal_ts)
                continue
            confirmed, confirm_skip = confirm_entry_signal(symbol, signal)
            if confirm_skip:
                logging.info(
                    "  [%s] EMA-RSI %s skip %s (entry confirm)",
                    symbol,
                    signal.side,
                    confirm_skip,
                )
                continue
            assert confirmed is not None
            logging.info(
                "  [%s] EMA-RSI signal %s entry=%.6f SL=%.6f TP=%.6f",
                symbol,
                confirmed.side.upper(),
                confirmed.entry,
                confirmed.sl,
                confirmed.tp,
            )
            trade_id = open_signal(symbol, confirmed)
            if trade_id:
                opened += 1
                occupied.add(symbol.upper())
    elif not is_trading_enabled():
        logging.info("Trading disabled — scan only")
    else:
        logging.info("Max open reached (%d/%d) — skip new entries", store.count_open(), MAX_OPEN)

    set_last_cycle_at(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    _log_balance()


def main() -> None:
    setup_logging()
    init_db()
    store.ensure_schema()

    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    try:
        from src.config import EXCHANGE
        from src.exchange.binance_ws import start_binance_ws

        if EXCHANGE == "binance" and has_credentials():
            _maybe_clear_stale_rate_limit()
            start_binance_ws()
            _wait_binance_ws_ready()
    except Exception as exc:  # noqa: BLE001
        logging.warning("Binance WS start skipped: %s", exc)
        notify_error("Binance WS start", str(exc))

    start_watcher()
    ranked = refresh_volume_rank(max_age_sec=BINANCE_VOLUME_RANK_REST_SEC)
    if not ranked:
        logging.warning("Volume rank empty at startup — will retry each cycle")
        notify_error(
            "EMA-RSI volume rank",
            "Volume rank empty at startup — will retry each cycle",
        )
    elif ranked:
        _sync_watched([], ranked)

    logging.info("%s EMA-RSI bot started", EXCHANGE_DISPLAY_NAME)
    logging.info("Dashboard: http://localhost:%d", WEB_PORT)
    logging.info(
        "Logic: 5m close cross EMA%d + RSI swing | RR 1:%.0f | margin %.1f%% equity | max %d",
        EMA_PERIOD,
        RR,
        MARGIN_PCT,
        MAX_OPEN,
    )
    if is_trading_enabled():
        logging.info("Trading: LIVE")
    else:
        logging.info("Trading: DISABLED — analysis and dashboard only")

    while True:
        try:
            run_cycle()
        except Exception as exc:
            logging.error("EMA-RSI cycle failed: %s", exc)
            notify_error("EMA-RSI cycle failed", str(exc))

        sleep_seconds = seconds_until_next_interval()
        logging.info(
            "Sleeping %.0f seconds until next %dm boundary...",
            sleep_seconds,
            INTERVAL_MINUTES,
        )
        time.sleep(sleep_seconds)
