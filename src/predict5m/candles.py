"""TradingView All candlestick patterns — directional labels only."""
from __future__ import annotations

import numpy as np

PACKS = (
    "ALL_directional",
    "TV_defaults",
    "REVERSAL_pack",
    "CONTINUATION_pack",
)

# TradingView "All candlestick patterns" default ON (blue/red). Marubozu, LLS,
# Harami, Tweezer, Piercing, etc. are defval=false and must not change latch.
TV_DIRECTIONAL = ("AbandonedBaby", "DragonflyDoji", "Engulfing", "Hammer")


def ema(x, n):
    a = 2.0 / (n + 1)
    out = np.empty_like(x, dtype=float)
    out[:] = np.nan
    if len(x) < n:
        return out
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def sma(x, n):
    out = np.empty_like(x, dtype=float)
    out[:] = np.nan
    c = np.cumsum(x)
    out[n - 1 :] = (c[n - 1 :] - np.concatenate(([0], c[:-n]))) / n
    return out


def detect(o, h, l, c):
    n = len(c)
    body_hi = np.maximum(c, o)
    body_lo = np.minimum(c, o)
    body = body_hi - body_lo
    rng = h - l
    up_sh = h - body_hi
    dn_sh = body_lo - l
    white = c > o
    black = c < o
    hl2 = 0.5 * (h + l)
    body_avg = ema(body, 14)
    sma50 = sma(c, 50)
    sma200 = sma(c, 200)
    up_tr = c > sma50
    dn_tr = c < sma50
    up_tr2 = (c > sma50) & (sma50 > sma200)
    dn_tr2 = (c < sma50) & (sma50 < sma200)
    small = body < body_avg
    longb = body > body_avg
    has_up = up_sh > 0.05 * np.maximum(body, 1e-12)
    has_dn = dn_sh > 0.05 * np.maximum(body, 1e-12)
    is_doji = (rng > 0) & (body <= rng * 0.05)
    with np.errstate(divide="ignore", invalid="ignore"):
        sh_rel = (np.abs(up_sh - dn_sh) / dn_sh * 100 < 100) & (
            np.abs(dn_sh - up_sh) / up_sh * 100 < 100
        )
    shadow_eq = (up_sh == dn_sh) | sh_rel
    # TV gray labels: D (Doji), STW / STB (spinning tops)
    plain_doji = is_doji & shadow_eq & ~((up_sh <= body) | (dn_sh <= body))
    spin_sh = (rng > 0) & (dn_sh >= rng * 0.34) & (up_sh >= rng * 0.34) & ~is_doji
    spinning_w = spin_sh & white
    spinning_b = spin_sh & black
    # shift helpers
    def p(a, k=1):
        z = np.empty_like(a)
        z[:k] = False if a.dtype == bool else np.nan
        if a.dtype == bool:
            z[:k] = False
            z[k:] = a[:-k]
        else:
            z[:k] = np.nan
            z[k:] = a[:-k]
        return z

    o1, c1, h1, l1 = p(o), p(c), p(h), p(l)
    o2, c2, h2, l2 = p(o, 2), p(c, 2), p(h, 2), p(l, 2)
    bh1, bl1 = p(body_hi), p(body_lo)
    bh2, bl2 = p(body_hi, 2), p(body_lo, 2)
    mid1 = p(body / 2 + body_lo)
    mid2 = p(body / 2 + body_lo, 2)
    w1, b1 = p(white), p(black)
    w2, b2 = p(white, 2), p(black, 2)
    sm1, lg1 = p(small), p(longb)
    sm2, lg2 = p(small, 2), p(longb, 2)
    sm3, lg3 = p(small, 3), p(longb, 3)
    lg4 = p(longb, 4)
    w3, b3 = p(white, 3), p(black, 3)
    w4, b4 = p(white, 4), p(black, 4)
    o3, c3, h3, l3 = p(o, 3), p(c, 3), p(h, 3), p(l, 3)
    o4, c4, h4, l4 = p(o, 4), p(c, 4), p(h, 4), p(l, 4)
    up1, dn1 = p(up_tr), p(dn_tr)
    up2t, dn2t = p(up_tr, 2), p(dn_tr, 2)
    up4, dn4 = p(up_tr, 4), p(dn_tr, 4)
    dj1, dj2 = p(is_doji), p(is_doji, 2)
    hu1, hd1 = p(has_up), p(has_dn)

    ok = ~np.isnan(body_avg) & ~np.isnan(sma50) & (rng != 0)
    ok1 = ok & ~np.isnan(o1)
    ok2 = ok1 & ~np.isnan(o2)

    patterns = {}  # name -> (+1 long / -1 short) int8 array

    def put(name, side, mask):
        a = np.zeros(n, dtype=np.int8)
        a[mask & ok] = side
        patterns[name] = a

    # Engulfing
    put(
        "Engulfing",
        1,
        dn_tr & white & longb & b1 & sm1 & (c >= o1) & (o <= c1) & ((c > o1) | (o < c1)),
    )
    put(
        "Engulfing",
        -1,
        up_tr & black & longb & w1 & sm1 & (c <= o1) & (o >= c1) & ((c < o1) | (o > c1)),
    )
    # merge engulfing: two puts overwrite - need combine
    eng = np.zeros(n, dtype=np.int8)
    eng[dn_tr & white & longb & b1 & sm1 & (c >= o1) & (o <= c1) & ((c > o1) | (o < c1)) & ok] = 1
    eng[up_tr & black & longb & w1 & sm1 & (c <= o1) & (o >= c1) & ((c < o1) | (o > c1)) & ok] = -1
    patterns["Engulfing"] = eng

    ham = np.zeros(n, dtype=np.int8)
    ham_shape = small & (body > 0) & (body_lo > hl2) & (dn_sh >= 2 * body) & ~has_up
    ham[ham_shape & dn_tr & ok] = 1
    patterns["Hammer"] = ham
    hang = np.zeros(n, dtype=np.int8)
    hang[ham_shape & up_tr & ok] = -1
    patterns["HangingMan"] = hang

    star_shape = small & (body > 0) & (body_hi < hl2) & (up_sh >= 2 * body) & ~has_dn
    ss = np.zeros(n, dtype=np.int8)
    ss[star_shape & up_tr & ok] = -1
    patterns["ShootingStar"] = ss
    ih = np.zeros(n, dtype=np.int8)
    ih[star_shape & dn_tr & ok] = 1
    patterns["InvertedHammer"] = ih

    piercing = np.zeros(n, dtype=np.int8)
    piercing[dn1 & b1 & lg1 & white & (o <= l1) & (c > mid1) & (c < o1) & ok1] = 1
    patterns["Piercing"] = piercing
    dcc = np.zeros(n, dtype=np.int8)
    dcc[up1 & w1 & lg1 & black & (o >= h1) & (c < mid1) & (c > o1) & ok1] = -1
    patterns["DarkCloudCover"] = dcc

    harami = np.zeros(n, dtype=np.int8)
    harami[lg1 & b1 & dn1 & white & small & (h <= bh1) & (l >= bl1) & ok1] = 1
    harami[lg1 & w1 & up1 & black & small & (h <= bh1) & (l >= bl1) & ok1] = -1
    patterns["Harami"] = harami
    hcross = np.zeros(n, dtype=np.int8)
    hcross[lg1 & b1 & dn1 & is_doji & (h <= bh1) & (l >= bl1) & ok1] = 1
    hcross[lg1 & w1 & up1 & is_doji & (h <= bh1) & (l >= bl1) & ok1] = -1
    patterns["HaramiCross"] = hcross

    tweezer = np.zeros(n, dtype=np.int8)
    tweezer[
        dn1
        & (~is_doji | (has_up & has_dn))
        & (np.abs(l - l1) <= body_avg * 0.05)
        & b1
        & white
        & lg1
        & ok1
    ] = 1
    tweezer[
        up1
        & (~is_doji | (has_up & has_dn))
        & (np.abs(h - h1) <= body_avg * 0.05)
        & w1
        & black
        & lg1
        & ok1
    ] = -1
    patterns["Tweezer"] = tweezer

    ms = np.zeros(n, dtype=np.int8)
    ms[
        lg2
        & sm1
        & longb
        & dn_tr
        & b2
        & (bh1 < bl2)
        & white
        & (body_hi >= mid2)
        & (body_hi < bh2)
        & (bh1 < body_lo)
        & ok2
    ] = 1
    patterns["MorningStar"] = ms
    es = np.zeros(n, dtype=np.int8)
    es[
        lg2
        & sm1
        & longb
        & up_tr
        & w2
        & (bl1 > bh2)
        & black
        & (body_lo <= mid2)
        & (body_lo > bl2)
        & (bl1 > body_hi)
        & ok2
    ] = -1
    patterns["EveningStar"] = es

    mds = np.zeros(n, dtype=np.int8)
    mds[
        lg2
        & dj1
        & longb
        & dn_tr
        & b2
        & (bh1 < bl2)
        & white
        & (body_hi >= mid2)
        & (body_hi < bh2)
        & (bh1 < body_lo)
        & ok2
    ] = 1
    patterns["MorningDojiStar"] = mds
    eds = np.zeros(n, dtype=np.int8)
    eds[
        lg2
        & dj1
        & longb
        & up_tr
        & w2
        & (bl1 > bh2)
        & black
        & (body_lo <= mid2)
        & (body_lo > bl2)
        & (bl1 > body_hi)
        & ok2
    ] = -1
    patterns["EveningDojiStar"] = eds

    ds = np.zeros(n, dtype=np.int8)
    ds[dn_tr & b1 & lg1 & is_doji & (body_hi < bl1) & ok1] = 1
    ds[up_tr & w1 & lg1 & is_doji & (body_lo > bh1) & ok1] = -1
    patterns["DojiStar"] = ds

    maru = np.zeros(n, dtype=np.int8)
    maru[white & longb & (up_sh <= 0.05 * body) & (dn_sh <= 0.05 * body) & ok] = 1
    maru[black & longb & (up_sh <= 0.05 * body) & (dn_sh <= 0.05 * body) & ok] = -1
    patterns["Marubozu"] = maru

    dfly = np.zeros(n, dtype=np.int8)
    dfly[is_doji & (up_sh <= body) & ok] = 1
    patterns["DragonflyDoji"] = dfly
    gstone = np.zeros(n, dtype=np.int8)
    gstone[is_doji & (dn_sh <= body) & ok] = -1
    patterns["GravestoneDoji"] = gstone

    lls = np.zeros(n, dtype=np.int8)
    lls[(dn_sh > rng * 0.75) & (rng > 0) & ok] = 1
    patterns["LongLowerShadow"] = lls
    lus = np.zeros(n, dtype=np.int8)
    lus[(up_sh > rng * 0.75) & (rng > 0) & ok] = -1
    patterns["LongUpperShadow"] = lus

    tws = np.zeros(n, dtype=np.int8)
    no_up = rng * 0.05 > up_sh
    no_up1, no_up2 = p(no_up), p(no_up, 2)
    tws[
        longb
        & lg1
        & lg2
        & white
        & w1
        & w2
        & (c > c1)
        & (c1 > c2)
        & (o < c1)
        & (o > o1)
        & (o1 < c2)
        & (o1 > o2)
        & no_up
        & no_up1
        & no_up2
        & ok2
    ] = 1
    patterns["ThreeWhiteSoldiers"] = tws
    tbc = np.zeros(n, dtype=np.int8)
    no_dn = rng * 0.05 > dn_sh
    no_dn1, no_dn2 = p(no_dn), p(no_dn, 2)
    tbc[
        longb
        & lg1
        & lg2
        & black
        & b1
        & b2
        & (c < c1)
        & (c1 < c2)
        & (o > c1)
        & (o < o1)
        & (o1 > c2)
        & (o1 < o2)
        & no_dn
        & no_dn1
        & no_dn2
        & ok2
    ] = -1
    patterns["ThreeBlackCrows"] = tbc

    rw = np.zeros(n, dtype=np.int8)
    rw[up1 & (rng != 0) & (p(rng) != 0) & (l > h1) & ok1] = 1
    patterns["RisingWindow"] = rw
    fw = np.zeros(n, dtype=np.int8)
    fw[dn1 & (rng != 0) & (p(rng) != 0) & (h < l1) & ok1] = -1
    patterns["FallingWindow"] = fw

    rtm = np.zeros(n, dtype=np.int8)
    rtm[
        up4
        & lg4
        & w4
        & sm3
        & b3
        & (o3 < h4)
        & (c3 > l4)
        & sm2
        & b2
        & (o2 < h4)
        & (c2 > l4)
        & sm1
        & b1
        & (o1 < h4)
        & (c1 > l4)
        & longb
        & white
        & (c > c4)
        & ok
        & ~np.isnan(o4)
    ] = 1
    patterns["RisingThreeMethods"] = rtm
    ftm = np.zeros(n, dtype=np.int8)
    ftm[
        dn4
        & lg4
        & b4
        & sm3
        & w3
        & (o3 > l4)
        & (c3 < h4)
        & sm2
        & w2
        & (o2 > l4)
        & (c2 < h4)
        & sm1
        & w1
        & (o1 > l4)
        & (c1 < h4)
        & longb
        & black
        & (c < c4)
        & ok
        & ~np.isnan(o4)
    ] = -1
    patterns["FallingThreeMethods"] = ftm

    oneck = np.zeros(n, dtype=np.int8)
    oneck[
        dn_tr
        & b1
        & lg1
        & white
        & (o < c1)
        & small
        & (rng != 0)
        & (np.abs(c - l1) <= body_avg * 0.05)
        & ok1
    ] = -1
    patterns["OnNeck"] = oneck

    dtg = np.zeros(n, dtype=np.int8)
    dtg[lg2 & sm1 & dn_tr & b2 & (bh1 < bl2) & b1 & white & (body_hi <= bl2) & (body_hi >= bh1) & ok2] = -1
    patterns["DownsideTasukiGap"] = dtg
    utg = np.zeros(n, dtype=np.int8)
    utg[lg2 & sm1 & up_tr & w2 & (bl1 > bh2) & w1 & black & (body_lo >= bh2) & (body_lo <= bl1) & ok2] = 1
    patterns["UpsideTasukiGap"] = utg

    ab = np.zeros(n, dtype=np.int8)
    ab[dn2t & b2 & dj1 & (l2 > h1) & white & (h1 < l) & ok2] = 1
    ab[up2t & w2 & dj1 & (h2 < l1) & black & (l1 > h) & ok2] = -1
    patterns["AbandonedBaby"] = ab

    kicking = np.zeros(n, dtype=np.int8)
    maru_b = longb & (up_sh <= 0.05 * body) & (dn_sh <= 0.05 * body)
    kicking[p(maru_b & black) & maru_b & white & (h1 < l) & ok1] = 1
    kicking[p(maru_b & white) & maru_b & black & (l1 > h) & ok1] = -1
    patterns["Kicking"] = kicking

    # ALL directional (conflict → 0)
    all_sig = np.zeros(n, dtype=np.int8)
    conflict = np.zeros(n, dtype=bool)
    for a in patterns.values():
        both = (all_sig != 0) & (a != 0) & (np.sign(all_sig) != np.sign(a))
        all_sig[a != 0] = a[a != 0]
        all_sig[both] = 0
        conflict |= both
    patterns["ALL_directional"] = all_sig

    tv_def = np.zeros(n, dtype=np.int8)
    tv_conflict = np.zeros(n, dtype=bool)
    for name in TV_DIRECTIONAL:
        a = patterns[name]
        both = (tv_def != 0) & (a != 0) & (np.sign(tv_def) != np.sign(a))
        tv_def[a != 0] = a[a != 0]
        tv_def[both] = 0
        tv_conflict |= both
    patterns["TV_defaults"] = tv_def
    patterns["_TvConflict"] = tv_conflict.astype(np.int8)

    reversal = np.zeros(n, dtype=np.int8)
    for name in (
        "Engulfing",
        "Hammer",
        "HangingMan",
        "ShootingStar",
        "InvertedHammer",
        "Piercing",
        "DarkCloudCover",
        "Harami",
        "MorningStar",
        "EveningStar",
        "Tweezer",
        "DojiStar",
        "AbandonedBaby",
        "Kicking",
        "ThreeWhiteSoldiers",
        "ThreeBlackCrows",
    ):
        a = patterns[name]
        both = (reversal != 0) & (a != 0) & (np.sign(reversal) != np.sign(a))
        reversal[a != 0] = a[a != 0]
        reversal[both] = 0
    patterns["REVERSAL_pack"] = reversal

    cont = np.zeros(n, dtype=np.int8)
    for name in ("RisingWindow", "FallingWindow", "RisingThreeMethods", "FallingThreeMethods", "OnNeck", "UpsideTasukiGap", "DownsideTasukiGap"):
        a = patterns[name]
        both = (cont != 0) & (a != 0) & (np.sign(cont) != np.sign(a))
        cont[a != 0] = a[a != 0]
        cont[both] = 0
    patterns["CONTINUATION_pack"] = cont
    gray = (plain_doji | spinning_w | spinning_b) & ok
    patterns["_Doji"] = (plain_doji & ok).astype(np.int8)
    patterns["_Gray"] = gray.astype(np.int8)
    patterns["_Conflict"] = conflict.astype(np.int8)

    return patterns


def directional_on_bar(patterns: dict) -> np.ndarray:
    return patterns["TV_defaults"]


def latch_labels(raw: np.ndarray, clear: np.ndarray | None = None) -> np.ndarray:
    """Keep last blue/red until a new directional label.

    Any non-blue/red label (gray D/STW/STB, or blue+red conflict) clears the latch,
    including when it shares the bar with a directional pattern.
    """
    out = np.zeros(len(raw), dtype=np.int8)
    last = 0
    for i, v in enumerate(raw):
        if clear is not None and clear[i]:
            last = 0
        elif v != 0:
            last = int(v)
        out[i] = last
    return out


def latest_labels(o, h, l, c) -> np.ndarray:
    pat = detect(o, h, l, c)
    clear = (pat["_Gray"] != 0) | (pat["_TvConflict"] != 0)
    return latch_labels(directional_on_bar(pat), clear)
