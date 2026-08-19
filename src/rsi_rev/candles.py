from __future__ import annotations

import logging

from src.candles import get_closed_candles
from src.config import EXCHANGE, GRANULARITY, INTERVAL_MINUTES
from src.exchange import ExchangeClientError, fetch_candles
from src.exchange.binance import RateLimitError
from src.rsi_rev.config import CANDLE_LIMIT, RSI_PERIOD

# Match fetch_candles WS gate (min 20) so scan can run as soon as cache is usable.
WARMUP_MIN_BARS = max(20, RSI_PERIOD + 5)


def scan_candles_trusted(symbol: str) -> bool:
    if EXCHANGE != "binance":
        return True
    try:
        from src.exchange.binance import candles_trusted_for_entry
        from src.exchange.binance_ws import is_ws_enabled

        if not is_ws_enabled():
            return True
        return candles_trusted_for_entry(symbol)
    except Exception:  # noqa: BLE001
        return True


def fetch_scan_candles(symbol: str) -> list:
    """Cycle candles: WS/cache only — no REST fallback."""
    if not scan_candles_trusted(symbol):
        raise ExchangeClientError(f"scan: kline not fresh for {symbol.upper()}")
    return fetch_candles(
        symbol=symbol,
        granularity=GRANULARITY,
        limit=CANDLE_LIMIT,
        ws_only=True,
    )


def _ws_closed_count(symbol: str) -> int:
    raw = fetch_candles(
        symbol=symbol,
        granularity=GRANULARITY,
        limit=CANDLE_LIMIT,
        ws_only=True,
    )
    closed = get_closed_candles(raw, interval_minutes=INTERVAL_MINUTES)
    return len(closed)


def warmup_symbol_candles(symbol: str) -> str:
    """Seed REST klines if WS/disk has too few bars.

    Returns ready | seeded | blocked | failed.
    REST is serialized by boot_optional_rest_slot (gap + weight).
    """
    try:
        if _ws_closed_count(symbol) >= WARMUP_MIN_BARS:
            return "ready"
    except ExchangeClientError:
        pass
    except Exception as exc:  # noqa: BLE001
        logging.debug("  [%s] WS candle warmup probe skipped: %s", symbol, exc)

    try:
        from src.exchange import binance as binance_mod

        if EXCHANGE == "binance" and binance_mod.is_optional_rest_blocked():
            logging.info(
                "  [%s] Skip REST kline warmup — weight/cooldown %.0fs",
                symbol,
                binance_mod.optional_rest_blocked_sec(),
            )
            return "blocked"
    except Exception:  # noqa: BLE001
        pass

    try:
        from src.exchange import binance as binance_mod

        if EXCHANGE == "binance":
            with binance_mod.boot_optional_rest_slot():
                fetch_candles(
                    symbol=symbol,
                    granularity=GRANULARITY,
                    limit=CANDLE_LIMIT,
                    require_confirmed=False,
                )
        else:
            fetch_candles(
                symbol=symbol,
                granularity=GRANULARITY,
                limit=CANDLE_LIMIT,
                require_confirmed=False,
            )
        logging.info("  [%s] REST kline warmup seeded", symbol)
        return "seeded"
    except RateLimitError as exc:
        logging.info("  [%s] REST kline warmup blocked: %s", symbol, exc)
        return "blocked"
    except ExchangeClientError as exc:
        logging.warning("  [%s] REST kline warmup failed: %s", symbol, exc)
        return "failed"
