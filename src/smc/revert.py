"""Limit orders that trade back toward an SMC signal.

OP only for now. A new order is allowed when the last closed 15m candle is at
least 1% away from a live signal and the candle moved further away: green while
price is above the signal (short), red while price is below (long). Every level
still inside its 7-day window is followed, including levels that have left the
short live candle cache. Signals that
want the same side share one order, attached to the level closest to price. Both the
entry and the take-profit are limits. Take-profit is one ATR(14) from the fill,
never closer than the fee floor so the close still nets a profit. No new order is
placed while the coin's open positions are in profit together. Seven days after
the signal, resting entries are cancelled and open positions are closed at market.
"""

from __future__ import annotations

import logging
import time

from src.donchian.config import LEVERAGE
from src.exchange import ExchangeClientError, fetch_contract_spec, has_credentials, notional_to_size
from src.exchange.fills import commission_usdt_from_detail, parse_fill_price
from src.exchange.sizing import format_price
from src.smc import revert_store as store
from src.smc.outlook import BARS_7D
from src.smc.signals import BAR_MS, SWING, flipped, last_atr, setups

SYMBOLS = ("OPUSDT",)
MARGIN_USDT = 1.0
MIN_DISTANCE = 0.01
MAKER_FEE = 0.0002
TAKER_FEE = 0.0005
# Limit TP sits one ATR away. Floor assumes taker entry + maker exit so net > 0.
TP_ATR_MULT = 1.0
# A sweep is only valid with the same 400-bar context used to find it.
# That context has to sit before the oldest bar still inside 7 days.
HISTORY_BARS = BARS_7D + 400
_configured: set[str] = set()
_seen_bar: dict[str, tuple[int, int, int]] = {}
_bars: dict[str, list] = {}
_seed_bar: dict[str, int] = {}


def candle_color(open_: float, close: float) -> str | None:
    if close > open_:
        return "green"
    if close < open_:
        return "red"
    return None


def nearest_per_side(candidates: list[tuple[dict, str, float]]) -> list[tuple[dict, str]]:
    """Several signals on the same side become one order, on the level closest to price."""
    best: dict[str, tuple[float, dict]] = {}
    for level, side, distance in candidates:
        current = best.get(side)
        if current is None or distance < current[0]:
            best[side] = (distance, level)
    return [(level, side) for side, (_distance, level) in best.items()]


def entry_side(level: float, close: float, color: str | None) -> str | None:
    """Short a green candle above the signal. Long a red candle below it."""
    if level <= 0 or color is None:
        return None
    dist = (close - level) / level
    if dist >= MIN_DISTANCE and color == "green":
        return "short"
    if dist <= -MIN_DISTANCE and color == "red":
        return "long"
    return None


def fee_floor_distance(entry: float) -> float:
    """Smallest price move that still leaves a profit after taker-in + maker-out fees."""
    if entry <= 0:
        return 0.0
    return entry * (TAKER_FEE + MAKER_FEE) / (1.0 - MAKER_FEE)


def tp_distance(entry: float, atr: float) -> float:
    return max(float(atr) * TP_ATR_MULT, fee_floor_distance(entry))


def tp_price(side: str, entry: float, atr: float) -> float:
    dist = tp_distance(entry, atr)
    if side == "long":
        return entry + dist
    return entry - dist


def net_after_fees(side: str, entry: float, tp: float, qty: float = 1.0) -> float:
    """Expected net with taker entry and maker exit — the safer fee pair for a GTC limit."""
    if qty <= 0 or entry <= 0 or tp <= 0:
        return 0.0
    gross = (tp - entry) * qty if side == "long" else (entry - tp) * qty
    return gross - entry * qty * TAKER_FEE - abs(tp) * qty * MAKER_FEE


def _atr_from(candles: list) -> float | None:
    if len(candles) < 2:
        return None
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    closes = [float(c.close) for c in candles]
    return last_atr(highs, lows, closes)


