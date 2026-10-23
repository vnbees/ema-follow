"""4h Supertrend+ADX loop. One REST/order at a time to stay off Binance 418."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from src.bot_state import is_trading_enabled, set_last_cycle_at
from src.exchange import ExchangeClientError
from src.trend4h import broker, retire, store
from src.trend4h.config import INTERVAL, KLINE_LIMIT, LEVERAGE, MARGIN_USDT, MIN_BARS, REST_GAP_SEC, SYMBOLS, TICK_SEC
from src.trend4h.engine import Bar4h, bars_from_klines, desired_side, merge_bars, resample_closed_4h

_bars: dict[str, list[Bar4h]] = {}
_seeded: set[str] = set()
_seed_queue: list[str] = []
_last_rest = 0.0
_last_action = 0.0
_last_bar: dict[str, int] = {}
_eval_queue: list[str] = []


def _rate_blocked() -> bool:
    try:
        from src.exchange import binance as b

        return b.is_rate_limited() or b.optional_rest_blocked_sec() > 0 or b.is_boot_rest_quiet()
    except Exception:  # noqa: BLE001
        return False


def _now_ms() -> int:
    return int(time.time() * 1000)


def _from_ws(symbol: str) -> list[Bar4h]:
    try:
        from src.config import GRANULARITY
        from src.exchange.binance_ws.cache import CACHE

        raw = CACHE.get_candles(symbol.upper(), GRANULARITY, 2000) or []
    except Exception:  # noqa: BLE001
        return []
    return resample_closed_4h(raw, _now_ms())


def _seed_one(symbol: str) -> None:
    global _last_rest
    if _rate_blocked():
        logging.warning("trend4h seed skip %s — REST blocked", symbol)
        return
    now = time.monotonic()
    if now - _last_rest < REST_GAP_SEC:
        return
    from src.exchange.binance import fetch_candles_rest

    rows = fetch_candles_rest(symbol, granularity=INTERVAL, limit=KLINE_LIMIT, max_retries=1)
    _last_rest = time.monotonic()
    native = bars_from_klines(rows, _now_ms())
    merged = merge_bars(_bars.get(symbol, []), native)
    merged = merge_bars(merged, _from_ws(symbol))
    _bars[symbol] = merged
    if len(merged) >= MIN_BARS:
        _seeded.add(symbol)
        logging.info("trend4h seeded %s %d 4h bars", symbol, len(merged))
    else:
        logging.warning("trend4h %s only %d 4h bars (need %d)", symbol, len(merged), MIN_BARS)


def _refresh(symbol: str) -> list[Bar4h]:
    merged = merge_bars(_bars.get(symbol, []), _from_ws(symbol))
    _bars[symbol] = merged
    return merged


def _side_name(want: int) -> str:
    return "long" if want > 0 else "short"


def _have(symbol: str) -> int:
    row = store.open_row(symbol)
    if row is None:
        return 0
    return 1 if str(row["side"]) == "long" else -1


def _evaluate(symbol: str) -> bool:
    """Act on at most one market order. True if an order was sent."""
    bars = _refresh(symbol)
    if len(bars) < MIN_BARS:
        return False
    last = bars[-1]
    want = desired_side(bars)
    have = _have(symbol)
    if have != 0 and want != 0 and want != have:
        row = store.open_row(symbol)
        if row is None:
            return False
        closed = broker.close_side(row, "st_flip")
        if closed:
            from src.notify import notify_trend4h_close

            notify_trend4h_close(
                symbol,
                str(row["side"]),
                reason="st_flip",
                entry=float(row["entry_px"]),
                exit_px=float(closed["exit_px"]),
                pnl=float(closed["pnl"]),
            )
            return True
        return False
    if have == 0 and want != 0:
        opened = broker.open_side(symbol, _side_name(want), last.ts)
        if opened:
            from src.notify import notify_trend4h_open

            notify_trend4h_open(symbol, _side_name(want), entry=float(opened["entry_px"]), bar_ts=last.ts)
            return True
    return False


def _aligned(symbol: str) -> bool:
    bars = _bars.get(symbol) or []
    if len(bars) < MIN_BARS:
        return True
    want = desired_side(bars)
    have = _have(symbol)
    return have == want or (have != 0 and want == 0)


def _queue_due() -> None:
    """When a new 4h bar closes, line coins up for one-at-a-time action."""
    global _eval_queue
    if _eval_queue:
        return
    due: list[str] = []
    for symbol in SYMBOLS:
        bars = _refresh(symbol)
        if len(bars) < MIN_BARS:
            continue
        ts = bars[-1].ts
        if _last_bar.get(symbol) == ts:
            continue
        due.append(symbol)
    if due:
        _eval_queue = due
        logging.info("trend4h 4h close — evaluate %d coins, one order/tick", len(due))


def _tick() -> None:
    global _seed_queue, _last_action
    store.ensure_schema()
    if not retire.finished():
        retire.step()
        return
    if not _seed_queue and len(_seeded) < len(SYMBOLS):
        _seed_queue = [s for s in SYMBOLS if s not in _seeded]
    if _seed_queue:
        _seed_one(_seed_queue[0])
        if _seed_queue[0] in _seeded:
            _seed_queue.pop(0)
        return
    if not is_trading_enabled():
        return
    if _rate_blocked():
        return
    now = time.monotonic()
    if now - _last_action < REST_GAP_SEC:
        return
    _queue_due()
    if not _eval_queue:
        return
    symbol = _eval_queue[0]
    try:
        acted = _evaluate(symbol)
    except ExchangeClientError as exc:
        logging.warning("trend4h %s wait: %s", symbol, exc)
        return
    if acted:
        _last_action = time.monotonic()
    bars = _bars.get(symbol) or []
    if bars and _aligned(symbol):
        _last_bar[symbol] = bars[-1].ts
        _eval_queue.pop(0)
    try:
        set_last_cycle_at(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    except Exception as exc:  # noqa: BLE001
        logging.debug("trend4h last_cycle: %s", exc)


def snapshot() -> dict:
    opens = store.all_open()
    closed = store.recent_closed()
    pnl = sum(float(r.get("pnl") or 0) for r in closed)
    rows = []
    for symbol in SYMBOLS:
        bars = _bars.get(symbol) or []
        want = desired_side(bars) if len(bars) >= MIN_BARS else 0
        pos = next((p for p in opens if p["symbol"] == symbol), None)
        last = bars[-1] if bars else None
        rows.append(
            {
                "symbol": symbol,
                "want": "long" if want > 0 else "short" if want < 0 else "flat",
                "side": None if pos is None else pos["side"],
                "entry": None if pos is None else pos["entry_px"],
                "qty": None if pos is None else pos["qty"],
                "bars": len(bars),
                "last_bar": None if last is None else last.ts,
                "last_close": None if last is None else last.close,
                "seeded": symbol in _seeded,
            }
        )
    return {
        "opens": opens,
        "recent": closed,
        "closed_pnl": pnl,
        "rows": rows,
        "retired": retire.finished(),
        "seeded": len(_seeded),
        "need": len(SYMBOLS),
    }


def run_forever() -> None:
    store.ensure_schema()
    logging.info(
        "4h Supertrend(10,3) + ADX>=25 | %d coins | margin %.2f USDT × %dx | "
        "taker in/out at 4h close | REST gap %.1fs | flatten old books first",
        len(SYMBOLS),
        MARGIN_USDT,
        LEVERAGE,
        REST_GAP_SEC,
    )
    while True:
        try:
            _tick()
        except Exception as exc:  # noqa: BLE001
            logging.error("trend4h tick failed: %s", exc)
        time.sleep(TICK_SEC)
