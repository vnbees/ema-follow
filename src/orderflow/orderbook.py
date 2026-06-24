import logging
import time
from dataclasses import dataclass, field
from threading import Lock

from src.bitget_client import BitgetClientError, fetch_contract_spec
from src.config import OFI_BOOK_TICK_RANGE, OFI_SYMBOL
from src.orderflow.metrics import classify_book_bias, compute_book_pressure_near_mid

_STALE_SEC = 5.0


@dataclass
class BookPressureSnapshot:
    bid_volume: float = 0.0
    ask_volume: float = 0.0
    book_pressure_pct: float = 100.0
    book_bias: str = "neutral"
    best_bid: float = 0.0
    best_ask: float = 0.0
    mid_price: float = 0.0
    stale: bool = True
    last_updated_ms: int = 0


@dataclass
class _BookState:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    last_seq: int | None = None
    last_updated_ms: int = 0
    tick_size: float = 0.0
    tick_loaded: bool = False


_lock = Lock()
_book = _BookState()


def _ensure_tick_size() -> float:
    global _book
    if _book.tick_loaded and _book.tick_size > 0:
        return _book.tick_size
    try:
        spec = fetch_contract_spec(OFI_SYMBOL)
        _book.tick_size = 10 ** (-spec.price_place)
        _book.tick_loaded = True
    except BitgetClientError as exc:
        logging.warning("Order book tick size fetch failed: %s", exc)
        _book.tick_size = 0.01
    return _book.tick_size


def _apply_levels(book: dict[float, float], levels: list) -> None:
    for row in levels:
        try:
            price = float(row[0])
            size = float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if size <= 0:
            book.pop(price, None)
        else:
            book[price] = size


def reset_orderbook() -> None:
    global _book
    with _lock:
        _book = _BookState()


def on_book_message(payload: dict) -> None:
    channel = payload.get("arg", {}).get("channel", "")
    if channel != "books":
        return
    if payload.get("action") not in {"snapshot", "update"}:
        return

    rows = payload.get("data") or []
    if not rows:
        return

    row = rows[0]
    action = payload.get("action")
    ts_ms = int(row.get("ts") or time.time() * 1000)
    seq = row.get("seq")
    bids = row.get("bids") or []
    asks = row.get("asks") or []

    with _lock:
        if action == "snapshot":
            _book.bids.clear()
            _book.asks.clear()
        _apply_levels(_book.bids, bids)
        _apply_levels(_book.asks, asks)
        if seq is not None:
            _book.last_seq = int(seq)
        _book.last_updated_ms = ts_ms


def get_book_pressure() -> BookPressureSnapshot:
    tick = _ensure_tick_size()
    now_ms = int(time.time() * 1000)

    with _lock:
        bids = sorted(_book.bids.items(), key=lambda x: x[0], reverse=True)
        asks = sorted(_book.asks.items(), key=lambda x: x[0])
        last_ms = _book.last_updated_ms
        stale = (now_ms - last_ms) > int(_STALE_SEC * 1000) if last_ms > 0 else True

    if not bids or not asks:
        return BookPressureSnapshot(stale=True, last_updated_ms=last_ms)

    bid_vol, ask_vol, pressure = compute_book_pressure_near_mid(
        bids,
        asks,
        tick_size=tick,
        tick_range=OFI_BOOK_TICK_RANGE,
    )
    best_bid = bids[0][0]
    best_ask = asks[0][0]
    return BookPressureSnapshot(
        bid_volume=bid_vol,
        ask_volume=ask_vol,
        book_pressure_pct=pressure,
        book_bias=classify_book_bias(pressure),
        best_bid=best_bid,
        best_ask=best_ask,
        mid_price=(best_bid + best_ask) / 2.0,
        stale=stale,
        last_updated_ms=last_ms,
    )
