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
GRANULARITY = os.getenv("GRANULARITY", "5m")
CANDLE_LIMIT = int(os.getenv("CANDLE_LIMIT", "200"))
EMA_PERIODS = (34, 89, 144, 200)

RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_LONG_ENTRY = float(os.getenv("RSI_LONG_ENTRY", "25"))
RSI_LONG_EXIT = float(os.getenv("RSI_LONG_EXIT", "75"))
RSI_SHORT_ENTRY = float(os.getenv("RSI_SHORT_ENTRY", "75"))
RSI_SHORT_EXIT = float(os.getenv("RSI_SHORT_EXIT", "25"))
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", "5"))
RSI_MIN_CANDLES = RSI_PERIOD + 2

ORDER_SIZE_USDT = float(os.getenv("ORDER_SIZE_USDT", "5"))
ORDER_MARGIN_PCT = float(os.getenv("ORDER_MARGIN_PCT", "0.5"))
ORDER_MARGIN_MIN_USDT = float(os.getenv("ORDER_MARGIN_MIN_USDT", "1"))
LEGACY_MARGIN_USDT = float(os.getenv("LEGACY_MARGIN_USDT", "5"))
MARGIN_MODE = os.getenv("MARGIN_MODE", "crossed")
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "false").lower() in ("1", "true", "yes")
MAX_OPEN_SYMBOLS = int(os.getenv("MAX_OPEN_SYMBOLS", os.getenv("MAX_OPEN_POSITIONS", "20")))
MAX_OPEN_POSITIONS = MAX_OPEN_SYMBOLS
MAX_OPEN_LEGS = MAX_OPEN_SYMBOLS * 2
PAIR_PROFIT_TARGET_PCT = float(os.getenv("PAIR_PROFIT_TARGET_PCT", "2"))

MARGIN_GUARD_ENABLED = os.getenv("MARGIN_GUARD_ENABLED", "true").lower() in ("1", "true", "yes")
MARGIN_MAINT_OK_PCT = float(os.getenv("MARGIN_MAINT_OK_PCT", "15"))
MARGIN_MAINT_WARN_PCT = float(os.getenv("MARGIN_MAINT_WARN_PCT", "20"))
MARGIN_MAINT_HIGH_PCT = float(os.getenv("MARGIN_MAINT_HIGH_PCT", "25"))
MARGIN_MAINT_CRITICAL_PCT = float(os.getenv("MARGIN_MAINT_CRITICAL_PCT", "35"))
MARGIN_MAINT_DELEVERAGE_PCT = float(os.getenv("MARGIN_MAINT_DELEVERAGE_PCT", "30"))
MARGIN_HIGH_TP_PCT = float(os.getenv("MARGIN_HIGH_TP_PCT", "1"))
MARGIN_DEPOSIT_TARGET_PCT = float(os.getenv("MARGIN_DEPOSIT_TARGET_PCT", "18"))
MARGIN_ELEVATED_CYCLE_LIMIT = int(os.getenv("MARGIN_ELEVATED_CYCLE_LIMIT", "3"))
MARGIN_HIGH_CYCLE_LIMIT = int(os.getenv("MARGIN_HIGH_CYCLE_LIMIT", "2"))
MARGIN_IMPROVEMENT_PCT = float(os.getenv("MARGIN_IMPROVEMENT_PCT", "0.3"))

MARGIN_PREFLIGHT_ENABLED = os.getenv("MARGIN_PREFLIGHT_ENABLED", "true").lower() in ("1", "true", "yes")
MARGIN_PREFLIGHT_BUFFER_PCT = float(os.getenv("MARGIN_PREFLIGHT_BUFFER_PCT", "10"))
MARGIN_PREFLIGHT_MAX_CLOSES = int(os.getenv("MARGIN_PREFLIGHT_MAX_CLOSES", "10"))


def order_notional_usdt() -> float:
    """Legacy helper: fixed margin × leverage (prefer order_sizing module)."""
    return ORDER_SIZE_USDT * LEVERAGE


WEB_PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "8080")))

DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "").strip()
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DASHBOARD_SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET", "").strip()
DASHBOARD_COOKIE_SECURE = os.getenv("DASHBOARD_COOKIE_SECURE", "false").lower() in (
    "1",
    "true",
    "yes",
)

VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:dashboard@localhost").strip()

