import logging

from src import database as db
from src.bitget_client import BitgetClientError, Position, fetch_all_open_positions, has_credentials
from src.ichimoku_trading import clear_ichi_state, load_ichi_state_from_db


def restore_tracked_positions() -> list[str]:
    """Load open trades from DB into memory on startup."""
    rows = db.get_open_ichimoku_trades()
    for row in rows:
        load_ichi_state_from_db(row)
    if rows:
        logging.info(
            "Restored %d open Ichimoku trade(s) from DB: %s",
            len(rows),
            ", ".join(row["symbol"] for row in rows),
        )
    return [row["symbol"] for row in rows]


def sync_exchange_positions() -> list[str]:
    """Reconcile DB with Bitget open positions; return all symbols to manage."""
    db_symbols = {row["symbol"] for row in db.get_open_ichimoku_trades()}

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
        row = db.get_open_ichimoku_trade(symbol)
        if row is None:
            db.insert_ichimoku_trade(
                symbol=symbol,
                side=pos.side or "long",
                entry_price=pos.avg_price or 0.0,
                stop_price=0.0,
                risk_distance=0.0,
                tp1_price=0.0,
                trigger_type="adopted",
                position_size=pos.size,
            )
            row = db.get_open_ichimoku_trade(symbol)
            logging.info(
                "  Adopted exchange position %s %s size=%.4f (no DB record)",
                symbol,
                pos.side,
                pos.size,
            )
        if row is not None:
            load_ichi_state_from_db(row)
            db.update_ichimoku_trade(symbol, position_size=pos.size)

    for symbol in db_symbols - exchange_symbols:
        db.close_ichimoku_trade(symbol)
        clear_ichi_state(symbol)
        logging.info("  Closed DB trade %s — no longer open on exchange", symbol)

    managed = sorted(exchange_symbols | {row["symbol"] for row in db.get_open_ichimoku_trades()})
    if managed:
        logging.info("Tracking %d open position(s): %s", len(managed), ", ".join(managed))
    return managed


def get_managed_symbols() -> list[str]:
    return sync_exchange_positions()
