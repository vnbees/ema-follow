import asyncio
import contextlib
import json
import logging
import threading
import time
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from src.bitget_client import Candle
from src.config import BITGET_WS_PUBLIC, OFI_REALTIME_REFRESH_SEC, OFI_SYMBOL, PRODUCT_TYPE_API
from src.orderflow.aggregator import on_trade
from src.orderflow.ofi_state import on_candle_snapshot, on_candle_update, refresh_live_from_trades, reset_ofi_session


_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_stop_event = threading.Event()
_resubscribe_event: asyncio.Event | None = None


def _subscribe_args() -> list[dict[str, str]]:
    return [
        {
            "instType": PRODUCT_TYPE_API,
            "channel": "trade",
            "instId": OFI_SYMBOL,
        },
        {
            "instType": PRODUCT_TYPE_API,
            "channel": "candle1m",
            "instId": OFI_SYMBOL,
        },
    ]


def _parse_candle_row(row: list | dict) -> Candle | None:
    try:
        if isinstance(row, list):
            ts = int(row[0])
            o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
            vol = float(row[5]) if len(row) > 5 else 0.0
        else:
            ts = int(row.get("ts") or row.get("timestamp") or 0)
            o = float(row.get("open") or row.get("o") or 0)
            h = float(row.get("high") or row.get("h") or 0)
            l = float(row.get("low") or row.get("l") or 0)
            c = float(row.get("close") or row.get("c") or 0)
            vol = float(row.get("volume") or row.get("v") or 0)
        if ts <= 0:
            return None
        return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=vol)
    except (TypeError, ValueError):
        return None


def _parse_trade_message(payload: dict[str, Any]) -> None:
    if payload.get("event") in {"subscribe", "unsubscribe"}:
        return
    if payload.get("action") not in {"snapshot", "update"}:
        return

    channel = payload.get("arg", {}).get("channel")
    symbol = str(payload.get("arg", {}).get("instId", "")).upper()
    if symbol != OFI_SYMBOL:
        return

    if channel == "trade":
        for item in payload.get("data") or []:
            try:
                side = str(item.get("side", ""))
                size = float(item.get("size", 0))
                ts_ms = int(item.get("ts", 0))
            except (TypeError, ValueError):
                continue
            if size <= 0 or ts_ms <= 0:
                continue
            on_trade(symbol, side, size, ts_ms)
        refresh_live_from_trades()
        return

    if channel == "candle1m":
        rows = payload.get("data") or []
        action = payload.get("action")
        if action == "snapshot":
            candles = [c for r in rows if (c := _parse_candle_row(r)) is not None]
            if candles:
                latest = max(candles, key=lambda c: c.timestamp)
                on_candle_snapshot(latest)
            return
        for row in rows:
            candle = _parse_candle_row(row)
            if candle is not None:
                on_candle_update(candle)


async def _ping_loop(ws: Any) -> None:
    while not _stop_event.is_set():
        await asyncio.sleep(25)
        try:
            await ws.send("ping")
        except ConnectionClosed:
            break


async def _run_ws_session() -> None:
    global _resubscribe_event
    _resubscribe_event = asyncio.Event()

    while not _stop_event.is_set():
        try:
            async with websockets.connect(
                BITGET_WS_PUBLIC,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=5,
            ) as ws:
                logging.info("Order flow WS connected (%s 1m)", OFI_SYMBOL)
                reset_ofi_session()
                await ws.send(json.dumps({"op": "subscribe", "args": _subscribe_args()}))
                ping_task = asyncio.create_task(_ping_loop(ws))

                try:
                    while not _stop_event.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        if raw == "pong":
                            continue
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        _parse_trade_message(payload)
                finally:
                    ping_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await ping_task
        except ConnectionClosed as exc:
            logging.warning("Order flow WS disconnected: %s", exc)
        except Exception as exc:
            logging.warning("Order flow WS error: %s", exc)

        if not _stop_event.is_set():
            await asyncio.sleep(3)


def _realtime_refresh_loop() -> None:
    while not _stop_event.is_set():
        try:
            refresh_live_from_trades()
        except Exception as exc:
            logging.warning("OFI realtime refresh failed: %s", exc)
        time.sleep(OFI_REALTIME_REFRESH_SEC)


async def _main_async() -> None:
    await _run_ws_session()


def start_orderflow_ws_loop(_initial_symbols: list[str] | None = None) -> None:
    global _loop, _loop_thread

    reset_ofi_session()
    _loop = asyncio.new_event_loop()

    def _runner() -> None:
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(_main_async())

    _loop_thread = threading.Thread(target=_runner, daemon=True, name="orderflow-ws")
    _loop_thread.start()

    refresh_thread = threading.Thread(target=_realtime_refresh_loop, daemon=True, name="ofi-refresh")
    refresh_thread.start()


def update_watchlist(_symbols: list[str]) -> None:
    """No-op: OFI is fixed to OFI_SYMBOL only."""


def stop_orderflow_ws() -> None:
    _stop_event.set()
    if _loop is not None and _loop.is_running():
        _loop.call_soon_threadsafe(_loop.stop)
