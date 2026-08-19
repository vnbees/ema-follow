import os

from src.config import LEVERAGE as SHARED_LEVERAGE
from src.config import ORDER_MARGIN_MIN_USDT

SYMBOLS = tuple(
    s.strip().upper()
    for s in os.getenv("RSI_REV_SYMBOLS", "LINKUSDT,SUIUSDT,WLDUSDT,HYPEUSDT").split(",")
    if s.strip()
)
RSI_PERIOD = int(os.getenv("RSI_REV_RSI_PERIOD", "14"))
MID_LOW = float(os.getenv("RSI_REV_MID_LOW", "48"))
MID_HIGH = float(os.getenv("RSI_REV_MID_HIGH", "52"))
MOVE_AWAY_PCT = float(os.getenv("RSI_REV_MOVE_AWAY_PCT", "0.005"))
ZONE_PCT = float(os.getenv("RSI_REV_ZONE_PCT", "0.0025"))
# Skip open when remaining entry→TP is below this (covers ~0.10% RT taker + buffer).
MIN_TP_ROOM_PCT = float(os.getenv("RSI_REV_MIN_TP_ROOM_PCT", "0.0012"))
BE_AFTER_HOURS = float(os.getenv("RSI_REV_BE_AFTER_HOURS", "168"))
MAX_AGE_DAYS = float(os.getenv("RSI_REV_MAX_AGE_DAYS", "30"))
MARGIN_PCT = float(os.getenv("RSI_REV_MARGIN_PCT", "0.5"))
MARGIN_MIN_USDT = float(os.getenv("RSI_REV_MARGIN_MIN_USDT", str(ORDER_MARGIN_MIN_USDT)))
# 0 = no cap (match backtest).
MAX_OPEN = int(os.getenv("RSI_REV_MAX_OPEN", "0"))
# 0 = open every trigger in the closed candle.
ENTRIES_PER_CYCLE = int(os.getenv("RSI_REV_ENTRIES_PER_CYCLE", "0"))
CANDLE_LIMIT = int(os.getenv("RSI_REV_CANDLE_LIMIT", "200"))
WATCHER_INTERVAL_SEC = float(os.getenv("RSI_REV_WATCHER_INTERVAL_SEC", "2"))
LEVERAGE = SHARED_LEVERAGE
BALANCE_CACHE_MAX_AGE_SEC = float(os.getenv("RSI_REV_BALANCE_CACHE_MAX_AGE_SEC", "30"))
