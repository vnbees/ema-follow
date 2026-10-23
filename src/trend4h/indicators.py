"""Wilder ATR/ADX and TradingView Supertrend. Pure Python, same as the hunt BT."""

from __future__ import annotations


def wilder(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    acc = sum(values[:period])
    out[period - 1] = acc / period
    for i in range(period, n):
        acc = acc - acc / period + values[i]
        out[i] = acc / period
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float | None]:
    n = len(closes)
    tr = [0.0] * n
    if n:
        tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return wilder(tr, period)


def adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    tr = [0.0] * n
    pdm = [0.0] * n
    mdm = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        mdm[i] = dn if dn > up and dn > 0 else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    str_ = wilder(tr, period)
    sp = wilder(pdm, period)
    sm = wilder(mdm, period)
    dxs: list[float] = []
    acc: float | None = None
    for i in range(n):
        a = str_[i]
        if a is None or a <= 0 or sp[i] is None or sm[i] is None:
            continue
        pdi = 100.0 * float(sp[i]) / a
        mdi = 100.0 * float(sm[i]) / a
        denom = pdi + mdi
        dx = 0.0 if denom <= 0 else 100.0 * abs(pdi - mdi) / denom
        dxs.append(dx)
        if len(dxs) == period:
            acc = sum(dxs) / period
            out[i] = acc
        elif acc is not None:
            acc = (acc * (period - 1) + dx) / period
            out[i] = acc
    return out


def supertrend(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 10,
    factor: float = 3.0,
) -> list[int]:
    n = len(closes)
    a = atr(highs, lows, closes, period)
    direction = [0] * n
    upper = [0.0] * n
    lower = [0.0] * n
    started = False
    for i in range(n):
        if a[i] is None:
            continue
        hl2 = 0.5 * (highs[i] + lows[i])
        bu = hl2 + factor * float(a[i])
        bl = hl2 - factor * float(a[i])
        if not started:
            upper[i], lower[i] = bu, bl
            direction[i] = 1 if closes[i] >= hl2 else -1
            started = True
            continue
        prev_u, prev_l = upper[i - 1], lower[i - 1]
        upper[i] = bu if bu < prev_u or closes[i - 1] > prev_u else prev_u
        lower[i] = bl if bl > prev_l or closes[i - 1] < prev_l else prev_l
        if direction[i - 1] == -1:
            direction[i] = 1 if closes[i] > upper[i] else -1
        else:
            direction[i] = -1 if closes[i] < lower[i] else 1
    return direction


def want_side(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    adx_min: float = 25.0,
    st_period: int = 10,
    st_factor: float = 3.0,
    adx_period: int = 14,
) -> list[int]:
    """Desired position after each close: -1 short, 0 flat (no new reverse), +1 long.

    Live matches the hunt simulator: only *open or reverse* when ADX >= adx_min.
    A 0 does not flatten an existing trade.
    """
    st = supertrend(highs, lows, closes, st_period, st_factor)
    adx_v = adx(highs, lows, closes, adx_period)
    out = [0] * len(closes)
    for i, value in enumerate(adx_v):
        if value is None or value < adx_min:
            continue
        out[i] = st[i]
    return out
