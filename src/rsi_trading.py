import logging
import threading
from datetime import datetime, timedelta, timezone

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
    AGGREGATE_TP_ENABLED,
    BREAKEVEN_AFTER_HOURS,
    BREAKEVEN_WHEN_LOSING_ENABLED,
    LEVERAGE,
    MARGIN_MODE,
    MARGIN_PREFLIGHT_ENABLED,
    MAX_AGE_CLOSES_PER_CYCLE,
    MAX_LOT_AGE_DAYS,
    MAX_OPEN_SYMBOLS,
    PAIR_PROFIT_TARGET_PCT,
    TRADING_ENABLED,
)
from src.order_sizing import compute_entry_margin_usdt, margin_to_notional
from src.rsi import RsiSnapshot
from src.rsi_signals import (
    RsiSignal,
    detect_pair_event,
    is_underwater,
    price_move_pct,
    should_take_profit,
)
from src.exchange.symbols import is_tradeable_symbol
from src.trading import (
    _get_state,
    _record_market_entry,
    ensure_symbol_configured,
    resolve_order_fill,
)

_CONFIG_TRADING_ENABLED = TRADING_ENABLED

# Shared by the 5m cycle TP scan, the age-close scan, BE scan and the realtime TP watcher
# so two threads can never batch-close the same lots concurrently.
TP_CLOSE_LOCK = threading.RLock()

# Global per-cycle budget of age-close exchange orders (reset by run_cycle).
_age_close_budget = 0

# Sticky BE arms: once a lot side goes underwater after BREAKEVEN_AFTER_HOURS,
# keep BE target until close (in-memory; re-arms after restart if still underwater).
_breakeven_armed: set[tuple[int, str]] = set()
_breakeven_arm_lock = threading.Lock()


def reset_age_close_budget() -> None:
    global _age_close_budget
    _age_close_budget = MAX_AGE_CLOSES_PER_CYCLE


def age_close_budget_remaining() -> int:
    return _age_close_budget


def reset_breakeven_arms() -> None:
    """Test helper — clear sticky BE arm set."""
    with _breakeven_arm_lock:
        _breakeven_armed.clear()


def is_breakeven_armed(lot_id: int, side: str) -> bool:
    with _breakeven_arm_lock:
        return (int(lot_id), side) in _breakeven_armed


def clear_breakeven_arm(lot_id: int, side: str) -> None:
    with _breakeven_arm_lock:
        _breakeven_armed.discard((int(lot_id), side))


def arm_breakeven_if_needed(
    lot_id: int,
    side: str,
    opened_at: datetime | None,
    entry: float,
    mark: float,
) -> bool:
    """Arm sticky BE when lot is old enough and currently underwater. Returns armed state."""
    if not BREAKEVEN_WHEN_LOSING_ENABLED or BREAKEVEN_AFTER_HOURS <= 0:
        return False
    if opened_at is None or entry <= 0 or mark <= 0:
        return is_breakeven_armed(lot_id, side)
    age_h = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600.0
    if age_h + 1e-9 < BREAKEVEN_AFTER_HOURS:
        return is_breakeven_armed(lot_id, side)
    if is_underwater(side, entry, mark):
        with _breakeven_arm_lock:
            _breakeven_armed.add((int(lot_id), side))
        return True
    return is_breakeven_armed(lot_id, side)


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
    try:
        for pos in fetch_all_open_positions():
            if not is_tradeable_symbol(pos.symbol):
                symbols.add(pos.symbol.upper())
    except ExchangeClientError as exc:
        logging.warning("  Skip exchange scan for blocked symbols: %s", exc)
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
    _flush_post_order_reconcile()
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
    db_avg: float | None = None,
) -> None:
    positions = fetch_symbol_positions(symbol)
    pos = positions[side]
    if pos.size <= 0:
        return
    size_str = _format_close_size(symbol, pos.size)
    pnl = fetch_side_unrealized_pnl(symbol, side)
    entry_for_move = db_avg if db_avg and db_avg > 0 else pos.avg_price
    move = price_move_pct(side, entry_for_move, mark)
    logging.info(
        "  [%s] Aggregate take profit %s | trigger=%s | move=%+.2f%% | size=%s | "
        "pnl≈%+.2f | db_avg=%s | exchange_avg=%.6f",
        symbol,
        side.upper(),
        trigger,
        move,
        size_str,
        pnl,
        f"{db_avg:.6f}" if db_avg and db_avg > 0 else "—",
        pos.avg_price,
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
) -> dict | None:
    """Close one open lot side. Returns {fill, pnl, size, entry} or None if skipped."""
    if side == "long":
        if lot["long_status"] != "open":
            return None
        entry = float(lot["long_entry"])
        size = float(lot["long_size"])
    else:
        if lot["short_status"] != "open":
            return None
        entry = float(lot["short_entry"])
        size = float(lot["short_size"])
    if size <= 0:
        return None

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
    return {"fill": fill, "pnl": pnl, "size": size, "entry": entry, "move_pct": move}


