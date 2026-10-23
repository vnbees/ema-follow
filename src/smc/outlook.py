"""Nearest signal level and the measured chance price comes back to it.

The level is the entry of the latest flipped sweep in the candles passed in.
Those candles must be the closed 15m bars available at the moment of the call.
The percentages are the leave-then-touch rates measured on exchange 15m bars
from 2025-10-01 through 2026-09-30. This module does not place orders.
"""

from __future__ import annotations

from src.smc.signals import BAR_MS, setups, flipped

# Eligible signals and how many returned: 4h, 1d, then 7d. Each pair is count, hits.
# Exchange 15m, 2025-10-01 through 2026-09-30. Not refreshed on a timer.
MEASURED: dict[str, tuple[int, int, int, int, int, int]] = {
    "BTCUSDT": (1009, 657, 1008, 858, 966, 909),
    "ETHUSDT": (1058, 706, 1058, 930, 1035, 984),
    "BNBUSDT": (1048, 737, 1042, 915, 1021, 967),
    "SOLUSDT": (1134, 802, 1133, 1004, 1116, 1071),
    "XRPUSDT": (974, 673, 974, 843, 956, 913),
    "TRXUSDT": (776, 494, 774, 670, 768, 729),
    "ADAUSDT": (882, 603, 880, 757, 872, 825),
    "AVAXUSDT": (1177, 810, 1169, 1009, 1130, 1078),
    "DOTUSDT": (894, 609, 889, 765, 877, 844),
    "LINKUSDT": (1008, 662, 1007, 860, 978, 925),
    "LTCUSDT": (981, 693, 970, 858, 954, 907),
    "BCHUSDT": (1055, 702, 1055, 918, 1039, 987),
    "XLMUSDT": (1096, 764, 1096, 966, 1076, 1031),
    "ATOMUSDT": (940, 655, 940, 825, 912, 861),
    "NEARUSDT": (1001, 685, 999, 881, 983, 946),
    "APTUSDT": (1202, 845, 1197, 1046, 1169, 1110),
    "SUIUSDT": (961, 600, 958, 814, 952, 908),
    "ARBUSDT": (920, 599, 920, 792, 899, 851),
    "OPUSDT": (911, 641, 910, 797, 895, 858),
    "UNIUSDT": (960, 614, 960, 793, 951, 904),
}
BARS_4H = 16
BARS_1D = 96
BARS_7D = 672


def _rate(hit: int, n: int) -> float:
    return round(100.0 * hit / n, 1)


def pooled_rates() -> tuple[float, float, float, int, int, int]:
    """4h rate, 1-day rate, 7-day rate, then the 1-day, 4-hour and 7-day samples."""
    n4 = sum(row[0] for row in MEASURED.values())
    hit4 = sum(row[1] for row in MEASURED.values())
    n1 = sum(row[2] for row in MEASURED.values())
    hit1 = sum(row[3] for row in MEASURED.values())
    n7 = sum(row[4] for row in MEASURED.values())
    hit7 = sum(row[5] for row in MEASURED.values())
    return _rate(hit4, n4), _rate(hit1, n1), _rate(hit7, n7), n1, n4, n7


def _remain(deadline_ms: int, now_ms: int) -> tuple[bool, str]:
    left = int(deadline_ms) - int(now_ms)
    if left <= 0:
        return False, "đã qua"
    minutes = left // 60_000
    if minutes < 60:
        return True, f"còn {minutes} phút"
    hours, mins = divmod(minutes, 60)
    if hours < 48:
        return True, f"còn {hours} giờ {mins} phút"
    days, hours = divmod(hours, 24)
    return True, f"còn {days} ngày {hours} giờ"


def level_for(
    symbol: str,
    ts: list[int],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    mark: float,
    now_ms: int,
) -> dict:
    """One coin. `entry` is the price the study measured a return to."""
    sym = symbol.upper()
    measured = MEASURED.get(sym)
    if measured is None:
        h4, d1, d7, n1, n4, n7 = pooled_rates()
        source = "pooled"
    else:
        n4, hit4, n1, hit1, n7, hit7 = measured
        h4, d1, d7 = _rate(hit4, n4), _rate(hit1, n1), _rate(hit7, n7)
        source = "coin"
    empty = {
        "symbol": sym,
        "has_signal": False,
        "prob_4h": h4,
        "prob_1d": d1,
        "prob_7d": d7,
        "prob_source": source,
        "n_4h": n4,
        "n_1d": n1,
        "n_7d": n7,
        "sample": n1,
    }
    if len(closes) < 2 or len(closes) != len(ts):
        return empty
    opens = list(closes)
    rows = setups(ts, opens, highs, lows, closes)
    if not rows:
        return empty
    setup = flipped(rows[-1])
    last_ts = int(ts[-1])
    bars_ago = max(0, int((last_ts - int(setup.signal_ts)) / BAR_MS))
    deadline_4h = int(setup.signal_ts) + BARS_4H * BAR_MS
    deadline_1d = int(setup.signal_ts) + BARS_1D * BAR_MS
    deadline_7d = int(setup.signal_ts) + BARS_7D * BAR_MS
    open_4h, text_4h = _remain(deadline_4h, now_ms)
    open_1d, text_1d = _remain(deadline_1d, now_ms)
    open_7d, text_7d = _remain(deadline_7d, now_ms)
    entry = float(setup.entry)
    distance = None
    if mark > 0 and entry > 0:
        distance = (float(mark) - entry) / entry * 100.0
    return {
        "symbol": sym,
        "has_signal": True,
        "side": setup.side,
        "entry": entry,
        "signal_ts": int(setup.signal_ts),
        "bars_ago": bars_ago,
        "age_min": bars_ago * 15,
        "mark": float(mark) if mark > 0 else None,
        "distance_pct": distance,
        "prob_4h": h4,
        "prob_1d": d1,
        "prob_7d": d7,
        "prob_source": source,
        "n_4h": n4,
        "n_1d": n1,
        "n_7d": n7,
        "sample": n1,
        "deadline_4h": deadline_4h,
        "deadline_1d": deadline_1d,
        "deadline_7d": deadline_7d,
        "window_4h_open": open_4h,
        "window_1d_open": open_1d,
        "window_7d_open": open_7d,
        "window_4h": text_4h,
        "window_1d": text_1d,
        "window_7d": text_7d,
    }