PROFIT_TARGET_PCT = float(os.getenv("PROFIT_TARGET_PCT", "0"))
SAR_AF = float(os.getenv("SAR_AF", "0.02"))
SAR_MAX_AF = float(os.getenv("SAR_MAX_AF", "0.2"))
OFI_SPIKE_THRESHOLD = float(os.getenv("OFI_SPIKE_THRESHOLD", "1.5"))
OFI_HISTORY_CANDLES = int(os.getenv("OFI_HISTORY_CANDLES", "10"))
OFI_SYMBOL = os.getenv("OFI_SYMBOL", "SOLUSDT").upper()
OFI_INTERVAL_MINUTES = int(os.getenv("OFI_INTERVAL_MINUTES", "1"))
OFI_REALTIME_REFRESH_SEC = float(os.getenv("OFI_REALTIME_REFRESH_SEC", "1"))
OFI_IMBALANCE_STRONG_PCT = float(os.getenv("OFI_IMBALANCE_STRONG_PCT", "200"))
OFI_IMBALANCE_EXTREME_PCT = float(os.getenv("OFI_IMBALANCE_EXTREME_PCT", "300"))
OFI_BOOK_TICK_RANGE = int(os.getenv("OFI_BOOK_TICK_RANGE", "8"))
OFI_BOOK_STRONG_PCT = float(os.getenv("OFI_BOOK_STRONG_PCT", "150"))
OFI_DELTA_SPIKE_MIN = float(os.getenv("OFI_DELTA_SPIKE_MIN", "1.5"))
OFI_EARLY_ENTRY_SEC = int(os.getenv("OFI_EARLY_ENTRY_SEC", "5"))
OFI_TRADING_ENABLED = os.getenv("OFI_TRADING_ENABLED", "false").lower() in ("1", "true", "yes")
BITGET_WS_PUBLIC = os.getenv("BITGET_WS_PUBLIC", "wss://ws.bitget.com/v2/ws/public")

BITGET_API_BASE = "https://api.bitget.com"
CANDLES_ENDPOINT = "/api/v2/mix/market/candles"
TICKERS_ENDPOINT = "/api/v2/mix/market/tickers"
ACCOUNT_ENDPOINT = "/api/v2/mix/account/account"
CONTRACTS_ENDPOINT = "/api/v2/mix/market/contracts"
PENDING_ORDERS_ENDPOINT = "/api/v2/mix/order/orders-pending"
PLACE_ORDER_ENDPOINT = "/api/v2/mix/order/place-order"
CANCEL_ORDER_ENDPOINT = "/api/v2/mix/order/cancel-order"
ORDER_DETAIL_ENDPOINT = "/api/v2/mix/order/detail"
CLOSE_POSITIONS_ENDPOINT = "/api/v2/mix/order/close-positions"
SINGLE_POSITION_ENDPOINT = "/api/v2/mix/position/single-position"
ALL_POSITIONS_ENDPOINT = "/api/v2/mix/position/all-position"
SET_LEVERAGE_ENDPOINT = "/api/v2/mix/account/set-leverage"
SET_MARGIN_MODE_ENDPOINT = "/api/v2/mix/account/set-margin-mode"
SET_POSITION_MODE_ENDPOINT = "/api/v2/mix/account/set-position-mode"
MARGIN_COIN = "USDT"

BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET_KEY = os.getenv("BITGET_SECRET_KEY", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")

_EXCHANGE_RAW = os.getenv("EXCHANGE", "bitget").strip().lower()
if _EXCHANGE_RAW not in ("bitget", "binance"):
    _EXCHANGE_RAW = "bitget"
EXCHANGE = _EXCHANGE_RAW
EXCHANGE_DISPLAY_NAME = "Binance" if EXCHANGE == "binance" else "Bitget"

BINANCE_API_BASE = os.getenv("BINANCE_API_BASE", "https://fapi.binance.com")
BINANCE_SPOT_API_BASE = os.getenv("BINANCE_SPOT_API_BASE", "https://api.binance.com")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")

SPOT_TRANSFER_ENABLED = os.getenv("SPOT_TRANSFER_ENABLED", "true").lower() in ("1", "true", "yes")
SPOT_TRANSFER_PCT = float(os.getenv("SPOT_TRANSFER_PCT", "1"))
SPOT_TRANSFER_PREPARE_HHMM = os.getenv("SPOT_TRANSFER_PREPARE_HHMM", "0655").strip()
SPOT_TRANSFER_EXECUTE_HHMM = os.getenv("SPOT_TRANSFER_EXECUTE_HHMM", "0700").strip()
