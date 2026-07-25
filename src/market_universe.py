import logging
import threading
import time
from datetime import datetime, timezone

from src.exchange import ExchangeClientError, fetch_top_futures_by_volume

_lock = threading.Lock()
_volume_rank: list[tuple[str, float]] = []
_last_refreshed: str = ""
_last_refreshed_mono: float = 0.0
_last_scan_checked: int = 0
_last_signal_symbol: str = ""


def refresh_volume_rank(
    *,
    force: bool = False,
    max_age_sec: float | None = None,
) -> list[tuple[str, float]]:
    """Refresh USDT-M volume rank.

    When ``max_age_sec`` is set and a cached rank is younger than that age,
    skip the heavy ``ticker/24hr`` call unless ``force`` is True.
    """
    global _volume_rank, _last_refreshed, _last_refreshed_mono

    if not force and max_age_sec is not None and max_age_sec > 0:
        with _lock:
            age = time.monotonic() - _last_refreshed_mono if _last_refreshed_mono else None
            cached = list(_volume_rank)
        if cached and age is not None and age < max_age_sec:
            logging.info(
                "Volume rank cache hit (age=%.0fs < %.0fs) — skip ticker/24hr",
                age,
                max_age_sec,
            )
            return cached

    try:
        rows = fetch_top_futures_by_volume(limit=None)
    except ExchangeClientError as exc:
        logging.warning("Failed to refresh volume rank: %s", exc)
        with _lock:
            return list(_volume_rank)

    with _lock:
        _volume_rank = rows
        _last_refreshed = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        _last_refreshed_mono = time.monotonic()
    if rows:
        logging.info(
            "Volume rank loaded: %d USDT-M perpetuals (lead %s %.0f USDT 24h)",
            len(rows),
            rows[0][0],
            rows[0][1],
        )
    return rows


def get_volume_ranked() -> list[tuple[str, float]]:
    with _lock:
        if _volume_rank:
            return list(_volume_rank)
    return refresh_volume_rank(force=True)


def get_volume_rank_map() -> dict[str, tuple[int, float]]:
    ranked = get_volume_ranked()
    return {symbol: (idx + 1, volume) for idx, (symbol, volume) in enumerate(ranked)}


def set_scan_progress(checked: int, signal_symbol: str = "") -> None:
    global _last_scan_checked, _last_signal_symbol
    with _lock:
        _last_scan_checked = checked
        _last_signal_symbol = signal_symbol


def get_last_refreshed() -> str:
    with _lock:
        return _last_refreshed


def get_scan_stats() -> tuple[int, str]:
    with _lock:
        return _last_scan_checked, _last_signal_symbol
