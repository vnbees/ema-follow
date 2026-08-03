import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from src.config import (
    BINANCE_API_BASE,
    BINANCE_API_KEY,
    BINANCE_SECRET_KEY,
    BINANCE_SPOT_API_BASE,
    CANDLE_LIMIT,
    DATABASE_PATH,
    GRANULARITY,
    LEVERAGE,
    MARGIN_COIN,
    MARGIN_MODE,
    SYMBOL,
)
from src.exchange.binance_auth import auth_headers, signed_params
from src.exchange.symbols import is_scan_symbol
from src.exchange.sizing import format_size, notional_to_size
from src.exchange.types import (
    Candle,
    ContractSpec,
    ExchangeClientError,
    FuturesAccountBalance,
    PendingOrder,
    Position,
)

_SPEC_CACHE: dict[str, ContractSpec] = {}
_EXCHANGE_INFO_CACHE: dict | None = None

# Short-TTL REST caches — reuse within a symbol evaluation; invalidate after orders.
_REST_CACHE_TTL_MS = 2500.0
_rest_cache_lock = threading.Lock()
_position_by_symbol: dict[str, tuple[float, dict[str, Position]]] = {}
_positions_all: tuple[float, list[Position]] | None = None
_account_cache: tuple[float, FuturesAccountBalance] | None = None
_volume_rank_rest_at_mono: float = 0.0
_volume_rank_rest_cache: list[tuple[str, float]] = []
_candle_rest_at_mono: dict[str, float] = {}
_candle_rest_lock = threading.Lock()
_last_candle_rest_mono: float = 0.0


def _invalidate_position_cache(symbol: str | None = None) -> None:
    """Drop cached positionRisk (and account). Call after any order that changes size."""
    global _positions_all, _account_cache
    with _rest_cache_lock:
        _positions_all = None
        _account_cache = None
        if symbol is None:
            _position_by_symbol.clear()
        else:
            _position_by_symbol.pop(symbol.upper(), None)


def has_credentials() -> bool:
    return bool(BINANCE_API_KEY and BINANCE_SECRET_KEY)


def _ensure_credentials() -> None:
    if not has_credentials():
        raise ExchangeClientError("Missing Binance API credentials")


def _parse_api_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        return f"code={payload.get('code')} msg={payload.get('msg')}"
    except ValueError:
        return response.text[:200]


class RateLimitError(ExchangeClientError):
    """HTTP 429/418 from Binance — must NOT be retried."""


# Cooldown window: while active, all REST calls fail fast without hitting Binance.
# Persisted across restarts so redeploy does not re-hit a banned IP (extends bans).
_rate_limit_lock = threading.Lock()
_rate_limited_until_ms = 0.0
_RATE_LIMIT_FILE = Path(DATABASE_PATH).expanduser().resolve().parent / "binance_rate_limit_until_ms"


def _now_ms() -> float:
    return time.time() * 1000


