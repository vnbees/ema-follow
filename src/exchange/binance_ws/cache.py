"""Thread-safe in-memory cache fed by Binance market + user data streams."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from src.exchange.types import Candle, FuturesAccountBalance, PendingOrder, Position


def _now() -> float:
    return time.monotonic()


@dataclass
class BinanceWsCache:
    lock: threading.RLock = field(default_factory=threading.RLock)

    # Market
    candles: dict[str, list[Candle]] = field(default_factory=dict)
    candle_interval: dict[str, str] = field(default_factory=dict)
    candle_last_msg_at: dict[str, float] = field(default_factory=dict)
    quote_volumes: dict[str, float] = field(default_factory=dict)
    marks: dict[str, float] = field(default_factory=dict)
    market_last_msg_at: float = 0.0
    kline_connected: bool = False
    kline_disconnect_since: float | None = None
    mini_ticker_seeded: bool = False

    # Account / positions / orders
    positions_by_symbol: dict[str, dict[str, Position]] = field(default_factory=dict)
    all_positions: list[Position] = field(default_factory=list)
    balance: FuturesAccountBalance | None = None
    pending_by_symbol: dict[str, list[PendingOrder]] = field(default_factory=dict)
    order_details: dict[str, dict] = field(default_factory=dict)
    account_updated_at: float = 0.0
    positions_updated_at: float = 0.0
    user_last_msg_at: float = 0.0

    # Health
    market_connected: bool = False
    user_connected: bool = False
    market_disconnect_since: float | None = None
    user_disconnect_since: float | None = None
    last_reconcile_at: float = 0.0
    rest_calls: dict[str, int] = field(default_factory=dict)

    def bump_rest(self, name: str) -> None:
        with self.lock:
            self.rest_calls[name] = self.rest_calls.get(name, 0) + 1

    def reset_rest_counts(self) -> dict[str, int]:
        with self.lock:
            out = dict(self.rest_calls)
            self.rest_calls.clear()
            return out

    def touch_market(self) -> None:
        with self.lock:
            self.market_last_msg_at = _now()
            self.market_connected = True
            self.market_disconnect_since = None

    def touch_user(self) -> None:
        with self.lock:
            self.user_last_msg_at = _now()
            self.user_connected = True
            self.user_disconnect_since = None

    def mark_market_down(self) -> None:
        with self.lock:
            self.market_connected = False
            if self.market_disconnect_since is None:
                self.market_disconnect_since = _now()

    def touch_kline(self, symbol: str | None = None) -> None:
        with self.lock:
            now = _now()
            self.kline_connected = True
            self.kline_disconnect_since = None
            if symbol:
                self.candle_last_msg_at[symbol.upper()] = now

    def mark_kline_down(self) -> None:
        with self.lock:
            self.kline_connected = False
            if self.kline_disconnect_since is None:
                self.kline_disconnect_since = _now()

    def candle_age_sec(self, symbol: str) -> float | None:
        with self.lock:
            ts = self.candle_last_msg_at.get(symbol.upper())
            if not ts:
                return None
            return _now() - ts

    def mark_user_down(self) -> None:
        with self.lock:
            self.user_connected = False
            if self.user_disconnect_since is None:
                self.user_disconnect_since = _now()

    def market_age_sec(self) -> float | None:
        with self.lock:
            if self.market_last_msg_at <= 0:
                return None
            return _now() - self.market_last_msg_at

    def user_age_sec(self) -> float | None:
        with self.lock:
            if self.user_last_msg_at <= 0 and self.positions_updated_at <= 0:
                return None
            ref = max(self.user_last_msg_at, self.positions_updated_at)
            return _now() - ref

    def set_candles(self, symbol: str, interval: str, candles: list[Candle]) -> None:
        symbol = symbol.upper()
        with self.lock:
            self.candles[symbol] = list(candles)
            self.candle_interval[symbol] = interval

    def get_candles(self, symbol: str, interval: str, limit: int) -> list[Candle] | None:
        symbol = symbol.upper()
        with self.lock:
            if self.candle_interval.get(symbol) != interval:
                return None
            rows = self.candles.get(symbol)
            if not rows:
                return None
            return list(rows[-limit:])

    def _upsert_candle_row(self, rows: list[Candle], candle: Candle) -> None:
        if rows and rows[-1].timestamp == candle.timestamp:
            rows[-1] = candle
        elif rows and rows[-1].timestamp > candle.timestamp:
            for i, existing in enumerate(rows):
                if existing.timestamp == candle.timestamp:
                    rows[i] = candle
                    return
                if existing.timestamp > candle.timestamp:
                    rows.insert(i, candle)
                    return
            rows.append(candle)
        else:
            rows.append(candle)
        if len(rows) > 500:
            del rows[:-400]

    def upsert_closed_candle(self, symbol: str, interval: str, candle: Candle) -> None:
        self.upsert_kline_update(symbol, interval, candle, is_closed=True)

    def upsert_kline_update(
        self,
        symbol: str,
        interval: str,
        candle: Candle,
        *,
        is_closed: bool,
    ) -> None:
        _ = is_closed
        symbol = symbol.upper()
        with self.lock:
            stored = self.candle_interval.get(symbol)
            if stored not in (None, interval):
                from src.config import GRANULARITY

                if interval != GRANULARITY:
                    return
                # Take over: leftover 5m cache would otherwise block 15m WS updates.
                self.candles[symbol] = []
            self.candle_interval[symbol] = interval
            rows = self.candles.setdefault(symbol, [])
            self._upsert_candle_row(rows, candle)
            now = _now()
            self.candle_last_msg_at[symbol] = now
            self.kline_connected = True
            self.kline_disconnect_since = None

    def set_quote_volumes(self, volumes: dict[str, float], *, seeded: bool = False) -> None:
        with self.lock:
            self.quote_volumes.update({k.upper(): float(v) for k, v in volumes.items()})
            if seeded:
                self.mini_ticker_seeded = True

    def update_quote_volume(self, symbol: str, quote_volume: float) -> None:
        with self.lock:
            self.quote_volumes[symbol.upper()] = float(quote_volume)
            # After enough live miniTicker updates, rank can work without REST seed.
            if not self.mini_ticker_seeded and len(self.quote_volumes) >= 80:
                self.mini_ticker_seeded = True

    def ranked_volumes(self) -> list[tuple[str, float]]:
        with self.lock:
            ranked = [(s, v) for s, v in self.quote_volumes.items() if v > 0]
            ranked.sort(key=lambda row: row[1], reverse=True)
            return ranked

    def set_mark(self, symbol: str, mark: float) -> None:
        if mark <= 0:
            return
        with self.lock:
            self.marks[symbol.upper()] = float(mark)

    def get_mark(self, symbol: str) -> float | None:
        with self.lock:
            mark = self.marks.get(symbol.upper())
            return float(mark) if mark and mark > 0 else None

    def set_balance(self, balance: FuturesAccountBalance) -> None:
        with self.lock:
            self.balance = balance
            self.account_updated_at = _now()

    def get_balance(self) -> FuturesAccountBalance | None:
        with self.lock:
            return self.balance

    def set_positions(
        self,
        all_positions: list[Position],
        by_symbol: dict[str, dict[str, Position]] | None = None,
    ) -> None:
        with self.lock:
            self.all_positions = list(all_positions)
            if by_symbol is not None:
                self.positions_by_symbol = {
                    sym.upper(): {
                        "long": sides["long"],
                        "short": sides["short"],
                    }
                    for sym, sides in by_symbol.items()
                }
            else:
                rebuilt: dict[str, dict[str, Position]] = {}
                for pos in all_positions:
                    bucket = rebuilt.setdefault(
                        pos.symbol.upper(),
                        {
                            "long": Position(symbol=pos.symbol, side=None, size=0.0, avg_price=0.0),
                            "short": Position(symbol=pos.symbol, side=None, size=0.0, avg_price=0.0),
                        },
                    )
                    if pos.side in bucket:
                        bucket[pos.side] = pos
                self.positions_by_symbol = rebuilt
            self.positions_updated_at = _now()

    def apply_position_updates(self, updates: list[Position], closed_keys: list[tuple[str, str]]) -> None:
        """Merge UDS position deltas (changed rows only). closed_keys: (symbol, side).

        Empty ACCOUNT_UPDATE payloads must NOT bump positions_updated_at — that made
        flush_pending_reconcile think the cache was fresh and skip real updates, then
        later fall through to heavy REST.
        """
        if not updates and not closed_keys:
            return
        with self.lock:
            by_symbol = {
                sym: {
                    "long": sides["long"],
                    "short": sides["short"],
                }
                for sym, sides in self.positions_by_symbol.items()
            }
            for symbol, side in closed_keys:
                symbol = symbol.upper()
                side = side.lower()
                bucket = by_symbol.setdefault(
                    symbol,
                    {
                        "long": Position(symbol=symbol, side=None, size=0.0, avg_price=0.0),
                        "short": Position(symbol=symbol, side=None, size=0.0, avg_price=0.0),
                    },
                )
                if side in bucket:
                    bucket[side] = Position(symbol=symbol, side=None, size=0.0, avg_price=0.0)

            for pos in updates:
                symbol = pos.symbol.upper()
                bucket = by_symbol.setdefault(
                    symbol,
                    {
                        "long": Position(symbol=symbol, side=None, size=0.0, avg_price=0.0),
                        "short": Position(symbol=symbol, side=None, size=0.0, avg_price=0.0),
                    },
                )
                if pos.side in bucket:
                    bucket[pos.side] = pos

            all_positions: list[Position] = []
            for symbol, sides in by_symbol.items():
                for side_name in ("long", "short"):
                    pos = sides[side_name]
                    if pos.size > 0:
                        all_positions.append(pos)
            self.positions_by_symbol = by_symbol
            self.all_positions = all_positions
            self.positions_updated_at = _now()

    def get_symbol_positions(self, symbol: str) -> dict[str, Position] | None:
        symbol = symbol.upper()
        with self.lock:
            sides = self.positions_by_symbol.get(symbol)
            if sides is None:
                return None
            return {"long": sides["long"], "short": sides["short"]}

    def get_all_positions(self) -> list[Position] | None:
        with self.lock:
            if self.positions_updated_at <= 0:
                return None
            return list(self.all_positions)

    def refresh_unrealized_from_marks(self) -> None:
        with self.lock:
            updated_all: list[Position] = []
            for symbol, sides in self.positions_by_symbol.items():
                mark = self.marks.get(symbol)
                new_sides: dict[str, Position] = {}
                for side_name in ("long", "short"):
                    pos = sides[side_name]
                    if pos.size <= 0 or not mark or pos.avg_price <= 0:
                        new_sides[side_name] = pos
                        if pos.size > 0:
                            updated_all.append(pos)
                        continue
                    if side_name == "long":
                        upnl = (mark - pos.avg_price) * pos.size
                    else:
                        upnl = (pos.avg_price - mark) * pos.size
                    refreshed = Position(
                        symbol=pos.symbol,
                        side=pos.side,
                        size=pos.size,
                        avg_price=pos.avg_price,
                        unrealized_pnl=upnl,
                    )
                    new_sides[side_name] = refreshed
                    updated_all.append(refreshed)
                self.positions_by_symbol[symbol] = new_sides
            self.all_positions = updated_all

    def set_pending(self, symbol: str, orders: list[PendingOrder]) -> None:
        with self.lock:
            self.pending_by_symbol[symbol.upper()] = list(orders)

    def get_pending(self, symbol: str) -> list[PendingOrder] | None:
        with self.lock:
            if symbol.upper() not in self.pending_by_symbol:
                return None
            return list(self.pending_by_symbol[symbol.upper()])

    def upsert_order_detail(self, order_id: str, detail: dict) -> None:
        """Merge fill events. Sum USDT commission per tradeId (ORDER_TRADE_UPDATE n/N)."""
        with self.lock:
            oid = str(order_id)
            prev = self.order_details.get(oid) or {}
            merged = dict(prev)
            skip = {"commission", "fees_by_trade", "tradeId"}
            for key, value in detail.items():
                if key not in skip:
                    merged[key] = value
            fees = dict(prev.get("fees_by_trade") or {})
            incoming = detail.get("fees_by_trade")
            if isinstance(incoming, dict):
                fees.update({str(k): float(v) for k, v in incoming.items()})
            trade_id = str(detail.get("tradeId") or "")
            asset = str(detail.get("commissionAsset") or "USDT").upper()
            raw_fee = detail.get("commission")
            if trade_id and trade_id not in {"0", "None"} and raw_fee not in (None, ""):
                if asset in {"USDT", "USD", ""}:
                    try:
                        fees[trade_id] = abs(float(raw_fee))
                    except (TypeError, ValueError):
                        pass
            merged["fees_by_trade"] = fees
            merged["commission"] = round(sum(float(v) for v in fees.values()), 8)
            self.order_details[oid] = merged

    def get_order_detail(self, order_id: str) -> dict | None:
        with self.lock:
            detail = self.order_details.get(str(order_id))
            return dict(detail) if detail else None

    def mark_reconciled(self) -> None:
        with self.lock:
            self.last_reconcile_at = _now()


CACHE = BinanceWsCache()
