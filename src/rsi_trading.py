import logging
from datetime import datetime, timezone

from src import database as db
from src.exchange import (
    ExchangeClientError,
    close_position_side,
    fetch_all_open_positions,
    fetch_contract_spec,
    fetch_futures_balance,
    fetch_pending_orders,
    fetch_side_mark_price,
    fetch_side_unrealized_pnl,
    fetch_symbol_positions,
    format_size,
    has_credentials,
    notional_to_size,
    place_market_order,
)
from src.bot_state import is_trading_enabled, update_symbol_status
from src.config import (
    LEVERAGE,
    MARGIN_MODE,
    MARGIN_PREFLIGHT_ENABLED,
    MAX_OPEN_SYMBOLS,
    PAIR_PROFIT_TARGET_PCT,
    TRADING_ENABLED,
)
from src.order_sizing import compute_entry_margin_usdt, margin_to_notional
from src.rsi import RsiSnapshot
from src.rsi_signals import RsiSignal, detect_pair_event, price_move_pct, should_take_profit
from src.exchange.symbols import is_tradeable_symbol
from src.trading import (
    _get_state,
    _record_market_entry,
    ensure_symbol_configured,
    resolve_order_fill,
)

_CONFIG_TRADING_ENABLED = TRADING_ENABLED


def _trading_enabled() -> bool:
    if TRADING_ENABLED != _CONFIG_TRADING_ENABLED:
        return bool(TRADING_ENABLED)
    return is_trading_enabled()


def _close_side_and_resolve_fill(
    symbol: str,
    side: str,
    size: float,
    fallback_price: float,
) -> float:
    size_str = _format_close_size(symbol, size)
    result = close_position_side(symbol, side, size_str)
    _verify_side_reduced(symbol, side, size)
    return resolve_order_fill(symbol, result, fallback_price=fallback_price)


def _force_close_blocked_symbol(symbol: str, mark: float) -> bool:
    """Close all legs for symbols blocked from trading (e.g. USDCUSDT)."""
    if is_tradeable_symbol(symbol):
        return False
    if mark <= 0:
        mark = fetch_side_mark_price(symbol)
    closed_any = False
    positions = fetch_symbol_positions(symbol)
    for side in ("long", "short"):
        pos = positions[side]
        if pos.size <= 0:
            continue
        size_str = _format_close_size(symbol, pos.size)
        logging.info(
            "  [%s] Force close blocked symbol — %s size=%s",
            symbol,
            side.upper(),
            size_str,
        )
        fill = _close_side_and_resolve_fill(symbol, side, pos.size, mark)
        db.close_all_lot_sides(symbol, side, close_price=fill)
        closed_any = True
    if closed_any:
        from src.notify import notify_close

        notify_close(symbol, "L+S")
    return closed_any


def close_hedge_symbol(symbol: str, mark: float | None = None) -> bool:
    """Close both long and short on exchange + sync DB lots."""
    if mark is None or mark <= 0:
        mark = fetch_side_mark_price(symbol)
    positions = fetch_symbol_positions(symbol)
    closed_any = False
    for side in ("long", "short"):
        pos = positions[side]
        if pos.size <= 0:
            continue
        size_str = _format_close_size(symbol, pos.size)
        logging.info(
            "  [%s] Close hedge pair — %s size=%s",
            symbol,
            side.upper(),
            size_str,
        )
        fill = _close_side_and_resolve_fill(symbol, side, pos.size, mark)
        db.close_all_lot_sides(symbol, side, close_price=fill)
        closed_any = True
    if closed_any:
        from src.notify import notify_close

        notify_close(symbol, "L+S")
    return closed_any


def _rank_symbols_for_deleverage(symbols: list[str]) -> list[str]:
    candidates: list[tuple[int, str, float, str]] = []
    for symbol in symbols:
        positions = fetch_symbol_positions(symbol)
        if positions["long"].size <= 0 and positions["short"].size <= 0:
            continue
        open_lots = db.get_open_pair_lots(symbol)
        active = [
            row for row in open_lots
            if row["long_status"] == "open" or row["short_status"] == "open"
        ]
        lot_count = len(active)
        latest_opened = max((str(row["opened_at"]) for row in active), default="")
        try:
            long_pnl = fetch_side_unrealized_pnl(symbol, "long")
            short_pnl = fetch_side_unrealized_pnl(symbol, "short")
        except ExchangeClientError:
            long_pnl = short_pnl = 0.0
        net_abs = abs(long_pnl + short_pnl)
        candidates.append((lot_count, latest_opened, net_abs, symbol.upper()))

    candidates.sort(key=lambda row: (row[0], row[1], -row[2]), reverse=True)
    return [row[3] for row in candidates]