def _load_persisted_rate_limit() -> None:
    global _rate_limited_until_ms
    try:
        if not _RATE_LIMIT_FILE.is_file():
            return
        until_ms = float(_RATE_LIMIT_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if until_ms > _now_ms():
        with _rate_limit_lock:
            if until_ms > _rate_limited_until_ms:
                _rate_limited_until_ms = until_ms
        # Do not logging.warning here — import-time logging freezes root at WARNING
        # and hides all INFO (Bot started / market WS) until force=True basicConfig.


def _persist_rate_limit(until_ms: float) -> None:
    try:
        _RATE_LIMIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _RATE_LIMIT_FILE.write_text(str(int(until_ms)), encoding="utf-8")
    except OSError as exc:
        logging.debug("Binance rate-limit persist failed: %s", exc)


def _clear_persisted_rate_limit() -> None:
    try:
        if _RATE_LIMIT_FILE.is_file():
            _RATE_LIMIT_FILE.unlink()
    except OSError:
        pass


def _set_rate_limited_until(until_ms: float) -> None:
    global _rate_limited_until_ms
    with _rate_limit_lock:
        if until_ms > _rate_limited_until_ms:
            _rate_limited_until_ms = until_ms
            logging.warning(
                "Binance rate limited — pausing REST calls for %.0fs",
                max(0.0, until_ms - _now_ms()) / 1000,
            )
            _persist_rate_limit(until_ms)


def _check_rate_limit_pause() -> None:
    global _rate_limited_until_ms
    with _rate_limit_lock:
        until_ms = _rate_limited_until_ms
    if _now_ms() < until_ms:
        remaining = (until_ms - _now_ms()) / 1000
        raise RateLimitError(
            f"Rate-limit cooldown active — {remaining:.0f}s remaining, request skipped"
        )
    # Ban expired — drop stale file so next boot stays clean.
    if until_ms > 0 and _now_ms() >= until_ms:
        with _rate_limit_lock:
            if _rate_limited_until_ms == until_ms:
                _rate_limited_until_ms = 0.0
        _clear_persisted_rate_limit()


def rate_limit_remaining_sec() -> float:
    """Seconds left in local 418/429 cooldown (0 if clear)."""
    with _rate_limit_lock:
        until_ms = _rate_limited_until_ms
    return max(0.0, (until_ms - _now_ms()) / 1000)


def is_rate_limited() -> bool:
    return rate_limit_remaining_sec() > 0


_load_persisted_rate_limit()


def _handle_rate_limit_response(response: requests.Response) -> None:
    """Register cooldown for HTTP 429/418 and raise RateLimitError."""
    if response.status_code not in (429, 418):
        return
    detail = _parse_api_error(response)
    until_ms = 0.0
    match = re.search(r"banned until (\d{13})", detail)
    if match:
        until_ms = float(match.group(1))
    else:
        try:
            retry_after_sec = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry_after_sec = 0.0
        if retry_after_sec > 0:
            until_ms = _now_ms() + retry_after_sec * 1000
    if until_ms <= _now_ms():
        # No usable hint: back off for a full minute (weight window).
        until_ms = _now_ms() + 60_000
    _set_rate_limited_until(until_ms)
    raise RateLimitError(f"HTTP {response.status_code}: {detail}")


def _public_get(path: str, params: dict[str, str], max_retries: int = 3) -> Any:
    url = f"{BINANCE_API_BASE}{path}"
    last_error: Exception | None = None
    for attempt in range(max_retries):
        _check_rate_limit_pause()
        try:
            response = requests.get(url, params=params, timeout=10)
            _handle_rate_limit_response(response)
            if not response.ok:
                raise ExchangeClientError(f"HTTP {response.status_code}: {_parse_api_error(response)}")
            return response.json()
        except RateLimitError:
            raise
        except (requests.RequestException, ExchangeClientError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise ExchangeClientError(f"GET {path} failed after {max_retries} attempts: {last_error}")


def _private_request(
    method: str,
    path: str,
    params: dict[str, str | int | float | bool],
    max_retries: int = 3,
) -> Any:
    _ensure_credentials()
    url = f"{BINANCE_API_BASE}{path}"
    headers = auth_headers(BINANCE_API_KEY)
    last_error: Exception | None = None
    for attempt in range(max_retries):
        _check_rate_limit_pause()
        # Re-sign each attempt so timestamp stays inside recvWindow after backoff sleeps.
        signed = signed_params(BINANCE_API_KEY, BINANCE_SECRET_KEY, params)
        try:
            if method == "GET":
                response = requests.get(url, params=signed, headers=headers, timeout=10)
            elif method == "PUT":
                response = requests.put(url, params=signed, headers=headers, timeout=10)
            elif method == "DELETE":
                response = requests.delete(url, params=signed, headers=headers, timeout=10)
            else:
                response = requests.post(url, params=signed, headers=headers, timeout=10)
            _handle_rate_limit_response(response)
            if not response.ok:
                raise ExchangeClientError(f"HTTP {response.status_code}: {_parse_api_error(response)}")
            if not response.text:
                return {}
            return response.json()
        except RateLimitError:
            raise
        except (requests.RequestException, ExchangeClientError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise ExchangeClientError(f"{method} {path} failed after {max_retries} attempts: {last_error}")


def _private_get(path: str, params: dict[str, str | int | float | bool], max_retries: int = 3) -> Any:
    return _private_request("GET", path, params, max_retries=max_retries)


def _private_post(path: str, params: dict[str, str | int | float | bool], max_retries: int = 3) -> Any:
    return _private_request("POST", path, params, max_retries=max_retries)


def _spot_private_request(
    method: str,
    path: str,
    params: dict[str, str | int | float | bool],
    max_retries: int = 3,
) -> Any:
    _ensure_credentials()
    url = f"{BINANCE_SPOT_API_BASE}{path}"
    headers = auth_headers(BINANCE_API_KEY)
    last_error: Exception | None = None
    for attempt in range(max_retries):
        _check_rate_limit_pause()
        signed = signed_params(BINANCE_API_KEY, BINANCE_SECRET_KEY, params)
        try:
            if method == "GET":
                response = requests.get(url, params=signed, headers=headers, timeout=10)
            else:
                response = requests.post(url, params=signed, headers=headers, timeout=10)
            _handle_rate_limit_response(response)
            if not response.ok:
                raise ExchangeClientError(f"HTTP {response.status_code}: {_parse_api_error(response)}")
            if not response.text:
                return {}
            return response.json()
        except RateLimitError:
            raise
        except (requests.RequestException, ExchangeClientError, ValueError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise ExchangeClientError(f"{method} {path} failed after {max_retries} attempts: {last_error}")


def transfer_futures_to_spot(asset: str, amount: float) -> dict:
    """Transfer from USDT-M futures wallet to spot (UMFUTURE_MAIN)."""
    if amount <= 0:
        raise ExchangeClientError("Transfer amount must be positive")
    result = _spot_private_request(
        "POST",
        "/sapi/v1/asset/transfer",
        {
            "type": "UMFUTURE_MAIN",
            "asset": asset.upper(),
            "amount": amount,
        },
    )
    return {
        "tranId": str(result.get("tranId", "")),
        "clientOid": str(result.get("clientTranId", "") or result.get("tranId", "")),
        "raw": result,
    }


def fetch_spot_balance(asset: str = "USDT") -> float:
    asset = asset.upper()
    rows = _spot_private_request(
        "POST",
        "/sapi/v3/asset/getUserAsset",
        {"asset": asset},
    )
    if isinstance(rows, list):
        for row in rows:
            if str(row.get("asset", "")).upper() == asset:
                free = float(row.get("free") or 0)
                locked = float(row.get("locked") or 0)
                return free + locked
        return 0.0
    if isinstance(rows, dict):
        free = float(rows.get("free") or 0)
        locked = float(rows.get("locked") or 0)
        return free + locked
    return 0.0


def fetch_futures_transfers(
    start_ms: int,
    end_ms: int | None = None,
    limit: int = 1000,
) -> list[dict]:
    """Income rows of type TRANSFER on USDT-M futures (deposits > 0, withdrawals < 0).

    Used to auto-sync the spot-transfer high-water mark when the user moves
    funds in/out of futures manually.
    """
    params: dict[str, str | int | float | bool] = {
        "incomeType": "TRANSFER",
        "startTime": int(start_ms),
        "limit": int(limit),
    }
    if end_ms is not None:
        params["endTime"] = int(end_ms)
    rows = _private_get("/fapi/v1/income", params)
    out: list[dict] = []
    for row in rows or []:
        out.append(
            {
                "tranId": str(row.get("tranId", "") or ""),
                "asset": str(row.get("asset", "") or "").upper(),
                "income": float(row.get("income") or 0),
                "time": int(row.get("time") or 0),
            }
        )
    return out


def _decimals_from_step(step: str) -> int:
    if not step or "e" in step.lower():
        return 8
    if "." not in step:
        return 0
    trimmed = step.rstrip("0")
    if trimmed.endswith("."):
        return 0
    return len(trimmed.split(".")[1])


def _load_exchange_info() -> dict:
    global _EXCHANGE_INFO_CACHE
    if _EXCHANGE_INFO_CACHE is None:
        _EXCHANGE_INFO_CACHE = _public_get("/fapi/v1/exchangeInfo", {})
    return _EXCHANGE_INFO_CACHE


def _parse_contract_spec(symbol: str, info: dict) -> ContractSpec:
    for item in info.get("symbols", []):
        if item.get("symbol") != symbol:
            continue
        if item.get("contractType") != "PERPETUAL":
            continue
        if item.get("status") != "TRADING":
            continue
        lot_step = "0.001"
        min_qty = 0.0
        min_notional = 5.0
        tick_size = "0.01"
        for filt in item.get("filters", []):
            ftype = filt.get("filterType")
            if ftype == "LOT_SIZE":
                lot_step = str(filt.get("stepSize", lot_step))
                min_qty = float(filt.get("minQty", 0))
            elif ftype == "MIN_NOTIONAL":
                min_notional = float(filt.get("notional", min_notional))
            elif ftype == "PRICE_FILTER":
                tick_size = str(filt.get("tickSize", tick_size))
        volume_place = _decimals_from_step(lot_step)
        price_place = _decimals_from_step(tick_size)
        return ContractSpec(
            symbol=symbol,
            volume_place=volume_place,
            price_place=price_place,
            min_trade_num=min_qty,
            min_trade_usdt=min_notional,
            size_multiplier=float(lot_step),
        )
    raise ExchangeClientError(f"Contract spec not found for {symbol}")


def fetch_contract_spec(symbol: str) -> ContractSpec:
    symbol = symbol.upper()
    if symbol not in _SPEC_CACHE:
        _SPEC_CACHE[symbol] = _parse_contract_spec(symbol, _load_exchange_info())
    return _SPEC_CACHE[symbol]


def fetch_candles_rest(
    symbol: str = SYMBOL,
    *,
    granularity: str = GRANULARITY,
    limit: int = CANDLE_LIMIT,
    max_retries: int = 3,
) -> list[Candle]:
    data = _public_get(
        "/fapi/v1/klines",
        {
            "symbol": symbol.upper(),
            "interval": granularity,
            "limit": str(limit),
        },
        max_retries=max_retries,
    )
    candles = [
        Candle(
            timestamp=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in data
    ]
    candles.sort(key=lambda c: c.timestamp)
    return candles


def fetch_candles(
    symbol: str = SYMBOL,
    *,
    granularity: str = GRANULARITY,
    limit: int = CANDLE_LIMIT,
    max_retries: int = 3,
) -> list[Candle]:
    global _last_candle_rest_mono

    symbol_u = symbol.upper()
    try:
        from src.exchange.binance_ws import get_candles_from_ws, watch_symbols

        watch_symbols([symbol_u])
        cached = get_candles_from_ws(symbol_u, granularity, limit)
        if cached is not None and len(cached) >= min(limit, 20):
            return cached
    except Exception:  # noqa: BLE001 — fall through to REST
        pass

    # Within cooldown: reuse last REST seed if it is still a valid closed series.
    try:
        from src.candles import is_candle_series_stale
        from src.config import BINANCE_CANDLE_REST_SEC, INTERVAL_MINUTES
        from src.exchange.binance_ws.cache import CACHE
        from src.exchange.binance_ws.manager import is_ws_enabled

        with _candle_rest_lock:
            last_rest = _candle_rest_at_mono.get(symbol_u, 0.0)
        if last_rest > 0 and (time.monotonic() - last_rest) < BINANCE_CANDLE_REST_SEC:
            if is_ws_enabled():
                rows = CACHE.get_candles(symbol_u, granularity, limit)
                if (
                    rows is not None
                    and len(rows) >= min(limit, 20)
                    and not is_candle_series_stale(rows, interval_minutes=INTERVAL_MINUTES)
                ):
                    return rows
    except Exception:  # noqa: BLE001
        pass

    if is_rate_limited():
        raise RateLimitError(
            f"Rate-limit cooldown active — {rate_limit_remaining_sec():.0f}s remaining, "
            "no candle cache yet (wait for WS history or ban end)"
        )

    try:
        from src.config import BINANCE_CANDLE_REST_STAGGER_SEC

        with _candle_rest_lock:
            gap = time.monotonic() - _last_candle_rest_mono
            wait = BINANCE_CANDLE_REST_STAGGER_SEC - gap if _last_candle_rest_mono > 0 else 0.0
        if wait > 0:
            time.sleep(wait)
    except Exception:  # noqa: BLE001
        pass

    candles = fetch_candles_rest(
        symbol_u, granularity=granularity, limit=limit, max_retries=max_retries
    )
    now = time.monotonic()
    with _candle_rest_lock:
        _candle_rest_at_mono[symbol_u] = now
        _last_candle_rest_mono = now
    try:
        from src.exchange.binance_ws.cache import CACHE
        from src.exchange.binance_ws.manager import is_ws_enabled
        from src.exchange.binance_ws.persist import save_candles_snapshot

        if is_ws_enabled():
            CACHE.set_candles(symbol_u, granularity, candles)
            save_candles_snapshot()
    except Exception:  # noqa: BLE001
        pass
    return candles


def _listing_age_ok(item: dict) -> bool:
    """Skip freshly listed contracts (MIN_LISTING_AGE_DAYS, 0 = off)."""
    from src.config import MIN_LISTING_AGE_DAYS

    if MIN_LISTING_AGE_DAYS <= 0:
        return True
    try:
        onboard_ms = float(item.get("onboardDate") or 0)
    except (TypeError, ValueError):
        return True
    if onboard_ms <= 0:
        return True
    age_ms = _now_ms() - onboard_ms
    return age_ms >= MIN_LISTING_AGE_DAYS * 86_400_000


def _scan_universe_from_info(info: dict) -> set[str]:
    return {
        item["symbol"]
        for item in info.get("symbols", [])
        if item.get("contractType") == "PERPETUAL"
        and item.get("status") == "TRADING"
        and item.get("quoteAsset") == "USDT"
        and item.get("marginAsset") == "USDT"
        and is_scan_symbol(str(item.get("symbol", "")))
        and _listing_age_ok(item)
    }


def fetch_top_futures_by_volume_rest(limit: int | None = None) -> list[tuple[str, float]]:
    info = _load_exchange_info()
    trading_perps = _scan_universe_from_info(info)
    tickers = _public_get("/fapi/v1/ticker/24hr", {})
    ranked: list[tuple[str, float]] = []
    for item in tickers:
        symbol = str(item.get("symbol", "")).upper()
        if symbol not in trading_perps:
            continue
        try:
            volume = float(item.get("quoteVolume") or 0)
        except (TypeError, ValueError):
            volume = 0.0
        if volume <= 0:
            continue
        ranked.append((symbol, volume))
    ranked.sort(key=lambda row: row[1], reverse=True)
    if limit is None:
        return ranked
    return ranked[:limit]


def fetch_top_futures_by_volume(limit: int | None = None) -> list[tuple[str, float]]:
    """Prefer miniTicker WS rank; throttled REST fallback when WS rank is empty."""
    global _volume_rank_rest_at_mono, _volume_rank_rest_cache

    try:
        from src.exchange.binance_ws.cache import CACHE
        from src.exchange.binance_ws.manager import is_ws_enabled
        from src.exchange.symbols import is_scan_symbol as _is_scan

        if is_ws_enabled():
            ranked_raw = CACHE.ranked_volumes()
            if ranked_raw and (CACHE.mini_ticker_seeded or len(ranked_raw) >= 30):
                ranked = [(s, v) for s, v in ranked_raw if _is_scan(s)]
                if not is_rate_limited():
                    try:
                        info = _load_exchange_info()
                        trading_perps = _scan_universe_from_info(info)
                        ranked = [(s, v) for s, v in ranked if s in trading_perps]
                    except Exception:  # noqa: BLE001
                        pass
                if ranked:
                    return ranked if limit is None else ranked[:limit]
    except Exception:  # noqa: BLE001
        pass

    if is_rate_limited():
        if _volume_rank_rest_cache:
            return _volume_rank_rest_cache if limit is None else _volume_rank_rest_cache[:limit]
        return []

    from src.config import BINANCE_VOLUME_RANK_REST_SEC

    now = time.monotonic()
    if _volume_rank_rest_at_mono > 0 and (now - _volume_rank_rest_at_mono) < BINANCE_VOLUME_RANK_REST_SEC:
        if _volume_rank_rest_cache:
            return _volume_rank_rest_cache if limit is None else _volume_rank_rest_cache[:limit]
        return []

    _volume_rank_rest_at_mono = now
    try:
        from src.exchange.binance_ws.manager import is_ws_enabled

        if is_ws_enabled():
            logging.info("Volume rank REST fallback — WS miniTicker empty or not seeded")
    except Exception:  # noqa: BLE001
        pass

    ranked = fetch_top_futures_by_volume_rest(limit=limit)
    if ranked:
        _volume_rank_rest_cache = ranked
        try:
            from src.exchange.binance_ws.cache import CACHE
            from src.exchange.binance_ws.manager import is_ws_enabled

            if is_ws_enabled():
                CACHE.set_quote_volumes({s: v for s, v in ranked}, seeded=True)
        except Exception:  # noqa: BLE001
            pass
    elif _volume_rank_rest_cache:
        return _volume_rank_rest_cache if limit is None else _volume_rank_rest_cache[:limit]
    return ranked


def fetch_futures_balance_rest(symbol: str = SYMBOL) -> FuturesAccountBalance:
    global _account_cache
    _ = symbol
    now = _now_ms()
    with _rest_cache_lock:
        cached = _account_cache
        if cached is not None and now - cached[0] < _REST_CACHE_TTL_MS:
            return cached[1]

    data = _private_get("/fapi/v2/account", {})
    available = float(data.get("availableBalance", 0))
    equity = float(data.get("totalMarginBalance", data.get("totalWalletBalance", 0)))
    maint = float(data.get("totalMaintMargin", 0) or 0)
    initial = float(data.get("totalInitialMargin", 0) or 0)
    balance = FuturesAccountBalance(
        margin_coin=MARGIN_COIN,
        available=available,
        account_equity=equity,
        usdt_equity=equity,
        total_maint_margin=maint,
        total_initial_margin=initial,
    )
    with _rest_cache_lock:
        _account_cache = (now, balance)
    return balance


def fetch_futures_balance(symbol: str = SYMBOL) -> FuturesAccountBalance:
    try:
        from src.exchange.binance_ws import flush_pending_reconcile, get_balance_from_ws

        flush_pending_reconcile()
        cached = get_balance_from_ws()
        if cached is not None:
            return cached
    except Exception:  # noqa: BLE001
        pass
    return fetch_futures_balance_rest(symbol)


def _empty_position(symbol: str) -> Position:
    return Position(symbol=symbol, side=None, size=0.0, avg_price=0.0, unrealized_pnl=0.0)


def _unrealized_from_row(row: dict, side: str, size: float, entry: float) -> float:
    pnl = float(row.get("unRealizedProfit", 0) or 0)
    if pnl != 0:
        return pnl
    mark = float(row.get("markPrice", 0) or 0)
    if mark <= 0 or entry <= 0 or size <= 0:
        return 0.0
    if side == "long":
        return (mark - entry) * size
    return (entry - mark) * size


def _parse_position_row(symbol: str, row: dict) -> Position | None:
    position_side = str(row.get("positionSide", "")).upper()
    if position_side not in ("LONG", "SHORT"):
        return None
    size = abs(float(row.get("positionAmt", 0) or 0))
    if size <= 0:
        return None
    side = position_side.lower()
    entry = float(row.get("entryPrice", 0) or 0)
    return Position(
        symbol=symbol,
        side=side,
        size=size,
        avg_price=entry,
        unrealized_pnl=_unrealized_from_row(row, side, size, entry),
    )


def _positions_dict_from_rows(symbol: str, rows: list) -> dict[str, Position]:
    result = {
        "long": _empty_position(symbol),
        "short": _empty_position(symbol),
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        pos = _parse_position_row(symbol, row)
        if pos and pos.side in result:
            result[pos.side] = pos
    return result


def fetch_symbol_positions_rest(symbol: str) -> dict[str, Position]:
    symbol = symbol.upper()
    now = _now_ms()
    with _rest_cache_lock:
        cached = _position_by_symbol.get(symbol)
        if cached is not None and now - cached[0] < _REST_CACHE_TTL_MS:
            return {
                "long": cached[1]["long"],
                "short": cached[1]["short"],
            }

    rows = _private_get("/fapi/v2/positionRisk", {"symbol": symbol})
    if not isinstance(rows, list):
        rows = []
    result = _positions_dict_from_rows(symbol, rows)
    with _rest_cache_lock:
        _position_by_symbol[symbol] = (now, result)
    return result


def fetch_symbol_positions(symbol: str) -> dict[str, Position]:
    try:
        from src.exchange.binance_ws import (
            flush_pending_reconcile,
            get_symbol_positions_from_ws,
            watch_symbols,
        )

        watch_symbols([symbol])
        flush_pending_reconcile()
        cached = get_symbol_positions_from_ws(symbol)
        if cached is not None:
            return cached
    except Exception:  # noqa: BLE001
        pass
    return fetch_symbol_positions_rest(symbol)


def fetch_all_open_positions_rest() -> list[Position]:
    global _positions_all
    now = _now_ms()
    with _rest_cache_lock:
        if _positions_all is not None and now - _positions_all[0] < _REST_CACHE_TTL_MS:
            return list(_positions_all[1])

    rows = _private_get("/fapi/v2/positionRisk", {})
    positions: list[Position] = []
    by_symbol: dict[str, dict[str, Position]] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol", "")).upper()
            if not symbol:
                continue
            pos = _parse_position_row(symbol, row)
            if not pos:
                continue
            positions.append(pos)
            bucket = by_symbol.setdefault(
                symbol,
                {"long": _empty_position(symbol), "short": _empty_position(symbol)},
            )
            if pos.side in bucket:
                bucket[pos.side] = pos

    with _rest_cache_lock:
        _positions_all = (now, positions)
        for sym, sides in by_symbol.items():
            _position_by_symbol[sym] = (now, sides)
    return list(positions)


def fetch_all_open_positions() -> list[Position]:
    try:
        from src.exchange.binance_ws import get_all_positions_from_ws

        cached = get_all_positions_from_ws()
        if cached is not None:
            return cached
    except Exception:  # noqa: BLE001
        pass
    return fetch_all_open_positions_rest()


def fetch_side_mark_price(symbol: str) -> float:
    try:
        from src.exchange.binance_ws import get_mark_from_ws

        mark = get_mark_from_ws(symbol)
        if mark is not None and mark > 0:
            return mark
    except Exception:  # noqa: BLE001
        pass
    data = _public_get("/fapi/v1/premiumIndex", {"symbol": symbol.upper()})
    if isinstance(data, list):
        data = data[0] if data else {}
    return float(data.get("markPrice", 0) or 0)


def fetch_side_unrealized_pnl(symbol: str, hold_side: str) -> float:
    hold_side = hold_side.lower()
    positions = fetch_symbol_positions(symbol)
    pos = positions.get(hold_side)
    if pos is None or pos.size <= 0:
        return 0.0
    return float(pos.unrealized_pnl or 0.0)


def fetch_total_unrealized_pnl(symbols: list[str]) -> tuple[float, int]:
    wanted = {s.upper() for s in symbols if s}
    total = 0.0
    open_count = 0
    try:
        all_positions = fetch_all_open_positions()
    except ExchangeClientError:
        return 0.0, 0
    for pos in all_positions:
        if wanted and pos.symbol.upper() not in wanted:
            continue
        if pos.size <= 0:
            continue
        total += float(pos.unrealized_pnl or 0.0)
        open_count += 1
    return total, open_count


def fetch_order_detail(symbol: str, order_id: str) -> dict:
    try:
        from src.exchange.binance_ws import get_order_detail_from_ws

        cached = get_order_detail_from_ws(order_id)
        if cached is not None:
            status = str(cached.get("status") or "").lower()
            if status in {"filled", "canceled", "cancelled", "expired", "rejected", "new", "partially_filled"}:
                return cached
    except Exception:  # noqa: BLE001
        pass
    data = _private_get(
        "/fapi/v1/order",
        {"symbol": symbol.upper(), "orderId": order_id},
    )
    status = str(data.get("status", "")).lower()
    avg_price = data.get("avgPrice")
    return {
        "orderId": str(data.get("orderId", order_id)),
        "status": status,
        "state": status,
        "avgPrice": avg_price,
        "priceAvg": avg_price,
    }


def fetch_pending_orders(symbol: str) -> list[PendingOrder]:
    try:
        from src.exchange.binance_ws import get_pending_from_ws

        cached = get_pending_from_ws(symbol)
        if cached is not None:
            return cached
    except Exception:  # noqa: BLE001
        pass
    rows = _private_get("/fapi/v1/openOrders", {"symbol": symbol.upper()})
    if not isinstance(rows, list):
        return []
    orders: list[PendingOrder] = []
    for item in rows:
        if str(item.get("type", "")).upper() != "LIMIT":
            continue
        orders.append(
            PendingOrder(
                order_id=str(item.get("orderId", "")),
                client_oid=str(item.get("clientOrderId", "")),
                side=str(item.get("side", "")).lower(),
                price=float(item.get("price", 0)),
                size=float(item.get("origQty", 0)),
            )
        )
    return orders


def _ignore_config_error(exc: ExchangeClientError, *codes: str) -> None:
    msg = str(exc)
    for code in codes:
        if code in msg:
            return
    lower = msg.lower()
    if "no need to change" in lower:
        return
    raise exc


def set_dual_side_position() -> None:
    try:
        _private_post("/fapi/v1/positionSide/dual", {"dualSidePosition": "true"})
    except ExchangeClientError as exc:
        _ignore_config_error(exc, "-4059", "-4061")


def set_margin_type(symbol: str) -> None:
    margin_type = "CROSSED" if MARGIN_MODE.lower() in ("crossed", "cross") else "ISOLATED"
    try:
        _private_post(
            "/fapi/v1/marginType",
            {"symbol": symbol.upper(), "marginType": margin_type},
        )
    except ExchangeClientError as exc:
        _ignore_config_error(exc, "-4046", "-4047")


def set_leverage(symbol: str, leverage: int = LEVERAGE) -> None:
    try:
        _private_post(
            "/fapi/v1/leverage",
            {"symbol": symbol.upper(), "leverage": leverage},
        )
    except ExchangeClientError as exc:
        _ignore_config_error(exc, "-4028")


def configure_symbol_trading(symbol: str) -> None:
    set_dual_side_position()
    set_margin_type(symbol)
    set_leverage(symbol)


def market_order_params(hold_side: str, trade_side: str) -> tuple[str, str]:
    """Return (side, positionSide) for Binance hedge mode."""
    hold_side = hold_side.lower()
    trade_side = trade_side.lower()
    if hold_side == "long":
        return ("BUY", "LONG") if trade_side == "open" else ("SELL", "LONG")
    return ("SELL", "SHORT") if trade_side == "open" else ("BUY", "SHORT")


def place_market_order(
    symbol: str,
    side: str,
    size: str,
    *,
    hold_side: str | None = None,
    trade_side: str | None = None,
    reduce_only: bool = False,
) -> dict:
    client_oid = f"bot_{uuid.uuid4().hex[:16]}"
    params: dict[str, str | int | float | bool] = {
        "symbol": symbol.upper(),
        "type": "MARKET",
        "quantity": size,
        "newClientOrderId": client_oid,
    }
    if hold_side and trade_side:
        order_side, position_side = market_order_params(hold_side, trade_side)
        params["side"] = order_side
        params["positionSide"] = position_side
        # Hedge mode: close via side + positionSide; reduceOnly is one-way only.
    else:
        params["side"] = side.upper()
        if reduce_only:
            params["reduceOnly"] = "true"
    result = _private_post("/fapi/v1/order", params)
    _invalidate_position_cache(symbol)
    try:
        from src.exchange.binance_ws import on_order_placed

        on_order_placed(symbol)
    except Exception as exc:  # noqa: BLE001
        logging.debug("Binance WS post-order reconcile skipped: %s", exc)
    return {
        "orderId": str(result.get("orderId", "")),
        "clientOid": str(result.get("clientOrderId", client_oid)),
        "avgPrice": result.get("avgPrice"),
        "status": str(result.get("status", "")).lower(),
    }


def close_position_side(symbol: str, hold_side: str, size: str) -> dict:
    return place_market_order(
        symbol,
        "",
        size,
        hold_side=hold_side,
        trade_side="close",
        reduce_only=True,
    )


class BinanceExchange:
    has_credentials = staticmethod(has_credentials)
    fetch_candles = staticmethod(fetch_candles)
    fetch_top_futures_by_volume = staticmethod(fetch_top_futures_by_volume)
    fetch_contract_spec = staticmethod(fetch_contract_spec)
    fetch_futures_balance = staticmethod(fetch_futures_balance)
    fetch_symbol_positions = staticmethod(fetch_symbol_positions)
    fetch_all_open_positions = staticmethod(fetch_all_open_positions)
    fetch_side_mark_price = staticmethod(fetch_side_mark_price)
    fetch_side_unrealized_pnl = staticmethod(fetch_side_unrealized_pnl)
    fetch_total_unrealized_pnl = staticmethod(fetch_total_unrealized_pnl)
    fetch_pending_orders = staticmethod(fetch_pending_orders)
    fetch_order_detail = staticmethod(fetch_order_detail)
    configure_symbol_trading = staticmethod(configure_symbol_trading)
    place_market_order = staticmethod(place_market_order)
    close_position_side = staticmethod(close_position_side)
    transfer_futures_to_spot = staticmethod(transfer_futures_to_spot)
    fetch_spot_balance = staticmethod(fetch_spot_balance)
    fetch_futures_transfers = staticmethod(fetch_futures_transfers)
