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
EMA_PERIODS = (20, 50, 100, 200)
INTERVAL_MINUTES = 5

ORDER_SIZE_USDT = float(os.getenv("ORDER_SIZE_USDT", "5"))
MARGIN_MODE = os.getenv("MARGIN_MODE", "crossed")
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

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
