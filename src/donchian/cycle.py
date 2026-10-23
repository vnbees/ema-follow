"""Donchian parallel-trend cycle — runs every INTERVAL (default 15m)."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

import uvicorn

from src.bot_state import is_trading_enabled, set_last_cycle_at, update_account_balance
from src.config import (
    BINANCE_CLEAR_RATE_LIMIT,
    EXCHANGE_DISPLAY_NAME,
    GRANULARITY,
    LOG_DIR,
    REST_BOOT_GAP_SEC,
    REST_BOOT_QUIET_SEC,
    SPOT_TRANSFER_DAY_CAP_PCT,
    SPOT_TRANSFER_DD_PAUSE_PCT,
    SPOT_TRANSFER_EXECUTE_HHMM,
    SPOT_TRANSFER_MODE,
    SPOT_TRANSFER_MONTH_CAP_PCT,
    SPOT_TRANSFER_SKIM,
    WEB_PORT,
)
from src.database import init_db, insert_equity_snapshot
from src.exchange import ExchangeClientError, has_credentials
from src.exchange.types import Candle
from src.notify import notify_error, install_error_hooks
from src.donchian import store
from src.donchian.config import (
    ADD_ATR_MULT,
    ATR_PERIOD,
    CANDLE_LIMIT,
    DONCHIAN_PERIOD,
    DUAL_LIMIT_ENABLED,
    ENTRY_TTL_BARS,
    FIXED_SCAN_SYMBOLS,
    INTERVAL,
    LEVERAGE,
    MARGIN_CAP_PCT,
    MARGIN_PCT,
    MAX_BODY_ATR,
    MAX_COINS,
    MAX_OPEN,
    MIN_BODY_ATR,
    MIN_POT_RR,
    MIN_VOL_RATIO,
    PARALLEL_TOL,
    SCAN_MODE,
    SIZE_BY_RR,
    SLOPE_LOOKBACK,
    STALE_OPP_HOURS,
    SYMBOL_FILTER_ENABLED,
    TOP_K_PER_CYCLE,
    TOP_N_SYMBOLS,
    VOL_RATIO_PERIOD,
    WARMUP_MIN_BARS,
    is_excluded_symbol,
    is_untradable,
    mark_untradable,
)
from src.donchian.signals import DonchianBar, EntrySignal, SignalState, process_closed_bars
from src.donchian.symbol_filter import filter_ranked_symbols, is_scan_eligible
from src.donchian.trading import live_account_balance, open_lot
from src.donchian.watcher import start_watcher
from src.web.app import app as web_app

_INTERVAL_MINUTES = int(INTERVAL.rstrip("m").rstrip("h")) * (60 if INTERVAL.endswith("h") else 1)
_first_cycle = True
# Chờ WS đẩy nến vừa đóng trước khi scan — tránh REST seed giả ở boundary.
_BOUNDARY_WS_GRACE_SEC = 8.0
_WS_CLOSE_WAIT_SEC = 12.0

_warmup_lock = threading.Lock()
_warmup_running = False
_warmup_pending: list[str] = []

_signal_states: dict[str, SignalState] = {}
_states_lock = threading.Lock()

_cached_symbols: list[str] = []
_symbols_refreshed_at: float = 0.0
_SYMBOLS_REFRESH_INTERVAL_SEC = 4 * 3600  # refresh top-N mỗi 4h


def setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / "donchian.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True,
    )


def seconds_until_next_interval() -> float:
    interval_seconds = _INTERVAL_MINUTES * 60
    now = datetime.now(timezone.utc)
    elapsed = (now.minute % _INTERVAL_MINUTES) * 60 + now.second
    remaining = interval_seconds - elapsed
    if remaining <= 0:
        remaining = interval_seconds
    return float(remaining) + _BOUNDARY_WS_GRACE_SEC


def start_web_server() -> None:
    try:
        config = uvicorn.Config(web_app, host="0.0.0.0", port=WEB_PORT, log_level="warning")
        server = uvicorn.Server(config)
        server.run()
    except Exception as exc:  # noqa: BLE001
        logging.error("Dashboard server failed: %s", exc, extra={"skip_discord": True})
        notify_error("Dashboard server", str(exc), cooldown_sec=30)


def _top_symbols() -> list[str]:
    """Scan pool: fixed BT-20 list or top-N by 24h quote volume (cached 4h)."""
    global _cached_symbols, _symbols_refreshed_at

    now = time.monotonic()
    if SCAN_MODE == "fixed":
        syms = [
            s
            for s in FIXED_SCAN_SYMBOLS
            if not is_excluded_symbol(s) and not is_untradable(s)
        ]
        if syms != _cached_symbols:
            _cached_symbols = syms
            _symbols_refreshed_at = now
            logging.info(
                "Fixed scan pool (%d, BT majors): %s",
                len(syms),
                ", ".join(syms),
            )
        return syms

    if _cached_symbols and (now - _symbols_refreshed_at) < _SYMBOLS_REFRESH_INTERVAL_SEC:
        return [s for s in _cached_symbols if not is_untradable(s)]

    try:
        from src.exchange.binance_ws.cache import CACHE

        vols = dict(CACHE.quote_volumes)
        if not vols:
            return _cached_symbols  # giữ cache cũ nếu chưa có data
        ranked = sorted(
            [(sym, vol) for sym, vol in vols.items() if not is_excluded_symbol(sym) and not is_untradable(sym)],
            key=lambda x: x[1],
            reverse=True,
        )
        try:
            from src.exchange.binance import fetch_contract_spec
            from src.exchange.protocol import ExchangeClientError as _ExchangeClientError

            def _tradable(sym: str) -> bool:
                try:
                    fetch_contract_spec(sym)
                    return True
                except _ExchangeClientError as exc:
                    if "not found" in str(exc).lower():
                        logging.info("  [%s] skip — no USDT-M perpetual spec", sym)
                        mark_untradable(sym)
                        return False
                    return True
                except Exception:
                    return True

            pool = ranked[: max(TOP_N_SYMBOLS * 5, TOP_N_SYMBOLS)]
            tradable_pool = [(sym, vol) for sym, vol in pool if _tradable(sym)]
            new_symbols, skipped = filter_ranked_symbols(
                tradable_pool, limit=TOP_N_SYMBOLS, interval=INTERVAL
            )
            if skipped:
                sample = ", ".join(f"{s}({r})" for s, r in skipped[:8])
                extra = f" +{len(skipped) - 8} more" if len(skipped) > 8 else ""
                logging.info(
                    "Donchian symbol filter skipped %d: %s%s",
                    len(skipped),
                    sample,
                    extra,
                )
        except Exception:  # noqa: BLE001
            tradable = [(sym, vol) for sym, vol in ranked[: TOP_N_SYMBOLS * 5] if not is_untradable(sym)]
            new_symbols, skipped = filter_ranked_symbols(tradable, limit=TOP_N_SYMBOLS, interval=INTERVAL)
            if skipped:
                logging.info("Donchian symbol filter skipped %d (fallback path)", len(skipped))
        if new_symbols:
            _cached_symbols = new_symbols
            _symbols_refreshed_at = now
            logging.info(
                "Top-%d scan pool reset: %s",
                len(new_symbols),
                ", ".join(new_symbols),
            )
        return _cached_symbols
    except Exception:  # noqa: BLE001
        return _cached_symbols


def _sync_watched(symbols: list[str]) -> None:
    try:
        from src.config import EXCHANGE
        from src.exchange.binance_ws import is_ws_enabled, set_watched_symbols

        if EXCHANGE != "binance" or not is_ws_enabled():
            return
        watched = list(dict.fromkeys(symbols))
        for sym in sorted(store.active_coins()):
            if sym not in watched:
                watched.append(sym)
        set_watched_symbols(watched)
    except Exception as exc:  # noqa: BLE001
        logging.debug("Donchian WS watch sync skipped: %s", exc)


def _boot_quiet_active() -> bool:
    try:
        from src.config import EXCHANGE
        from src.exchange import binance as binance_mod

        return EXCHANGE == "binance" and binance_mod.is_boot_rest_quiet()
    except Exception:  # noqa: BLE001
        return False


def _wait_binance_ws_ready() -> None:
    for _ in range(60):
        time.sleep(0.5)
        try:
            from src.exchange.binance_ws.cache import CACHE

            if CACHE.kline_connected:
                return
        except Exception:  # noqa: BLE001
            return
    logging.warning("Binance kline WS not connected after 30s — Donchian cycle waits for WS")
    notify_error("Binance kline WS", "kline WS not connected after 30s")


def _maybe_clear_stale_rate_limit() -> None:
    if not BINANCE_CLEAR_RATE_LIMIT:
        return
    try:
        from src.config import EXCHANGE
        from src.exchange import binance as binance_mod

        if EXCHANGE != "binance":
            return
        if binance_mod.clear_rate_limit_cooldown():
            logging.warning("Binance REST cooldown cleared on boot (BINANCE_CLEAR_RATE_LIMIT)")
    except Exception as exc:  # noqa: BLE001
        logging.debug("Binance cooldown clear skipped: %s", exc)


def _ws_closed_count(symbol: str) -> tuple[int, int]:
    """Return (count_closed, last_closed_ts_ms) from WS/disk cache.

    Reads directly from CACHE to avoid global INTERVAL_MINUTES staleness check
    (bot uses 15m candles but global config may be 5m).
    last_closed_ts_ms=0 if no closed candles.
    """
    try:
        from src.exchange.binance_ws.cache import CACHE

        raw = CACHE.get_candles(symbol.upper(), INTERVAL, CANDLE_LIMIT)
        if not raw:
            return 0, 0
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        interval_ms = _INTERVAL_MINUTES * 60 * 1000
        period_start = (now_ms // interval_ms) * interval_ms
        closed = [c for c in raw if c.timestamp < period_start]
        last_ts = int(closed[-1].timestamp) if closed else 0
        return len(closed), last_ts
    except Exception:  # noqa: BLE001
        return 0, 0


def _expected_last_ts(now_ms: int | None = None) -> int:
    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    interval_ms = _INTERVAL_MINUTES * 60 * 1000
    return (now_ms // interval_ms) * interval_ms - interval_ms


def _series_has_hole(symbol: str) -> bool:
    """True if the last WARMUP_MIN_BARS closed candles skip an interval."""
    try:
        from src.exchange.binance_ws.cache import CACHE

        raw = CACHE.get_candles(symbol.upper(), INTERVAL, CANDLE_LIMIT)
        if not raw:
            return True
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        interval_ms = _INTERVAL_MINUTES * 60 * 1000
        period_start = (now_ms // interval_ms) * interval_ms
        closed = [c for c in raw if c.timestamp < period_start]
        recent = closed[-WARMUP_MIN_BARS:]
        if len(recent) < 2:
            return False
        for a, b in zip(recent, recent[1:]):
            gap = int(b.timestamp) - int(a.timestamp)
            if gap != interval_ms:
                return True
        return False
    except Exception:  # noqa: BLE001
        return True


def _gap_bars(symbol: str) -> int:
    """Số nến đóng bị thiếu so với nến vừa đóng. 0 = đủ. 999 = cache rỗng/ngắn/lỗ giữa chuỗi."""
    count, last_ts = _ws_closed_count(symbol)
    if count < WARMUP_MIN_BARS or last_ts <= 0:
        return 999
    expected_last = _expected_last_ts()
    interval_ms = _INTERVAL_MINUTES * 60 * 1000
    if last_ts < expected_last:
        return max(1, int((expected_last - last_ts) // interval_ms))
    if _series_has_hole(symbol):
        return 999
    return 0


def _candle_cache_stale(symbol: str) -> bool:
    """True khi thiếu nến thật (cache ngắn hoặc gap ≥ 1 sau khi đã chờ WS)."""
    return _gap_bars(symbol) >= 1


def _wait_for_just_closed(symbols: list[str], timeout_sec: float = _WS_CLOSE_WAIT_SEC) -> None:
    """Chờ WS đẩy nến vừa đóng. Gap đúng 1 nến lúc boundary thường là trễ WS, không phải downtime."""
    deadline = time.monotonic() + timeout_sec
    pending = [s for s in symbols if _gap_bars(s) == 1]
    if not pending:
        return
    logging.info("Waiting up to %.0fs for %d just-closed kline(s) via WS", timeout_sec, len(pending))
    while time.monotonic() < deadline:
        pending = [s for s in symbols if _gap_bars(s) == 1]
        if not pending:
            logging.info("Just-closed klines arrived via WS")
            return
        time.sleep(0.5)
    logging.info("WS still missing just-closed bar for %d symbol(s) — will REST seed", len(pending))


def _warmup_symbol(symbol: str) -> str:
    """Seed REST klines nếu cache thiếu hoặc có gap sau downtime.

    Returns ready|seeded|blocked|failed.
    """
    from src.exchange import ExchangeClientError, fetch_candles
    from src.exchange.binance import RateLimitError

    if _gap_bars(symbol) == 0:
        return "ready"

    count, last_ts = _ws_closed_count(symbol)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    interval_ms = _INTERVAL_MINUTES * 60 * 1000
    expected_last = (now_ms // interval_ms) * interval_ms - interval_ms

    # Log lý do cần seed
    if count < WARMUP_MIN_BARS:
        logging.info("  [%s] kline cache short (%d<%d) — REST seed", symbol, count, WARMUP_MIN_BARS)
    else:
        gap_bars = max(0, (expected_last - last_ts) // interval_ms)
        logging.info("  [%s] kline cache gap ~%d bars — REST seed", symbol, gap_bars)

    try:
        from src.config import EXCHANGE
        from src.exchange import binance as binance_mod

        if EXCHANGE == "binance" and binance_mod.is_optional_rest_blocked():
            logging.info(
                "  [%s] Skip REST kline warmup — weight/cooldown %.0fs",
                symbol, binance_mod.optional_rest_blocked_sec(),
            )
            return "blocked"
    except Exception:  # noqa: BLE001
        pass

    try:
        from src.config import EXCHANGE
        from src.exchange import binance as binance_mod

        if EXCHANGE == "binance":
            with binance_mod.boot_optional_rest_slot():
                fetch_candles(symbol=symbol, granularity=INTERVAL, limit=CANDLE_LIMIT, require_confirmed=False)
        else:
            fetch_candles(symbol=symbol, granularity=INTERVAL, limit=CANDLE_LIMIT, require_confirmed=False)
        logging.info("  [%s] REST kline warmup seeded", symbol)
        return "seeded"
    except RateLimitError as exc:
        logging.info("  [%s] REST kline warmup blocked: %s", symbol, exc)
        return "blocked"
    except ExchangeClientError as exc:
        logging.warning("  [%s] REST kline warmup failed: %s", symbol, exc)
        return "failed"


def _boot_warmup_loop(symbols: list[str]) -> None:
    logging.info(
        "Boot REST warmup started (quiet=%.0fs gap=%.0fs) symbols=%d",
        REST_BOOT_QUIET_SEC, REST_BOOT_GAP_SEC, len(symbols),
    )
    for symbol in symbols:
        fails = 0
        while True:
            status = _warmup_symbol(symbol)
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
            # blocked: chờ ngắn rồi thử lại (rate limit sẽ tự expire)
            time.sleep(5)
    logging.info("Boot REST warmup finished")


def _start_boot_warmup(symbols: list[str]) -> None:
    global _warmup_running
    if not symbols:
        return
    with _warmup_lock:
        known = set(_warmup_pending)
        added = [s for s in symbols if s not in known]
        _warmup_pending.extend(added)
        if _warmup_running:
            if added:
                logging.info("Warmup running — queued %d more symbol(s)", len(added))
            return
        _warmup_running = True

    def _run() -> None:
        global _warmup_running
        try:
            while True:
                with _warmup_lock:
                    batch = list(_warmup_pending)
                    _warmup_pending.clear()
                    if not batch:
                        _warmup_running = False
                        return
                _boot_warmup_loop(batch)
        except Exception:
            with _warmup_lock:
                _warmup_running = False
            raise

    threading.Thread(target=_run, name="donchian-boot-warmup", daemon=True).start()


def _load_state(symbol: str) -> SignalState:
    with _states_lock:
        if symbol in _signal_states:
            return _signal_states[symbol]
        db_state = store.load_state(symbol)
        if db_state:
            s = SignalState(
                trend=db_state.get("trend"),
                trend_ts=db_state.get("trend_ts"),
                waiting_entry=bool(db_state.get("waiting_entry", 0)),
                prev_parallel=bool(db_state.get("prev_parallel", 0)),
                last_processed_ts=db_state.get("last_processed_ts"),
            )
        else:
            s = SignalState()
        _signal_states[symbol] = s
        return s


def _persist_state(symbol: str, state: SignalState) -> None:
    store.save_state(
        symbol,
        trend=state.trend,
        trend_ts=state.trend_ts,
        waiting_entry=state.waiting_entry,
        prev_parallel=state.prev_parallel,
        last_processed_ts=state.last_processed_ts,
    )


def _candles_to_bars(raw: list[Candle]) -> list[DonchianBar]:
    return [
        DonchianBar(
            ts=int(c.timestamp),
            open=float(c.open),
            high=float(c.high),
            low=float(c.low),
            close=float(c.close),
            volume=float(c.volume),
            quote_volume=float(c.quote_volume or 0),
        )
        for c in raw
    ]


def _closed_bars_for_symbol(symbol: str, now_ms: int) -> list[DonchianBar] | None:
    """WS closed candles for symbol, or None if warm-up / gap / missing."""
    try:
        from src.exchange.binance_ws.cache import CACHE as _CACHE

        raw = _CACHE.get_candles(symbol.upper(), INTERVAL, CANDLE_LIMIT) or []
    except ExchangeClientError:
        return None
    if not raw:
        return None
    interval_ms = _INTERVAL_MINUTES * 60 * 1000
    period_start = (now_ms // interval_ms) * interval_ms
    closed = [c for c in raw if c.timestamp < period_start]
    if len(closed) < WARMUP_MIN_BARS:
        return None
    last_ts = int(closed[-1].timestamp)
    expected_last = period_start - interval_ms
    if last_ts < expected_last:
        return None
    return _candles_to_bars(closed)


def _should_apply_symbol_filter() -> bool:
    """Backtest fixed pool has no listing/range filter."""
    return SYMBOL_FILTER_ENABLED and SCAN_MODE != "fixed"


def _evaluate_symbol(symbol: str, now_ms: int) -> EntrySignal | None:
    """Detect entry signal and persist state. Does not open orders."""
    if not store.has_open_lot_for_symbol(symbol) and _should_apply_symbol_filter():
        eligible, reason = is_scan_eligible(symbol, interval=INTERVAL)
        if not eligible:
            logging.debug("  [%s] scan skip — %s", symbol, reason)
            return None

    bars = _closed_bars_for_symbol(symbol, now_ms)
    if bars is None:
        return None

    state = _load_state(symbol)
    entry = process_closed_bars(
        bars,
        state,
        period=DONCHIAN_PERIOD,
        slope_lookback=SLOPE_LOOKBACK,
        tol=PARALLEL_TOL,
        allow_entry=DUAL_LIMIT_ENABLED or STALE_OPP_HOURS > 0 or not store.has_open_lot_for_symbol(symbol),
        apply_quality_filter=True,
        atr_period=ATR_PERIOD,
        min_body_atr=MIN_BODY_ATR,
        max_body_atr=MAX_BODY_ATR,
        min_pot_rr=MIN_POT_RR,
        size_by_rr=SIZE_BY_RR,
        min_vol_ratio=MIN_VOL_RATIO,
        vol_ratio_period=VOL_RATIO_PERIOD,
    )

    held = [] if DUAL_LIMIT_ENABLED else store.get_open_lots(symbol)
    if entry is not None and held:
        held_sides = {str(row["side"]).lower() for row in held}
        if entry.side.lower() in held_sides:
            # Same side already open — keep waiting for a later opposite candle.
            state.waiting_entry = True
            _persist_state(symbol, state)
            return None
        from src.donchian.stale_opposite import qualifies_stale_opposite
        from src.donchian.trading import _get_mark

        if not qualifies_stale_opposite(
            held,
            entry.side,
            _get_mark(symbol),
            datetime.now(timezone.utc),
            min_hours=STALE_OPP_HOURS,
        ):
            state.waiting_entry = True
            _persist_state(symbol, state)
            return None

    if entry is not None:
        # Backtest clears waiting only on actual open — keep waiting if not selected in top_k.
        state.waiting_entry = True

    _persist_state(symbol, state)
    return entry


def _entry_atr(bars: list, entry_ts: int | None) -> float | None:
    from src.donchian.signals import compute_atr

    if not bars:
        return None
    atrs = compute_atr(bars, ATR_PERIOD)
    if entry_ts is None:
        val = atrs[-1]
        return float(val) if val else None
    for bar, atr in zip(bars, atrs):
        if int(bar.ts) == int(entry_ts) and atr:
            return float(atr)
    return None


def _open_favorable_adds(symbols: list[str], now_ms: int) -> int:
    """Same-side add when the latest closed 15m is ≥ ADD_ATR_MULT ATR in favor.

    Counts toward MAX_OPEN. A full book skips the add and retries a later cycle
    while the close is still past the threshold. One add per original lot.
    """
    if DUAL_LIMIT_ENABLED or ADD_ATR_MULT <= 0 or not is_trading_enabled() or not has_credentials():
        return 0
    from src.donchian.favorable_add import add_parents, favorable_move
    from src.donchian.signals import rolling_channel

    opened = 0
    for symbol in symbols:
        if store.count_open() >= MAX_OPEN:
            break
        held = store.get_open_lots(symbol)
        if not held:
            continue
        bars = _closed_bars_for_symbol(symbol, now_ms)
        if bars is None:
            continue
        last = bars[-1]
        channel = rolling_channel(
            [bar.high for bar in bars],
            [bar.low for bar in bars],
            DONCHIAN_PERIOD,
        )
        if channel is None:
            continue
        upper, lower = channel
        for parent in add_parents(held):
            if store.count_open() >= MAX_OPEN:
                break
            entry_ts = parent["entry_ts"]
            if entry_ts is not None and int(last.ts) <= int(entry_ts):
                continue
            atr = parent["entry_atr"] if "entry_atr" in parent.keys() else None
            if not atr or float(atr) <= 0:
                atr = _entry_atr(bars, int(entry_ts) if entry_ts is not None else None)
            if not atr:
                continue
            side = str(parent["side"]).lower()
            entry_px = float(parent["entry_px"])
            if not favorable_move(side, entry_px, float(last.close), float(atr), ADD_ATR_MULT):
                continue
            margin = float(parent["margin_usdt"] or 0)
            if margin <= 0:
                continue
            size_mult = float(parent["size_mult"] or 1.0)
            tp_band = upper if side == "long" else lower
            opp_band = lower if side == "long" else upper
            move = float(last.close) - entry_px if side == "long" else entry_px - float(last.close)
            status = open_lot(
                symbol,
                side=side,
                trend=str(parent["trend"] or side),
                trend_ts=parent["trend_ts"],
                entry_ts=int(last.ts),
                tp_band=tp_band,
                size_mult=size_mult,
                opp_band=opp_band,
                why=(
                    f"lệnh 2: close thuận {move:.6g} ≥ {ADD_ATR_MULT:g}×ATR "
                    f"({float(atr):.6g}) so với lệnh #{int(parent['id'])} entry={entry_px:.6g}"
                ),
                entry_atr=float(atr),
                add_on=True,
                margin_fixed=margin,
            )
            if status == "opened":
                opened += 1
    return opened


def _open_candidate(symbol: str, entry: EntrySignal, now_ms: int) -> bool:
    """Open one ranked candidate. Returns True if lot opened."""
    if not is_trading_enabled() or not has_credentials():
        return False

    bars = _closed_bars_for_symbol(symbol, now_ms)
    if bars is None:
        return False

    state = _load_state(symbol)
    last_bar = bars[-1]
    status = open_lot(
        symbol,
        side=entry.side,
        trend=state.trend or entry.side,
        trend_ts=state.trend_ts,
        entry_ts=last_bar.ts,
        tp_band=entry.tp_band,
        size_mult=entry.size_mult,
        opp_band=entry.opp_band,
        body_atr=entry.body_atr,
        pot_rr=entry.pot_rr,
        why=entry.why,
        entry_atr=_entry_atr(bars, last_bar.ts),
    )
    if status == "opened":
        state.waiting_entry = False
        _persist_state(symbol, state)
        return True

    if status == "hold_wait":
        state.waiting_entry = True
        _persist_state(symbol, state)
        return False

    same_open = any(
        str(row["side"]).lower() == entry.side.lower() for row in store.get_open_lots(symbol)
    )
    if status == "error" and not same_open:
        state.waiting_entry = True
        state.last_processed_ts = int(bars[-2].ts) if len(bars) >= 2 else None
        logging.info(
            "  [%s] Donchian %s not opened (%s) — will retry next cycle",
            symbol,
            entry.side,
            status,
        )
    else:
        state.waiting_entry = False
        logging.info(
            "  [%s] Donchian %s not opened (%s) — signal discarded (no retry)",
            symbol,
            entry.side,
            status,
        )
    _persist_state(symbol, state)
    return False


def _place_dual_candidate(symbol: str, entry: EntrySignal, now_ms: int, reserved: float) -> tuple[bool, float]:
    """Post-only LONG + SHORT for one signal. Returns (placed, margin committed)."""
    from src.donchian.dual_limit import DualSignal, place_pair

    if not is_trading_enabled() or not has_credentials():
        return False, 0.0
    bars = _closed_bars_for_symbol(symbol, now_ms)
    if bars is None:
        return False, 0.0
    state = _load_state(symbol)
    last_bar = bars[-1]
    sig = DualSignal(
        symbol=symbol,
        trend=state.trend or ("up" if entry.side == "long" else "down"),
        trend_ts=state.trend_ts,
        entry_ts=int(last_bar.ts),
        close=float(entry.entry_px),
        upper=max(float(entry.tp_band), float(entry.opp_band)),
        lower=min(float(entry.tp_band), float(entry.opp_band)),
        size_mult=float(entry.size_mult),
        body_atr=entry.body_atr,
        pot_rr=entry.pot_rr,
        atr=_entry_atr(bars, last_bar.ts),
        why=entry.why,
    )
    status, committed = place_pair(sig, reserved_usdt=reserved)
    if status == "placed":
        state.waiting_entry = False
    elif status in ("coin_cap", "cap_skip"):
        state.waiting_entry = False
        logging.info("  [%s] Dual limit not placed (%s) — signal discarded", symbol, status)
    elif status in ("error", "rate_limited"):
        state.waiting_entry = True
        logging.info("  [%s] Dual limit not placed (%s) — wait for next counter candle", symbol, status)
    _persist_state(symbol, state)
    return status == "placed", committed


def _log_balance() -> None:
    if not has_credentials():
        return
    if _boot_quiet_active():
        return
    try:
        from src.config import EXCHANGE
        from src.exchange.binance_ws import get_balance_from_ws

        if EXCHANGE == "binance" and get_balance_from_ws() is None:
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        symbol = "BTCUSDT"
        bal = live_account_balance(symbol)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        update_account_balance(
            available=bal.available,
            equity=bal.account_equity,
            margin_coin=bal.margin_coin,
            last_updated=now_str,
            maint_margin_pct=bal.maint_margin_pct,
            initial_margin_pct=bal.initial_margin_pct,
        )
        insert_equity_snapshot(bal.account_equity, bal.available, maint_margin_pct=bal.maint_margin_pct)
        if DUAL_LIMIT_ENABLED:
            from src.web.book import ensure_dual_book

            ensure_dual_book(float(bal.account_equity or 0))
        open_n = store.count_open()
        if DUAL_LIMIT_ENABLED:
            logging.info(
                "Futures equity=%.2f %s | maint=%.2f%% | open=%d lots | coins=%d/%d | pending=%d",
                bal.account_equity, bal.margin_coin, bal.maint_margin_pct, open_n,
                len(store.active_coins()), MAX_COINS, len(store.get_pending_entries()),
            )
        else:
            cap = str(MAX_OPEN) if MAX_OPEN > 0 else "∞"
            logging.info(
                "Futures equity=%.2f %s | maint=%.2f%% | open=%d/%s",
                bal.account_equity, bal.margin_coin, bal.maint_margin_pct, open_n, cap,
            )
        try:
            from src.spot_transfer import check_risk_warnings, note_equity_peak

            note_equity_peak(float(bal.account_equity or 0))
            check_risk_warnings(
                symbol,
                equity=float(bal.account_equity or 0),
                maint_pct=float(bal.maint_margin_pct or 0),
                initial_pct=float(bal.initial_margin_pct or 0),
            )
        except Exception as exc:  # noqa: BLE001
            logging.debug("Risk warn check skipped: %s", exc)
    except ExchangeClientError as exc:
        logging.debug("Donchian balance log skipped: %s", exc)


def run_cycle() -> None:
    global _first_cycle
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    symbols = _top_symbols()
    if not symbols:
        if SCAN_MODE == "fixed":
            logging.info("Fixed scan pool empty — check DONCHIAN_FIXED_SYMBOLS")
        else:
            logging.info("No symbols from WS volume cache yet — waiting...")
        return

    _sync_watched(symbols)

    if _first_cycle:
        logging.info("First cycle — skip REST position reconcile (WS-only boot)")
        _first_cycle = False

    opened = 0
    stale_symbols: list[str] = []
    if not is_trading_enabled():
        logging.info("Trading disabled — signal detection only, no orders")

    _wait_for_just_closed(symbols)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    if DUAL_LIMIT_ENABLED and has_credentials():
        from src.donchian.dual_limit import expire_pending

        try:
            expired = expire_pending(now_ms)
            if expired:
                logging.info("Dual limit: %d entry order(s) closed out after %d bar(s)", expired, ENTRY_TTL_BARS)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Dual limit expiry failed: %s", exc)

    candidates: list[tuple[str, EntrySignal]] = []
    for symbol in symbols:
        if _candle_cache_stale(symbol):
            stale_symbols.append(symbol)
            continue
        entry = _evaluate_symbol(symbol, now_ms)
        if entry is not None:
            candidates.append((symbol, entry))

    added = _open_favorable_adds(symbols, now_ms)
    opened += added

    candidates.sort(key=lambda item: item[1].pot_rr, reverse=True)
    if DUAL_LIMIT_ENABLED:
        if candidates:
            logging.info(
                "Entry candidates: %d — dual post-only limit LONG+SHORT for all (max_coins=%d)",
                len(candidates),
                MAX_COINS,
            )
        reserved = 0.0
        for symbol, entry in candidates:
            placed, committed = _place_dual_candidate(symbol, entry, now_ms, reserved)
            reserved += committed
            if placed:
                opened += 1
    else:
        if candidates:
            logging.info(
                "Entry candidates: %d — opening top %d by pot_rr (max_open=%d)",
                len(candidates),
                TOP_K_PER_CYCLE,
                MAX_OPEN,
            )
        for symbol, entry in candidates[:TOP_K_PER_CYCLE]:
            if store.count_open() >= MAX_OPEN:
                break
            if _open_candidate(symbol, entry, now_ms):
                opened += 1

    if stale_symbols:
        logging.info("Gap detected for %d symbol(s) — triggering re-warmup", len(stale_symbols))
        _start_boot_warmup(stale_symbols)

    set_last_cycle_at(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    _log_balance()
    try:
        from src.spot_transfer import process_daily_spot_transfer

        process_daily_spot_transfer("BTCUSDT")
    except Exception as exc:  # noqa: BLE001
        logging.warning("Daily spot transfer check failed: %s", exc)
    if opened:
        if DUAL_LIMIT_ENABLED:
            logging.info("Cycle done — %d signal(s) got dual limit orders", opened)
        else:
            logging.info("Cycle done — %d new lot(s) opened", opened)


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

    initial_symbols: list[str] = []
    try:
        from src.config import EXCHANGE
        from src.exchange.binance_ws import start_binance_ws

        if EXCHANGE == "binance" and has_credentials():
            _maybe_clear_stale_rate_limit()
            start_binance_ws()
            _wait_binance_ws_ready()
            if SCAN_MODE == "fixed":
                initial_symbols = _top_symbols()
            else:
                # Wait for miniTicker to populate quote_volumes (15m candles → no rush)
                for _ in range(6):
                    time.sleep(5)
                    if _top_symbols():
                        break
                initial_symbols = _top_symbols()
            from src.fade5m.config import ENABLED as FADE5M_ENABLED, watch_symbols as fade_watch
            from src.trend4h.config import ENABLED as TREND4H_ENABLED

            if FADE5M_ENABLED:
                from src.exchange.binance_ws import set_watched_symbols, watch_fade5m_symbols

                set_watched_symbols([])
                fade_syms = fade_watch()
                watch_fade5m_symbols(fade_syms)
                logging.info(
                    "Fade5m on — kline WS 5m+4h %s, skip 15m REST warmup",
                    ",".join(fade_syms),
                )
            else:
                if initial_symbols:
                    _sync_watched(initial_symbols)
                if TREND4H_ENABLED:
                    logging.info("Trend4h on — skip 15m REST warmup, seed 4h klines one coin at a time")
                else:
                    _start_boot_warmup(initial_symbols or [])
    except Exception as exc:  # noqa: BLE001
        logging.warning("Binance WS start skipped: %s", exc)
        notify_error("Binance WS start", str(exc))

    from src.fade5m.config import ENABLED as FADE5M_ENABLED
    from src.trend4h.config import ENABLED as TREND4H_ENABLED

    smc_on = os.getenv("SMC_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    if FADE5M_ENABLED or TREND4H_ENABLED:
        smc_on = False
    if not smc_on and not TREND4H_ENABLED and not FADE5M_ENABLED:
        start_watcher()

    if not TREND4H_ENABLED and not FADE5M_ENABLED:
        logging.info("%s Donchian Parallel-Trend bot started", EXCHANGE_DISPLAY_NAME)
        logging.info("Dashboard: http://localhost:%d", WEB_PORT)
        logging.info(
            "Logic: Donchian(%d) slope_lb=%d tol=%.3f%% interval=%s | WS kline=%s | scan=%s | "
            "margin=%.2f%% (cap %.0f%%) × %dx | max_open=%d top_k=%d | vol_ratio≥%.2f (period=%d)",
            DONCHIAN_PERIOD, SLOPE_LOOKBACK, PARALLEL_TOL, INTERVAL, GRANULARITY,
            f"fixed×{len(FIXED_SCAN_SYMBOLS)}" if SCAN_MODE == "fixed" else f"top-{TOP_N_SYMBOLS}",
            MARGIN_PCT * 100, MARGIN_CAP_PCT * 100, LEVERAGE, MAX_OPEN, TOP_K_PER_CYCLE,
            MIN_VOL_RATIO, VOL_RATIO_PERIOD,
        )
        if DUAL_LIMIT_ENABLED:
            logging.info(
                "Dual limit: every signal → post-only LONG + SHORT, cancel after %d bar(s), "
                "no lot cap, max %d coins. Market entry, stale-opposite and lệnh 2 are off.",
                ENTRY_TTL_BARS,
                MAX_COINS,
            )
        elif STALE_OPP_HOURS > 0:
            logging.info(
                "Stale opposite: allow one reverse entry when the open lot is losing for ≥ %.0fh",
                STALE_OPP_HOURS,
            )
    if is_trading_enabled():
        logging.info("Trading: LIVE")
    else:
        logging.info("Trading: DISABLED — analysis and dashboard only")
    if SPOT_TRANSFER_MODE == "month":
        logging.info(
            "Spot transfer: cuối tháng rút hết lãi, trần %.0f%% mốc đầu tháng, %s +07 ngày 1",
            SPOT_TRANSFER_MONTH_CAP_PCT * 100,
            SPOT_TRANSFER_EXECUTE_HHMM,
        )
    else:
        logging.info(
            "Spot transfer: mode=%s skim=%.0f%% day_cap=%.2f%% dd_pause=%.0f%% at %s +07",
            SPOT_TRANSFER_MODE,
            SPOT_TRANSFER_SKIM * 100,
            SPOT_TRANSFER_DAY_CAP_PCT * 100,
            SPOT_TRANSFER_DD_PAUSE_PCT * 100,
            SPOT_TRANSFER_EXECUTE_HHMM,
        )

    if FADE5M_ENABLED:
        logging.info("%s 5m fade vs 4h color bot started", EXCHANGE_DISPLAY_NAME)
        logging.info("Dashboard: http://localhost:%d", WEB_PORT)
        from src.fade5m.runtime import run_forever

        run_forever()
        return

    if TREND4H_ENABLED:
        logging.info("%s 4h Supertrend+ADX bot started", EXCHANGE_DISPLAY_NAME)
        logging.info("Dashboard: http://localhost:%d", WEB_PORT)
        from src.trend4h.runtime import run_forever

        run_forever()
        return

    if smc_on:
        logging.info("%s SMC liquidity-sweep bot started", EXCHANGE_DISPLAY_NAME)
        logging.info("Dashboard: http://localhost:%d  (sổ lệnh: /so-lenh, Donchian cũ: /donchian)", WEB_PORT)
        from src.smc.runtime import run_forever

        run_forever()
        return

    while True:
        try:
            run_cycle()
        except Exception as exc:
            logging.error("Donchian cycle failed: %s", exc, extra={"skip_discord": True})
            notify_error("Donchian cycle failed", str(exc))

        sleep_sec = seconds_until_next_interval()
        logging.info("Sleeping %.0f seconds until next %dm boundary...", sleep_sec, _INTERVAL_MINUTES)
        time.sleep(sleep_sec)
