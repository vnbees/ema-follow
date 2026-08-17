import logging

from src import database as db
from src.exchange import ExchangeClientError, fetch_all_open_positions, has_credentials
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
    live_exchange_symbols: set[str] = set()

    for symbol in exchange_symbols:
        if db.symbol_has_open_lots(symbol):
            live_exchange_symbols.add(symbol)
            continue
        # WS can retain ghost sides after a flat close; never invent lots from
        # cache alone — confirm with REST before adopt (skip while REST cooldown).
        try:
            from src.exchange.binance import (
                fetch_symbol_positions_rest,
                is_optional_rest_blocked,
            )

            if is_optional_rest_blocked():
                logging.info("  Skip adopt %s — optional REST blocked", symbol)
                continue
            positions = fetch_symbol_positions_rest(symbol, priority="optional")
        except ExchangeClientError as exc:
            logging.warning(
                "  Skip adopt %s — REST confirm failed: %s", symbol, exc,
            )
            continue
        long_size = positions["long"].size
        short_size = positions["short"].size
        if long_size <= 0 and short_size <= 0:
            logging.info(
                "  Skip adopt %s — REST flat (stale WS ghost)", symbol,
            )
            try:
                from src.exchange.binance_ws.cache import CACHE

                CACHE.apply_position_updates(
                    [],
                    closed_keys=[(symbol, "long"), (symbol, "short")],
                )
            except Exception:  # noqa: BLE001
                pass
            continue
        lot_id = db.insert_pair_lot(
            symbol,
            long_size=long_size,
            long_entry=positions["long"].avg_price or 0.0,
            short_size=short_size,
            short_entry=positions["short"].avg_price or 0.0,
            margin_usdt=LEGACY_MARGIN_USDT,
            entry_trigger="adopted",
        )
        # insert_pair_lot always marks both sides open — close empty legs.
        if long_size <= 0:
            db.close_lot_side(
                lot_id, "long", realized_pnl_usdt=0.0, close_price=None, close_reason="sync"
            )
        if short_size <= 0:
            db.close_lot_side(
                lot_id, "short", realized_pnl_usdt=0.0, close_price=None, close_reason="sync"
            )
        logging.info("  Adopted exchange pair %s (no lot record)", symbol)
        live_exchange_symbols.add(symbol)

    managed = sorted(
        live_exchange_symbols | {row["symbol"] for row in db.get_all_open_pair_lots()}
    )
    if managed:
        logging.info("Tracking %d symbol(s): %s", len(managed), ", ".join(managed))
    return managed


def get_open_position_count() -> int:
    return db.count_open_symbols()


def get_managed_symbols() -> list[str]:
    return sync_exchange_positions()
