"""Lifecycle manager for Binance market + user WebSocket streams."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from src.config import (
    BINANCE_WS_DISCONNECT_NOTIFY_SEC,
    BINANCE_WS_ENABLED,
    BINANCE_WS_RECONCILE_SEC,
    BINANCE_WS_STALE_SEC,
    EXCHANGE,
    GRANULARITY,
    INTERVAL_MINUTES,
)
from src.exchange.binance_ws.cache import CACHE, _now
from src.exchange.binance_ws.market_stream import AllMarketStream, KlineStream
from src.exchange.binance_ws.user_stream import UserStream
from src.exchange.types import Candle, FuturesAccountBalance, PendingOrder, Position

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_stop_event: asyncio.Event | None = None
_started = False
_watch_symbols: set[str] = set()
_last_disconnect_notify_at = 0.0
_pending_reconcile = False
_pending_reconcile_symbols: set[str] = set()
_UDS_WAIT_SEC = 2.0
_UDS_POLL_SEC = 0.05
_uds_connect_count = 0
_listen_key_validated = False


def is_ws_enabled() -> bool:
    return BINANCE_WS_ENABLED and EXCHANGE == "binance"


def watch_symbols(symbols: list[str] | set[str]) -> None:
    """Ensure kline streams for these symbols (managed; scan candidates via fetch_candles)."""
    with _lock:
        _watch_symbols.update(s.upper() for s in symbols if s)


def set_watched_symbols(symbols: list[str] | set[str]) -> None:
    """Replace the watched kline set (prune streams no longer needed)."""
    desired = {s.upper() for s in symbols if s}
    with _lock:
        _watch_symbols.clear()
        _watch_symbols.update(desired)


def watched_symbols() -> set[str]:
    with _lock:
        return set(_watch_symbols)


def market_fresh(max_age: float | None = None) -> bool:
    age = CACHE.market_age_sec()
    if age is None:
        return False
    limit = BINANCE_WS_STALE_SEC if max_age is None else max_age
    return CACHE.market_connected and age <= limit


def _user_stream_alive(max_silence: float | None = None) -> bool:
    """True when UDS is connected and recently touched (recv or keepalive timeout)."""
    silence = BINANCE_WS_STALE_SEC * 4 if max_silence is None else max_silence
    with CACHE.lock:
        if not CACHE.user_connected or CACHE.user_last_msg_at <= 0:
            return False
        return (_now() - CACHE.user_last_msg_at) <= silence


def positions_fresh(max_age: float | None = None) -> bool:
    with CACHE.lock:
        if CACHE.positions_updated_at <= 0:
            return False
        age = _now() - CACHE.positions_updated_at
    limit = BINANCE_WS_STALE_SEC if max_age is None else max_age
    # Positions are event-driven; quiet books stay fresh while UDS is alive.
    if age <= max(limit, 120.0):
        return True
    return _user_stream_alive()


def account_fresh(max_age: float | None = None) -> bool:
    with CACHE.lock:
        if CACHE.account_updated_at <= 0:
            return False
        age = _now() - CACHE.account_updated_at
    limit = BINANCE_WS_RECONCILE_SEC if max_age is None else max_age
    if age <= limit:
        return True
    # Balance events are sparse — trust UDS while connected, soft-reconcile on timer.
    return _user_stream_alive() and age <= max(limit * 2, 600.0)


def _bootstrap_candles_rest(symbol: str, interval: str) -> None:
    """Disabled for mass-subscribe — caused 80× klines REST bursts and IP bans.

    Candle history is loaded lazily via fetch_candles() one symbol at a time.
    """
    _ = symbol, interval
    return


def _create_listen_key() -> str:
    """Reuse persisted listenKey; create via REST only when disk has none.

    Never REST-validate on boot/deploy — PUT validate was hitting HTTP 418 and
    writing a multi-hour IP cooldown after every railway up. Dead keys are
    cleared by keepalive -1125 or listenKeyExpired on the user stream.
    """
    global _listen_key_validated
    from src.exchange import binance as binance_mod
    from src.exchange.binance_ws.persist import load_listen_key, save_listen_key

    existing = load_listen_key()
    if existing:
        if not _listen_key_validated:
            _listen_key_validated = True
            logging.info("Binance listenKey reused from disk (no REST validate)")
        return existing

    wait = binance_mod.boot_optional_rest_wait_sec()
    if wait > 0:
        raise binance_mod.RateLimitError(
            f"Boot REST not ready — {wait:.0f}s remaining, request skipped"
        )
    with binance_mod.boot_optional_rest_slot():
        CACHE.bump_rest("listenKey_create")
        data = binance_mod._private_post("/fapi/v1/listenKey", {})
    key = str(data.get("listenKey") or "")
    if not key:
        raise RuntimeError("Empty listenKey from Binance")
    save_listen_key(key)
    _listen_key_validated = True
    logging.info("Binance listenKey created via REST")
    return key


def _keepalive_listen_key(listen_key: str) -> None:
    global _listen_key_validated
    from src.exchange import binance as binance_mod
    from src.exchange.binance_ws.persist import clear_listen_key

    if binance_mod.is_optional_rest_blocked():
        # PUT is REST — skip during ban/resume; key lasts ~60m without keepalive.
        logging.debug(
            "Binance listenKey keepalive skipped — rate-limit %.0fs left",
            binance_mod.optional_rest_blocked_sec(),
        )
        return
    CACHE.bump_rest("listenKey_keepalive")
    try:
        binance_mod._private_request(
            "PUT",
            "/fapi/v1/listenKey",
            {"listenKey": listen_key},
            max_retries=1,
        )
        _listen_key_validated = True
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        if "-1125" in detail or isinstance(exc, binance_mod.NonRetriableApiError):
            logging.warning("Binance listenKey keepalive dead key — clearing disk: %s", detail)
            clear_listen_key()
            _listen_key_validated = False
        raise


def reconcile_account_state(*, force: bool = False) -> None:
    """REST snapshot into WS cache (reconnect / after orders / soft cycle). Never on hot ban."""
    if not is_ws_enabled():
        return
    from src.exchange import binance as binance_mod

    if binance_mod.is_optional_rest_blocked():
        logging.debug(
            "Binance WS reconcile skipped — rate-limit %.0fs left",
            binance_mod.optional_rest_blocked_sec(),
        )
        return

    with CACHE.lock:
        last = CACHE.last_reconcile_at
    # last==0 means never reconciled (cold start without disk). Disk restore marks reconciled.
    due = force or last <= 0 or (_now() - last) >= BINANCE_WS_RECONCILE_SEC
    if not due:
        return

    try:
        if force:
            binance_mod._invalidate_position_cache()
        CACHE.bump_rest("reconcile_account")
        balance = binance_mod.fetch_futures_balance_rest()
        CACHE.set_balance(balance)

        with CACHE.lock:
            positions_empty = CACHE.positions_updated_at <= 0
        # Periodic cycle: account only. Full positionRisk every 5m stacked into 418.
        # Positions stay on UDS; full book only when forced or cache never filled.
        if force or positions_empty:
            CACHE.bump_rest("reconcile_positions")
            positions = binance_mod.fetch_all_open_positions_rest()
            by_symbol: dict[str, dict[str, Position]] = {}
            for pos in positions:
                bucket = by_symbol.setdefault(
                    pos.symbol.upper(),
                    {
                        "long": Position(symbol=pos.symbol, side=None, size=0.0, avg_price=0.0),
                        "short": Position(symbol=pos.symbol, side=None, size=0.0, avg_price=0.0),
                    },
                )
                if pos.side in bucket:
                    bucket[pos.side] = pos
            CACHE.set_positions(positions, by_symbol)
            logging.debug("Binance WS reconcile OK (%d positions)", len(positions))
        else:
            logging.debug("Binance WS reconcile account-only (skip full positionRisk)")
        CACHE.refresh_unrealized_from_marks()
        CACHE.mark_reconciled()
        try:
            from src.exchange.binance_ws.persist import save_account_snapshot

            save_account_snapshot()
        except Exception:  # noqa: BLE001
            pass
    except binance_mod.RateLimitError as exc:
        logging.warning("Binance WS reconcile paused (rate limit): %s", exc)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Binance WS reconcile failed: %s", exc)


def _on_user_stream_connected() -> None:
    """UDS connected: never REST on first connect (disk/WS); soft cycle reconciles later.

    Deploy/restart used to force one account/positionRisk REST here and re-trigger
    HTTP 418 even when the bot had been running fine on WS-only. Mid-run UDS
    reconnects still reconcile once IP is clear to catch missed events.
    """
    global _uds_connect_count
    _uds_connect_count += 1
    from src.exchange import binance as binance_mod

    with CACHE.lock:
        has_cache = CACHE.balance is not None or CACHE.positions_updated_at > 0

    # During 418 cooldown, disk/WS cache is the only safe source — do not REST.
    if binance_mod.is_optional_rest_blocked():
        logging.info(
            "Binance UDS connect — skip REST reconcile (rate-limit %.0fs, using disk/WS cache)",
            binance_mod.optional_rest_blocked_sec(),
        )
        return

    if _uds_connect_count == 1:
        if has_cache:
            logging.info(
                "Binance UDS first connect — skip REST (disk/WS cache; soft reconcile later)"
            )
        else:
            logging.info(
                "Binance UDS first connect — skip REST (no disk account; deferred reconcile when IP clear)"
            )
        return

    # Mid-run reconnect: soft reconcile only if due — force=True after every flap
    # re-triggered IP bans. Soft cycle (every BINANCE_WS_RECONCILE_SEC) is enough.
    reconcile_account_state(force=False)


def seed_volume_rank_from_rest() -> None:
    """Optional heavy ticker/24hr seed — off by default when market WS is enabled."""
    from src.config import BINANCE_WS_REST_TICKER_SEED
    from src.exchange import binance as binance_mod

    if not BINANCE_WS_REST_TICKER_SEED:
        logging.debug("Binance WS ticker REST seed disabled (use miniTicker)")
        return
    if binance_mod.is_optional_rest_blocked():
        logging.info(
            "Binance WS volume seed deferred — rate-limit %.0fs left",
            binance_mod.optional_rest_blocked_sec(),
        )
        return
    CACHE.bump_rest("ticker_seed")
    ranked = binance_mod.fetch_top_futures_by_volume_rest()
    CACHE.set_quote_volumes({s: v for s, v in ranked}, seeded=True)


def maybe_notify_disconnect() -> None:
    global _last_disconnect_notify_at
    if not is_ws_enabled():
        return
    from src.exchange import binance as binance_mod

    # During IP ban/resume, user WS cannot create listenKey — expected, don't spam Discord.
    if binance_mod.is_optional_rest_blocked():
        return
    now = time.time()
    if now - _last_disconnect_notify_at < 180:
        return
    market_down = CACHE.market_disconnect_since
    user_down = CACHE.user_disconnect_since
    kline_down = CACHE.kline_disconnect_since
    msgs: list[str] = []
    if market_down is not None and (_now() - market_down) >= BINANCE_WS_DISCONNECT_NOTIFY_SEC:
        msgs.append(f"all-market WS down {(_now() - market_down):.0f}s")
    if kline_down is not None and (_now() - kline_down) >= BINANCE_WS_DISCONNECT_NOTIFY_SEC:
        msgs.append(f"kline WS down {(_now() - kline_down):.0f}s")
    if user_down is not None and (_now() - user_down) >= BINANCE_WS_DISCONNECT_NOTIFY_SEC:
        msgs.append(f"user WS down {(_now() - user_down):.0f}s")
    if not msgs:
        return
    _last_disconnect_notify_at = now
    try:
        from src.notify import notify_error

        notify_error("Binance WebSocket", "; ".join(msgs))
    except Exception:  # noqa: BLE001
        logging.warning("Binance WS disconnect notify failed")


async def _async_main() -> None:
    assert _stop_event is not None
    all_market = AllMarketStream(stop_event=_stop_event)
    klines = KlineStream(
        interval=GRANULARITY,
        symbols_provider=watched_symbols,
        stop_event=_stop_event,
    )
    user = UserStream(
        create_listen_key=_create_listen_key,
        keepalive_listen_key=_keepalive_listen_key,
        on_reconnect=_on_user_stream_connected,
        stop_event=_stop_event,
    )

    async def _health_watch() -> None:
        while not _stop_event.is_set():
            maybe_notify_disconnect()
            await asyncio.sleep(30)

    async def _deferred_rest_after_market() -> None:
        """Cold-start only: REST reconcile if disk had no account snapshot.

        When disk restore marked reconciled, this path stays idle — UDS + soft
        cycle reconcile (every BINANCE_WS_RECONCILE_SEC) keep state correct.
        """
        from src.exchange import binance as binance_mod

        while not _stop_event.is_set():
            wait = binance_mod.boot_optional_rest_wait_sec()
            if wait > 0:
                await asyncio.sleep(min(wait + 2.0, 60.0))
                continue
            if not market_fresh(max_age=BINANCE_WS_STALE_SEC * 2):
                await asyncio.sleep(5)
                continue
            try:
                if CACHE.last_reconcile_at <= 0:
                    logging.info(
                        "Binance WS cold start — deferred REST reconcile (no disk account)"
                    )

                    def _boot_reconcile() -> None:
                        with binance_mod.boot_optional_rest_slot():
                            reconcile_account_state(force=True)

                    await asyncio.to_thread(_boot_reconcile)
                if not CACHE.mini_ticker_seeded:
                    await asyncio.to_thread(seed_volume_rank_from_rest)
            except Exception as exc:  # noqa: BLE001
                logging.debug("Binance WS deferred reconcile: %s", exc)
            await asyncio.sleep(60)

    await asyncio.gather(
        all_market.run(),
        klines.run(),
        user.run(),
        _health_watch(),
        _deferred_rest_after_market(),
    )


def start_binance_ws() -> None:
    global _loop, _thread, _stop_event, _started, _uds_connect_count
    if not is_ws_enabled():
        logging.info("Binance WS disabled (BINANCE_WS_ENABLED=false or not binance)")
        return
    with _lock:
        if _started:
            return
        _started = True
        _uds_connect_count = 0

    from src.exchange import binance as binance_mod

    # Zero REST on boot — ticker/24hr + force reconcile here caused 418 on every deploy.
    from src.exchange.binance_ws.persist import (
        load_account_snapshot,
        load_candles_snapshot,
        load_listen_key,
    )

    n_candles = load_candles_snapshot()
    loaded_acct = load_account_snapshot()
    if n_candles or loaded_acct:
        logging.info(
            "Binance WS disk restore: candles_symbols=%d account=%s listenKey=%s",
            n_candles,
            "yes" if loaded_acct else "no",
            "yes" if load_listen_key() else "no",
        )

    try:
        from src.rsi_rev.config import SYMBOLS
        from src.rsi_rev import store

        open_rows = store.get_open_lots()
        managed = list(dict.fromkeys(
            [*(SYMBOLS or ()), *[str(row["symbol"]) for row in open_rows]]
        ))
        if managed:
            set_watched_symbols(managed)
            logging.info("Binance WS pre-subscribe klines for %s", ",".join(managed))
    except Exception as exc:  # noqa: BLE001
        logging.debug("Binance WS open-symbol preload skipped: %s", exc)

    remaining = binance_mod.rate_limit_remaining_sec()
    if remaining > 0:
        logging.warning(
            "Binance WS starting in REST-pause mode — rate-limit %.0fs left "
            "(restored from disk; market/user WS without new REST)",
            remaining,
        )
    else:
        logging.info(
            "Binance WS starting without REST seed — miniTicker + deferred reconcile"
        )

    _stop_event = asyncio.Event()
    _loop = asyncio.new_event_loop()

    def _runner() -> None:
        assert _loop is not None
        asyncio.set_event_loop(_loop)
        try:
            _loop.run_until_complete(_async_main())
        finally:
            _loop.close()

    _thread = threading.Thread(target=_runner, name="binance-ws", daemon=True)
    _thread.start()
    logging.info("Binance WS manager started (all-market + kline + user data)")


def stop_binance_ws() -> None:
    global _started, _loop, _thread, _stop_event
    if _stop_event is not None:
        _loop.call_soon_threadsafe(_stop_event.set) if _loop else _stop_event.set()
    if _thread is not None and _thread.is_alive():
        _thread.join(timeout=5)
    with _lock:
        _started = False
        _thread = None
        _loop = None
        _stop_event = None


def get_candles_from_ws(
    symbol: str,
    interval: str,
    limit: int,
    *,
    quiet: bool = False,
) -> list[Candle] | None:
    if not is_ws_enabled():
        return None
    from src.candles import is_candle_series_stale

    watch_symbols([symbol])
    candles = CACHE.get_candles(symbol, interval, limit)
    if candles is not None and len(candles) >= min(limit, 20):
        if not is_candle_series_stale(candles, interval_minutes=INTERVAL_MINUTES):
            return candles
        if not quiet:
            logging.info(
                "Binance WS candle cache stale for %s — will REST refresh",
                symbol.upper(),
            )
    return None


def kline_fresh(symbol: str, max_age: float | None = None) -> bool:
    """True when this symbol recently received a kline WS update."""
    from src.config import BINANCE_WS_KLINE_SILENCE_SEC

    age = CACHE.candle_age_sec(symbol)
    if age is None:
        return False
    limit = BINANCE_WS_KLINE_SILENCE_SEC if max_age is None else max_age
    return CACHE.kline_connected and age <= limit


def get_volume_rank_from_ws(limit: int | None = None) -> list[tuple[str, float]] | None:
    if not is_ws_enabled():
        return None
    if not CACHE.mini_ticker_seeded and not market_fresh():
        return None
    ranked = CACHE.ranked_volumes()
    if not ranked:
        return None
    if limit is None:
        return ranked
    return ranked[:limit]


def get_mark_from_ws(symbol: str) -> float | None:
    if not is_ws_enabled() or not market_fresh(max_age=BINANCE_WS_STALE_SEC * 2):
        return None
    return CACHE.get_mark(symbol)


def get_balance_from_ws() -> FuturesAccountBalance | None:
    if not is_ws_enabled() or not account_fresh():
        return None
    return CACHE.get_balance()


def get_symbol_positions_from_ws(symbol: str) -> dict[str, Position] | None:
    if not is_ws_enabled() or not positions_fresh():
        return None
    sides = CACHE.get_symbol_positions(symbol)
    if sides is None:
        # Known empty after reconcile: return empty hedge sides
        if CACHE.positions_updated_at > 0:
            return {
                "long": Position(symbol=symbol.upper(), side=None, size=0.0, avg_price=0.0),
                "short": Position(symbol=symbol.upper(), side=None, size=0.0, avg_price=0.0),
            }
        return None
    return sides


def get_symbol_positions_lenient(symbol: str) -> dict[str, Position] | None:
    """Return last WS snapshot even when cache is marked dirty after an order.

    Avoids per-read REST while UDS is catching up. Callers that need a hard
    refresh must use flush_pending_reconcile / symbol-scoped REST explicitly.
    """
    if not is_ws_enabled():
        return None
    sides = CACHE.get_symbol_positions(symbol)
    if sides is None:
        return None
    return sides


def get_all_positions_from_ws() -> list[Position] | None:
    if not is_ws_enabled() or not positions_fresh():
        return None
    return CACHE.get_all_positions()


def get_pending_from_ws(symbol: str) -> list[PendingOrder] | None:
    """Pending LIMIT orders from UDS.

    Returns [] when UDS is alive but this symbol has no cached entry — RSI hedge
    path is market-only, so missing key must NOT fall through to openOrders REST
    (that was ~20 REST calls every 5m cycle and stacked into HTTP 418).
    Returns None only when WS/UDS is unavailable so callers may REST-fallback.
    """
    if not is_ws_enabled():
        return None
    if not _user_stream_alive() and not positions_fresh():
        return None
    cached = CACHE.get_pending(symbol)
    return [] if cached is None else cached


def get_order_detail_from_ws(order_id: str) -> dict | None:
    if not is_ws_enabled():
        return None
    return CACHE.get_order_detail(order_id)


def on_order_placed(symbol: str) -> None:
    """Mark cache dirty after an order; defer REST reconcile to flush_pending_reconcile."""
    global _pending_reconcile
    if not is_ws_enabled():
        return
    with _lock:
        _pending_reconcile = True
        if symbol:
            _pending_reconcile_symbols.add(symbol.upper())
    with CACHE.lock:
        # Only invalidate positions — zeroing account too forced a full REST
        # reconcile whenever UDS was >0.5s late, which stacked into 418 bans.
        CACHE.positions_updated_at = 0.0
    watch_symbols([symbol])


def note_uds_position_refresh(symbols: set[str] | None = None) -> None:
    """Clear pending reconcile for symbols refreshed via UDS ACCOUNT_UPDATE."""
    global _pending_reconcile
    if not is_ws_enabled():
        return
    with _lock:
        if not _pending_reconcile:
            return
        if symbols:
            for sym in symbols:
                _pending_reconcile_symbols.discard(sym.upper())
        if not _pending_reconcile_symbols:
            _pending_reconcile = False


def _reconcile_symbols_rest(symbols: set[str]) -> bool:
    """Refresh only the dirty symbols via positionRisk?symbol= (+ one account)."""
    if not symbols:
        return True
    from src.exchange import binance as binance_mod

    if binance_mod.is_optional_rest_blocked():
        return False
    try:
        CACHE.bump_rest("reconcile_account")
        balance = binance_mod.fetch_futures_balance_rest()
        CACHE.set_balance(balance)

        with CACHE.lock:
            by_symbol = {
                sym: {"long": sides["long"], "short": sides["short"]}
                for sym, sides in CACHE.positions_by_symbol.items()
            }
        for sym in sorted(symbols):
            CACHE.bump_rest("reconcile_symbol")
            sides = binance_mod.fetch_symbol_positions_rest(sym)
            by_symbol[sym.upper()] = sides
        all_positions: list[Position] = []
        for _sym, sides in by_symbol.items():
            for side_name in ("long", "short"):
                pos = sides[side_name]
                if pos.size > 0:
                    all_positions.append(pos)
        CACHE.set_positions(all_positions, by_symbol)
        CACHE.refresh_unrealized_from_marks()
        CACHE.mark_reconciled()
        try:
            from src.exchange.binance_ws.persist import save_account_snapshot

            save_account_snapshot()
        except Exception:  # noqa: BLE001
            pass
        logging.debug(
            "Binance WS symbol reconcile OK (%s)",
            ",".join(sorted(symbols)),
        )
        return True
    except binance_mod.RateLimitError as exc:
        logging.warning("Binance WS symbol reconcile paused (rate limit): %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001
        logging.warning("Binance WS symbol reconcile failed: %s", exc)
        return False


def flush_pending_reconcile(*, wait_uds_sec: float | None = None) -> bool:
    """Wait briefly for UDS; if still dirty, REST-refresh only pending symbols.

    Returns True when a REST symbol reconcile ran. Never clears pending on failure
    (avoids falling into per-call positionRisk storms after a ban).
    """
    global _pending_reconcile
    if not is_ws_enabled():
        return False
    with _lock:
        if not _pending_reconcile:
            return False
        symbols = set(_pending_reconcile_symbols)

    wait = _UDS_WAIT_SEC if wait_uds_sec is None else max(0.0, wait_uds_sec)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        with _lock:
            # UDS may have cleared pending via note_uds_position_refresh.
            if not _pending_reconcile:
                return False
            symbols = set(_pending_reconcile_symbols)
        if positions_fresh(max_age=max(BINANCE_WS_STALE_SEC, wait + 1.0)):
            with _lock:
                _pending_reconcile = False
                _pending_reconcile_symbols.clear()
            logging.debug(
                "Binance post-order cache refreshed via UDS (skip REST) symbols=%s",
                sorted(symbols) or "—",
            )
            return False
        time.sleep(_UDS_POLL_SEC)

    with _lock:
        symbols = set(_pending_reconcile_symbols)
    ok = _reconcile_symbols_rest(symbols)
    if ok:
        with _lock:
            _pending_reconcile = False
            _pending_reconcile_symbols.clear()
    return ok


def pending_reconcile() -> bool:
    with _lock:
        return _pending_reconcile


def log_rest_stats_if_any() -> None:
    counts = CACHE.reset_rest_counts()
    if counts:
        logging.info("Binance REST via WS path: %s", counts)