def _take_profit_lot_side(
    symbol: str,
    lot,
    side: str,
    mark: float,
    snap: RsiSnapshot,
    trigger: str,
    *,
    reopen_pair: bool,
    tp_target_pct: float | None = None,
) -> None:
    """Single-lot TP helper (tests / callers). Prefer batched path in _scan_take_profits."""
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
    if size <= 0 or not should_take_profit(side, entry, mark, target_pct=tp_target_pct):
        return

    close_lot_leg(symbol, lot, side, mark, trigger)
    if reopen_pair and is_tradeable_symbol(symbol):
        _open_pair(symbol, snap, f"{trigger}_tp_lot{lot['id']}_{side}")


def _lot_side_fields(lot, side: str) -> tuple[float, float] | None:
    if side == "long":
        if lot["long_status"] != "open":
            return None
        entry = float(lot["long_entry"])
        size = float(lot["long_size"])
    else:
        if lot["short_status"] != "open":
            return None
        entry = float(lot["short_entry"])
        size = float(lot["short_size"])
    if size <= 0:
        return None
    return entry, size


def _take_profit_lots_side_batched(
    symbol: str,
    mark: float,
    snap: RsiSnapshot,
    trigger: str,
    side: str,
    *,
    reopen_pair: bool,
    tp_target_pct: float | None = None,
) -> bool:
    """Close all lots on one side that hit TP with a single exchange order."""
    positions = fetch_symbol_positions(symbol)
    exchange_size = positions[side].size
    if exchange_size <= 0:
        # Phantom lot sides (e.g. DB restored after exchange already flat).
        n = _trim_lot_side_to_exchange(symbol, side, 0.0)
        if n:
            logging.info(
                "  [%s] Skip batch TP %s — exchange flat, closed %d phantom lot side(s)",
                symbol,
                side.upper(),
                n,
            )
        return False

    candidates: list[tuple[object, float, float]] = []
    for lot in db.get_open_pair_lots(symbol):
        fields = _lot_side_fields(lot, side)
        if fields is None:
            continue
        entry, size = fields
        if should_take_profit(side, entry, mark, target_pct=tp_target_pct):
            candidates.append((lot, entry, size))

    if not candidates:
        return False

    # Cap to live exchange size — never send reduceOnly larger than position.
    selected: list[tuple[object, float, float]] = []
    selected_size = 0.0
    for lot, entry, size in candidates:
        if selected_size + size > exchange_size + 1e-9:
            continue
        selected.append((lot, entry, size))
        selected_size += size
    if not selected:
        logging.warning(
            "  [%s] Skip batch TP %s — TP lots exceed exchange size %.4f (sync will trim)",
            symbol,
            side.upper(),
            exchange_size,
        )
        _trim_lot_side_to_exchange(symbol, side, exchange_size)
        return False

    size_str = _format_close_size(symbol, selected_size)
    lot_ids = [int(lot["id"]) for lot, _, _ in selected]  # type: ignore[index]
    logging.info(
        "  [%s] Batch take profit %s | trigger=%s | lots=%s | size=%s | n=%d",
        symbol,
        side.upper(),
        trigger,
        lot_ids,
        size_str,
        len(selected),
    )
    fill = _close_side_and_resolve_fill(symbol, side, selected_size, mark)
    for lot, entry, size in selected:
        pnl = _estimate_leg_pnl(side, entry, fill, size)
        logging.info(
            "  [%s] Lot #%d batch-close %s fill=%.6f | pnl≈%+.2f",
            symbol,
            int(lot["id"]),  # type: ignore[index]
            side.upper(),
            fill,
            pnl,
        )
        db.close_lot_side(
            int(lot["id"]),  # type: ignore[index]
            side,
            realized_pnl_usdt=pnl,
            close_price=fill,
        )

    from src.notify import notify_close

    notify_close(symbol, f"{side.upper()}×{len(selected)}")
    if reopen_pair and is_tradeable_symbol(symbol):
        _open_pair(symbol, snap, f"{trigger}_tp_batch_{side}")
    return True