def deleverage_one_symbol() -> str | None:
    """Close one hedge pair (L+S). Returns symbol closed or None."""
    from src.rsi_positions import get_managed_symbols

    symbols = _rank_symbols_for_deleverage(get_managed_symbols())
    if not symbols:
        symbols = _rank_symbols_for_deleverage(
            sorted({row["symbol"] for row in db.get_all_open_pair_lots()}),
        )
    for symbol in symbols:
        try:
            mark = fetch_side_mark_price(symbol)
            if close_hedge_symbol(symbol, mark):
                return symbol
        except ExchangeClientError as exc:
            logging.warning("  [%s] Deleverage failed: %s", symbol, exc)
    return None


def liquidate_all_hedge_pairs(symbols: list[str]) -> float:
    """Close all hedge pairs on exchange and sync DB. Returns equity after."""
    if not has_credentials():
        return 0.0
    seen: set[str] = set()
    for symbol in symbols:
        seen.add(symbol.upper())
    for row in db.get_all_open_pair_lots():
        seen.add(str(row["symbol"]).upper())
    for pos in fetch_all_open_positions():
        seen.add(pos.symbol.upper())

    for symbol in sorted(seen):
        try:
            mark = fetch_side_mark_price(symbol)
            close_hedge_symbol(symbol, mark)
        except ExchangeClientError as exc:
            logging.error("  [%s] Hedge liquidation failed: %s", symbol, exc)

    if not seen:
        return 0.0
    balance = fetch_futures_balance(next(iter(seen)))
    return balance.account_equity


def close_all_blocked_symbols() -> int:
    """Close exchange + DB legs for every blocked symbol. Returns count closed."""
    if not has_credentials() or not _trading_enabled():
        return 0
    symbols: set[str] = set()
    for row in db.get_all_open_pair_lots():
        sym = str(row["symbol"]).upper()
        if not is_tradeable_symbol(sym):
            symbols.add(sym)
    for pos in fetch_all_open_positions():
        if not is_tradeable_symbol(pos.symbol):
            symbols.add(pos.symbol.upper())
    closed = 0
    for symbol in sorted(symbols):
        try:
            mark = fetch_side_mark_price(symbol)
            if _force_close_blocked_symbol(symbol, mark):
                closed += 1
        except ExchangeClientError as exc:
            logging.warning("  [%s] Failed to close blocked symbol: %s", symbol, exc)
    if closed:
        logging.info("Force-closed %d blocked symbol(s)", closed)
    return closed


def can_open_new_symbol() -> bool:
    return db.count_open_symbols() < MAX_OPEN_SYMBOLS


def can_open_new_position() -> bool:
    return can_open_new_symbol()


def load_rsi_state_from_db(_row) -> None:
    return None


def clear_rsi_state(_symbol: str) -> None:
    return None


def _size_for_margin(symbol: str, margin_usdt: float, price: float) -> str:
    spec = fetch_contract_spec(symbol)
    return notional_to_size(margin_to_notional(margin_usdt), price, spec)


def _format_close_size(symbol: str, size: float) -> str:
    spec = fetch_contract_spec(symbol)
    return format_size(size, spec)


def _verify_pair_opened_sizes(
    symbol: str,
    before: dict,
    expected_delta: float,
) -> None:
    after = fetch_symbol_positions(symbol)
    for side in ("long", "short"):
        delta = after[side].size - before[side].size
        if abs(delta - expected_delta) > 1e-6:
            logging.warning(
                "  [%s] Open %s size mismatch: expected +%.4f, got +%.4f (before=%.4f after=%.4f)",
                symbol,
                side.upper(),
                expected_delta,
                delta,
                before[side].size,
                after[side].size,
            )


