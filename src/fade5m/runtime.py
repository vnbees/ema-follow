"""5m fade loop. Limit in on fade 5m; market-flat this book at 4h close.

Candles come from kline WebSocket (5m + 4h). No REST klines — restart stays off
the Binance weight budget.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from src.bot_state import is_trading_enabled, set_last_cycle_at
from src.exchange import ExchangeClientError, close_position_side, fetch_contract_spec
from src.exchange.fills import commission_usdt_from_detail, parse_fill_price
from src.exchange.sizing import format_size
from src.exchange.types import Candle
from src.fade5m import broker, store
from src.fade5m.config import (
    H4_MS,
    INTERVAL_5M,
    KLINE_LIMIT,
    LEVERAGE,
    M5_MS,
    MARGIN_USDT,
    REST_GAP_SEC,
    LIVE_NOW,
    START_AT,
    SWITCH_AT,
    SYMBOLS_NEXT,
    TAKER_FEE,
    TICK_SEC,
    VN_TZ,
    entries_open,
    trade_symbols,
    watch_symbols,
)
from src.fade5m.engine import (
    Bar5m,
    bars_from_klines,
    book_unrealized,
    bucket_4h,
    candle_side,
    closed_blocks,
    fourh_open,
    is_last_5m_of_4h,
    last_atr,
    lots_on_side,
    merge_bars,
    opposite_qty,
    signal_side,
    skip_add_when_profit,
)

_bars: dict[str, list[Bar5m]] = {}
_last_bar: dict[str, int] = {}
_h4_open: dict[str, float] = {}
_last_rest = 0.0
_flattening: str | None = None
_ws_ready_logged: set[str] = set()
_close_queue: list[tuple[str, Bar5m]] = []
_wait_logged = False
_open_logged = False
_trade_logged: tuple[str, ...] | None = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _orders_blocked() -> bool:
    """Hard 418/429 only. Limit/flatten are critical POSTs — do not wait on optional REST."""
    try:
        from src.exchange import binance as b

        return b.is_rate_limited()
    except Exception:  # noqa: BLE001
        return False


def _gap_ok() -> bool:
    return time.monotonic() - _last_rest >= REST_GAP_SEC


def _touch_rest() -> None:
    global _last_rest
    _last_rest = time.monotonic()


def _side_name(want: int) -> str:
    return "long" if want > 0 else "short"


def _ws_candles(symbol: str, interval: str, limit: int) -> list[Candle]:
    try:
        from src.exchange.binance_ws.cache import CACHE

        return CACHE.get_candles(symbol, interval, limit) or []
    except Exception:  # noqa: BLE001
        return []


def _pull_ws(symbol: str) -> list[Bar5m]:
    rows = _ws_candles(symbol, INTERVAL_5M, KLINE_LIMIT)
    merged = merge_bars(_bars.get(symbol, []), bars_from_klines(rows, _now_ms()))
    _bars[symbol] = merged[-KLINE_LIMIT:]
    h4_rows = _ws_candles(symbol, "4h", 4)
    if h4_rows:
        _h4_open[symbol] = float(h4_rows[-1].open)
    return _bars[symbol]


def _h4_open_px(symbol: str, bar: Bar5m) -> float | None:
    live = _h4_open.get(symbol)
    if live is not None and live > 0:
        return live
    return fourh_open(_bars.get(symbol) or [], bucket_4h(bar.ts))


def _atr_for(symbol: str) -> float | None:
    bars = _bars.get(symbol) or []
    if len(bars) < 16:
        bars = _pull_ws(symbol)
    atr = last_atr(bars)
    if atr is not None:
        return atr
    if _orders_blocked() or not _gap_ok():
        return None
    try:
        from src.exchange.binance import fetch_candles_rest

        rows = fetch_candles_rest(symbol, granularity=INTERVAL_5M, limit=30, max_retries=1)
        _touch_rest()
        merged = merge_bars(bars, bars_from_klines(rows, _now_ms()))
        _bars[symbol] = merged[-KLINE_LIMIT:]
        atr = last_atr(_bars[symbol])
        logging.info("fade5m ATR REST %s bars=%d atr=%s", symbol, len(_bars[symbol]), atr)
        return atr
    except Exception as exc:  # noqa: BLE001
        logging.warning("fade5m ATR REST %s: %s", symbol, exc)
        return None


def _try_place_tp(row: dict) -> None:
    if str(row.get("tp_order_id") or ""):
        return
    if _orders_blocked() or not _gap_ok():
        return
    atr = _atr_for(str(row["symbol"]))
    if atr is None or not _gap_ok():
        return
    placed = broker.place_tp(row, atr)
    if placed is not None:
        _touch_rest()


def _poll_fills() -> None:
    for row in store.pending_rows():
        try:
            if broker.realize_pending(row):
                from src.notify import notify_fade5m_fill

                fresh = next((r for r in store.open_rows(str(row["symbol"])) if r["id"] == row["id"]), None)
                if fresh:
                    notify_fade5m_fill(
                        str(fresh["symbol"]),
                        str(fresh["side"]),
                        entry=float(fresh["entry_px"]),
                        bar_ts=int(fresh["bar_ts"]),
                    )
                    _try_place_tp(fresh)
        except Exception as exc:  # noqa: BLE001
            logging.warning("fade5m fill poll %s: %s", row.get("id"), exc)
    for row in store.open_rows():
        try:
            pnl = broker.realize_tp(row)
            if pnl is not None:
                from src.notify import notify_fade5m_close

                notify_fade5m_close(
                    str(row["symbol"]),
                    str(row["side"]),
                    reason="tp",
                    entry=float(row.get("entry_px") or row["limit_px"]),
                    exit_px=float(row.get("tp_px") or 0),
                    pnl=pnl,
                )
        except Exception as exc:  # noqa: BLE001
            logging.warning("fade5m TP poll %s: %s", row.get("id"), exc)


def _arm_tps() -> None:
    """Hang 1 ATR TP on open lots that still lack one. Skip if ATR/REST is missing."""
    if _flattening or _orders_blocked() or not _gap_ok():
        return
    for row in store.open_rows():
        if str(row.get("tp_order_id") or ""):
            continue
        atr = _atr_for(str(row["symbol"]))
        if atr is None or not _gap_ok():
            continue
        placed = broker.place_tp(row, atr)
        if placed is None:
            continue
        _touch_rest()
        return


def _cancel_pending(
    symbol: str,
    *,
    reason: str,
    match_side: str | None = None,
    skip_side: str | None = None,
) -> bool:
    """Cancel matching pending limits. False if any target row is still pending."""
    for row in store.pending_rows(symbol):
        side = str(row["side"])
        if skip_side and side == skip_side:
            continue
        if match_side and side != match_side:
            continue
        if not _gap_ok() or _orders_blocked():
            return False
        broker.cancel_pending(row, reason)
        _touch_rest()
    for row in store.pending_rows(symbol):
        side = str(row["side"])
        if skip_side and side == skip_side:
            continue
        if match_side and side != match_side:
            continue
        return False
    return True


def _overdue_symbol(now_ms: int) -> str | None:
    for row in store.pending_rows() + store.open_rows():
        if int(row["bucket_ts"]) + H4_MS <= now_ms:
            return str(row["symbol"])
    return None


def _flatten(symbol: str) -> bool:
    """Cancel our limits and market-close our fills. True when this coin is flat."""
    global _flattening
    if _orders_blocked():
        return False
    pending = store.pending_rows(symbol)
    while pending and _gap_ok() and not _orders_blocked():
        broker.cancel_pending(pending[0], "4h_end")
        _touch_rest()
        pending = store.pending_rows(symbol)
    if pending:
        return False
    opens = store.open_rows(symbol)
    for row in opens:
        if not str(row.get("tp_order_id") or ""):
            continue
        if not _gap_ok() or _orders_blocked():
            return False
        pnl = broker.cancel_tp(row)
        _touch_rest()
        if pnl is not None:
            from src.notify import notify_fade5m_close

            notify_fade5m_close(
                str(row["symbol"]),
                str(row["side"]),
                reason="tp",
                entry=float(row.get("entry_px") or row["limit_px"]),
                exit_px=float(row.get("tp_px") or 0),
                pnl=pnl,
            )
    opens = store.open_rows(symbol)
    if not opens:
        _flattening = None
        logging.info("fade5m %s 4h flatten done", symbol)
        return True
    if not _gap_ok():
        return False
    side = str(opens[0]["side"])
    group = [r for r in opens if str(r["side"]) == side]
    spec = fetch_contract_spec(symbol)
    qty = sum(float(r["qty"]) for r in group)
    size = format_size(qty, spec)
    try:
        result = close_position_side(symbol, side, size)
    except ExchangeClientError as exc:
        logging.warning("fade5m flatten %s %s: %s", symbol, side, exc)
        return False
    _touch_rest()
    px = parse_fill_price(result) or float(group[0].get("entry_px") or 0)
    fee_total = commission_usdt_from_detail(result)
    if fee_total <= 0:
        fee_total = px * qty * TAKER_FEE
    oid = str(result.get("orderId") or "")
    now_ms = _now_ms()
    for row in group:
        q = float(row["qty"])
        entry = float(row.get("entry_px") or row["limit_px"])
        fee_o = float(row.get("fee_open") or 0)
        fee_c = fee_total * (q / qty) if qty else 0.0
        pnl = (px - entry) * q if side == "long" else (entry - px) * q
        pnl -= fee_o + fee_c
        store.mark_closed(
            int(row["id"]),
            exit_px=px,
            fee_close=fee_c,
            pnl=pnl,
            reason="4h_end",
            closed_ts=now_ms,
            close_order_id=oid,
        )
        from src.notify import notify_fade5m_close

        notify_fade5m_close(symbol, side, reason="4h_end", entry=entry, exit_px=px, pnl=pnl)
    logging.info("fade5m flatten %s %s qty %s @ %.6f n=%d", symbol, side, size, px, len(group))
    return not store.open_rows(symbol) and not store.pending_rows(symbol)


def _on_close(symbol: str, bar: Bar5m) -> None:
    global _flattening
    if not entries_open():
        return
    if is_last_5m_of_4h(bar.ts):
        logging.info("fade5m %s 4h close — flatten book", symbol)
        _flattening = symbol
        _flatten(symbol)
        return
    if not is_trading_enabled():
        logging.info("fade5m %s skip — trading disabled", symbol)
        return
    if symbol not in trade_symbols():
        return
    h4_open = _h4_open_px(symbol, bar)
    if h4_open is None:
        logging.warning("fade5m %s waiting 4h kline WS for open", symbol)
        return
    want = signal_side(bar, h4_open)
    h4 = candle_side(h4_open, bar.close)
    m5 = candle_side(bar.open, bar.close)
    logging.info(
        "fade5m %s 5m close %s 5m=%s 4h=%s want=%s o=%.6f c=%.6f h4o=%.6f",
        symbol,
        bar.ts,
        "green" if m5 > 0 else "red" if m5 < 0 else "doji",
        "green" if h4 > 0 else "red" if h4 < 0 else "doji",
        "long" if want > 0 else "short" if want < 0 else "none",
        bar.open,
        bar.close,
        h4_open,
    )
    if want == 0:
        return
    want_name = _side_name(want)
    if _orders_blocked():
        logging.warning("fade5m %s skip place — REST cooldown", symbol)
        return
    if not _cancel_pending(symbol, skip_side=want_name, reason="4h_flip"):
        logging.warning("fade5m %s defer — opposite limit still pending", symbol)
        return
    opens = store.open_rows(symbol)
    lots = [
        (str(r["side"]), float(r.get("entry_px") or r["limit_px"]), float(r["qty"]))
        for r in opens
    ]
    extra = opposite_qty(lots, want_name)
    same = lots_on_side(lots, want_name)
    if extra <= 0 and skip_add_when_profit(same, bar.close):
        pnl = book_unrealized(same, bar.close)
        logging.info("fade5m %s skip add — same side in profit %.4f @ %.6f", symbol, pnl, bar.close)
        return
    if extra > 0:
        if not _cancel_pending(symbol, match_side=want_name, reason="cover_replace"):
            logging.warning("fade5m %s defer cover replace — old limit still live", symbol)
            return
        logging.info("fade5m %s 4h flip — one cover opposite qty %.8f + 1 USDT", symbol, extra)
    opened = broker.place_limit(
        symbol,
        want_name,
        bar.close,
        bar.ts,
        bucket_4h(bar.ts),
        extra_qty=extra,
    )
    if opened:
        _touch_rest()
        from src.notify import notify_fade5m_limit

        notify_fade5m_limit(
            symbol,
            want_name,
            px=float(opened["limit_px"]),
            bar_ts=bar.ts,
            qty=float(opened["qty"]),
        )


def _stamp() -> None:
    try:
        set_last_cycle_at(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    except Exception as exc:  # noqa: BLE001
        logging.debug("fade5m last_cycle: %s", exc)


def _tick() -> None:
    global _flattening, _wait_logged, _open_logged, _close_queue, _trade_logged
    store.ensure_schema()
    if entries_open():
        _poll_fills()
        _arm_tps()
    active = trade_symbols()
    if _trade_logged != active:
        logging.info("fade5m trade list %d coins: %s", len(active), ",".join(active))
        _trade_logged = active
    if not entries_open():
        if not _wait_logged:
            when = START_AT.astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M") if START_AT else ""
            logging.info(
                "fade5m idle until %s +07 — no place/cancel/flatten (watch WS only)",
                when,
            )
            _wait_logged = True
    elif _wait_logged and not _open_logged:
        logging.info(
            "fade5m entries OPEN — %d coins, 1 USDT each, leftover exchange lots kept",
            len(trade_symbols()),
        )
        _open_logged = True
    if not entries_open():
        for symbol in watch_symbols():
            bars = _pull_ws(symbol)
            if symbol not in _ws_ready_logged and (_h4_open.get(symbol) or bars):
                _ws_ready_logged.add(symbol)
                last = bars[-1] if bars else None
                logging.info(
                    "fade5m WS ready %s 5m_closed=%d h4_open=%s last=%s",
                    symbol,
                    len(bars),
                    _h4_open.get(symbol),
                    None if last is None else last.ts,
                )
            if bars:
                _last_bar[symbol] = bars[-1].ts
        _stamp()
        return
    if _flattening:
        _flatten(_flattening)
        _stamp()
        return
    due_flat = _overdue_symbol(_now_ms())
    if due_flat:
        logging.info("fade5m %s 4h overdue — flatten book", due_flat)
        _flattening = due_flat
        _flatten(due_flat)
        _stamp()
        return
    if _close_queue:
        skipped: list[tuple[str, Bar5m]] = []
        while _close_queue:
            symbol, bar = _close_queue.pop(0)
            tradeable = is_last_5m_of_4h(bar.ts) or (
                is_trading_enabled() and symbol in trade_symbols()
            )
            if not tradeable:
                _on_close(symbol, bar)
                continue
            skipped.append((symbol, bar))
            break
        _close_queue = skipped + _close_queue
        if _close_queue:
            if _orders_blocked() or not _gap_ok():
                _stamp()
                return
            symbol, bar = _close_queue.pop(0)
            _on_close(symbol, bar)
        _stamp()
        return
    for symbol in watch_symbols():
        bars = _pull_ws(symbol)
        if symbol not in _ws_ready_logged and (_h4_open.get(symbol) or bars):
            _ws_ready_logged.add(symbol)
            last = bars[-1] if bars else None
            logging.info(
                "fade5m WS ready %s 5m_closed=%d h4_open=%s last=%s",
                symbol,
                len(bars),
                _h4_open.get(symbol),
                None if last is None else last.ts,
            )
        if not bars:
            continue
        last = bars[-1]
        prev = _last_bar.get(symbol)
        if prev is None:
            _last_bar[symbol] = last.ts
            age = _now_ms() - (last.ts + M5_MS)
            if 0 <= age < 120_000:
                _close_queue.append((symbol, last))
            continue
        if last.ts == prev:
            continue
        _last_bar[symbol] = last.ts
        if last.ts > prev:
            _close_queue.append((symbol, last))
    _stamp()


def snapshot() -> dict:
    opens = store.open_rows()
    pending = store.pending_rows()
    closed = store.recent_closed()
    blocks = closed_blocks(closed, H4_MS)
    pnl = sum(float(b["pnl"]) for b in blocks)
    active = trade_symbols()
    rows = []
    for symbol in active:
        bars = _bars.get(symbol, [])
        last = bars[-1] if bars else None
        h4_open = _h4_open.get(symbol)
        if h4_open is None and last is not None:
            h4_open = fourh_open(bars, bucket_4h(last.ts))
        h4_side = 0
        if last and h4_open is not None:
            h4_side = candle_side(h4_open, last.close)
        rows.append(
            {
                "symbol": symbol,
                "h4": "green" if h4_side > 0 else "red" if h4_side < 0 else "doji",
                "last_close": None if last is None else last.close,
                "last_bar": None if last is None else last.ts,
                "n_open": sum(1 for r in opens if r["symbol"] == symbol),
                "n_pending": sum(1 for r in pending if r["symbol"] == symbol),
                "entries_open": entries_open(symbol=symbol),
            }
        )
    first = rows[0] if rows else {}
    start_txt = START_AT.astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M") if START_AT else ""
    switch_txt = SWITCH_AT.astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M") if SWITCH_AT else ""
    out = {
        "symbol": first.get("symbol", ""),
        "opens": opens,
        "pending": pending,
        "recent": closed,
        "closed_blocks": blocks,
        "rows": rows,
        "closed_pnl": pnl,
        "seeded": len(_ws_ready_logged),
        "last_bar": first.get("last_bar"),
        "last_close": first.get("last_close"),
        "h4": first.get("h4", "doji"),
        "n_open": len(opens),
        "n_pending": len(pending),
        "n_symbols": len(active),
        "margin_usdt": MARGIN_USDT,
        "leverage": LEVERAGE,
        "h4_ms": H4_MS,
        "trading_enabled": is_trading_enabled(),
        "entries_open": entries_open(),
        "live_now": list(LIVE_NOW),
        "start_at": start_txt,
        "switch_at": switch_txt,
        "next_symbols": list(SYMBOLS_NEXT),
        "switched": bool(SYMBOLS_NEXT) and active == SYMBOLS_NEXT,
        "flattening": _flattening,
        "last_cycle_at": None,
        "source": "kline_ws",
    }
    try:
        from src.bot_state import get_last_cycle_at

        out["last_cycle_at"] = get_last_cycle_at()
    except Exception:  # noqa: BLE001
        pass
    return out


def run_forever() -> None:
    store.ensure_schema()
    start_txt = START_AT.astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M +07") if START_AT else "now"
    switch_txt = SWITCH_AT.astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M +07") if SWITCH_AT else ""
    live = ",".join(LIVE_NOW) or "none"
    watched = watch_symbols()
    logging.info(
        "5m fade vs 4h color | trade %d watch %d | margin %.2f USDT × %dx | kline WS 5m+4h | %s live now, others from %s | switch %s | leftover exchange lots kept",
        len(trade_symbols()),
        len(watched),
        MARGIN_USDT,
        LEVERAGE,
        live,
        start_txt,
        switch_txt or "none",
    )
    try:
        from src.exchange.binance_ws import watch_fade5m_symbols

        watch_fade5m_symbols(list(watched))
    except Exception as exc:  # noqa: BLE001
        logging.debug("fade5m watch skipped: %s", exc)
    while True:
        try:
            _tick()
        except Exception as exc:  # noqa: BLE001
            logging.error("fade5m tick failed: %s", exc)
        time.sleep(TICK_SEC)