def _tp_text(side: str, entry: float, atr: float, spec, qty: float) -> str | None:
    """Format a TP that still nets profit after fees. Bump one tick if rounding eats the edge."""
    raw = tp_price(side, entry, atr)
    price = format_price(raw, spec)
    tick = 10 ** (-spec.price_place)
    for _ in range(5):
        if net_after_fees(side, entry, float(price), qty) > 0:
            return price
        bumped = float(price) + tick if side == "long" else float(price) - tick
        if bumped <= 0:
            return None
        price = format_price(bumped, spec)
    return None


def unrealized_net(side: str, fill: float, mark: float, qty: float, fee_open: float) -> float:
    """Profit if the position were closed at `mark` with a maker fee."""
    gross = (mark - fill) * qty if side == "long" else (fill - mark) * qty
    return gross - fee_open - abs(mark * qty) * MAKER_FEE


def book_in_profit(rows: list[dict], mark: float) -> bool:
    """True when open positions together would close green. No opens is not profit."""
    opens = [
        row for row in rows
        if str(row.get("status")) == "open" and float(row.get("qty") or 0) > 0 and float(row.get("fill_px") or 0) > 0
    ]
    if not opens:
        return False
    if mark <= 0:
        return True
    total = sum(
        unrealized_net(str(row["side"]), float(row["fill_px"]), mark, float(row["qty"]), float(row.get("fee_open") or 0))
        for row in opens
    )
    return total > 0


def open_pnl(rows: list[dict], mark: float) -> float | None:
    if mark <= 0:
        return None
    total = 0.0
    found = False
    for row in rows:
        if str(row.get("status")) != "open":
            continue
        qty = float(row.get("qty") or 0)
        fill = float(row.get("fill_px") or 0)
        if qty <= 0 or fill <= 0:
            continue
        found = True
        total += unrealized_net(str(row["side"]), fill, mark, qty, float(row.get("fee_open") or 0))
    return total if found else None


def _ws_closed(symbol: str, now_ms: int) -> list:
    from src.exchange.binance_ws.cache import CACHE

    raw = CACHE.get_candles(symbol, "15m", 400) or []
    return [c for c in raw if int(c.timestamp) + BAR_MS <= now_ms]


def _seed_history(symbol: str, now_ms: int) -> list | None:
    """One kline request so a level that left the 400-bar cache is still followed."""
    from src.exchange.binance import fetch_candles_rest, is_rate_limited

    if is_rate_limited():
        return None
    try:
        rows = fetch_candles_rest(symbol, granularity="15m", limit=HISTORY_BARS, max_retries=1)
    except Exception as exc:  # noqa: BLE001
        logging.warning("revert history %s failed: %s", symbol, exc)
        return []
    closed = [c for c in rows if int(c.timestamp) + BAR_MS <= now_ms]
    logging.info("revert history %s seeded %d closed 15m bars", symbol, len(closed))
    return closed


def _history(symbol: str, now_ms: int) -> list:
    live = _ws_closed(symbol, now_ms)
    stored = list(_bars.get(symbol) or [])
    if len(stored) < HISTORY_BARS:
        last = int(live[-1].timestamp) if live else 0
        if _seed_bar.get(symbol) != last:
            seeded = _seed_history(symbol, now_ms)
            if seeded is None:
                return live or stored
            _seed_bar[symbol] = last
            if seeded:
                stored = seeded
    by_ts = {int(c.timestamp): c for c in stored}
    for candle in live:
        by_ts[int(candle.timestamp)] = candle
    merged = [by_ts[ts] for ts in sorted(by_ts)]
    if len(merged) > HISTORY_BARS:
        merged = merged[-HISTORY_BARS:]
    _bars[symbol] = merged
    return merged


def _mark(symbol: str) -> float:
    try:
        from src.exchange.binance_ws import get_mark_from_ws

        mark = get_mark_from_ws(symbol)
        if mark is not None and mark > 0:
            return float(mark)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _ws(order_id: str) -> dict | None:
    if not order_id:
        return None
    try:
        from src.exchange.binance_ws import get_order_detail_from_ws

        return get_order_detail_from_ws(order_id)
    except Exception:  # noqa: BLE001
        return None


def _ensure(symbol: str) -> None:
    if symbol in _configured:
        return
    from src.exchange import configure_symbol_trading

    configure_symbol_trading(symbol)
    _configured.add(symbol)


def _level_price(raw: float, spec) -> float:
    return float(format_price(raw, spec))


