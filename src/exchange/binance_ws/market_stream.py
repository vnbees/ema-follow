"""Binance USD-M public market WebSocket (klines, miniTicker, markPrice).

All-market streams (miniTicker / markPrice) and per-symbol klines run on
separate connections so all-market traffic cannot mask dead kline feeds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from src.config import (
    BINANCE_WS_KLINE_SILENCE_SEC,
    BINANCE_WS_MARKET_URL,
    GRANULARITY,
)
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

    if payload.get("e") == "kline" or (
        isinstance(payload.get("data"), dict) and payload["data"].get("e") == "kline"
    ):
        parsed = parse_kline_message(payload)
        if parsed:
            symbol, interval, candle, is_closed = parsed
            CACHE.upsert_kline_update(symbol, interval, candle, is_closed=is_closed)
        return

    CACHE.touch_market()
    stream = str(payload.get("stream") or "")
    if "miniTicker" in stream or payload.get("e") == "24hrMiniTicker":
        for symbol, quote in parse_mini_ticker_message(payload):
            CACHE.update_quote_volume(symbol, quote)
        return
    if isinstance(payload.get("data"), list):
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


def _is_control_reply(payload: Any) -> bool:
    return isinstance(payload, dict) and "id" in payload and ("result" in payload or "error" in payload)


def _control_ok(payload: dict[str, Any]) -> bool:
    if payload.get("error"):
        return False
    return True


class AllMarketStream:
    """miniTicker + markPrice on /market/ws (raw array payloads)."""

    def __init__(self, *, stop_event: asyncio.Event | None = None) -> None:
        self._stop = stop_event or asyncio.Event()
        self._ws: Any = None
        self._subscribed: set[str] = set()
        self._msg_id = 1
        self._base_streams = {"!miniTicker@arr", "!markPrice@arr@1s"}
        self._ws_url = BINANCE_WS_MARKET_URL
        self._pending_sub: dict[int, set[str]] = {}

    def request_stop(self) -> None:
        self._stop.set()

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _send_sub(self, streams: set[str], *, subscribe: bool) -> None:
        if not streams or self._ws is None:
            return
        method = "SUBSCRIBE" if subscribe else "UNSUBSCRIBE"
        msg_id = self._next_id()
        if subscribe:
            self._pending_sub[msg_id] = set(streams)
        await self._ws.send(
            json.dumps({"method": method, "params": sorted(streams), "id": msg_id})
        )
        if not subscribe:
            self._subscribed -= streams

    async def _sync(self) -> None:
        desired = set(self._base_streams)
        to_add = desired - self._subscribed
        if to_add:
            await self._send_sub(to_add, subscribe=True)
            logging.info("Binance all-market WS subscribe requested +%d streams", len(to_add))

    def _handle_control(self, payload: dict[str, Any]) -> None:
        msg_id = payload.get("id")
        pending = self._pending_sub.pop(msg_id, None) if msg_id is not None else None
        if pending is None:
            return
        if _control_ok(payload):
            self._subscribed |= pending
            logging.info("Binance all-market WS subscribed %d streams", len(pending))
        else:
            logging.warning("Binance all-market WS subscribe failed: %s", payload.get("error"))

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
                    self._pending_sub.clear()
                    CACHE.touch_market()
                    logging.info("Binance all-market WS connected")
                    await self._sync()
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=20)
                        except asyncio.TimeoutError:
                            await self._sync()
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
                        if _is_control_reply(payload):
                            self._handle_control(payload)
                            continue
                        if isinstance(payload, (dict, list)):
                            apply_market_payload(payload)
            except ConnectionClosed as exc:
                logging.warning("Binance all-market WS disconnected: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logging.warning("Binance all-market WS error: %s", exc)
            finally:
                self._ws = None
                CACHE.mark_market_down()
            if not self._stop.is_set():
                await asyncio.sleep(3)


class KlineStream:
    """Per-symbol klines on a dedicated /market/ws connection (SUBSCRIBE only)."""

    def __init__(
        self,
        *,
        interval: str = GRANULARITY,
        symbols_provider: SubscribeFn | None = None,
        stop_event: asyncio.Event | None = None,
        silence_sec: float | None = None,
    ) -> None:
        self.interval = interval
        self.symbols_provider = symbols_provider or (lambda: set())
        self._stop = stop_event or asyncio.Event()
        self._ws: Any = None
        self._subscribed: set[str] = set()
        self._msg_id = 1
        self._last_sub_sync_at = 0.0
        self._pending_sub: dict[int, set[str]] = {}
        self._subscribed_since: dict[str, float] = {}
        self._silence_sec = (
            BINANCE_WS_KLINE_SILENCE_SEC if silence_sec is None else float(silence_sec)
        )
        # Must be /market/ws — legacy /ws ACKs kline SUBSCRIBE but sends no frames.
        self._ws_url = BINANCE_WS_MARKET_URL

    def request_stop(self) -> None:
        self._stop.set()

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def desired_streams(self) -> set[str]:
        return {_kline_stream(symbol, self.interval) for symbol in self.symbols_provider()}

    async def _send_sub(self, streams: set[str], *, subscribe: bool) -> None:
        if not streams or self._ws is None:
            return
        method = "SUBSCRIBE" if subscribe else "UNSUBSCRIBE"
        msg_id = self._next_id()
        if subscribe:
            self._pending_sub[msg_id] = set(streams)
        await self._ws.send(
            json.dumps({"method": method, "params": sorted(streams), "id": msg_id})
        )
        if not subscribe:
            self._subscribed -= streams
            for stream in streams:
                self._subscribed_since.pop(stream, None)

    def _handle_control(self, payload: dict[str, Any]) -> None:
        msg_id = payload.get("id")
        pending = self._pending_sub.pop(msg_id, None) if msg_id is not None else None
        if pending is None:
            return
        if _control_ok(payload):
            now = time.monotonic()
            self._subscribed |= pending
            for stream in pending:
                self._subscribed_since.setdefault(stream, now)
            logging.info("Binance kline WS subscribed +%d streams", len(pending))
        else:
            logging.warning("Binance kline WS subscribe failed: %s", payload.get("error"))

    async def _maybe_sync(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_sub_sync_at < 3.0:
            return
        self._last_sub_sync_at = now
        await self._sync()

    async def _force_resubscribe_silent(self) -> None:
        now = time.monotonic()
        silent: set[str] = set()
        for stream in list(self._subscribed):
            symbol = stream.split("@", 1)[0].upper()
            age = CACHE.candle_age_sec(symbol)
            if age is not None:
                if age < self._silence_sec:
                    continue
            else:
                # age=never: never received a message — give 2× grace period
                # (low-volume stock tokens may not tick for many minutes)
                since = self._subscribed_since.get(stream, now)
                if (now - since) < self._silence_sec * 2:
                    continue
            silent.add(stream)
            logging.warning(
                "Binance kline WS silent for %s (age=%s) — resubscribe",
                symbol,
                f"{age:.0f}s" if age is not None else "never",
            )
        if not silent:
            return
        await self._send_sub(silent, subscribe=False)
        self._subscribed -= silent
        for stream in silent:
            self._subscribed_since.pop(stream, None)
            with CACHE.lock:
                CACHE.candle_last_msg_at.pop(stream.split("@", 1)[0].upper(), None)

    async def _sync(self) -> None:
        await self._force_resubscribe_silent()
        desired = self.desired_streams()
        pending: set[str] = set()
        for streams in self._pending_sub.values():
            pending |= streams
        to_add = desired - self._subscribed - pending
        to_drop = self._subscribed - desired
        if to_add:
            await self._send_sub(to_add, subscribe=True)
            logging.info("Binance kline WS subscribe requested +%d streams", len(to_add))
        if to_drop:
            await self._send_sub(to_drop, subscribe=False)
            logging.info("Binance kline WS unsubscribed -%d streams", len(to_drop))

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
                    self._subscribed_since.clear()
                    self._pending_sub.clear()
                    CACHE.touch_kline()
                    logging.info("Binance kline WS connected (%s)", self._ws_url)
                    await self._maybe_sync(force=True)
                    data_msgs = 0
                    last_stat = time.monotonic()
                    logged_first = False
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=15)
                        except asyncio.TimeoutError:
                            await self._maybe_sync(force=True)
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
                        if _is_control_reply(payload):
                            self._handle_control(payload)
                            continue
                        data_msgs += 1
                        if not logged_first:
                            logged_first = True
                            kind = (
                                "list"
                                if isinstance(payload, list)
                                else str(
                                    (payload or {}).get("e")
                                    or (payload or {}).get("stream")
                                    or type(payload).__name__
                                )
                            )
                            logging.info("Binance kline WS first data msg type=%s", kind)
                        now = time.monotonic()
                        if now - last_stat >= 60:
                            logging.info(
                                "Binance kline WS data msgs=%d in last 60s (watched=%d)",
                                data_msgs,
                                len(self._subscribed),
                            )
                            data_msgs = 0
                            last_stat = now
                        if isinstance(payload, (dict, list)):
                            apply_market_payload(payload)
                            if isinstance(payload, dict):
                                k = None
                                if payload.get("e") == "kline":
                                    k = payload.get("k") or {}
                                elif (
                                    isinstance(payload.get("data"), dict)
                                    and payload["data"].get("e") == "kline"
                                ):
                                    k = payload["data"].get("k") or {}
                                if isinstance(k, dict) and k.get("x"):
                                    try:
                                        from src.exchange.binance_ws.persist import (
                                            save_candles_snapshot,
                                        )

                                        save_candles_snapshot()
                                    except Exception:  # noqa: BLE001
                                        pass
                        await self._maybe_sync()
            except ConnectionClosed as exc:
                logging.warning("Binance kline WS disconnected: %s", exc)
            except Exception as exc:  # noqa: BLE001
                logging.warning("Binance kline WS error: %s", exc)
            finally:
                self._ws = None
                CACHE.mark_kline_down()
            if not self._stop.is_set():
                await asyncio.sleep(3)


MarketStream = KlineStream
