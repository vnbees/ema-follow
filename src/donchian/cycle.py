"""Donchian parallel-trend cycle — runs every INTERVAL (default 15m)."""

from __future__ import annotations

import logging
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
    SPOT_TRANSFER_SKIM,
    WEB_PORT,
)
from src.database import init_db, insert_equity_snapshot
from src.exchange import ExchangeClientError, has_credentials
from src.exchange.types import Candle
from src.notify import notify_error, install_error_hooks
from src.donchian import store
from src.donchian.config import (
    ATR_PERIOD,
    BREADTH_ENABLED,
    BREADTH_MIN_N,
    BREADTH_MODE,
    BREADTH_RATIO,
    BREADTH_UNIVERSE,
    CANDLE_LIMIT,
    DONCHIAN_PERIOD,
    INTERVAL,
    LEVERAGE,
    MAJOR_SYMBOLS,
    MARGIN_PCT,
    MAX_BODY_ATR,
    MAX_OPEN,
    MIN_BODY_ATR,
    MIN_POT_RR,
    PARALLEL_TOL,
    SIZE_BY_RR,
    SLOPE_LOOKBACK,
    TOP_N_SYMBOLS,
    WARMUP_MIN_BARS,
    is_excluded_symbol,
    is_untradable,
    mark_untradable,
)
from src.donchian.breadth import BreadthVote, allows_side, flip_entry_signal, mid_side, vote_breadth
from src.donchian.signals import DonchianBar, SignalState, process_closed_bars
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
    """Pick top-N USDT-M futures by 24h quote volume. Cached for 4h to reduce churn."""
    global _cached_symbols, _symbols_refreshed_at

    now = time.monotonic()
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
        for lot in store.get_open_lots():
            sym = str(lot["symbol"]).upper()
            if sym not in watched:
                watched.append(sym)
        # Breadth mid needs majors' klines even if not in top-N volume
        if BREADTH_ENABLED and BREADTH_UNIVERSE == "majors":
            for sym in MAJOR_SYMBOLS:
                if sym not in watched and not is_untradable(sym) and not is_excluded_symbol(sym):
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
        DonchianBar(ts=int(c.timestamp), open=float(c.open), high=float(c.high), low=float(c.low), close=float(c.close))
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


def _breadth_universe(scan_symbols: list[str]) -> list[str]:
    if BREADTH_UNIVERSE == "scan":
        return list(scan_symbols)
    return sorted(MAJOR_SYMBOLS)


def compute_breadth_vote(scan_symbols: list[str], now_ms: int) -> BreadthVote | None:
    """Pool mid vote for this cycle; None if breadth disabled."""
    if not BREADTH_ENABLED:
        return None
    universe = _breadth_universe(scan_symbols)
    sides: list[str | None] = []
    for sym in universe:
        bars = _closed_bars_for_symbol(sym, now_ms)
        if bars is None:
            sides.append(None)
            continue
        sides.append(mid_side(bars, DONCHIAN_PERIOD))
    vote = vote_breadth(sides, ratio=BREADTH_RATIO, min_n=BREADTH_MIN_N)
    if vote.side is None:
        logging.info(
            "Breadth mid: NEUTRAL ups=%d downs=%d tot=%d (need n≥%d lead≥%.2f×) mode=%s universe=%s",
            vote.ups,
            vote.downs,
            vote.total,
            BREADTH_MIN_N,
            BREADTH_RATIO,
            BREADTH_MODE,
            BREADTH_UNIVERSE,
        )
    else:
        action = "FLIP opposite entries" if BREADTH_MODE == "flip" else "SKIP opposite entries"
        logging.info(
            "Breadth mid %s: vote=%s ups=%d downs=%d tot=%d lead=%.2f× universe=%s — %s",
            BREADTH_MODE.upper(),
            vote.side.upper(),
            vote.ups,
            vote.downs,
            vote.total,
            vote.lead_ratio,
            BREADTH_UNIVERSE,
            action,
        )
    return vote


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
        open_n = store.count_open()
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


