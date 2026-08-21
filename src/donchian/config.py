import os

from src.config import LEVERAGE as SHARED_LEVERAGE
from src.config import ORDER_MARGIN_MIN_USDT

DONCHIAN_PERIOD = int(os.getenv("DONCHIAN_PERIOD", "20"))
SLOPE_LOOKBACK = int(os.getenv("DONCHIAN_SLOPE_LB", "5"))
PARALLEL_TOL = float(os.getenv("DONCHIAN_PARALLEL_TOL", "0.015"))
INTERVAL = os.getenv("DONCHIAN_INTERVAL", "15m")
MARGIN_PCT = float(os.getenv("DONCHIAN_MARGIN_PCT", "0.005"))
MARGIN_MIN_USDT = float(os.getenv("DONCHIAN_MARGIN_MIN_USDT", str(ORDER_MARGIN_MIN_USDT)))
MAX_OPEN = int(os.getenv("DONCHIAN_MAX_OPEN", "20"))
TOP_N_SYMBOLS = int(os.getenv("DONCHIAN_TOP_N", "30"))
WATCHER_INTERVAL_SEC = float(os.getenv("DONCHIAN_WATCHER_INTERVAL_SEC", "2"))
BALANCE_CACHE_MAX_AGE_SEC = float(os.getenv("DONCHIAN_BALANCE_CACHE_MAX_AGE_SEC", "30"))
# Minimum nến cần để tính đủ Donchian + slope warmup
WARMUP_MIN_BARS = DONCHIAN_PERIOD + SLOPE_LOOKBACK + 5
CANDLE_LIMIT = WARMUP_MIN_BARS + 50

# Symbol pool filter (see symbol_filter.py)
MIN_LISTING_DAYS = float(os.getenv("DONCHIAN_MIN_LISTING_DAYS", "365"))
MAX_RANGE_24H_PCT = float(os.getenv("DONCHIAN_MAX_RANGE_24H_PCT", "15"))
SYMBOL_FILTER_ENABLED = os.getenv("DONCHIAN_SYMBOL_FILTER", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# Majors skip 24h range cap (still require MIN_LISTING_DAYS).
# Criteria: large/liquid, multi-year track record, non-meme, non-narrative hype.
# Excludes: DOGE/PEPE (meme), HYPE (high-vol newer), mid/small speculative alts.
# Override: DONCHIAN_MAJOR_SYMBOLS=BTCUSDT,ETHUSDT,...
_DEFAULT_MAJORS = (
    # Tier-1: top market-cap / settlement
    "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,TRXUSDT,"
    # Established L1 / L2 / infra (years live, deep liquidity)
    "ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,LTCUSDT,BCHUSDT,XLMUSDT,"
    "ATOMUSDT,NEARUSDT,APTUSDT,SUIUSDT,ARBUSDT,OPUSDT,"
    # Blue-chip DeFi / storage (not meme)
    "UNIUSDT,AAVEUSDT,FILUSDT"
)
MAJOR_SYMBOLS: frozenset[str] = frozenset(
    s.strip().upper()
    for s in os.getenv("DONCHIAN_MAJOR_SYMBOLS", _DEFAULT_MAJORS).split(",")
    if s.strip()
)

LEVERAGE = SHARED_LEVERAGE

_UNTRADABLE: set[str] = set()


def mark_untradable(symbol: str) -> None:
    _UNTRADABLE.add(symbol.upper())


def is_untradable(symbol: str) -> bool:
    return symbol.upper() in _UNTRADABLE

# Stablecoin và leverage token bị loại khỏi scan
_STABLE_SUFFIXES = ("USDC", "BUSD", "FDUSD", "TUSD", "USDP", "GUSD", "DAI", "UST")
_LEVERAGE_KEYWORDS = ("UP", "DOWN", "BULL", "BEAR")


def is_excluded_symbol(symbol: str) -> bool:
    s = symbol.upper().replace("USDT", "")
    for suffix in _STABLE_SUFFIXES:
        if s == suffix or s.endswith(suffix):
            return True
    for kw in _LEVERAGE_KEYWORDS:
        if s.endswith(kw):
            return True
    return False