def _open_pair(symbol: str, snap: RsiSnapshot, trigger: str) -> int | None:
    if not is_tradeable_symbol(symbol):
        logging.info("  [%s] Blocked symbol — skip open pair", symbol)
        return None

    ensure_symbol_configured(symbol)

    if MARGIN_PREFLIGHT_ENABLED:
        from src.margin_preflight import ensure_available_for_pair

        if not ensure_available_for_pair(symbol, snap, trigger):
            logging.warning(
                "  [%s] Skip open pair — insufficient available after preflight",
                symbol,
            )
            return None

    balance = fetch_futures_balance(symbol)
    margin_usdt = compute_entry_margin_usdt(balance.account_equity)
    price = snap.close if snap.close > 0 else fetch_side_mark_price(symbol)
    size_str = _size_for_margin(symbol, margin_usdt, price)
    size_val = float(size_str)

    positions_before = fetch_symbol_positions(symbol)

    logging.info(
        "  [%s] Open pair L+S | trigger=%s | margin/leg=%.2f USDT | size=%s | available=%.2f",
        symbol,
        trigger,
        margin_usdt,
        size_str,
        balance.available,
    )

    long_result = place_market_order(
        symbol, "buy", size_str, hold_side="long", trade_side="open",
    )
    try:
        short_result = place_market_order(
            symbol, "sell", size_str, hold_side="short", trade_side="open",
        )
    except ExchangeClientError as exc:
        logging.error(
            "  [%s] Short open failed — rollback long size=%s: %s",
            symbol,
            size_str,
            exc,
        )
        try:
            close_position_side(symbol, "long", size_str)
            _verify_side_reduced(symbol, "long", float(size_str))
        except ExchangeClientError as rollback_exc:
            logging.error(
                "  [%s] Long rollback failed after short error: %s",
                symbol,
                rollback_exc,
            )
        raise

    trade_state = _get_state(symbol)
    long_oid = str(long_result.get("orderId", ""))
    long_coid = str(long_result.get("clientOid", ""))
    short_oid = str(short_result.get("orderId", ""))
    short_coid = str(short_result.get("clientOid", ""))

    long_fill = _record_market_entry(
        symbol, "long", long_oid, long_coid, size_str, price, trade_state.open_cycle_id,
        order_result=long_result,
    )
    short_fill = _record_market_entry(
        symbol, "short", short_oid, short_coid, size_str, price, trade_state.open_cycle_id,
        order_result=short_result,
    )

    _verify_pair_opened_sizes(symbol, positions_before, size_val)

    lot_id = db.insert_pair_lot(
        symbol,
        long_size=size_val,
        long_entry=long_fill,
        short_size=size_val,
        short_entry=short_fill,
        margin_usdt=margin_usdt,
        entry_trigger=trigger,
    )
    logging.info(
        "  [%s] Pair opened lot #%d | long fill=%.4f short fill=%.4f",
        symbol,
        lot_id,
        long_fill,
        short_fill,
    )
    return lot_id


def _verify_side_reduced(symbol: str, side: str, size_before: float) -> None:
    positions = fetch_symbol_positions(symbol)
    size_after = positions[side].size
    if size_after >= size_before - 1e-6:
        other = "short" if side == "long" else "long"
        other_after = positions[other].size
        logging.error(
            "  [%s] Close %s may have failed — %s size %.4f -> %.4f | other %s=%.4f",
            symbol,
            side.upper(),
            side,
            size_before,
            size_after,
            other,
            other_after,
        )


def _take_profit_aggregate_side(
    symbol: str,
    side: str,
    mark: float,
    snap: RsiSnapshot,
    trigger: str,
    *,
    reopen_pair: bool,
) -> None:
    positions = fetch_symbol_positions(symbol)
    pos = positions[side]
    if pos.size <= 0:
        return
    size_str = _format_close_size(symbol, pos.size)
    pnl = fetch_side_unrealized_pnl(symbol, side)
    move = price_move_pct(side, pos.avg_price, mark)
    logging.info(
        "  [%s] Aggregate take profit %s | trigger=%s | move=%+.2f%% | size=%s | pnl≈%+.2f",
        symbol,
        side.upper(),
        trigger,
        move,
        size_str,
        pnl,
    )
    fill = _close_side_and_resolve_fill(symbol, side, pos.size, mark)
    db.close_all_lot_sides(symbol, side, close_price=fill)
    from src.notify import notify_close

    notify_close(symbol, side.upper())
    if reopen_pair and is_tradeable_symbol(symbol):
        _open_pair(symbol, snap, f"{trigger}_tp_agg_{side}")


def _estimate_leg_pnl(side: str, entry: float, mark: float, size: float) -> float:
    if side == "long":
        return (mark - entry) * size
    return (entry - mark) * size


