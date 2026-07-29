"""Persist Binance WS state on volume so REST bans don't wipe candle/listenKey/positions."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from src.config import DATABASE_PATH, GRANULARITY
from src.exchange.binance_ws.cache import CACHE
from src.exchange.types import Candle, FuturesAccountBalance, Position

_DATA_DIR = Path(DATABASE_PATH).expanduser().resolve().parent
_LISTEN_KEY_FILE = _DATA_DIR / "binance_listen_key"
_CANDLES_FILE = _DATA_DIR / "binance_ws_candles.json"
_ACCOUNT_FILE = _DATA_DIR / "binance_ws_account.json"
_io_lock = threading.Lock()


def load_listen_key() -> str | None:
    try:
        if not _LISTEN_KEY_FILE.is_file():
            return None
        key = _LISTEN_KEY_FILE.read_text(encoding="utf-8").strip()
        return key or None
    except OSError:
        return None


def save_listen_key(listen_key: str) -> None:
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _LISTEN_KEY_FILE.write_text(listen_key.strip(), encoding="utf-8")
    except OSError as exc:
        logging.debug("listenKey persist failed: %s", exc)


def clear_listen_key() -> None:
    try:
        if _LISTEN_KEY_FILE.is_file():
            _LISTEN_KEY_FILE.unlink()
    except OSError:
        pass


def save_candles_snapshot() -> None:
    with CACHE.lock:
        payload = {
            "interval": dict(CACHE.candle_interval),
            "candles": {
                sym: [
                    {
                        "t": c.timestamp,
                        "o": c.open,
                        "h": c.high,
                        "l": c.low,
                        "c": c.close,
                        "v": c.volume,
                    }
                    for c in rows
                ]
                for sym, rows in CACHE.candles.items()
            },
        }
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _io_lock:
            _CANDLES_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        logging.debug("candle snapshot persist failed: %s", exc)


def load_candles_snapshot() -> int:
    """Load candles into CACHE. Returns number of symbols restored."""
    try:
        if not _CANDLES_FILE.is_file():
            return 0
        raw = json.loads(_CANDLES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return 0
    intervals = raw.get("interval") or {}
    candles = raw.get("candles") or {}
    count = 0
    for sym, rows in candles.items():
        interval = str(intervals.get(sym) or GRANULARITY)
        parsed: list[Candle] = []
        for row in rows:
            try:
                parsed.append(
                    Candle(
                        timestamp=int(row["t"]),
                        open=float(row["o"]),
                        high=float(row["h"]),
                        low=float(row["l"]),
                        close=float(row["c"]),
                        volume=float(row["v"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        if parsed:
            CACHE.set_candles(str(sym).upper(), interval, parsed)
            count += 1
    return count


def save_account_snapshot() -> None:
    with CACHE.lock:
        positions = [
            {
                "symbol": p.symbol,
                "side": p.side,
                "size": p.size,
                "avg_price": p.avg_price,
                "unrealized_pnl": p.unrealized_pnl,
            }
            for p in CACHE.all_positions
        ]
        bal = CACHE.balance
        balance = None
        if bal is not None:
            balance = {
                "margin_coin": bal.margin_coin,
                "available": bal.available,
                "account_equity": bal.account_equity,
                "usdt_equity": bal.usdt_equity,
                "total_maint_margin": bal.total_maint_margin,
                "total_initial_margin": bal.total_initial_margin,
            }
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _io_lock:
            _ACCOUNT_FILE.write_text(
                json.dumps({"positions": positions, "balance": balance}),
                encoding="utf-8",
            )
    except OSError as exc:
        logging.debug("account snapshot persist failed: %s", exc)


def load_account_snapshot() -> bool:
    try:
        if not _ACCOUNT_FILE.is_file():
            return False
        raw = json.loads(_ACCOUNT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    positions: list[Position] = []
    by_symbol: dict[str, dict[str, Position]] = {}
    for row in raw.get("positions") or []:
        try:
            symbol = str(row["symbol"]).upper()
            side = row.get("side")
            pos = Position(
                symbol=symbol,
                side=side,
                size=float(row.get("size") or 0),
                avg_price=float(row.get("avg_price") or 0),
                unrealized_pnl=float(row.get("unrealized_pnl") or 0),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if pos.size <= 0 or side not in ("long", "short"):
            continue
        positions.append(pos)
        bucket = by_symbol.setdefault(
            symbol,
            {
                "long": Position(symbol=symbol, side=None, size=0.0, avg_price=0.0),
                "short": Position(symbol=symbol, side=None, size=0.0, avg_price=0.0),
            },
        )
        bucket[side] = pos
    bal = raw.get("balance")
    bal_ok = False
    if isinstance(bal, dict):
        try:
            CACHE.set_balance(
                FuturesAccountBalance(
                    margin_coin=str(bal.get("margin_coin") or "USDT"),
                    available=float(bal.get("available") or 0),
                    account_equity=float(bal.get("account_equity") or 0),
                    usdt_equity=float(bal.get("usdt_equity") or 0),
                    total_maint_margin=float(bal.get("total_maint_margin") or 0),
                    total_initial_margin=float(bal.get("total_initial_margin") or 0),
                )
            )
            bal_ok = True
        except (TypeError, ValueError):
            pass
    # Always stamp positions (even empty) so WS getters don't fall back to REST on boot.
    if positions or bal_ok or "positions" in raw:
        CACHE.set_positions(positions, by_symbol)
    loaded = bool(positions or bal_ok)
    if loaded:
        # Treat disk restore as a successful reconcile so boot skips force REST.
        CACHE.mark_reconciled()
    return loaded
