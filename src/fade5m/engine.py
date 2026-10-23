"""Signal: 4h in-progress green + closed 5m red → long; 4h red + 5m green → short."""

from __future__ import annotations

from dataclasses import dataclass

from src.exchange.types import Candle
from src.fade5m.config import ATR_PERIOD, H4_MS, M5_MS, MAKER_FEE, TAKER_FEE, TP_ATR_MULT


@dataclass(frozen=True)
class Bar5m:
    ts: int
    open: float
    high: float
    low: float
    close: float


def bucket_4h(ts: int) -> int:
    return (int(ts) // H4_MS) * H4_MS


def is_last_5m_of_4h(ts: int) -> bool:
    return int(ts) + M5_MS == bucket_4h(ts) + H4_MS


def candle_side(open_px: float, close_px: float) -> int:
    if close_px > open_px:
        return 1
    if close_px < open_px:
        return -1
    return 0


def fourh_open(bars: list[Bar5m], bucket: int) -> float | None:
    for bar in bars:
        if bucket_4h(bar.ts) == bucket:
            return bar.open
    return None


def signal_side(bar: Bar5m, h4_open: float) -> int:
    """Wanted hold side after this 5m close. 0 = no new limit."""
    h4 = candle_side(h4_open, bar.close)
    m5 = candle_side(bar.open, bar.close)
    if h4 == 1 and m5 == -1:
        return 1
    if h4 == -1 and m5 == 1:
        return -1
    return 0


def lot_unrealized(side: str, entry: float, qty: float, mark: float) -> float:
    if side == "long":
        return (mark - entry) * qty
    return (entry - mark) * qty


def book_unrealized(opens: list[tuple[str, float, float]], mark: float) -> float:
    """Gross mark-to-entry of open lots. Each item is (side, entry, qty)."""
    return sum(lot_unrealized(side, entry, qty, mark) for side, entry, qty in opens)


def skip_add_when_profit(opens: list[tuple[str, float, float]], mark: float) -> bool:
    """True when the open book is already in profit — do not stack another limit."""
    if not opens or mark <= 0:
        return False
    return book_unrealized(opens, mark) > 0


def lots_on_side(opens: list[tuple[str, float, float]], side: str) -> list[tuple[str, float, float]]:
    return [row for row in opens if row[0] == side]


def last_atr(bars: list[Bar5m], period: int = ATR_PERIOD) -> float | None:
    """Wilder ATR of closed 5m bars. None until `period` true ranges exist."""
    n = len(bars)
    if n < period + 1 or period <= 0:
        return None
    tr = [0.0] * n
    tr[0] = bars[0].high - bars[0].low
    for i in range(1, n):
        prev = bars[i - 1].close
        tr[i] = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev),
            abs(bars[i].low - prev),
        )
    acc = sum(tr[1 : period + 1])
    atr = acc / period
    for i in range(period + 1, n):
        atr = atr - atr / period + tr[i] / period
    if atr <= 1e-12:
        return None
    return float(atr)


def fee_floor_distance(entry: float) -> float:
    if entry <= 0:
        return 0.0
    return entry * (TAKER_FEE + MAKER_FEE) / (1.0 - MAKER_FEE)


def tp_distance(entry: float, atr: float) -> float:
    return max(float(atr) * TP_ATR_MULT, fee_floor_distance(entry))


def tp_price(side: str, entry: float, atr: float) -> float:
    dist = tp_distance(entry, atr)
    if side == "long":
        return entry + dist
    return entry - dist


def opposite_qty(opens: list[tuple[str, float, float]], want_side: str) -> float:
    """Filled qty on the other hedge side — cover this plus 1 USDT on a 4h color flip."""
    return sum(qty for side, _entry, qty in opens if side != want_side)


def closed_blocks(rows: list[dict], h4_ms: int) -> list[dict]:
    """Group filled closes by the 4h bucket they belonged to (07→11 +07, …)."""
    by: dict[int, list[dict]] = {}
    for row in rows:
        if str(row.get("status") or "") != "closed":
            continue
        if str(row.get("reason") or "") == "user_flat":
            continue
        bucket = int(row.get("bucket_ts") or 0)
        by.setdefault(bucket, []).append(row)
    out: list[dict] = []
    for bucket in sorted(by, reverse=True):
        items = by[bucket]
        pnl = sum(float(r.get("pnl") or 0) for r in items)
        orders = []
        for r in sorted(items, key=lambda x: int(x.get("opened_ts") or x.get("bar_ts") or 0)):
            orders.append(
                {
                    "symbol": r.get("symbol"),
                    "side": r.get("side"),
                    "qty": r.get("qty"),
                    "entry_px": r.get("entry_px") or r.get("limit_px"),
                    "exit_px": r.get("exit_px"),
                    "pnl": float(r.get("pnl") or 0),
                    "reason": r.get("reason") or r.get("status"),
                    "opened_ts": r.get("opened_ts") or r.get("bar_ts"),
                    "closed_ts": r.get("closed_ts"),
                }
            )
        out.append(
            {
                "bucket_ts": bucket,
                "end_ts": bucket + int(h4_ms),
                "n": len(orders),
                "pnl": pnl,
                "orders": orders,
            }
        )
    return out


def bars_from_klines(rows: list[Candle], now_ms: int) -> list[Bar5m]:
    out: list[Bar5m] = []
    for candle in rows:
        ts = int(candle.timestamp)
        if ts + M5_MS > now_ms:
            continue
        out.append(
            Bar5m(
                ts=ts,
                open=float(candle.open),
                high=float(candle.high),
                low=float(candle.low),
                close=float(candle.close),
            )
        )
    out.sort(key=lambda b: b.ts)
    return out


def merge_bars(history: list[Bar5m], extra: list[Bar5m]) -> list[Bar5m]:
    by = {b.ts: b for b in history}
    for bar in extra:
        by[bar.ts] = bar
    return [by[ts] for ts in sorted(by)]
