import os

from src.config import LEVERAGE as SHARED_LEVERAGE
from src.config import ORDER_MARGIN_MIN_USDT

DONCHIAN_PERIOD = int(os.getenv("DONCHIAN_PERIOD", "20"))
SLOPE_LOOKBACK = int(os.getenv("DONCHIAN_SLOPE_LB", "5"))
PARALLEL_TOL = float(os.getenv("DONCHIAN_PARALLEL_TOL", "0.015"))
INTERVAL = os.getenv("DONCHIAN_INTERVAL", "15m")
# body_size_rr05 live default: 1% equity × size_mult (was 0.5%)
MARGIN_PCT = float(os.getenv("DONCHIAN_MARGIN_PCT", "0.01"))
MARGIN_MIN_USDT = float(os.getenv("DONCHIAN_MARGIN_MIN_USDT", str(ORDER_MARGIN_MIN_USDT)))
MAX_OPEN = int(os.getenv("DONCHIAN_MAX_OPEN", "20"))
TOP_N_SYMBOLS = int(os.getenv("DONCHIAN_TOP_N", "30"))
WATCHER_INTERVAL_SEC = float(os.getenv("DONCHIAN_WATCHER_INTERVAL_SEC", "2"))
BALANCE_CACHE_MAX_AGE_SEC = float(os.getenv("DONCHIAN_BALANCE_CACHE_MAX_AGE_SEC", "30"))

# Entry quality filter (body_size_rr05) — matches backtest config D
ATR_PERIOD = int(os.getenv("DONCHIAN_ATR_PERIOD", "14"))
MIN_BODY_ATR = float(os.getenv("DONCHIAN_MIN_BODY_ATR", "0.3"))
MAX_BODY_ATR = float(os.getenv("DONCHIAN_MAX_BODY_ATR", "1.2"))
MIN_POT_RR = float(os.getenv("DONCHIAN_MIN_POT_RR", "0.5"))
SIZE_BY_RR = os.getenv("DONCHIAN_SIZE_BY_RR", "true").lower() in ("1", "true", "yes", "on")

# Breadth mid (BT breadth_mid / breadth_flip): pool close ≷ Donchian mid vote
# Mode: flip (default, live) | hard (skip opposite) | off
_raw_breadth_mode = os.getenv("DONCHIAN_BREADTH_MODE")
if _raw_breadth_mode is not None:
    BREADTH_MODE = _raw_breadth_mode.strip().lower()
    if BREADTH_MODE in ("off", "none", "false", "0"):
        BREADTH_MODE = "off"
    elif BREADTH_MODE not in ("flip", "hard"):
        BREADTH_MODE = "flip"
else:
    # Legacy DONCHIAN_BREADTH_HARD: false→off, else default flip (replaces old hard default)
    _legacy_hard = os.getenv("DONCHIAN_BREADTH_HARD", "true").lower() in ("1", "true", "yes", "on")
    BREADTH_MODE = "flip" if _legacy_hard else "off"
BREADTH_ENABLED = BREADTH_MODE in ("flip", "hard")
BREADTH_HARD = BREADTH_MODE == "hard"  # compat alias
BREADTH_RATIO = float(os.getenv("DONCHIAN_BREADTH_RATIO", "1.3"))
BREADTH_MIN_N = int(os.getenv("DONCHIAN_BREADTH_MIN_N", "12"))
# majors = MAJOR_SYMBOLS mid vote (near BT 20); scan = current top-N only
BREADTH_UNIVERSE = os.getenv("DONCHIAN_BREADTH_UNIVERSE", "majors").strip().lower()

# Minimum nến: Donchian + slope + ATR warmup
WARMUP_MIN_BARS = DONCHIAN_PERIOD + SLOPE_LOOKBACK + ATR_PERIOD + 5
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