def _parse_opened_at(raw) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _max_age_trigger() -> str:
    return f"max_age_{MAX_LOT_AGE_DAYS:g}d"


def _close_expired_lots_side_batched(
    symbol: str,
    mark: float,
    side: str,
    cutoff: datetime,
) -> bool:
    """Close all expired lot sides (opened before cutoff) with one exchange order."""
    global _age_close_budget

    positions = fetch_symbol_positions(symbol)
    exchange_size = positions[side].size
    if exchange_size <= 0:
        return False

    candidates: list[tuple[object, float, float]] = []
    for lot in db.get_open_pair_lots(symbol):
        fields = _lot_side_fields(lot, side)
        if fields is None:
            continue
        opened = _parse_opened_at(lot["opened_at"])
        if opened is None or opened > cutoff:
            continue
        entry, size = fields
        candidates.append((lot, entry, size))

    if not candidates:
        return False

    # Cap to live exchange size — never send reduceOnly larger than position.
    selected: list[tuple[object, float, float]] = []
    selected_size = 0.0
    for lot, entry, size in candidates:
        if selected_size + size > exchange_size + 1e-9:
            continue
        selected.append((lot, entry, size))
        selected_size += size
    if not selected:
        logging.warning(
            "  [%s] Skip age close %s — expired lots exceed exchange size %.4f (sync will trim)",
            symbol,
            side.upper(),
            exchange_size,
        )
        return False

    trigger = _max_age_trigger()
    size_str = _format_close_size(symbol, selected_size)
    lot_ids = [int(lot["id"]) for lot, _, _ in selected]  # type: ignore[index]
    logging.info(
        "  [%s] Batch age close %s | trigger=%s | lots=%s | size=%s | n=%d | budget=%d",
        symbol,
        side.upper(),
        trigger,
        lot_ids,
        size_str,
        len(selected),
        _age_close_budget,
    )
    fill = _close_side_and_resolve_fill(symbol, side, selected_size, mark)
    _age_close_budget -= 1
    for lot, entry, size in selected:
        pnl = _estimate_leg_pnl(side, entry, fill, size)
        logging.info(
            "  [%s] Lot #%d age-close %s fill=%.6f | pnl≈%+.2f | trigger=%s",
            symbol,
            int(lot["id"]),  # type: ignore[index]
            side.upper(),
            fill,
            pnl,
            trigger,
        )
        db.close_lot_side(
            int(lot["id"]),  # type: ignore[index]
            side,
            realized_pnl_usdt=pnl,
            close_price=fill,
        )

    from src.notify import notify_close

    notify_close(symbol, f"{side.upper()}×{len(selected)} ({trigger})")
    return True


def _scan_max_age_closes(symbol: str, mark: float) -> bool:
    """Close lot legs older than MAX_LOT_AGE_DAYS (no reopen, budget per cycle)."""
    if MAX_LOT_AGE_DAYS <= 0 or mark <= 0:
        return False
    if _age_close_budget <= 0:
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_LOT_AGE_DAYS)
    took_action = False
    with TP_CLOSE_LOCK:
        for side in ("long", "short"):
            if _age_close_budget <= 0:
                break
            try:
                if _close_expired_lots_side_batched(symbol, mark, side, cutoff):
                    took_action = True
            except ExchangeClientError as exc:
                logging.error("  [%s] Age close %s failed: %s", symbol, side.upper(), exc)
    if took_action:
        _flush_post_order_reconcile()
    return took_action


def _breakeven_trigger() -> str:
    return f"be_after_{BREAKEVEN_AFTER_HOURS:g}h"


def _lot_side_eligible_for_breakeven(
    lot,
    side: str,
    mark: float,
) -> tuple[object, float, float] | None:
    """Return (lot, entry, size) when sticky-armed and at/above BE (does not arm)."""
    fields = _lot_side_fields(lot, side)
    if fields is None:
        return None
    entry, size = fields
    lot_id = int(lot["id"])
    if not is_breakeven_armed(lot_id, side):
        return None
    if not should_take_profit(side, entry, mark, target_pct=0.0):
        return None
    return lot, entry, size


