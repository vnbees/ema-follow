"""LazyBear SQZMOM_LB on closed bars. Gray dot = sqzOff."""

from __future__ import annotations

import math

LENGTH = 20
MULT_KC = 1.5


def _sma(arr: list[float], i: int, length: int) -> float:
    return sum(arr[i - length + 1 : i + 1]) / length


def _stdev(arr: list[float], i: int, length: int) -> float:
    """Pine `stdev` is biased (divide by n), not sample n-1."""
    w = arr[i - length + 1 : i + 1]
    m = sum(w) / length
    return math.sqrt(sum((x - m) ** 2 for x in w) / length)


def _linreg(src: list[float], i: int, length: int) -> float:
    y = src[i - length + 1 : i + 1]
    ln = length
    xm = (ln - 1) / 2.0
    ym = sum(y) / ln
    num = sum((k - xm) * (y[k] - ym) for k in range(ln))
    den = sum((k - xm) ** 2 for k in range(ln))
    return ym - (num / den) * xm + (num / den) * (ln - 1)


def squeeze(highs: list[float], lows: list[float], closes: list[float], length: int = LENGTH, mult_kc: float = MULT_KC):
    """Return (hist_color, dot_color, val) lists. None until warm."""
    n = len(closes)
    tr = [highs[0] - lows[0]]
    for i in range(1, n):
        tr.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    delta: list[float | None] = [None] * n
    for i in range(length - 1, n):
        hi = max(highs[i - length + 1 : i + 1])
        lo = min(lows[i - length + 1 : i + 1])
        delta[i] = closes[i] - (hi + lo + 2 * _sma(closes, i, length)) / 4.0

    hist: list[str | None] = [None] * n
    dot: list[str | None] = [None] * n
    val: list[float | None] = [None] * n
    prev = None
    for i in range(2 * length - 2, n):
        window = delta[i - length + 1 : i + 1]
        if any(x is None for x in window):
            continue
        cur = _linreg([float(x) for x in window], length - 1, length)
        val[i] = cur
        if prev is not None:
            if cur > 0:
                hist[i] = "lime" if cur > prev else "green"
            else:
                hist[i] = "red" if cur < prev else "maroon"
        prev = cur

        basis = _sma(closes, i, length)
        dev = mult_kc * _stdev(closes, i, length)
        upper_bb, lower_bb = basis + dev, basis - dev
        ma = basis
        rangema = _sma(tr, i, length)
        upper_kc = ma + rangema * mult_kc
        lower_kc = ma - rangema * mult_kc
        sqz_on = lower_bb > lower_kc and upper_bb < upper_kc
        sqz_off = lower_bb < lower_kc and upper_bb > upper_kc
        if not sqz_on and not sqz_off:
            dot[i] = "blue"
        elif sqz_on:
            dot[i] = "black"
        else:
            dot[i] = "gray"

    return hist, dot, val


def hist_matches_label(label: int, hist: str | None) -> bool:
    if hist is None:
        return False
    if label == 1:
        return hist in ("lime", "green")
    if label == -1:
        return hist in ("red", "maroon")
    return False
