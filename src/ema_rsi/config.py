import os

from src.config import LEVERAGE as SHARED_LEVERAGE
from src.config import ORDER_MARGIN_MIN_USDT

EMA_PERIOD = int(os.getenv("EMA_RSI_EMA_PERIOD", "200"))
RSI_PERIOD = int(os.getenv("EMA_RSI_RSI_PERIOD", "14"))
RSI_LOW = float(os.getenv("EMA_RSI_RSI_LOW", "25"))
RSI_HIGH = float(os.getenv("EMA_RSI_RSI_HIGH", "75"))
RR = float(os.getenv("EMA_RSI_RR", "2"))
MARGIN_PCT = float(os.getenv("EMA_RSI_MARGIN_PCT", "1"))
MARGIN_MIN_USDT = float(os.getenv("EMA_RSI_MARGIN_MIN_USDT", str(ORDER_MARGIN_MIN_USDT)))
MAX_OPEN = int(os.getenv("EMA_RSI_MAX_OPEN", "20"))
SCAN_LIMIT = int(os.getenv("EMA_RSI_SCAN_LIMIT", "50"))
CANDLE_LIMIT = int(os.getenv("EMA_RSI_CANDLE_LIMIT", "500"))
ENTRIES_PER_CYCLE = int(os.getenv("EMA_RSI_ENTRIES_PER_CYCLE", "3"))
WATCHER_INTERVAL_SEC = float(os.getenv("EMA_RSI_WATCHER_INTERVAL_SEC", "2"))
LEVERAGE = SHARED_LEVERAGE
