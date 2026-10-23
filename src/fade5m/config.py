"""5m fade against the live 4h candle. One USDT margin per limit."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from src.donchian.config import BACKTEST_SYMBOLS_20
from src.donchian.config import LEVERAGE as SHARED_LEVERAGE

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _csv(raw: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    text = (raw or "").strip()
    if not text:
        return default
    return tuple(s.strip().upper() for s in text.split(",") if s.strip())


ENABLED = os.getenv("FADE5M_ENABLED", "false").lower() in ("1", "true", "yes", "on")
SYMBOLS: tuple[str, ...] = _csv(os.getenv("FADE5M_SYMBOLS", ""), BACKTEST_SYMBOLS_20)
SYMBOLS_NEXT: tuple[str, ...] = _csv(os.getenv("FADE5M_SYMBOLS_NEXT", ""))
MARGIN_USDT = float(os.getenv("FADE5M_MARGIN_USDT", "1"))
START_AT_RAW = os.getenv("FADE5M_START_AT", "").strip()
SWITCH_AT_RAW = os.getenv("FADE5M_SWITCH_AT", "").strip()
LIVE_NOW: tuple[str, ...] = _csv(os.getenv("FADE5M_LIVE_NOW", ""))
INTERVAL_5M = "5m"
M5_MS = 5 * 60 * 1000
H4_MS = 4 * 60 * 60 * 1000
KLINE_LIMIT = 80
TICK_SEC = 1.0
REST_GAP_SEC = 2.5
TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
ATR_PERIOD = 14
TP_ATR_MULT = 1.0
LEVERAGE = SHARED_LEVERAGE


def parse_start_at(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=VN_TZ)
        return dt
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=VN_TZ)
        except ValueError:
            continue
    return None


START_AT = parse_start_at(START_AT_RAW)
SWITCH_AT = parse_start_at(SWITCH_AT_RAW)


def watch_symbols() -> tuple[str, ...]:
    """Current + next lists so WS is warm before the switch."""
    seen: set[str] = set()
    out: list[str] = []
    for symbol in (*SYMBOLS, *SYMBOLS_NEXT):
        if symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return tuple(out)


def trade_symbols(now: datetime | None = None) -> tuple[str, ...]:
    """Coins allowed to place new limits. Flatten still uses the store + last 5m."""
    if not SYMBOLS_NEXT or SWITCH_AT is None:
        return SYMBOLS
    clock = now or datetime.now(VN_TZ)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=VN_TZ)
    if clock >= SWITCH_AT.astimezone(clock.tzinfo):
        return SYMBOLS_NEXT
    return SYMBOLS


def entries_open(now: datetime | None = None, symbol: str | None = None) -> bool:
    if symbol and symbol.strip().upper() in LIVE_NOW:
        return True
    if START_AT is None:
        return True
    clock = now or datetime.now(VN_TZ)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=VN_TZ)
    return clock >= START_AT.astimezone(clock.tzinfo)
