"""SMC process loop. Replaces the Donchian cycle when SMC_ENABLED is on."""

from __future__ import annotations

import logging
import time

from src.smc.broker import manage
from src.smc.flatten import flatten_donchian_once

_TICK_SEC = 2.0


def _maybe_bookkeeping(last_bar: int) -> int:
    """Equity snapshot and the daily spot skim, once per closed 15m bar."""
    from src.smc.signals import BAR_MS

    now_ms = int(time.time() * 1000)
    closed = now_ms - (now_ms % BAR_MS) - BAR_MS
    if closed <= last_bar:
        return last_bar
    try:
        from src.donchian.cycle import _log_balance

        _log_balance()
    except Exception as exc:  # noqa: BLE001
        logging.debug("SMC equity log skipped: %s", exc)
    try:
        from src.spot_transfer import process_daily_spot_transfer

        process_daily_spot_transfer("BTCUSDT")
    except Exception as exc:  # noqa: BLE001
        logging.debug("SMC spot transfer skipped: %s", exc)
    return closed


def _seed_available_book() -> None:
    """Count this bot's resting entries immediately, before the REST sync."""
    from src.exchange.binance_ws.cache import CACHE
    from src.exchange.types import PendingOrder
    from src.smc import store

    book: dict[str, list[PendingOrder]] = {}
    for row in store.active_rows():
        if str(row["status"]) != "pending":
            continue
        order_id = str(row["entry_order_id"] or "")
        qty = float(row["qty"] or 0)
        if not order_id or qty <= 0:
            continue
        side = str(row["side"]).lower()
        symbol = str(row["symbol"]).upper()
        book.setdefault(symbol, []).append(
            PendingOrder(
                order_id=order_id,
                client_oid="",
                side="buy" if side == "long" else "sell",
                price=float(row["entry_px"] or 0),
                size=qty,
                position_side=side,
            )
        )
    CACHE.replace_pending_book(book)
    bal = CACHE.get_balance()
    if bal is not None:
        logging.info(
            "SMC available from resting entries: equity=%.2f available=%.2f locked=%.2f",
            bal.account_equity,
            bal.available,
            bal.total_initial_margin,
        )


def run_forever() -> None:
    _seed_available_book()
    logging.info(
        "SMC reverse: swing 50, wick ≤ 0.8 ATR, stop-entry at the swing, "
        "target at the wick, stop at the midpoint, 41-minute hold, 16-bar TTL, "
        "margin 2% of equity, leverage 10"
    )
    while not flatten_donchian_once():
        logging.info("SMC waiting to flatten the Donchian book")
        time.sleep(15)
    last_bar = 0
    while True:
        try:
            manage()
            last_bar = _maybe_bookkeeping(last_bar)
        except Exception as exc:  # noqa: BLE001
            logging.error("SMC manage failed: %s", exc)
        time.sleep(_TICK_SEC)