def _register(symbol: str, candles: list, spec, now_ms: int) -> None:
    if len(candles) < SWING + 2:
        return
    span = (int(candles[0].timestamp), int(candles[-1].timestamp), len(candles))
    if _seen_bar.get(symbol) == span:
        return
    ts = [int(c.timestamp) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    closes = [float(c.close) for c in candles]
    for setup in setups(ts, closes, highs, lows, closes):
        order = flipped(setup)
        deadline = int(order.signal_ts) + BARS_7D * BAR_MS
        if now_ms >= deadline:
            continue
        level = _level_price(float(order.entry), spec)
        if level <= 0:
            continue
        store.get_or_create_signal(symbol, level, int(order.signal_ts), deadline)
    _seen_bar[symbol] = span


def _pnl(side: str, entry: float, close: float, qty: float, fee_open: float, fee_close: float) -> float:
    gross = (close - entry) * qty if side == "long" else (entry - close) * qty
    return gross - fee_open - fee_close


def _finish(order: dict, *, close_px: float, fee_close: float, reason: str, level: float) -> None:
    entry = float(order.get("fill_px") or order["entry_px"])
    qty = float(order.get("qty") or 0)
    fee_open = float(order.get("fee_open") or 0)
    pnl = _pnl(str(order["side"]), entry, close_px, qty, fee_open, fee_close)
    if not store.mark_closed(int(order["id"]), close_px=close_px, fee_close=fee_close, pnl=pnl, reason=reason):
        return
    from src.notify import notify_revert_close

    notify_revert_close(
        str(order["symbol"]),
        str(order["side"]),
        reason=reason,
        level=level,
        entry=entry,
        close_px=close_px,
        pnl_usdt=pnl,
    )
    logging.info("revert close %s %s %s pnl %.4f", order["symbol"], order["side"], reason, pnl)


def _poll(symbol: str, levels: dict[int, dict], now_ms: int, candles: list) -> None:
    from src.exchange.binance import place_limit_close_order

    atr = _atr_from(candles)
    for order in store.working_orders(symbol):
        level = levels.get(int(order["signal_id"]))
        level_px = float(level["level_px"]) if level else 0.0
        expired = level is None or now_ms >= int(level["deadline_ts"])
        if str(order["status"]) == "pending":
            detail = _ws(str(order.get("entry_order_id") or ""))
            if not detail or str(detail.get("status") or "").lower() != "filled":
                continue
            fill = parse_fill_price(detail) or float(order["entry_px"])
            fee = commission_usdt_from_detail(detail)
            if fee <= 0:
                fee = fill * float(order.get("qty") or 0) * MAKER_FEE
            if not store.mark_open(int(order["id"]), fill_px=fill, fee_open=fee):
                continue
            order = {**order, "status": "open", "fill_px": fill, "fee_open": fee, "tp_order_id": ""}
            logging.info("revert fill %s %s @ %s", symbol, order["side"], fill)
        if str(order["status"]) != "open":
            continue
        tp_id = str(order.get("tp_order_id") or "")
        detail = _ws(tp_id) if tp_id else None
        if detail and str(detail.get("status") or "").lower() == "filled":
            close_px = parse_fill_price(detail) or float(order["tp_px"])
            fee = commission_usdt_from_detail(detail)
            if fee <= 0:
                rate = TAKER_FEE if expired else MAKER_FEE
                fee = close_px * float(order.get("qty") or 0) * rate
            _finish(order, close_px=close_px, fee_close=fee, reason="expire" if expired else "tp", level=level_px)
            continue
        if tp_id and not expired:
            continue
        if tp_id and expired:
            from src.exchange.binance import cancel_limit_order

            try:
                cancel_limit_order(symbol, tp_id)
            except ExchangeClientError as exc:
                logging.warning("revert cancel tp %s failed: %s", symbol, exc)
                continue
            store.clear_tp(int(order["id"]))
            tp_id = ""
        fill = float(order.get("fill_px") or order["entry_px"])
        spec = fetch_contract_spec(symbol)
        if expired:
            _close_market(symbol, order, spec, fill, level_px)
            continue
        if atr is None:
            logging.warning("revert tp %s skipped, ATR not ready", symbol)
            continue
        price = _tp_text(str(order["side"]), fill, atr, spec, float(order.get("qty") or 0))
        if price is None:
            logging.warning("revert tp %s skipped, no profitable ATR target", symbol)
            continue
        store.set_tp_price(int(order["id"]), float(price))
        try:
            result = place_limit_close_order(symbol, str(order["side"]), _qty_text(order, spec), price)
        except ExchangeClientError as exc:
            logging.warning("revert close limit %s failed: %s", symbol, exc)
            continue
        oid = str(result.get("orderId") or "")
        if oid:
            store.set_tp_order(int(order["id"]), oid)
        if str(result.get("status") or "").lower() == "filled":
            close_px = parse_fill_price(result) or float(price)
            fee = close_px * float(order.get("qty") or 0) * MAKER_FEE
            _finish(order, close_px=close_px, fee_close=fee, reason="tp", level=level_px)


def _close_market(symbol: str, order: dict, spec, fill: float, level_px: float) -> None:
    """Flatten an expired position immediately. Entry stays a limit; this close does not."""
    from src.exchange.binance import close_position_side

    try:
        result = close_position_side(symbol, str(order["side"]), _qty_text(order, spec))
    except ExchangeClientError as exc:
        logging.warning("revert market close %s failed: %s", symbol, exc)
        return
    oid = str(result.get("orderId") or "")
    if oid:
        store.set_tp_order(int(order["id"]), oid)
    if str(result.get("status") or "").lower() != "filled":
        return
    close_px = parse_fill_price(result) or _mark(symbol) or fill
    fee = commission_usdt_from_detail(result)
    if fee <= 0:
        fee = close_px * float(order.get("qty") or 0) * TAKER_FEE
    _finish(order, close_px=close_px, fee_close=fee, reason="expire", level=level_px)


def _qty_text(order: dict, spec) -> str:
    from src.exchange.sizing import format_size

    return format_size(float(order.get("qty") or 0), spec)


def _expire(symbol: str, now_ms: int) -> None:
    from src.exchange.binance import cancel_limit_order

    for level in store.active_signals(symbol):
        if now_ms < int(level["deadline_ts"]):
            continue
        store.mark_signal_expired(int(level["id"]))
        for order in store.orders_for(int(level["id"])):
            if str(order["status"]) != "pending":
                continue
            oid = str(order.get("entry_order_id") or "")
            try:
                if oid:
                    cancel_limit_order(symbol, oid)
            except ExchangeClientError as exc:
                logging.warning("revert cancel %s %s failed: %s", symbol, oid, exc)
                continue
            if store.mark_cancelled(int(order["id"]), "expire"):
                from src.notify import notify_revert_close

                notify_revert_close(
                    symbol,
                    str(order["side"]),
                    reason="cancel",
                    level=float(level["level_px"]),
                    entry=float(order["entry_px"]),
                    close_px=None,
                    pnl_usdt=None,
                )


def _free_margin() -> float:
    from src.exchange.binance_ws.cache import CACHE

    bal = CACHE.get_balance()
    if bal is None:
        return 0.0
    return float(bal.available or 0)


def _refresh_pending_tp(symbol: str, candles: list, spec) -> None:
    atr = _atr_from(candles)
    if atr is None:
        return
    for order in store.working_orders(symbol):
        if str(order["status"]) != "pending":
            continue
        entry = float(order["entry_px"])
        qty = float(order.get("qty") or 0)
        tp = _tp_text(str(order["side"]), entry, atr, spec, qty)
        if tp is None:
            continue
        if abs(float(tp) - float(order["tp_px"])) > 0:
            store.set_tp_price(int(order["id"]), float(tp))


def _enter(symbol: str, candles: list, spec, now_ms: int) -> None:
    if len(candles) < 2:
        return
    bar = candles[-1]
    bar_ts = int(bar.timestamp)
    if now_ms >= bar_ts + 2 * BAR_MS:
        return
    color = candle_color(float(bar.open), float(bar.close))
    close = float(bar.close)
    mark = _mark(symbol) or close
    if book_in_profit(store.open_orders(symbol), mark):
        return
    atr = _atr_from(candles)
    if atr is None:
        return
    budget = _free_margin()
    from src.exchange.binance import place_limit_open_order
    from src.notify import notify_revert_open

    candidates: list[tuple[dict, str, float]] = []
    for level in store.active_signals(symbol):
        if now_ms >= int(level["deadline_ts"]):
            continue
        level_px = float(level["level_px"])
        side = entry_side(level_px, close, color)
        if side is None or store.bar_used(int(level["id"]), bar_ts) or store.side_used(symbol, side, bar_ts):
            continue
        entry = _level_price(close, spec)
        if entry_side(level_px, entry, color) is None:
            continue
        candidates.append((level, side, abs(close - level_px) / level_px))
    grouped: dict[str, list[dict]] = {}
    for level, side, _distance in candidates:
        grouped.setdefault(side, []).append(level)
    for level, side in nearest_per_side(candidates):
        if budget < MARGIN_USDT:
            logging.info("revert skip %s, free margin %.2f", symbol, budget)
            return
        entry = _level_price(close, spec)
        _ensure(symbol)
        qty = notional_to_size(MARGIN_USDT * LEVERAGE, entry, spec)
        entry_txt = format_price(entry, spec)
        tp = _tp_text(side, float(entry_txt), atr, spec, float(qty))
        if tp is None:
            logging.warning("revert entry %s %s skipped, no profitable ATR target", symbol, side)
            continue
        try:
            result = place_limit_open_order(symbol, side, qty, entry_txt, post_only=False)
        except ExchangeClientError as exc:
            logging.warning("revert entry %s %s failed: %s", symbol, side, exc)
            continue
        oid = str(result.get("orderId") or "")
        if not oid:
            continue
        store.insert_order(
            signal_id=int(level["id"]),
            symbol=symbol,
            side=side,
            bar_ts=bar_ts,
            entry_px=float(entry_txt),
            tp_px=float(tp),
            qty=float(qty),
            margin_usdt=MARGIN_USDT,
            entry_order_id=oid,
        )
        for other in grouped[side]:
            if int(other["id"]) != int(level["id"]):
                store.skip_bar(int(other["id"]), bar_ts)
        budget -= MARGIN_USDT
        notify_revert_open(symbol, side, level=float(level["level_px"]), entry=float(entry_txt), tp=float(tp), margin_usdt=MARGIN_USDT)
        logging.info("revert open %s %s limit %s tp %s atr %.6f bar %s", symbol, side, entry_txt, tp, atr, bar_ts)


def manage(now_ms: int | None = None) -> None:
    if not has_credentials():
        return
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    for symbol in SYMBOLS:
        try:
            candles = _history(symbol, now)
            if len(candles) < 2:
                continue
            spec = fetch_contract_spec(symbol)
            _register(symbol, candles, spec, now)
            levels = {int(row["id"]): row for row in store.recent_signals(symbol)}
            _poll(symbol, levels, now, candles)
            _expire(symbol, now)
            levels = {int(row["id"]): row for row in store.recent_signals(symbol)}
            _poll(symbol, levels, now, candles)
            _refresh_pending_tp(symbol, candles, spec)
            _enter(symbol, candles, spec, now)
        except Exception as exc:  # noqa: BLE001
            logging.error("revert %s failed: %s", symbol, exc)


def snapshot(now_ms: int | None = None) -> dict:
    """Dashboard view. Reads the book and the latest mark. Does not place orders."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    coins = []
    for symbol in SYMBOLS:
        mark = _mark(symbol)
        rows = store.recent_signals(symbol)
        opens = store.open_orders(symbol)
        pnl = open_pnl(opens, mark)
        signals = []
        for level in rows:
            orders = store.orders_for(int(level["id"]))
            if str(level["status"]) != "active" and not orders:
                continue
            signals.append({"signal": level, "orders": orders})
        coins.append(
            {
                "symbol": symbol,
                "mark": mark if mark > 0 else None,
                "in_profit": book_in_profit(opens, mark),
                "open_pnl": pnl,
                "signals": signals,
            }
        )
    return {
        "margin_usdt": MARGIN_USDT,
        "leverage": LEVERAGE,
        "min_distance_pct": MIN_DISTANCE * 100,
        "coins": coins,
        "now_ms": now,
    }