def _process_symbol(symbol: str, now_ms: int, breadth: BreadthVote | None = None) -> bool:
    """Return True if a lot was opened."""
    if not store.has_open_lot_for_symbol(symbol):
        eligible, reason = is_scan_eligible(symbol, interval=INTERVAL)
        if not eligible:
            logging.debug("  [%s] scan skip — %s", symbol, reason)
            return False

    bars = _closed_bars_for_symbol(symbol, now_ms)
    if bars is None:
        return False

    state = _load_state(symbol)
    entry = process_closed_bars(
        bars,
        state,
        period=DONCHIAN_PERIOD,
        slope_lookback=SLOPE_LOOKBACK,
        tol=PARALLEL_TOL,
        allow_entry=not store.has_open_lot_for_symbol(symbol),
        apply_quality_filter=True,
        atr_period=ATR_PERIOD,
        min_body_atr=MIN_BODY_ATR,
        max_body_atr=MAX_BODY_ATR,
        min_pot_rr=MIN_POT_RR,
        size_by_rr=SIZE_BY_RR,
    )

    if entry is None or not is_trading_enabled() or not has_credentials():
        _persist_state(symbol, state)
        return False

    if breadth is not None and breadth.side is not None and not allows_side(breadth, entry.side):
        if BREADTH_MODE == "hard":
            store.record_skip(symbol, "breadth_hard")
            state.waiting_entry = False
            logging.info(
                "  [%s] Donchian %s blocked by breadth hard (vote=%s ups=%d downs=%d) — discarded",
                symbol,
                entry.side,
                breadth.side,
                breadth.ups,
                breadth.downs,
            )
            _persist_state(symbol, state)
            return False
        # flip (default): reverse side to match vote
        orig = entry.side
        entry = flip_entry_signal(entry, vote=breadth)
        logging.info(
            "  [%s] Donchian breadth FLIP %s→%s (vote=%s ups=%d downs=%d) pot_rr=%.2f size_mult=%.2f",
            symbol,
            orig,
            entry.side,
            breadth.side,
            breadth.ups,
            breadth.downs,
            entry.pot_rr,
            entry.size_mult,
        )

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
    )
    if status != "opened":
        # check_signal already cleared waiting_entry on emit.
        # cap_skip (max open / margin / already open): discard like backtest — no queue.
        # error: keep waiting so a transient exchange failure can retry next cycle.
        if status == "error" and not store.has_open_lot_for_symbol(symbol):
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

    _persist_state(symbol, state)
    return True


def run_cycle() -> None:
    global _first_cycle
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    symbols = _top_symbols()
    if not symbols:
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

    breadth = compute_breadth_vote(symbols, now_ms)

    for symbol in symbols:
        if _candle_cache_stale(symbol):
            stale_symbols.append(symbol)
            continue
        did_open = _process_symbol(symbol, now_ms, breadth=breadth)
        if did_open:
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
            # Wait for miniTicker to populate quote_volumes (15m candles → no rush)
            for _ in range(6):
                time.sleep(5)
                if _top_symbols():
                    break
            initial_symbols = _top_symbols()
            if initial_symbols:
                _sync_watched(initial_symbols)
            _start_boot_warmup(initial_symbols or [])
    except Exception as exc:  # noqa: BLE001
        logging.warning("Binance WS start skipped: %s", exc)
        notify_error("Binance WS start", str(exc))

    start_watcher()

    logging.info("%s Donchian Parallel-Trend bot started", EXCHANGE_DISPLAY_NAME)
    logging.info("Dashboard: http://localhost:%d", WEB_PORT)
    logging.info(
        "Logic: Donchian(%d) slope_lb=%d tol=%.3f%% interval=%s | WS kline=%s | top_N=%d | "
        "margin=%.2f%% × %dx | max_open=%d | breadth=%s (ratio=%.2f min_n=%d universe=%s)",
        DONCHIAN_PERIOD, SLOPE_LOOKBACK, PARALLEL_TOL, INTERVAL, GRANULARITY,
        TOP_N_SYMBOLS, MARGIN_PCT * 100, LEVERAGE, MAX_OPEN,
        BREADTH_MODE, BREADTH_RATIO, BREADTH_MIN_N, BREADTH_UNIVERSE,
    )
    if is_trading_enabled():
        logging.info("Trading: LIVE")
    else:
        logging.info("Trading: DISABLED — analysis and dashboard only")
    logging.info(
        "Spot transfer: mode=%s skim=%.0f%% day_cap=%.2f%% dd_pause=%.0f%% at %s +07",
        SPOT_TRANSFER_MODE,
        SPOT_TRANSFER_SKIM * 100,
        SPOT_TRANSFER_DAY_CAP_PCT * 100,
        SPOT_TRANSFER_DD_PAUSE_PCT * 100,
        SPOT_TRANSFER_EXECUTE_HHMM,
    )

    while True:
        try:
            run_cycle()
        except Exception as exc:
            logging.error("Donchian cycle failed: %s", exc, extra={"skip_discord": True})
            notify_error("Donchian cycle failed", str(exc))

        sleep_sec = seconds_until_next_interval()
        logging.info("Sleeping %.0f seconds until next %dm boundary...", sleep_sec, _INTERVAL_MINUTES)
        time.sleep(sleep_sec)
