"""Binance USD-M User Data Stream (listenKey)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any, Callable

import websockets
from websockets.exceptions import ConnectionClosed

from src.config import BINANCE_WS_USER_BASE, MARGIN_COIN
from src.exchange.binance_ws.cache import CACHE
from src.exchange.types import FuturesAccountBalance, PendingOrder, Position

ListenKeyFactory = Callable[[], str]
ListenKeyKeepalive = Callable[[str], None]
ReconcileFn = Callable[[], None]


def parse_account_update(payload: dict[str, Any]) -> tuple[list[Position], list[tuple[str, str]], dict[str, float]]:
    """Return (updated positions, closed (symbol,side), balances {asset: wallet})."""
    account = payload.get("a") or {}
    balances: dict[str, float] = {}
    for row in account.get("B") or []:
        if not isinstance(row, dict):
            continue
        asset = str(row.get("a") or "").upper()
        try:
            wallet = float(row.get("wb") or 0)
        except (TypeError, ValueError):
            continue
        if asset:
            balances[asset] = wallet

    updates: list[Position] = []
    closed: list[tuple[str, str]] = []
    for row in account.get("P") or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("s") or "").upper()
        ps = str(row.get("ps") or "").upper()
        if not symbol or ps not in ("LONG", "SHORT"):
            continue
        side = ps.lower()
        try:
            amt = float(row.get("pa") or 0)
            entry = float(row.get("ep") or 0)
            upnl = float(row.get("up") or 0)
        except (TypeError, ValueError):
            continue
        size = abs(amt)
        if size <= 0:
            closed.append((symbol, side))
            continue
        updates.append(
            Position(
                symbol=symbol,
                side=side,
                size=size,
                avg_price=entry,
                unrealized_pnl=upnl,
            )
        )
    return updates, closed, balances


def parse_order_trade_update(payload: dict[str, Any]) -> tuple[dict | None, PendingOrder | None, bool]:
    """Return (order_detail, pending_limit_or_none, remove_pending)."""
    o = payload.get("o") or {}
    if not isinstance(o, dict):
        return None, None, False
    order_id = str(o.get("i") or "")
    status = str(o.get("X") or "").lower()
    avg = o.get("ap")
    detail = {
        "orderId": order_id,
        "status": status,
        "state": status,
        "avgPrice": avg,
        "priceAvg": avg,
        "symbol": str(o.get("s") or "").upper(),
    }
    order_type = str(o.get("o") or "").upper()
    symbol = str(o.get("s") or "").upper()
    is_limit = order_type == "LIMIT"
    remove = status in {"filled", "canceled", "cancelled", "expired", "rejected"}
    pending = None
    if is_limit and not remove and symbol:
        try:
            pending = PendingOrder(
                order_id=order_id,
                client_oid=str(o.get("c") or ""),
                side=str(o.get("S") or "").lower(),
                price=float(o.get("p") or 0),
                size=float(o.get("q") or 0),
            )
        except (TypeError, ValueError):
            pending = None
    return detail, pending, remove and is_limit


def apply_user_payload(payload: dict[str, Any]) -> None:
    event = payload.get("e")
    if event == "listenKeyExpired":
        logging.warning("Binance listenKey expired — reconnecting user stream")
        from src.exchange.binance_ws import manager as ws_manager
        from src.exchange.binance_ws.persist import clear_listen_key

        clear_listen_key()
        ws_manager._listen_key_validated = False
        raise RuntimeError("listenKeyExpired")

    CACHE.touch_user()
    if event == "ACCOUNT_UPDATE":
        updates, closed, balances = parse_account_update(payload)
        CACHE.apply_position_updates(updates, closed)
        if MARGIN_COIN in balances:
            prev = CACHE.get_balance()
            wallet = balances[MARGIN_COIN]
            if prev is not None:
                CACHE.set_balance(
                    FuturesAccountBalance(
                        margin_coin=prev.margin_coin,
                        available=min(prev.available, wallet) if prev.available > 0 else wallet,
                        account_equity=wallet if wallet > 0 else prev.account_equity,
                        usdt_equity=wallet if wallet > 0 else prev.usdt_equity,
                        total_maint_margin=prev.total_maint_margin,
                        total_initial_margin=prev.total_initial_margin,
                    )
                )
        try:
            from src.exchange.binance_ws.persist import save_account_snapshot

            save_account_snapshot()
        except Exception:  # noqa: BLE001
            pass
        return

    if event == "ORDER_TRADE_UPDATE":
        detail, pending, remove_pending = parse_order_trade_update(payload)
        if detail and detail.get("orderId"):
            CACHE.upsert_order_detail(str(detail["orderId"]), detail)
        symbol = str((detail or {}).get("symbol") or "")
        if symbol and pending is not None:
            existing = CACHE.get_pending(symbol) or []
            by_id = {o.order_id: o for o in existing}
            by_id[pending.order_id] = pending
            CACHE.set_pending(symbol, list(by_id.values()))
        elif symbol and remove_pending and detail:
            existing = CACHE.get_pending(symbol) or []
            CACHE.set_pending(
                symbol,
                [o for o in existing if o.order_id != str(detail.get("orderId"))],
            )


class UserStream:
    def __init__(
        self,
        *,
        create_listen_key: ListenKeyFactory,
        keepalive_listen_key: ListenKeyKeepalive,
        on_reconnect: ReconcileFn | None = None,
        stop_event: asyncio.Event | None = None,
        keepalive_sec: float = 30 * 60,
    ) -> None:
        self.create_listen_key = create_listen_key
        self.keepalive_listen_key = keepalive_listen_key
        self.on_reconnect = on_reconnect
        self._stop = stop_event or asyncio.Event()
        self.keepalive_sec = keepalive_sec

    def request_stop(self) -> None:
        self._stop.set()

    async def _keepalive_loop(self, listen_key: str) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.keepalive_sec)
            if self._stop.is_set():
                return
            try:
                await asyncio.to_thread(self.keepalive_listen_key, listen_key)
                logging.debug("Binance listenKey keepalive OK")
            except Exception as exc:  # noqa: BLE001
                logging.warning("Binance listenKey keepalive failed: %s", exc)
                return

    async def run(self) -> None:
        while not self._stop.is_set():
            session_open = False
            try:
                from src.exchange import binance as binance_mod

                wait = binance_mod.rate_limit_remaining_sec()
                from src.exchange.binance_ws.persist import clear_listen_key, load_listen_key

                # Persisted listenKey can reconnect over WS without REST create.
                has_key = bool(load_listen_key())
                if wait > 0 and not has_key:
                    logging.warning(
                        "Binance user WS waiting %.0fs for rate-limit cooldown before listenKey",
                        wait,
                    )
                    end = asyncio.get_running_loop().time() + wait + 2.0
                    while not self._stop.is_set() and asyncio.get_running_loop().time() < end:
                        await asyncio.sleep(min(30.0, max(0.1, end - asyncio.get_running_loop().time())))
                    continue

                listen_key = await asyncio.to_thread(self.create_listen_key)
                base = (BINANCE_WS_USER_BASE or "wss://fstream.binance.com/private").rstrip(
                    "/"
                )
                url = f"{base}/ws/{listen_key}"
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=60,
                    close_timeout=5,
                    max_queue=1024,
                ) as ws:
                    session_open = True
                    CACHE.touch_user()
                    logging.info("Binance user data WS connected")
                    if self.on_reconnect is not None:
                        try:
                            await asyncio.to_thread(self.on_reconnect)
                        except Exception as exc:  # noqa: BLE001
                            logging.warning("Binance UDS reconnect reconcile failed: %s", exc)
                    ka_task = asyncio.create_task(self._keepalive_loop(listen_key))
                    try:
                        while not self._stop.is_set():
                            try:
                                # UDS is often silent for minutes — TimeoutError must NOT reconnect
                                # (str(TimeoutError()) is "" which looked like empty "user WS error").
                                raw = await asyncio.wait_for(ws.recv(), timeout=120)
                            except asyncio.TimeoutError:
                                CACHE.touch_user()
                                continue
                            except ConnectionClosed as exc:
                                logging.warning(
                                    "Binance user WS disconnected: type=%s code=%s reason=%r",
                                    type(exc).__name__,
                                    getattr(exc, "code", None),
                                    getattr(exc, "reason", str(exc)),
                                )
                                break

                            if isinstance(raw, bytes):
                                raw = raw.decode("utf-8", errors="ignore")
                            if not raw:
                                continue
                            try:
                                payload = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(payload, dict):
                                continue
                            apply_user_payload(payload)
                    finally:
                        ka_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await ka_task
            except RuntimeError as exc:
                if "listenKeyExpired" in str(exc):
                    logging.warning("Recreating Binance listenKey")
                else:
                    logging.warning(
                        "Binance user WS error: type=%s msg=%r",
                        type(exc).__name__,
                        str(exc),
                    )
                await asyncio.sleep(3)
            except ConnectionClosed as exc:
                logging.warning(
                    "Binance user WS disconnected: type=%s code=%s reason=%r",
                    type(exc).__name__,
                    getattr(exc, "code", None),
                    getattr(exc, "reason", str(exc)),
                )
                await asyncio.sleep(3)
            except Exception as exc:  # noqa: BLE001
                from src.exchange.binance import RateLimitError

                if isinstance(exc, RateLimitError) or "Rate-limit cooldown" in str(exc):
                    logging.warning("Binance user WS: %s", exc)
                    continue
                logging.warning(
                    "Binance user WS error: type=%s msg=%r",
                    type(exc).__name__,
                    str(exc),
                )
                if not self._stop.is_set():
                    await asyncio.sleep(3)
            finally:
                if session_open:
                    CACHE.mark_user_down()

