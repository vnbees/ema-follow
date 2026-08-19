from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

import uvicorn

from src.bot_state import is_trading_enabled, set_last_cycle_at, update_account_balance
from src.candles import get_closed_candles
from src.config import (
    BINANCE_CLEAR_RATE_LIMIT,
    EXCHANGE_DISPLAY_NAME,
    INTERVAL_MINUTES,
    LOG_DIR,
    REST_BOOT_GAP_SEC,
    REST_BOOT_QUIET_SEC,
    WEB_PORT,
)
from src.database import init_db, insert_equity_snapshot
from src.exchange import ExchangeClientError, has_credentials
from src.notify import notify_error, install_error_hooks
from src.rsi_rev import store
from src.rsi_rev.candles import fetch_scan_candles, warmup_symbol_candles
from src.rsi_rev.config import (
    CANDLE_LIMIT,
    ENTRIES_PER_CYCLE,
    LEVERAGE,
    MARGIN_PCT,
    MAX_OPEN,
    RSI_PERIOD,
    SYMBOLS,
)
from src.rsi_rev.signals import detect_anchor_events, trigger_from_bar
from src.rsi_rev.trading import try_open
from src.rsi_rev.watcher import reconcile_open_lots, start_watcher
from src.web.app import app as web_app

_first_cycle = True


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / "rsi_rev.log"
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
    try:
        config = uvicorn.Config(web_app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
        server = uvicorn.Server(config)
        server.run()
    except Exception as exc:  # noqa: BLE001
        logging.error("Dashboard server failed: %s", exc, extra={"skip_discord": True})
        notify_error("Dashboard server", str(exc), cooldown_sec=30)


def _sync_watched() -> None:
    try:
        from src.config import EXCHANGE
        from src.exchange.binance_ws import is_ws_enabled, set_watched_symbols

        if EXCHANGE != "binance" or not is_ws_enabled():
            return
        set_watched_symbols(list(SYMBOLS))
    except Exception as exc:  # noqa: BLE001
        logging.debug("RSI-rev WS watch sync skipped: %s", exc)


def _boot_quiet_active() -> bool:
    try:
        from src.config import EXCHANGE
        from src.exchange import binance as binance_mod

        return EXCHANGE == "binance" and binance_mod.is_boot_rest_quiet()
    except Exception:  # noqa: BLE001
        return False


def _log_balance() -> None:
    if not has_credentials():
        return
    if _boot_quiet_active():
        logging.debug("RSI-rev balance log skipped — boot REST quiet")
        return
    try:
        from src.config import EXCHANGE
        from src.exchange.binance_ws import get_balance_from_ws

        if EXCHANGE == "binance" and get_balance_from_ws() is None:
            logging.debug("RSI-rev balance log skipped — waiting WS/disk equity")
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.rsi_rev.trading import live_account_balance

        bal = live_account_balance(SYMBOLS[0] if SYMBOLS else "LINKUSDT")
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
        open_n = store.count_open()
        cap = str(MAX_OPEN) if MAX_OPEN > 0 else "∞"
        logging.info(
            "Futures equity=%.2f %s | maint=%.2f%% | open=%d/%s pending=%d",
            bal.account_equity,
            bal.margin_coin,
            bal.maint_margin_pct,
            open_n,
            cap,
            store.count_pending(),
        )
    except ExchangeClientError as exc:
        logging.debug("RSI-rev balance log skipped: %s", exc)


def _wait_binance_ws_ready() -> None:
    for _ in range(60):
        time.sleep(0.5)
        try:
            from src.exchange.binance_ws.cache import CACHE

            if CACHE.kline_connected:
                return
        except Exception:  # noqa: BLE001
            return
    logging.warning(
        "Binance kline WS not connected after 30s — entries wait for WS kline"
    )
    notify_error(
        "Binance kline WS",
        "kline WS not connected after 30s — RSI-rev entries wait for WS kline",
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


def _boot_warmup_loop() -> None:
    """REST-seed klines only when WS/disk is short — one symbol, then REST_BOOT_GAP_SEC."""
    logging.info(
        "Boot REST warmup started (quiet=%.0fs gap=%.0fs) symbols=%s",
        REST_BOOT_QUIET_SEC,
        REST_BOOT_GAP_SEC,
        ",".join(SYMBOLS) or "—",
    )
    for symbol in SYMBOLS:
        fails = 0
        while True:
            status = warmup_symbol_candles(symbol)
            if status == "ready":
                logging.info("  [%s] kline cache ready — skip REST warmup", symbol)
                break
            if status == "seeded":
                break
            if status == "failed":
                fails += 1
                if fails >= 3:
                    logging.warning("  [%s] REST kline warmup gave up — wait for WS", symbol)
                    break
                time.sleep(15)
                continue
            time.sleep(5)
    logging.info("Boot REST warmup finished")


def _start_boot_warmup() -> None:
    thread = threading.Thread(
        target=_boot_warmup_loop,
        name="rsi-rev-boot-warmup",
        daemon=True,
    )
    thread.start()


def _process_symbol(symbol: str, *, opened: int) -> int:
    try:
        raw = fetch_scan_candles(symbol)
    except ExchangeClientError as exc:
        logging.debug("  [%s] scan candles skipped: %s", symbol, exc)
        return opened
    closed = get_closed_candles(raw)
    if len(closed) < RSI_PERIOD + 2:
        return opened
    last = closed[-1]
    for event in detect_anchor_events(closed):
        inserted = store.insert_pending(
            symbol=symbol,
            zone=event.zone,
            anchor_ts=event.ts,
            anchor_price=event.price,
            anchor_rsi=event.rsi,
        )
        if inserted:
            logging.info(
                "  [%s] RSI-rev pending %s rsi=%.1f anchor=%.6f ts=%s",
                symbol,
                event.zone,
                event.rsi,
                event.price,
                event.ts,
            )

    if not is_trading_enabled() or not has_credentials():
        return opened

    budget = None if ENTRIES_PER_CYCLE <= 0 else max(0, ENTRIES_PER_CYCLE - opened)
    if budget == 0:
        return opened

    for pending in store.list_pending(symbol):
        if budget is not None and opened >= ENTRIES_PER_CYCLE:
            break
        trigger = trigger_from_bar(
            zone=str(pending["zone"]),
            anchor_ts=int(pending["anchor_ts"]),
            anchor_price=float(pending["anchor_price"]),
            anchor_rsi=float(pending["anchor_rsi"] or 0),
            bar=last,
        )
        if trigger is None:
            continue
        status = try_open(symbol, trigger)
        if status == "opened":
            store.delete_pending(int(pending["id"]))
            opened += 1
        elif status == "skipped":
            store.delete_pending(int(pending["id"]))
        # cap_skip / error / disabled: keep pending for the next candle
    return opened


def run_cycle() -> None:
    global _first_cycle
    _sync_watched()
    if _first_cycle:
        logging.info(
            "First cycle — skip REST position reconcile (WS-only boot)"
        )
        _first_cycle = False
    elif not _boot_quiet_active():
        reconcile_open_lots()

    opened = 0
    if not is_trading_enabled():
        logging.info("Trading disabled — persist pending only")
    for symbol in SYMBOLS:
        opened = _process_symbol(symbol, opened=opened)

    set_last_cycle_at(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    _log_balance()


def main() -> None:
    setup_logging()
    install_error_hooks()
    init_db()
    store.ensure_schema()

    try:
        from src.config import EXCHANGE
        from src.exchange import binance as binance_mod

        if EXCHANGE == "binance":
            binance_mod.mark_boot_rest_quiet()
    except Exception:  # noqa: BLE001
        pass

    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()

    try:
        from src.config import EXCHANGE
        from src.exchange.binance_ws import start_binance_ws

        if EXCHANGE == "binance" and has_credentials():
            _maybe_clear_stale_rate_limit()
            _sync_watched()
            start_binance_ws()
            _wait_binance_ws_ready()
            _start_boot_warmup()
    except Exception as exc:  # noqa: BLE001
        logging.warning("Binance WS start skipped: %s", exc)
        notify_error("Binance WS start", str(exc))

    start_watcher()
    _sync_watched()

    cap = "no cap" if MAX_OPEN <= 0 else str(MAX_OPEN)
    entries = "all signals" if ENTRIES_PER_CYCLE <= 0 else str(ENTRIES_PER_CYCLE)
    logging.info("%s RSI-rev bot started", EXCHANGE_DISPLAY_NAME)
    logging.info("Dashboard: http://localhost:%d", WEB_PORT)
    logging.info(
        "Logic: 5m RSI%d reversion | symbols=%s | margin %.1f%% × %dx | max_open=%s | "
        "entries/cycle=%s | candles=%d",
        RSI_PERIOD,
        ",".join(SYMBOLS),
        MARGIN_PCT,
        LEVERAGE,
        cap,
        entries,
        CANDLE_LIMIT,
    )
    if is_trading_enabled():
        logging.info("Trading: LIVE")
    else:
        logging.info("Trading: DISABLED — analysis and dashboard only")

    while True:
        try:
            run_cycle()
        except Exception as exc:
            logging.error("RSI-rev cycle failed: %s", exc, extra={"skip_discord": True})
            notify_error("RSI-rev cycle failed", str(exc))

        sleep_seconds = seconds_until_next_interval()
        logging.info(
            "Sleeping %.0f seconds until next %dm boundary...",
            sleep_seconds,
            INTERVAL_MINUTES,
        )
        time.sleep(sleep_seconds)
