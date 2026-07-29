"""Binance USD-M public market WebSocket (klines, miniTicker, markPrice)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from src.config import BINANCE_WS_MARKET_URL, GRANULARITY
from src.exchange.binance_ws.cache import CACHE
from src.exchange.types import Candle

SubscribeFn = Callable[[], set[str]]
BootstrapFn = Callable[[str, str], None]


def _kline_stream(symbol: str, interval: str) -> str:
    return f"{symbol.lower()}@kline_{interval}"


def parse_kline_message(payload: dict[str, Any]) -> tuple[str, str, Candle, bool] | None:
    """Return (symbol, interval, candle, is_closed) or None."""
    data = payload.get("data") if "data" in payload and "e" not in payload else payload
    if not isinstance(data, dict):
        return None
    if data.get("e") != "kline":
        return None
    k = data.get("k") or {}
    try:
        symbol = str(k.get("s") or data.get("s") or "").upper()
        interval = str(k.get("i") or "")
        candle = Candle(
            timestamp=int(k["t"]),
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
        )
        is_closed = bool(k.get("x"))
    except (KeyError, TypeError, ValueError):
        return None
    if not symbol or not interval:
        return None
    return symbol, interval, candle, is_closed


def _parse_mini_ticker_rows(rows: list[Any]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            symbol = str(item.get("s", "")).upper()
            quote = float(item.get("q") or 0)
        except (TypeError, ValueError):
            continue
        if symbol and quote > 0:
            out.append((symbol, quote))
    return out


def parse_mini_ticker_message(payload: dict[str, Any] | list[Any]) -> list[tuple[str, float]]:
    if isinstance(payload, list):
        return _parse_mini_ticker_rows(payload)
    data = payload.get("data") if isinstance(payload.get("data"), list) else payload
    rows = data if isinstance(data, list) else [data]
    return _parse_mini_ticker_rows(rows)


def _parse_mark_price_rows(rows: list[Any]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        if item.get("e") not in (None, "markPriceUpdate"):
            if "s" not in item or "p" not in item:
                continue
        try:
            symbol = str(item.get("s", "")).upper()
            mark = float(item.get("p") or 0)
        except (TypeError, ValueError):
            continue
        if symbol and mark > 0:
            out.append((symbol, mark))
    return out


def parse_mark_price_message(payload: dict[str, Any]) -> list[tuple[str, float]]:
    data = payload.get("data") if "data" in payload else payload
    rows = data if isinstance(data, list) else [data]
    return _parse_mark_price_rows(rows)


def apply_market_payload(payload: dict[str, Any] | list[Any]) -> None:
    if isinstance(payload, list):
        if not payload:
            return
        CACHE.touch_market()
        sample = payload[0] if isinstance(payload[0], dict) else {}
        if isinstance(sample, dict) and "q" in sample and "s" in sample and "p" not in sample:
            rows = _parse_mini_ticker_rows(payload)
            for symbol, quote in rows:
                CACHE.update_quote_volume(symbol, quote)
            if rows:
                logging.debug("Binance WS miniTicker batch: %d symbols", len(rows))
        elif isinstance(sample, dict) and "p" in sample and "s" in sample:
            for symbol, mark in _parse_mark_price_rows(payload):
                CACHE.set_mark(symbol, mark)
            CACHE.refresh_unrealized_from_marks()
        return

    CACHE.touch_market()
    if payload.get("e") == "kline" or (
        isinstance(payload.get("data"), dict) and payload["data"].get("e") == "kline"
    ):
        parsed = parse_kline_message(payload)
        if parsed:
            symbol, interval, candle, is_closed = parsed
            CACHE.upsert_kline_update(symbol, interval, candle, is_closed=is_closed)
        return

    stream = str(payload.get("stream") or "")
    if "miniTicker" in stream or payload.get("e") == "24hrMiniTicker":
        for symbol, quote in parse_mini_ticker_message(payload):
            CACHE.update_quote_volume(symbol, quote)
        return
    if isinstance(payload.get("data"), list):
        # !miniTicker@arr or !markPrice@arr
        sample = payload["data"][0] if payload["data"] else {}
        if isinstance(sample, dict) and "q" in sample and "s" in sample and "p" not in sample:
            for symbol, quote in parse_mini_ticker_message(payload):
                CACHE.update_quote_volume(symbol, quote)
            return
        if isinstance(sample, dict) and "p" in sample and "s" in sample:
            for symbol, mark in parse_mark_price_message(payload):
                CACHE.set_mark(symbol, mark)
            CACHE.refresh_unrealized_from_marks()
            return

    if "markPrice" in stream or payload.get("e") == "markPriceUpdate":
        for symbol, mark in parse_mark_price_message(payload):
            CACHE.set_mark(symbol, mark)
        CACHE.refresh_unrealized_from_marks()


class MarketStream:
    def __init__(
        self,
        *,
        interval: str = GRANULARITY,
        symbols_provider: SubscribeFn | None = None,
        bootstrap_candles: BootstrapFn | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self.interval = interval
        self.symbols_provider = symbols_provider or (lambda: set())
        self.bootstrap_candles = bootstrap_candles
        self._stop = stop_event or asyncio.Event()
        self._ws: Any = None
        self._subscribed: set[str] = set()
        self._msg_id = 1
        self._last_sub_sync_at = 0.0
        self._base_streams = {"!miniTicker@arr", "!markPrice@arr@1s"}
        # /ws + SUBSCRIBE: Binance pushes !miniTicker@arr as a top-level JSON array.
        self._ws_url = BINANCE_WS_MARKET_URL

    def request_stop(self) -> None:
        self._stop.set()

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def desired_streams(self) -> set[str]:
        streams = set(self._base_streams)
        for symbol in self.symbols_provider():
            streams.add(_kline_stream(symbol, self.interval))
        return streams

    async def _send_sub(self, streams: set[str], *, subscribe: bool) -> None:
        if not streams or self._ws is None:
            return
        method = "SUBSCRIBE" if subscribe else "UNSUBSCRIBE"
        await self._ws.send(
            json.dumps({"method": method, "params": sorted(streams), "id": self._next_id()})
        )

    async def _maybe_sync_subscriptions(self, *, force: bool = False) -> None:
        import time

        now = time.monotonic()
        if not force and now - self._last_sub_sync_at < 3.0:
            return
        self._last_sub_sync_at = now
        await self._sync_subscriptions()

    async def _sync_subscriptions(self) -> None:
        desired = self.desired_streams()
        to_add = desired - self._subscribed
        to_drop = self._subscribed - desired
        # Never drop base streams while connected
        to_drop -= self._base_streams
        if to_add:
            await self._send_sub(to_add, subscribe=True)
            for stream in to_add:
                if stream.startswith("!") or "@kline_" not in stream:
                    continue
                symbol = stream.split("@", 1)[0].upper()
                if self.bootstrap_candles is not None:
                    try:
                        self.bootstrap_candles(symbol, self.interval)
                    except Exception as exc:  # noqa: BLE001
                        logging.warning("Binance WS candle bootstrap failed %s: %s", symbol, exc)
            self._subscribed |= to_add
            logging.info("Binance market WS subscribed +%d streams", len(to_add))
        if to_drop:
            await self._send_sub(to_drop, subscribe=False)
            self._subscribed -= to_drop

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                    max_queue=1024,
                ) as ws:
                    self._ws = ws
                    self._subscribed = set()
                    CACHE.touch_market()
                    logging.info("Binance market WS connected")
                    await self._maybe_sync_subscriptions(force=True)
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=15)
                        except asyncio.TimeoutError:
                            await self._maybe_sync_subscriptions(force=True)
                            continue
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="ignore")
                        if raw == "ping":
                            await ws.send("pong")
                            continue
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(payload, dict) and "result" in payload and "id" in payload:
                            continue
                        if isinstance(payload, (dict, list)):
                            apply_market_payload(payload)
                            if isinstance(payload, dict):
                                # Persist closed candles occasionally for REST-ban restarts.
                                k = None
                                if payload.get("e") == "kline":
                                    k = payload.get("k") or {}
                                elif isinstance(payload.get("data"), dict) and payload["data"].get("e") == "kline":
                                    k = payload["data"].get("k") or {}
                                if isinstance(k, dict) and k.get("x"):
                                    try:
                                        from src.exchange.binance_ws.persist import save_candles_snapshot

                                        save_candles_snapshot()
                                    except Exception:  # noqa: BLE001
                                        pass
                        await self._maybe_sync_subscriptions()
            except ConnectionClosed as exc:
                logging.warning("Binance market WS disconnected: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logging.warning("Binance market WS error: %s", exc)
            finally:
                self._ws = None
                CACHE.mark_market_down()
            if not self._stop.is_set():
                await asyncio.sleep(3)
