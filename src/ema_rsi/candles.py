from __future__ import annotations

from src.candles import get_closed_candles, is_candle_series_stale
from src.config import EXCHANGE, GRANULARITY, INTERVAL_MINUTES
from src.ema_rsi.config import CANDLE_LIMIT, EMA_PERIOD
from src.ema_rsi.signals import EntrySignal, detect_entry
from src.exchange import ExchangeClientError, fetch_candles


def scan_candles_trusted(symbol: str) -> bool:
    """True when WS kline is live or symbol was REST-seeded recently."""
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
    """Scan-phase candles: WS/cache only — no REST fallback (avoids kline bursts)."""
    if not scan_candles_trusted(symbol):
        raise ExchangeClientError(f"scan: kline not fresh for {symbol.upper()}")
    return fetch_candles(
        symbol=symbol,
        granularity=GRANULARITY,
        limit=CANDLE_LIMIT,
        ws_only=True,
    )


def same_entry_signal(a: EntrySignal, b: EntrySignal) -> bool:
    if a.side != b.side or a.signal_ts != b.signal_ts:
        return False
    ref = max(abs(a.entry), abs(b.entry), 1e-12)
    return abs(a.entry - b.entry) / ref < 1e-6


def confirm_entry_signal(
    symbol: str,
    signal: EntrySignal,
) -> tuple[EntrySignal | None, str | None]:
    """Re-fetch trusted candles and re-validate the signal before opening."""
    try:
        raw = fetch_candles(
            symbol=symbol,
            granularity=GRANULARITY,
            limit=CANDLE_LIMIT,
            require_confirmed=True,
        )
    except ExchangeClientError:
        return None, "candles_unconfirmed"

    closed = get_closed_candles(raw, interval_minutes=INTERVAL_MINUTES)
    if len(closed) < EMA_PERIOD + 2:
        return None, "candles_insufficient"
    if is_candle_series_stale(closed, interval_minutes=INTERVAL_MINUTES):
        return None, "stale_candles"

    confirmed = detect_entry(closed)
    if confirmed is None:
        return None, "signal_not_confirmed"
    if confirmed.skip_reason:
        return None, confirmed.skip_reason
    if not same_entry_signal(signal, confirmed):
        return None, "signal_mismatch"
    return confirmed, None
