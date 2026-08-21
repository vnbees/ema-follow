"""Donchian scan pool filters — skip high-vol / newly listed symbols.

Open lots are never force-closed; filtering only blocks new entries and
excludes symbols from the top-N scan list (watcher + WS watch keep running
for symbols with open lots via cycle._sync_watched).
"""

from __future__ import annotations

import logging
import time
from typing import Sequence

from src.donchian.config import (
    INTERVAL,
    MAJOR_SYMBOLS,
    MAX_RANGE_24H_PCT,
    MIN_LISTING_DAYS,
    SYMBOL_FILTER_ENABLED,
)

_MS_PER_DAY = 86_400_000

FILTER_ENABLED = SYMBOL_FILTER_ENABLED

_exchange_symbol_cache: dict[str, dict] | None = None
_exchange_symbol_cache_at: float = 0.0
_EXCHANGE_SYMBOL_CACHE_TTL_SEC = 3600.0


def _interval_minutes(interval: str) -> int:
    if interval.endswith("h"):
        return int(interval[:-1]) * 60
    if interval.endswith("m"):
        return int(interval[:-1])
    raise ValueError(f"unsupported interval: {interval}")


def _load_exchange_symbols() -> dict[str, dict]:
    global _exchange_symbol_cache, _exchange_symbol_cache_at
    now = time.monotonic()
    if _exchange_symbol_cache is not None and (now - _exchange_symbol_cache_at) < _EXCHANGE_SYMBOL_CACHE_TTL_SEC:
        return _exchange_symbol_cache
    try:
        from src.exchange.binance import _load_exchange_info

        info = _load_exchange_info()
        _exchange_symbol_cache = {
            str(item["symbol"]).upper(): item
            for item in info.get("symbols", [])
            if item.get("symbol")
        }
        _exchange_symbol_cache_at = now
        return _exchange_symbol_cache
    except Exception as exc:  # noqa: BLE001
        logging.debug("Donchian symbol filter: exchange info unavailable: %s", exc)
        return _exchange_symbol_cache or {}


def listing_age_days(symbol: str) -> float | None:
    """Days since Binance onboardDate, or None if unknown."""
    item = _load_exchange_symbols().get(symbol.upper())
    if not item:
        return None
    try:
        onboard_ms = float(item.get("onboardDate") or 0)
    except (TypeError, ValueError):
        return None
    if onboard_ms <= 0:
        return None
    age_ms = int(time.time() * 1000) - int(onboard_ms)
    return max(0.0, age_ms / _MS_PER_DAY)


def oldest_kline_age_days(symbol: str, *, interval: str = INTERVAL) -> float | None:
    """Age of oldest cached closed kline for symbol, in days."""
    try:
        from src.exchange.binance_ws.cache import CACHE

        raw = CACHE.get_candles(symbol.upper(), interval, 500) or []
        if not raw:
            return None
        oldest_ms = min(int(c.timestamp) for c in raw)
        age_ms = int(time.time() * 1000) - oldest_ms
        return max(0.0, age_ms / _MS_PER_DAY)
    except Exception:  # noqa: BLE001
        return None


def range_24h_pct(symbol: str, *, interval: str = INTERVAL) -> float | None:
    """(max high - min low) / last close over the last 24h of `interval` bars."""
    try:
        from src.exchange.binance_ws.cache import CACHE

        bar_min = _interval_minutes(interval)
        bars_24h = max(1, (24 * 60) // bar_min)
        need = min(bars_24h, 500)
        raw = CACHE.get_candles(symbol.upper(), interval, need) or []
        if len(raw) < max(8, bars_24h // 4):
            return None
        window = raw[-bars_24h:] if len(raw) >= bars_24h else raw
        hi = max(float(c.high) for c in window)
        lo = min(float(c.low) for c in window)
        close = float(window[-1].close)
        if close <= 0:
            return None
        return (hi - lo) / close * 100.0
    except Exception:  # noqa: BLE001
        return None


def is_major_symbol(symbol: str) -> bool:
    return symbol.upper() in MAJOR_SYMBOLS


def is_scan_eligible(symbol: str, *, interval: str = INTERVAL) -> tuple[bool, str]:
    """Return (eligible, reason) for opening new Donchian lots / scan pool."""
    if not FILTER_ENABLED:
        return True, ""

    sym = symbol.upper()
    min_days = MIN_LISTING_DAYS

    age = listing_age_days(sym)
    if age is not None:
        if age < min_days:
            return False, f"listing {age:.0f}d < {min_days:.0f}d"
    else:
        kline_age = oldest_kline_age_days(sym, interval=interval)
        if kline_age is None or kline_age < min_days:
            return False, f"no {min_days:.0f}d history (kline_age={kline_age})"

    # Vol cap applies to non-majors only — avoids dropping ETH/BTC on a volatile day.
    if MAX_RANGE_24H_PCT > 0 and not is_major_symbol(sym):
        rng = range_24h_pct(sym, interval=interval)
        if rng is not None and rng > MAX_RANGE_24H_PCT:
            return False, f"24h range {rng:.1f}% > {MAX_RANGE_24H_PCT:.1f}%"

    return True, ""


def filter_ranked_symbols(
    ranked: Sequence[tuple[str, float]],
    *,
    limit: int,
    interval: str = INTERVAL,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Pick up to `limit` eligible symbols from (symbol, volume) pairs."""
    selected: list[str] = []
    skipped: list[tuple[str, str]] = []
    for sym, _vol in ranked:
        ok, reason = is_scan_eligible(sym, interval=interval)
        if not ok:
            skipped.append((sym, reason))
            continue
        selected.append(sym)
        if len(selected) >= limit:
            break
    return selected, skipped