def close_lot_leg(
    symbol: str,
    lot,
    side: str,
    mark: float,
    trigger: str,
) -> None:
    if side == "long":
        if lot["long_status"] != "open":
            return
        entry = float(lot["long_entry"])
        size = float(lot["long_size"])
    else:
        if lot["short_status"] != "open":
            return
        entry = float(lot["short_entry"])
        size = float(lot["short_size"])
    if size <= 0:
        return

    size_str = _format_close_size(symbol, size)
    move = price_move_pct(side, entry, mark)
    logging.info(
        "  [%s] Lot #%d close %s | trigger=%s | move=%+.2f%% | size=%s | pnl≈(pending fill)",
        symbol,
        lot["id"],
        side.upper(),
        trigger,
        move,
        size_str,
    )
    fill = _close_side_and_resolve_fill(symbol, side, size, mark)
    pnl = _estimate_leg_pnl(side, entry, fill, size)
    logging.info(
        "  [%s] Lot #%d close %s fill=%.6f | pnl≈%+.2f",
        symbol,
        lot["id"],
        side.upper(),
        fill,
        pnl,
    )
    db.close_lot_side(
        int(lot["id"]),
        side,
        realized_pnl_usdt=pnl,
        close_price=fill,
    )
    from src.notify import notify_close

    notify_close(symbol, side.upper())


def _take_profit_lot_side(
    symbol: str,
    lot,
    side: str,
    mark: float,
    snap: RsiSnapshot,
    trigger: str,
    *,
    reopen_pair: bool,
) -> None:
    if side == "long":
        if lot["long_status"] != "open":
            return
        entry = float(lot["long_entry"])
        size = float(lot["long_size"])
    else:
        if lot["short_status"] != "open":
            return
        entry = float(lot["short_entry"])
        size = float(lot["short_size"])
    if size <= 0 or not should_take_profit(side, entry, mark):
        return

    close_lot_leg(symbol, lot, side, mark, trigger)
    if reopen_pair and is_tradeable_symbol(symbol):
        _open_pair(symbol, snap, f"{trigger}_tp_lot{lot['id']}_{side}")


def _scan_take_profits(
    symbol: str,
    mark: float,
    snap: RsiSnapshot,
    trigger: str,
    *,
    reopen_pair: bool,
    tp_target_pct: float | None = None,
) -> bool:
    took_action = False
    long_agg_closed = False
    short_agg_closed = False

    positions = fetch_symbol_positions(symbol)
    long_agg = positions["long"]
    if long_agg.size > 0 and should_take_profit(
        "long", long_agg.avg_price, mark, target_pct=tp_target_pct,
    ):
        _take_profit_aggregate_side(
            symbol, "long", mark, snap, trigger, reopen_pair=reopen_pair,
        )
        took_action = True
        long_agg_closed = True
        positions = fetch_symbol_positions(symbol)

    short_agg = positions["short"]
    if short_agg.size > 0 and should_take_profit(
        "short", short_agg.avg_price, mark, target_pct=tp_target_pct,
    ):
        _take_profit_aggregate_side(
            symbol, "short", mark, snap, trigger, reopen_pair=reopen_pair,
        )
        took_action = True
        short_agg_closed = True

    if not long_agg_closed:
        for lot in db.get_open_pair_lots(symbol):
            if lot["long_status"] != "open":
                continue
            entry = float(lot["long_entry"])
            if should_take_profit("long", entry, mark, target_pct=tp_target_pct):
                _take_profit_lot_side(
                    symbol, lot, "long", mark, snap, trigger, reopen_pair=reopen_pair,
                )
                took_action = True

    if not short_agg_closed:
        for lot in db.get_open_pair_lots(symbol):
            if lot["short_status"] != "open":
                continue
            entry = float(lot["short_entry"])
            if should_take_profit("short", entry, mark, target_pct=tp_target_pct):
                _take_profit_lot_side(
                    symbol, lot, "short", mark, snap, trigger, reopen_pair=reopen_pair,
                )
                took_action = True

    return took_action


def _sync_lots_with_exchange(symbol: str) -> None:
    positions = fetch_symbol_positions(symbol)
    long_size = positions["long"].size
    short_size = positions["short"].size
    open_lots = db.get_open_pair_lots(symbol)
    lot_long = sum(float(r["long_size"]) for r in open_lots if r["long_status"] == "open")
    lot_short = sum(float(r["short_size"]) for r in open_lots if r["short_status"] == "open")
    if abs(lot_long - long_size) > 1e-6 or abs(lot_short - short_size) > 1e-6:
        logging.warning(
            "  [%s] Lot/exchange size mismatch: lots L=%.4f S=%.4f vs exchange L=%.4f S=%.4f",
            symbol,
            lot_long,
            lot_short,
            long_size,
            short_size,
        )


