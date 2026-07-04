import logging

from src import database as db
from src.exchange import ExchangeClientError, fetch_all_open_positions, fetch_symbol_positions, has_credentials
from src.config import LEGACY_MARGIN_USDT


def restore_tracked_positions() -> list[str]:
    rows = db.get_all_open_pair_lots()
    if rows:
        logging.info(
            "Restored %d open pair lot(s): %s",
            len(rows),
            ", ".join(f"{r['symbol']}#{r['id']}" for r in rows),
        )
    return sorted({row["symbol"] for row in rows})


def sync_exchange_positions() -> list[str]:
    db_symbols = {row["symbol"] for row in db.get_all_open_pair_lots()}

    if not has_credentials():
        return sorted(db_symbols)

    try:
        all_positions = fetch_all_open_positions()
    except ExchangeClientError as exc:
        logging.warning("Exchange position sync failed: %s — using DB only", exc)
        return sorted(db_symbols)

    exchange_symbols = {pos.symbol for pos in all_positions if pos.size > 0}

    for symbol in exchange_symbols:
        if db.symbol_has_open_lots(symbol):
            continue
        positions = fetch_symbol_positions(symbol)
        if positions["long"].size > 0 or positions["short"].size > 0:
            db.insert_pair_lot(
                symbol,
                long_size=positions["long"].size,
                long_entry=positions["long"].avg_price or 0.0,
                short_size=positions["short"].size,
                short_entry=positions["short"].avg_price or 0.0,
                margin_usdt=LEGACY_MARGIN_USDT,
                entry_trigger="adopted",
            )
            logging.info("  Adopted exchange pair %s (no lot record)", symbol)

    managed = sorted(exchange_symbols | {row["symbol"] for row in db.get_all_open_pair_lots()})
    if managed:
        logging.info("Tracking %d symbol(s): %s", len(managed), ", ".join(managed))
    return managed


def get_open_position_count() -> int:
    return db.count_open_symbols()


def get_managed_symbols() -> list[str]:
    return sync_exchange_positions()