def arm_breakeven_lots_for_symbol(symbol: str, mark: float) -> int:
    """Arm sticky BE for old underwater open lots. Returns newly-checked armed count."""
    if not BREAKEVEN_WHEN_LOSING_ENABLED or BREAKEVEN_AFTER_HOURS <= 0 or mark <= 0:
        return 0
    armed_n = 0
    for lot in db.get_open_pair_lots(symbol):
        for side in ("long", "short"):
            fields = _lot_side_fields(lot, side)
            if fields is None:
                continue
            entry, _size = fields
            opened = _parse_opened_at(lot["opened_at"])
            if arm_breakeven_if_needed(int(lot["id"]), side, opened, entry, mark):
                armed_n += 1
    return armed_n


def side_has_breakeven_candidate(symbol: str, side: str, mark: float) -> bool:
    """True when at least one open lot on side is sticky-armed and at/above BE."""
    if not BREAKEVEN_WHEN_LOSING_ENABLED or BREAKEVEN_AFTER_HOURS <= 0 or mark <= 0:
        return False
    for lot in db.get_open_pair_lots(symbol):
        if _lot_side_eligible_for_breakeven(lot, side, mark) is not None:
            return True
    return False


def _close_breakeven_lots_side_batched(symbol: str, mark: float, side: str) -> bool:
    """Close sticky-armed BE lot sides (at/above entry) with one exchange order."""
    positions = fetch_symbol_positions(symbol)
    exchange_size = positions[side].size
    if exchange_size <= 0:
        return False

    candidates: list[tuple[object, float, float]] = []
    for lot in db.get_open_pair_lots(symbol):
        hit = _lot_side_eligible_for_breakeven(lot, side, mark)
        if hit is not None:
            candidates.append(hit)

    if not candidates:
        return False

    selected: list[tuple[object, float, float]] = []
    selected_size = 0.0
    for lot, entry, size in candidates:
        if selected_size + size > exchange_size + 1e-9:
            continue
        selected.append((lot, entry, size))
        selected_size += size
    if not selected:
        logging.warning(
            "  [%s] Skip BE close %s — BE lots exceed exchange size %.4f (sync will trim)",
            symbol,
            side.upper(),
            exchange_size,
        )
        return False

    trigger = _breakeven_trigger()
    size_str = _format_close_size(symbol, selected_size)
    lot_ids = [int(lot["id"]) for lot, _, _ in selected]  # type: ignore[index]
    logging.info(
        "  [%s] Batch be close %s | trigger=%s | lots=%s | size=%s | n=%d",
        symbol,
        side.upper(),
        trigger,
        lot_ids,
        size_str,
        len(selected),
    )
    fill = _close_side_and_resolve_fill(symbol, side, selected_size, mark)
    for lot, entry, size in selected:
        lot_id = int(lot["id"])  # type: ignore[index]
        pnl = _estimate_leg_pnl(side, entry, fill, size)
        logging.info(
            "  [%s] Lot #%d be-close %s fill=%.6f | pnl≈%+.2f | trigger=%s",
            symbol,
            lot_id,
            side.upper(),
            fill,
            pnl,
            trigger,
        )
        db.close_lot_side(
            lot_id,
            side,
            realized_pnl_usdt=pnl,
            close_price=fill,
        )
        clear_breakeven_arm(lot_id, side)

    from src.notify import notify_close

    notify_close(symbol, f"{side.upper()}×{len(selected)} ({trigger})")
    return True


def _scan_breakeven_closes(symbol: str, mark: float) -> bool:
    """Arm old underwater lots, then close sticky-armed BE legs at/above entry (no reopen)."""
    if not BREAKEVEN_WHEN_LOSING_ENABLED or BREAKEVEN_AFTER_HOURS <= 0 or mark <= 0:
        return False

    arm_breakeven_lots_for_symbol(symbol, mark)

    took_action = False
    with TP_CLOSE_LOCK:
        for side in ("long", "short"):
            try:
                if _close_breakeven_lots_side_batched(symbol, mark, side):
                    took_action = True
            except ExchangeClientError as exc:
                logging.error("  [%s] BE close %s failed: %s", symbol, side.upper(), exc)
    if took_action:
        _flush_post_order_reconcile()
    return took_action


def _flush_post_order_reconcile() -> None:
    try:
        from src.exchange.binance_ws import flush_pending_reconcile

        flush_pending_reconcile()
    except Exception as exc:  # noqa: BLE001
        logging.debug("Post-order reconcile flush skipped: %s", exc)


def _scan_take_profits(
    symbol: str,
    mark: float,
    snap: RsiSnapshot,
    trigger: str,
    *,
    reopen_pair: bool,
    tp_target_pct: float | None = None,
) -> bool:
    with TP_CLOSE_LOCK:
        return _scan_take_profits_locked(
            symbol, mark, snap, trigger,
            reopen_pair=reopen_pair, tp_target_pct=tp_target_pct,
        )