def _update_status(
    symbol: str,
    snap: RsiSnapshot,
    pair_event: RsiSignal | None,
    mark: float,
    now_str: str,
) -> None:
    positions = fetch_symbol_positions(symbol)
    pending = fetch_pending_orders(symbol)
    long_pos = positions["long"]
    short_pos = positions["short"]
    on_exchange = long_pos.size > 0 or short_pos.size > 0
    position_side = None
    position_size = 0.0
    avg_entry = None
    if long_pos.size >= short_pos.size and long_pos.size > 0:
        position_side = "long"
        position_size = long_pos.size
        avg_entry = long_pos.avg_price
    elif short_pos.size > 0:
        position_side = "short"
        position_size = short_pos.size
        avg_entry = short_pos.avg_price

    update_symbol_status(
        symbol,
        position_side=position_side,
        position_size=position_size,
        avg_entry=avg_entry,
        rsi_value=snap.rsi,
        rsi_prev=snap.prev_rsi,
        rsi_signal=pair_event.entry_trigger if pair_event else "",
        rsi_cross_up_25=snap.cross_up_25,
        rsi_cross_up_75=snap.cross_up_75,
        rsi_cross_down_75=snap.cross_down_75,
        rsi_cross_down_25=snap.cross_down_25,
        is_tracked=db.symbol_has_open_lots(symbol) or on_exchange,
        on_exchange=on_exchange,
        pending_orders=[
            {"order_id": o.order_id, "side": o.side, "price": o.price, "size": o.size}
            for o in pending
        ],
        margin_mode=MARGIN_MODE,
        leverage=LEVERAGE,
        last_updated=now_str,
    )


def evaluate_rsi_trade(
    symbol: str,
    snap: RsiSnapshot,
    signal: RsiSignal | None = None,
) -> None:
    if not has_credentials():
        logging.warning("  [%s] RSI trading skipped: missing API credentials", symbol)
        return

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    pair_event = signal if signal and signal.side == "pair" else detect_pair_event(snap)

    if not _trading_enabled():
        mark = fetch_side_mark_price(symbol) if has_credentials() else 0.0
        try:
            _sync_lots_with_exchange(symbol)
            _update_status(symbol, snap, pair_event, mark, now_str)
        except ExchangeClientError as exc:
            logging.warning("  [%s] Sync failed: %s", symbol, exc)
        logging.info("  [%s] Trading disabled — no orders", symbol)
        return

    if not snap.ready:
        logging.info("  [%s] RSI not ready — skip trading", symbol)
        return

    if not is_tradeable_symbol(symbol):
        mark = fetch_side_mark_price(symbol)
        if mark <= 0:
            mark = snap.close
        _force_close_blocked_symbol(symbol, mark)
        return

    mark = fetch_side_mark_price(symbol)
    if mark <= 0:
        mark = snap.close
    _sync_lots_with_exchange(symbol)
    _update_status(symbol, snap, pair_event, mark, now_str)

    from src.margin_guard import effective_tp_pct, should_block_new_entries

    tp_pct = effective_tp_pct()
    block_entries = should_block_new_entries()

    _scan_take_profits(
        symbol, mark, snap, trigger="cycle",
        reopen_pair=False, tp_target_pct=tp_pct,
    )

    if pair_event is None:
        return

    logging.info(
        "  [%s] RSI cross event: %s | RSI=%.2f",
        symbol,
        pair_event.entry_trigger,
        snap.rsi,
    )

    if block_entries:
        logging.info(
            "  [%s] Margin guard — skip stack/new entry (maint tier active)",
            symbol,
        )
        return

    trigger = pair_event.entry_trigger or "rsi_cross"
    took_action = _scan_take_profits(
        symbol, mark, snap, trigger=trigger,
        reopen_pair=not block_entries, tp_target_pct=tp_pct,
    )
    if took_action:
        return

    if db.symbol_has_open_lots(symbol):
        _open_pair(symbol, snap, f"{trigger}_stack")
        return

    if can_open_new_symbol():
        _open_pair(symbol, snap, trigger)
        return

    logging.info(
        "  [%s] Max open symbols reached (%d/%d) — skip pair entry",
        symbol,
        db.count_open_symbols(),
        MAX_OPEN_SYMBOLS,
    )
