import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
DATABASE_PATH = BASE_DIR / os.getenv("DATABASE_PATH", "data/bot.db")

DEFAULT_SYMBOL = os.getenv("SYMBOL", "SUIUSDT")
SYMBOL = DEFAULT_SYMBOL
PRODUCT_TYPE = "usdt-futures"
PRODUCT_TYPE_API = "USDT-FUTURES"
GRANULARITY = "5m"
CANDLE_LIMIT = 1000
EMA_PERIODS = (34, 89, 144, 200)
INTERVAL_MINUTES = 5

ORDER_SIZE_USDT = float(os.getenv("ORDER_SIZE_USDT", "5"))
MARGIN_MODE = os.getenv("MARGIN_MODE", "crossed")
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "false").lower() in ("1", "true", "yes")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
PROFIT_TARGET_PCT = float(os.getenv("PROFIT_TARGET_PCT", "1"))
SAR_AF = float(os.getenv("SAR_AF", "0.02"))
SAR_MAX_AF = float(os.getenv("SAR_MAX_AF", "0.2"))
OFI_SPIKE_THRESHOLD = float(os.getenv("OFI_SPIKE_THRESHOLD", "1.5"))
OFI_HISTORY_CANDLES = int(os.getenv("OFI_HISTORY_CANDLES", "10"))
OFI_SYMBOL = os.getenv("OFI_SYMBOL", "ETHUSDT").upper()
OFI_INTERVAL_MINUTES = int(os.getenv("OFI_INTERVAL_MINUTES", "1"))
OFI_REALTIME_REFRESH_SEC = float(os.getenv("OFI_REALTIME_REFRESH_SEC", "1"))
BITGET_WS_PUBLIC = os.getenv("BITGET_WS_PUBLIC", "wss://ws.bitget.com/v2/ws/public")

BITGET_API_BASE = "https://api.bitget.com"
CANDLES_ENDPOINT = "/api/v2/mix/market/candles"
ACCOUNT_ENDPOINT = "/api/v2/mix/account/account"
CONTRACTS_ENDPOINT = "/api/v2/mix/market/contracts"
PENDING_ORDERS_ENDPOINT = "/api/v2/mix/order/orders-pending"
PLACE_ORDER_ENDPOINT = "/api/v2/mix/order/place-order"
CANCEL_ORDER_ENDPOINT = "/api/v2/mix/order/cancel-order"
ORDER_DETAIL_ENDPOINT = "/api/v2/mix/order/detail"
CLOSE_POSITIONS_ENDPOINT = "/api/v2/mix/order/close-positions"
SINGLE_POSITION_ENDPOINT = "/api/v2/mix/position/single-position"
SET_LEVERAGE_ENDPOINT = "/api/v2/mix/account/set-leverage"
SET_MARGIN_MODE_ENDPOINT = "/api/v2/mix/account/set-margin-mode"
SET_POSITION_MODE_ENDPOINT = "/api/v2/mix/account/set-position-mode"
MARGIN_COIN = "USDT"

BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")