def _scan_take_profits_locked(
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
    long_db = db.compute_open_lot_side_avg(symbol, "long") if AGGREGATE_TP_ENABLED else None
    if (
        AGGREGATE_TP_ENABLED
        and long_db is not None
        and long_agg.size > 0
        and should_take_profit(
            "long", long_db[0], mark, target_pct=tp_target_pct,
        )
    ):
        _take_profit_aggregate_side(
            symbol,
            "long",
            mark,
            snap,
            trigger,
            reopen_pair=reopen_pair,
            db_avg=long_db[0],
        )
        took_action = True
        long_agg_closed = True
        positions = fetch_symbol_positions(symbol)

    short_agg = positions["short"]
    short_db = db.compute_open_lot_side_avg(symbol, "short") if AGGREGATE_TP_ENABLED else None
    if (
        AGGREGATE_TP_ENABLED
        and short_db is not None
        and short_agg.size > 0
        and should_take_profit(
            "short", short_db[0], mark, target_pct=tp_target_pct,
        )
    ):
        _take_profit_aggregate_side(
            symbol,
            "short",
            mark,
            snap,
            trigger,
            reopen_pair=reopen_pair,
            db_avg=short_db[0],
        )
        took_action = True
        short_agg_closed = True

    if not long_agg_closed:
        if _take_profit_lots_side_batched(
            symbol, mark, snap, trigger, "long",
            reopen_pair=reopen_pair, tp_target_pct=tp_target_pct,
        ):
            took_action = True

    if not short_agg_closed:
        if _take_profit_lots_side_batched(
            symbol, mark, snap, trigger, "short",
            reopen_pair=reopen_pair, tp_target_pct=tp_target_pct,
        ):
            took_action = True

    if took_action:
        _flush_post_order_reconcile()
    return took_action


def _trim_lot_side_to_exchange(symbol: str, side: str, exchange_size: float) -> int:
    """Close oldest open lot sides until DB total <= exchange size. Returns # sides closed."""
    side = side.lower()
    status_key = "long_status" if side == "long" else "short_status"
    size_key = "long_size" if side == "long" else "short_size"
    closed = 0
    closed_ids: set[int] = set()
    while True:
        open_lots = db.get_open_pair_lots(symbol)
        lot_total = sum(
            float(r[size_key]) for r in open_lots if r[status_key] == "open"
        )
        if lot_total <= exchange_size + 1e-6:
            break
        # FIFO by opened_at (get_open_pair_lots already ASC).
        target = next(
            (
                r
                for r in open_lots
                if r[status_key] == "open" and int(r["id"]) not in closed_ids
            ),
            None,
        )
        if target is None:
            logging.error(
                "  [%s] Lot sync stuck — %s lots still %.4f > exchange %.4f after closes",
                symbol,
                side.upper(),
                lot_total,
                exchange_size,
            )
            break
        lot_id = int(target["id"])
        lot_size = float(target[size_key])
        logging.warning(
            "  [%s] Sync-close phantom %s lot #%d size=%.4f (lots=%.4f > exchange=%.4f)",
            symbol,
            side.upper(),
            lot_id,
            lot_size,
            lot_total,
            exchange_size,
        )
        db.close_lot_side(lot_id, side, realized_pnl_usdt=0.0, close_price=None)
        closed_ids.add(lot_id)
        closed += 1
    return closed


def _sync_lots_with_exchange(symbol: str) -> None:
    """Align DB lots to exchange. Trim phantom oversize; warn when exchange has extra size."""
    positions = fetch_symbol_positions(symbol)
    long_size = positions["long"].size
    short_size = positions["short"].size
    trimmed_long = _trim_lot_side_to_exchange(symbol, "long", long_size)
    trimmed_short = _trim_lot_side_to_exchange(symbol, "short", short_size)

    open_lots = db.get_open_pair_lots(symbol)
    lot_long = sum(float(r["long_size"]) for r in open_lots if r["long_status"] == "open")
    lot_short = sum(float(r["short_size"]) for r in open_lots if r["short_status"] == "open")
    if abs(lot_long - long_size) > 1e-6 or abs(lot_short - short_size) > 1e-6:
        logging.warning(
            "  [%s] Lot/exchange size mismatch: lots L=%.4f S=%.4f vs exchange L=%.4f S=%.4f"
            "%s",
            symbol,
            lot_long,
            lot_short,
            long_size,
            short_size,
            (
                f" (trimmed L={trimmed_long} S={trimmed_short})"
                if trimmed_long or trimmed_short
                else " (exchange larger than lots — cannot invent lot rows)"
            ),
        )
    elif trimmed_long or trimmed_short:
        logging.info(
            "  [%s] Lot sync OK after trim L=%d S=%d → L=%.4f S=%.4f",
            symbol,
            trimmed_long,
            trimmed_short,
            lot_long,
            lot_short,
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

    # BE-when-losing after TP; hard-age still last so TP/BE win when eligible.
    _scan_breakeven_closes(symbol, mark)
    # Age rule runs after TP/BE so legs at TP/BE are always closed first.
    _scan_max_age_closes(symbol, mark)

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


def manual_close_side(symbol: str, side: str) -> dict:
    """Dashboard: close entire LONG or SHORT side for a symbol (no reopen)."""
    from src.exchange import binance as binance_mod

    symbol = symbol.upper()
    side = side.lower()
    if side not in ("long", "short"):
        return {"ok": False, "message": "side must be long or short"}
    if not is_trading_enabled() or not TRADING_ENABLED:
        return {"ok": False, "message": "trading disabled"}
    if not has_credentials():
        return {"ok": False, "message": "missing exchange credentials"}
    if binance_mod.is_rate_limited():
        return {
            "ok": False,
            "message": (
                f"rate-limit cooldown {binance_mod.rate_limit_remaining_sec():.0f}s — try later"
            ),
            "rate_limited": True,
        }

    positions = fetch_symbol_positions(symbol)
    pos = positions[side]
    if pos.size <= 0:
        return {"ok": False, "message": f"no open {side} on exchange for {symbol}"}

    mark = fetch_side_mark_price(symbol)
    if mark <= 0:
        mark = pos.avg_price
    db_avg_row = db.compute_open_lot_side_avg(symbol, side)
    db_avg = db_avg_row[0] if db_avg_row else None
    snap = RsiSnapshot(ready=False, rsi=0.0, close=mark)
    with TP_CLOSE_LOCK:
        _take_profit_aggregate_side(
            symbol,
            side,
            mark,
            snap,
            "dashboard_manual_side",
            reopen_pair=False,
            db_avg=db_avg,
        )
    _flush_post_order_reconcile()
    return {
        "ok": True,
        "message": f"closed {symbol} {side.upper()}",
        "symbol": symbol,
        "side": side,
        "size": pos.size,
        "db_avg": db_avg,
        "mark": mark,
    }


def manual_close_leg(lot_id: int, side: str) -> dict:
    """Dashboard: close one lot leg (long or short)."""
    from src.exchange import binance as binance_mod

    side = side.lower()
    if side not in ("long", "short"):
        return {"ok": False, "message": "side must be long or short"}
    if not is_trading_enabled() or not TRADING_ENABLED:
        return {"ok": False, "message": "trading disabled"}
    if not has_credentials():
        return {"ok": False, "message": "missing exchange credentials"}
    if binance_mod.is_rate_limited():
        return {
            "ok": False,
            "message": (
                f"rate-limit cooldown {binance_mod.rate_limit_remaining_sec():.0f}s — try later"
            ),
            "rate_limited": True,
        }

    lot = db.get_pair_lot_by_id(int(lot_id))
    if lot is None:
        return {"ok": False, "message": f"lot #{lot_id} not found"}
    status_key = "long_status" if side == "long" else "short_status"
    if lot[status_key] != "open":
        return {"ok": False, "message": f"lot #{lot_id} {side} already closed"}

    symbol = str(lot["symbol"]).upper()
    mark = fetch_side_mark_price(symbol)
    if mark <= 0:
        entry = float(lot["long_entry"] if side == "long" else lot["short_entry"])
        mark = entry
    with TP_CLOSE_LOCK:
        lot = db.get_pair_lot_by_id(int(lot_id))
        if lot is None or lot[status_key] != "open":
            return {"ok": False, "message": f"lot #{lot_id} {side} already closed"}
        result = close_lot_leg(symbol, lot, side, mark, "dashboard_manual_leg")
    if result is None:
        return {"ok": False, "message": f"could not close lot #{lot_id} {side}"}
    _flush_post_order_reconcile()
    return {
        "ok": True,
        "message": f"closed {symbol} lot #{lot_id} {side.upper()}",
        "symbol": symbol,
        "lot_id": int(lot_id),
        "side": side,
        **result,
    }
