"""Close every open futures position and cancel resting orders.

Run once, on the VPS, with the bot service already stopped:

    CONFIRM=yes PYTHONPATH=/home/bot/app .venv/bin/python scripts/smc_close_all.py

Take-profit limits are cancelled first so the size can be closed. Stops stay
up until the market close succeeds. The script does not start the bot.
"""

from __future__ import annotations

import os
import sys
import time

from src.bot_state import set_trading_enabled
from src.donchian.config import FIXED_SCAN_SYMBOLS
from src.exchange import ExchangeClientError, fetch_contract_spec
from src.exchange.binance import (
    cancel_order,
    close_position_side,
    fetch_all_open_positions_rest,
    fetch_open_algo_orders,
    fetch_side_mark_price,
)
from src.exchange.fills import resolve_order_commission, resolve_order_fill
from src.exchange.sizing import format_size
from src.smc import store
from src.smc.broker import TAKER_FEE_RATE, _cancel_order, _finish_open


def _positions() -> list:
    rows = fetch_all_open_positions_rest()
    return [row for row in rows if row.size and row.size > 0 and row.side]


def _cancel_tp(row) -> None:
    _cancel_order(str(row["symbol"]), str(row["tp_order_id"] or ""))


def _close_db_open(row) -> bool:
    symbol = str(row["symbol"])
    side = str(row["side"])
    _cancel_tp(row)
    spec = fetch_contract_spec(symbol)
    qty = float(row["qty"] or 0)
    size = format_size(qty, spec)
    if qty <= 0 or float(size) <= 0:
        print(f"skip db {symbol} {side} qty={qty}", flush=True)
        return False
    try:
        result = close_position_side(symbol, side, size)
    except ExchangeClientError as exc:
        print(f"CLOSE FAILED {symbol} {side} {size}: {exc}", flush=True)
        return False
    entry = float(row["fill_px"] or row["entry_px"] or 0)
    fallback = fetch_side_mark_price(symbol) or entry
    fill = resolve_order_fill(symbol, result, fallback_price=fallback)
    oid = str(result.get("orderId") or "")
    fee = resolve_order_commission(oid) if oid else qty * fill * TAKER_FEE_RATE
    print(f"closed {symbol} {side} qty={size} px={fill}", flush=True)
    _finish_open(row, close_px=fill, fee_close=fee, reason="flat")
    return True


def _cancel_pending(row) -> None:
    symbol = str(row["symbol"])
    oid = str(row["entry_order_id"] or "")
    if oid:
        try:
            cancel_order(symbol, oid)
        except ExchangeClientError as exc:
            print(f"cancel pending {symbol} {oid}: {exc}", flush=True)
        _cancel_order(symbol, oid)
    store.mark_cancelled(int(row["id"]), "flat")
    print(f"cancelled pending {symbol} {row['side']} id={row['id']}", flush=True)


def _close_exchange_left() -> int:
    failed = 0
    for pos in _positions():
        spec = fetch_contract_spec(pos.symbol)
        size = format_size(pos.size, spec)
        try:
            result = close_position_side(pos.symbol, pos.side, size)
        except ExchangeClientError as exc:
            print(f"LEFT FAILED {pos.symbol} {pos.side} {size}: {exc}", flush=True)
            failed += 1
            continue
        fill = resolve_order_fill(
            pos.symbol,
            result,
            fallback_price=fetch_side_mark_price(pos.symbol) or pos.avg_price,
        )
        print(f"closed leftover {pos.symbol} {pos.side} qty={size} px={fill}", flush=True)
    return failed


def _cancel_resting() -> None:
    from src.exchange.binance import _private_get

    rows = _private_get("/fapi/v1/openOrders", {}, max_retries=1, priority="critical")
    if isinstance(rows, list):
        for item in rows:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            oid = str(item.get("orderId") or "")
            if not symbol or not oid:
                continue
            try:
                cancel_order(symbol, oid)
                print(f"cancelled order {symbol} {oid} {item.get('type')}", flush=True)
            except ExchangeClientError as exc:
                print(f"cancel order {symbol} {oid}: {exc}", flush=True)
    for symbol in FIXED_SCAN_SYMBOLS:
        for item in fetch_open_algo_orders(symbol):
            oid = str(item.get("algoId") or item.get("orderId") or "")
            if not oid:
                continue
            try:
                cancel_order(symbol, oid)
                print(f"cancelled algo {symbol} {oid}", flush=True)
            except ExchangeClientError as exc:
                print(f"cancel algo {symbol} {oid}: {exc}", flush=True)


def main() -> int:
    if os.environ.get("CONFIRM") != "yes":
        print("Refusing: set CONFIRM=yes", file=sys.stderr)
        return 2
    store.ensure_schema()
    set_trading_enabled(False)
    active = list(store.active_rows())
    print(f"db active {len(active)}", flush=True)
    for row in active:
        if str(row["status"]) == "pending":
            _cancel_pending(row)
        elif str(row["status"]) == "open":
            _close_db_open(row)
    for row in store.hedges_still_open():
        from src.smc.broker import _close_hedge

        _close_hedge(row)
        print(f"hedge {row['symbol']} id={row['id']}", flush=True)
    time.sleep(1)
    failed = _close_exchange_left()
    _cancel_resting()
    time.sleep(1)
    left = _positions()
    print(f"positions left {len(left)}", flush=True)
    for pos in left:
        print(f"STILL OPEN {pos.symbol} {pos.side} {pos.size}", flush=True)
    return 1 if failed or left else 0


if __name__ == "__main__":
    raise SystemExit(main())
