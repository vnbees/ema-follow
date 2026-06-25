import logging

from src import database as db
from src.bitget_client import BitgetClientError, Position, fetch_all_open_positions, has_credentials
from src.supertrend_trading import clear_st_state, load_st_state_from_db


def restore_tracked_positions() -> list[str]:
    rows = db.get_open_supertrend_trades()
    for row in rows:
        load_st_state_from_db(row)
    if rows:
        logging.info(
            "Restored %d open SuperTrend trade(s) from DB: %s",
            len(rows),
            ", ".join(row["symbol"] for row in rows),
        )
    return [row["symbol"] for row in rows]


def sync_exchange_positions() -> list[str]:
    db_symbols = {row["symbol"] for row in db.get_open_supertrend_trades()}

    if not has_credentials():
        return sorted(db_symbols)

    try:
        exchange_positions = fetch_all_open_positions()
    except BitgetClientError as exc:
        logging.warning("Exchange position sync failed: %s — using DB only", exc)
        return sorted(db_symbols)

    exchange_map: dict[str, Position] = {
        pos.symbol: pos for pos in exchange_positions if pos.size > 0 and pos.side
    }
    exchange_symbols = set(exchange_map)

    for symbol, pos in exchange_map.items():
        row = db.get_open_supertrend_trade(symbol)
        if row is None:
            db.insert_supertrend_trade(
                symbol=symbol,
                side=pos.side or "long",
                entry_price=pos.avg_price or 0.0,
                trend_5m_entry="",
                trend_1h_entry="",
                entry_trigger="adopted",
                position_size=pos.size,
            )
            row = db.get_open_supertrend_trade(symbol)
            logging.info(
                "  Adopted exchange position %s %s size=%.4f (no DB record)",
                symbol,
                pos.side,
                pos.size,
            )
        if row is not None:
            load_st_state_from_db(row)
            db.update_supertrend_trade(symbol, position_size=pos.size)

    for symbol in db_symbols - exchange_symbols:
        db.close_supertrend_trade(symbol, close_reason="exchange_closed")
        clear_st_state(symbol)
        logging.info("  Closed DB trade %s — no longer open on exchange", symbol)

    managed = sorted(exchange_symbols | {row["symbol"] for row in db.get_open_supertrend_trades()})
    if managed:
        logging.info("Tracking %d open position(s): %s", len(managed), ", ".join(managed))
    return managed


def get_open_position_count() -> int:
    return len(db.get_open_supertrend_trades())


def get_managed_symbols() -> list[str]:
    return sync_exchange_positions()
