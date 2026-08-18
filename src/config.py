import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
_db_env = os.getenv("DATABASE_PATH", "data/bot.db")
DATABASE_PATH = Path(_db_env).expanduser() if os.path.isabs(_db_env) else BASE_DIR / _db_env

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
# Inventory mode: open/maintain pairs without RSI cross entry/stack.
RSI_ENTRY_ENABLED = os.getenv("RSI_ENTRY_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)

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
PAIR_PROFIT_TARGET_PCT = float(os.getenv("PAIR_PROFIT_TARGET_PCT", "0.5"))
# Close whole LONG/SHORT side when DB-weighted avg of open lots vs mark ≥ TP,
# then fall through to per-lot TP when side avg is below threshold.
# Uses lot entries in DB (not exchange entryPrice) to avoid closing underwater
# stacked lots when exchange avg alone looks green.
AGGREGATE_TP_ENABLED = os.getenv("AGGREGATE_TP_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
# After a TP close (0.5% hit), open a fresh L+S on the same symbol.
# Orphan-BE (partner already TP'd) and max-age closes do not reopen.
PAIR_REOPEN_ON_CLOSE = os.getenv("PAIR_REOPEN_ON_CLOSE", "true").lower() in (
    "1",
    "true",
    "yes",
)
# Bootstrap missing inventory slots: max new symbols opened per 5m cycle (anti-418).
INVENTORY_BOOTSTRAP_PER_CYCLE = int(os.getenv("INVENTORY_BOOTSTRAP_PER_CYCLE", "1"))

# Auto-close lot legs older than this many days (0 = disabled).
MAX_LOT_AGE_DAYS = float(os.getenv("MAX_LOT_AGE_DAYS", "3"))
# Global budget of age-close exchange orders per 5m cycle (stagger to avoid 418).
MAX_AGE_CLOSES_PER_CYCLE = int(os.getenv("MAX_AGE_CLOSES_PER_CYCLE", "4"))

# After N hours, underwater lots arm sticky BE (close at entry); still-green lots keep TP.
# Inventory mode uses orphan-BE (partner closed → target 0%) instead; leave off by default.
BREAKEVEN_WHEN_LOSING_ENABLED = os.getenv(
    "BREAKEVEN_WHEN_LOSING_ENABLED", "false"
).lower() in ("1", "true", "yes")
BREAKEVEN_AFTER_HOURS = float(os.getenv("BREAKEVEN_AFTER_HOURS", "24"))

# Realtime TP watcher: close legs from WS mark prices instead of waiting for the 5m cycle.
REALTIME_TP_ENABLED = os.getenv("REALTIME_TP_ENABLED", "true").lower() in ("1", "true", "yes")
REALTIME_TP_INTERVAL_SEC = float(os.getenv("REALTIME_TP_INTERVAL_SEC", "2"))

# Skip symbols listed on futures less than this many days ago (0 = disabled).
MIN_LISTING_AGE_DAYS = float(os.getenv("MIN_LISTING_AGE_DAYS", "30"))

MARGIN_GUARD_ENABLED = os.getenv("MARGIN_GUARD_ENABLED", "true").lower() in ("1", "true", "yes")
MARGIN_MAINT_OK_PCT = float(os.getenv("MARGIN_MAINT_OK_PCT", "15"))
MARGIN_MAINT_WARN_PCT = float(os.getenv("MARGIN_MAINT_WARN_PCT", "20"))
MARGIN_MAINT_HIGH_PCT = float(os.getenv("MARGIN_MAINT_HIGH_PCT", "25"))
MARGIN_MAINT_CRITICAL_PCT = float(os.getenv("MARGIN_MAINT_CRITICAL_PCT", "35"))
MARGIN_MAINT_DELEVERAGE_PCT = float(os.getenv("MARGIN_MAINT_DELEVERAGE_PCT", "30"))
MARGIN_HIGH_TP_PCT = float(os.getenv("MARGIN_HIGH_TP_PCT", "0.5"))
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

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

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
BINANCE_WS_ENABLED = os.getenv("BINANCE_WS_ENABLED", "true").lower() in ("1", "true", "yes")
# One-shot: clear persisted 418 cooldown on boot (e.g. after Railway region / IP change).
BINANCE_CLEAR_RATE_LIMIT = os.getenv("BINANCE_CLEAR_RATE_LIMIT", "false").lower() in (
    "1",
    "true",
    "yes",
)
# Post April 2026: legacy wss://fstream.binance.com/ws ACKs SUBSCRIBE but
# delivers no kline/miniTicker/markPrice data. Use /market for public market
# streams and /private for user data (listenKey).
BINANCE_WS_MARKET_URL = os.getenv(
    "BINANCE_WS_MARKET_URL",
    "wss://fstream.binance.com/market/ws",
)
BINANCE_WS_STREAM_BASE = os.getenv(
    "BINANCE_WS_STREAM_BASE",
    "wss://fstream.binance.com/market/stream",
)
BINANCE_WS_USER_BASE = os.getenv(
    "BINANCE_WS_USER_BASE",
    "wss://fstream.binance.com/private",
)
BINANCE_WS_STALE_SEC = float(os.getenv("BINANCE_WS_STALE_SEC", "45"))
BINANCE_WS_RECONCILE_SEC = float(os.getenv("BINANCE_WS_RECONCILE_SEC", "300"))
BINANCE_WS_DISCONNECT_NOTIFY_SEC = float(os.getenv("BINANCE_WS_DISCONNECT_NOTIFY_SEC", "120"))
# Skip heavy ticker/24hr seed when WS market is on (miniTicker fills rank).
BINANCE_WS_REST_TICKER_SEED = os.getenv("BINANCE_WS_REST_TICKER_SEED", "false").lower() in (
    "1",
    "true",
    "yes",
)
# Min interval between ticker/24hr volume-rank fallbacks when WS miniTicker is empty.
BINANCE_VOLUME_RANK_REST_SEC = float(os.getenv("BINANCE_VOLUME_RANK_REST_SEC", "300"))
# Reuse disk exchangeInfo this long (lot/tick filters). 0 = never REST-refresh if disk exists.
BINANCE_EXCHANGE_INFO_MAX_AGE_SEC = float(
    os.getenv("BINANCE_EXCHANGE_INFO_MAX_AGE_SEC", str(7 * 86_400))
)
# Resubscribe kline socket when a watched symbol is silent this long.
BINANCE_WS_KLINE_SILENCE_SEC = float(os.getenv("BINANCE_WS_KLINE_SILENCE_SEC", "90"))
# Per-symbol cooldown between REST kline refreshes (anti-418; still correct within interval).
BINANCE_CANDLE_REST_SEC = float(os.getenv("BINANCE_CANDLE_REST_SEC", "45"))
# Stagger between REST kline calls in a cycle.
BINANCE_CANDLE_REST_STAGGER_SEC = float(os.getenv("BINANCE_CANDLE_REST_STAGGER_SEC", "0.15"))
# Monitoring-only: min seconds between forced /fapi/v2/account REST (0 = never force; WS-first).
BALANCE_MONITOR_REST_SEC = float(os.getenv("BALANCE_MONITOR_REST_SEC", "900"))
# Monitoring-only: min seconds between spot snapshot REST (spot has no UDS).
SPOT_SNAPSHOT_INTERVAL_SEC = float(os.getenv("SPOT_SNAPSHOT_INTERVAL_SEC", "900"))
# Dashboard status only: min seconds between openOrders REST when UDS is down.
# RSI bot is market-order only — pending limits are not on the trade path.
PENDING_ORDERS_REST_SEC = float(os.getenv("PENDING_ORDERS_REST_SEC", "900"))
# Extra REST pause after Binance "banned until" (IP still hot at the exact timestamp).
REST_BAN_GRACE_SEC = float(os.getenv("REST_BAN_GRACE_SEC", "900"))
# After pad/grace: optional REST (listenKey/income/account) stays blocked this long.
# Critical REST (orders, per-symbol positionRisk) is allowed but single-flight + gap.
REST_BAN_RESUME_SEC = float(os.getenv("REST_BAN_RESUME_SEC", "900"))
REST_BAN_RESUME_GAP_SEC = float(os.getenv("REST_BAN_RESUME_GAP_SEC", "5"))
# Always serialize REST so TP + listenKey + spot cannot hit Binance in the same instant.
REST_SERIAL_GAP_SEC = float(os.getenv("REST_SERIAL_GAP_SEC", "0.25"))
# After deploy/restart: block optional REST this long (WS/disk only). Critical orders still allowed.
REST_BOOT_QUIET_SEC = float(os.getenv("REST_BOOT_QUIET_SEC", "600"))
# USDT-M futures IP weight / minute (Binance). Header X-MBX-USED-WEIGHT-1M.
REST_WEIGHT_LIMIT_1M = int(os.getenv("REST_WEIGHT_LIMIT_1M", "2400"))
# Skip optional REST at this used weight (leave headroom for shared Railway IPs).
REST_WEIGHT_SAFE_MAX = int(os.getenv("REST_WEIGHT_SAFE_MAX", "800"))
# Skip ALL REST including orders at this used weight.
REST_WEIGHT_HARD_MAX = int(os.getenv("REST_WEIGHT_HARD_MAX", "1800"))

SPOT_TRANSFER_ENABLED = os.getenv("SPOT_TRANSFER_ENABLED", "true").lower() in ("1", "true", "yes")
SPOT_TRANSFER_PCT = float(os.getenv("SPOT_TRANSFER_PCT", "1"))
# pct: fixed % of equity daily | hwm: share of equity above high-water mark (Binance only).
_SPOT_TRANSFER_MODE_RAW = os.getenv("SPOT_TRANSFER_MODE", "pct").strip().lower()
SPOT_TRANSFER_MODE = _SPOT_TRANSFER_MODE_RAW if _SPOT_TRANSFER_MODE_RAW in ("pct", "hwm") else "pct"
SPOT_TRANSFER_HWM_SHARE = float(os.getenv("SPOT_TRANSFER_HWM_SHARE", "50"))
SPOT_TRANSFER_PREPARE_HHMM = os.getenv("SPOT_TRANSFER_PREPARE_HHMM", "0655").strip()
SPOT_TRANSFER_EXECUTE_HHMM = os.getenv("SPOT_TRANSFER_EXECUTE_HHMM", "0700").strip()
