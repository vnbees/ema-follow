"""Live 4h Supertrend + ADX settings. Sized like the hunt backtest."""

from __future__ import annotations

import os

from src.donchian.config import BACKTEST_SYMBOLS_20
from src.donchian.config import LEVERAGE as SHARED_LEVERAGE

ENABLED = os.getenv("TREND4H_ENABLED", "true").lower() in ("1", "true", "yes", "on")
SYMBOLS: tuple[str, ...] = tuple(
    s.strip().upper()
    for s in os.getenv("TREND4H_SYMBOLS", ",".join(BACKTEST_SYMBOLS_20)).split(",")
    if s.strip()
)
MARGIN_USDT = float(os.getenv("TREND4H_MARGIN_USDT", "1"))
ADX_MIN = float(os.getenv("TREND4H_ADX_MIN", "25"))
ST_PERIOD = int(os.getenv("TREND4H_ST_PERIOD", "10"))
ST_FACTOR = float(os.getenv("TREND4H_ST_FACTOR", "3"))
ADX_PERIOD = 14
INTERVAL = "4h"
BAR_MS = 4 * 60 * 60 * 1000
MIN_BARS = 80
KLINE_LIMIT = 150
# One REST kline/order burst at a time. 20 coins × 2.5s ≈ 50s to seed.
REST_GAP_SEC = float(os.getenv("TREND4H_REST_GAP_SEC", "2.5"))
TICK_SEC = 5.0
TAKER_FEE = 0.0005
LEVERAGE = SHARED_LEVERAGE
