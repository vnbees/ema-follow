#!/usr/bin/env python3
"""Find alt-coin universe with amplitude similar to live SYMBOLS_20, then BT sma34/144 v1.2.

Disjoint from bot-main majors → avoids same-symbol netting if run in parallel.
Does NOT modify live bot.

Pipeline:
  1) Profile ATR%/range of current 20 majors (cached 15m)
  2) Screen Binance USDT-M perps (liquidity + 1d amplitude match)
  3) Fetch 15m for selected alts
  4) Backtest sma34/144 v1.2 on multi windows vs majors baseline
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CACHE_DIR = ROOT / "data" / "bt_klines_15m"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
BAR_MS = 15 * 60 * 1000
DAY_MS = 86_400_000
PAGE_SLEEP = 0.35
SYMBOL_SLEEP = 2.0

MAJORS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "TRXUSDT", "ADAUSDT",
    "AVAXUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT", "BCHUSDT", "XLMUSDT", "ATOMUSDT",
    "NEARUSDT", "APTUSDT", "SUIUSDT", "ARBUSDT", "OPUSDT", "UNIUSDT",
]
STABLE_BASES = {
    "USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USDE", "USD1", "USDP", "EUR", "AEUR",
}
# noisy / levered wrappers — skip for amplitude peer set
SKIP_PREFIXES = ("1000", "1M", "1000000")
SKIP_EXACT = {"BTCDOMUSDT", "DEFIUSDT"}


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pvc = _load("pvc_alt", "scripts/backtest_price_vol_channel.py")
r4 = _load("r4_sma_alt", "scripts/research_beat_config_a_r4_sma.py")
shared = _load("shared_vol_alt", "scripts/backtest_donchian_20coin_shared_d.py")

shared.PAGE_SLEEP = PAGE_SLEEP
shared.SYMBOL_SLEEP = SYMBOL_SLEEP
CAPITAL = pvc.CAPITAL
LEVERAGE = pvc.LEVERAGE


def http_json(url: str, retries: int = 6):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sma34-alt-universe/1.0"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            wait = min(90.0, 4.0 * (attempt + 1))
            if exc.code in (418, 429, 403):
                print(f"  HTTP {exc.code} — sleep {wait:.0f}s", flush=True)
                time.sleep(wait)
                continue
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"failed GET {url}")


def best_cache(sym: str) -> Path | None:
    files = sorted(CACHE_DIR.glob(f"{sym}_15m_*.csv"), key=lambda p: p.stat().st_size, reverse=True)
    for p in files:
        try:
            df = pd.read_csv(p, nrows=3)
        except Exception:
            continue
        if {"ts", "close", "high", "low", "quote_volume"}.issubset(df.columns):
            return p
    return None


def atr_pct_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(period, min_periods=period).mean()
    return 100.0 * atr / df["close"].replace(0, np.nan)


def profile_15m(df: pd.DataFrame, last_ms: int, days: int = 365) -> dict:
    start = last_ms - days * DAY_MS
    x = df[(df["ts"] >= start) & (df["ts"] <= last_ms)].copy()
    if len(x) < 500:
        return {}
    atrp = atr_pct_series(x)
    hl = 100.0 * (x["high"] - x["low"]) / x["close"].replace(0, np.nan)
    logret = np.log(x["close"] / x["close"].shift(1)).dropna()
    rv = float(logret.std(ddof=0) * math.sqrt(365 * 96) * 100) if len(logret) > 50 else float("nan")
    qv = float(x["quote_volume"].mean()) if "quote_volume" in x.columns else float("nan")
    return {
        "atr_pct_med": float(atrp.median()),
        "atr_pct_p25": float(atrp.quantile(0.25)),
        "atr_pct_p75": float(atrp.quantile(0.75)),
        "hl_pct_med": float(hl.median()),
        "rv_ann": rv,
        "qv_15m_mean": qv,
        "bars": len(x),
    }


def resample_daily(df15: pd.DataFrame) -> pd.DataFrame:
    x = df15.copy()
    x["dt"] = pd.to_datetime(x["ts"], unit="ms", utc=True)
    g = x.set_index("dt").resample("1D")
    out = pd.DataFrame(
        {
            "ts": g["ts"].first(),
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum() if "volume" in x.columns else 0,
            "quote_volume": g["quote_volume"].sum() if "quote_volume" in x.columns else 0,
        }
    ).dropna(subset=["open", "high", "low", "close"])
    out["ts"] = out["ts"].astype("int64")
    return out.reset_index(drop=True)


def profile_majors(days: int = 365) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Return 15m profile (report) + 1d target band (screening vs alt daily)."""
    rows_15 = []
    rows_1d = []
    lasts = []
    for sym in MAJORS:
        p = best_cache(sym)
        if p is None:
            print(f"  major miss cache: {sym}", flush=True)
            continue
        df = pd.read_csv(p)
        last = int(df["ts"].max())
        lasts.append(last)
        pr15 = profile_15m(df, last, days=days)
        if pr15:
            rows_15.append({"symbol": sym, **pr15})
        start = last - days * DAY_MS
        d1 = resample_daily(df[(df["ts"] >= start) & (df["ts"] <= last)])
        pr1 = profile_daily(d1)
        if pr1:
            rows_1d.append({"symbol": sym, **pr1})
    if not rows_15 or not rows_1d:
        raise RuntimeError("no majors profiled")
    pdf = pd.DataFrame(rows_15).sort_values("atr_pct_med")
    ddf = pd.DataFrame(rows_1d)
    target = {
        # screening uses DAILY amplitude (same scale as alt screen)
        "atr_med": float(ddf["atr_pct_med"].median()),
        "atr_lo": float(ddf["atr_pct_med"].quantile(0.10)),
        "atr_hi": float(ddf["atr_pct_med"].quantile(0.90)),
        "hl_med": float(ddf["hl_pct_med"].median()),
        "hl_lo": float(ddf["hl_pct_med"].quantile(0.10)),
        "hl_hi": float(ddf["hl_pct_med"].quantile(0.90)),
        "qv_med": float(pdf["qv_15m_mean"].median()),
        "qv_min": float(pdf["qv_15m_mean"].quantile(0.10)),
        "last_ms": int(min(lasts)),
        "atr15_med": float(pdf["atr_pct_med"].median()),
        "atr15_lo": float(pdf["atr_pct_med"].quantile(0.10)),
        "atr15_hi": float(pdf["atr_pct_med"].quantile(0.90)),
    }
    return pdf, target, ddf


def list_perp_usdt() -> list[str]:
    data = http_json("https://fapi.binance.com/fapi/v1/exchangeInfo")
    out = []
    for s in data["symbols"]:
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("status") != "TRADING":
            continue
        sym = s["symbol"]
        base = s.get("baseAsset", "")
        if sym in MAJORS or sym in SKIP_EXACT:
            continue
        if base in STABLE_BASES:
            continue
        if any(sym.startswith(p) for p in SKIP_PREFIXES):
            continue
        if not sym.isascii() or not base.isascii():
            continue
        out.append(sym)
    return sorted(out)


def ticker_map() -> dict[str, dict]:
    rows = http_json("https://fapi.binance.com/fapi/v1/ticker/24hr")
    return {r["symbol"]: r for r in rows}


def fetch_klines_interval(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    out: list[dict] = []
    cursor = start_ms
    bar = {"1d": DAY_MS, "15m": BAR_MS}[interval]
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": str(cursor),
            "endTime": str(end_ms),
            "limit": "1500",
        }
        url = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(params)
        rows = http_json(url)
        time.sleep(PAGE_SLEEP)
        if not rows:
            break
        for r in rows:
            ts = int(r[0])
            if start_ms <= ts < end_ms:
                out.append(
                    {
                        "ts": ts,
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                        "quote_volume": float(r[7]),
                    }
                )
        nxt = int(rows[-1][0]) + bar
        if nxt <= cursor:
            break
        cursor = nxt
    if not out:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "quote_volume"])
    return pd.DataFrame(out).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def profile_daily(df: pd.DataFrame) -> dict:
    if len(df) < 60:
        return {}
    atrp = atr_pct_series(df)
    hl = 100.0 * (df["high"] - df["low"]) / df["close"].replace(0, np.nan)
    logret = np.log(df["close"] / df["close"].shift(1)).dropna()
    rv = float(logret.std(ddof=0) * math.sqrt(365) * 100) if len(logret) > 30 else float("nan")
    return {
        "atr_pct_med": float(atrp.median()),
        "hl_pct_med": float(hl.median()),
        "rv_ann": rv,
        "qv_day_mean": float(df["quote_volume"].mean()),
        "days": len(df),
    }


def amplitude_score(atr: float, hl: float, target: dict) -> float:
    """0 = perfect match to majors median band; lower is better."""
    atr_mid = 0.5 * (target["atr_lo"] + target["atr_hi"])
    hl_mid = 0.5 * (target["hl_lo"] + target["hl_hi"])
    # normalize by band width
    atr_w = max(target["atr_hi"] - target["atr_lo"], 1e-6)
    hl_w = max(target["hl_hi"] - target["hl_lo"], 1e-6)
    return abs(atr - atr_mid) / atr_w + abs(hl - hl_mid) / hl_w


def screen_alts(target: dict, top_liquidity: int = 80, pick: int = 20) -> tuple[pd.DataFrame, pd.DataFrame]:
    print("\n== list perps + 24h tickers ==", flush=True)
    symbols = list_perp_usdt()
    tickers = ticker_map()
    liq = []
    for sym in symbols:
        t = tickers.get(sym)
        if not t:
            continue
        qv = float(t.get("quoteVolume") or 0)
        if qv <= 0:
            continue
        liq.append((sym, qv))
    liq.sort(key=lambda x: x[1], reverse=True)
    candidates = [s for s, _ in liq[:top_liquidity]]
    print(f"  candidates by 24h qv: {len(candidates)} (from {len(liq)} perps)", flush=True)

    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - 400 * DAY_MS
    rows = []
    for i, sym in enumerate(candidates):
        print(f"  [{i + 1}/{len(candidates)}] daily profile {sym}", flush=True)
        try:
            df = fetch_klines_interval(sym, "1d", start_ms, end_ms)
        except Exception as exc:
            print(f"    skip {sym}: {exc}", flush=True)
            continue
        pr = profile_daily(df)
        if not pr or pr["days"] < 120:
            continue
        atr, hl = pr["atr_pct_med"], pr["hl_pct_med"]
        score = amplitude_score(atr, hl, target)
        qv24 = float(tickers[sym].get("quoteVolume") or 0)
        rows.append(
            {
                "symbol": sym,
                "score": score,
                "atr_pct_med": atr,
                "hl_pct_med": hl,
                "rv_ann": pr["rv_ann"],
                "qv_24h": qv24,
                "days": pr["days"],
            }
        )
        time.sleep(0.12)
    if not rows:
        raise RuntimeError("no amplitude peers found")
    all_df = pd.DataFrame(rows)

    def band_mask(df: pd.DataFrame, alo: float, ahi: float, hlo: float, hhi: float) -> pd.Series:
        return (
            (df["atr_pct_med"] >= alo)
            & (df["atr_pct_med"] <= ahi)
            & (df["hl_pct_med"] >= hlo)
            & (df["hl_pct_med"] <= hhi)
        )

    tight = all_df[
        band_mask(
            all_df,
            target["atr_lo"] * 0.85,
            target["atr_hi"] * 1.25,
            target["hl_lo"] * 0.85,
            target["hl_hi"] * 1.25,
        )
    ]
    print(f"  tight band matches={len(tight)}/{len(all_df)}", flush=True)
    if len(tight) >= pick:
        sdf = tight.sort_values(["score", "qv_24h"], ascending=[True, False])
    else:
        wide = all_df[
            band_mask(
                all_df,
                target["atr_lo"] * 0.55,
                target["atr_hi"] * 1.8,
                target["hl_lo"] * 0.55,
                target["hl_hi"] * 1.8,
            )
        ]
        print(f"  wide band matches={len(wide)}/{len(all_df)}", flush=True)
        base = wide if len(wide) >= max(8, pick // 2) else all_df
        sdf = base.sort_values(["score", "qv_24h"], ascending=[True, False])
    picked = sdf.head(pick).copy()
    return sdf, picked


def fetch_15m_pool(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    # align end to closed 15m
    end_ms = end_ms - (end_ms % BAR_MS)
    start_ms = end_ms - (days + 20) * DAY_MS  # warmup buffer
    out: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols):
        print(f"[{i + 1}/{len(symbols)}] fetch 15m {sym} (~{days}d)", flush=True)
        # reuse longest cache if covers window
        p = best_cache(sym)
        if p is not None:
            raw = pd.read_csv(p)
            if int(raw["ts"].min()) <= start_ms + 5 * DAY_MS and int(raw["ts"].max()) >= end_ms - DAY_MS:
                df = raw[(raw["ts"] >= start_ms) & (raw["ts"] < end_ms)].copy().reset_index(drop=True)
                if len(df) > 500:
                    out[sym] = df
                    print(f"  cache reuse bars={len(df)}", flush=True)
                    continue
        df = shared.fetch_klines_slow(sym, start_ms, end_ms)
        if df is None or df.empty or len(df) < 500:
            print(f"  skip {sym} — insufficient bars", flush=True)
            time.sleep(SYMBOL_SLEEP)
            continue
        out[sym] = df
        time.sleep(SYMBOL_SLEEP)
    return out


def load_pool_from_dfs(dfs: dict[str, pd.DataFrame], days: int) -> tuple[dict[str, pd.DataFrame], int]:
    if len(dfs) < 5:
        raise RuntimeError(f"need ≥5 symbols, got {len(dfs)}")
    last = min(int(d["ts"].max()) for d in dfs.values())
    wf = last - (days + 12) * DAY_MS
    trimmed = {}
    for sym, raw in dfs.items():
        df = raw[raw["ts"] >= wf - 300 * BAR_MS].copy().reset_index(drop=True)
        if len(df) < 500:
            continue
        trimmed[sym] = df
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in trimmed.values()]))
    if not common:
        raise RuntimeError("empty common timeline")
    eval_start = max(common[0], common[-1] - days * DAY_MS)
    # drop symbols with sparse coverage on common
    keep = {}
    for sym, df in trimmed.items():
        sub = df[df["ts"].isin(common)]
        if len(sub) >= len(common) * 0.98:
            keep[sym] = df
    if len(keep) < 5:
        # fallback: use intersection of those present
        keep = trimmed
    print(f"  pool={len(keep)} common_bars={len(common)} eval_days≈{(common[-1]-eval_start)/DAY_MS:.1f}", flush=True)
    return keep, eval_start


@dataclass
class DetailCfg:
    name: str = "sma34/144 v1.2"
    fast: int = 34
    slow: int = 144
    min_vol: float = 1.2
    min_pot_rr: float = 0.5
    margin_pct: float = 0.01
    max_open: int = 10
    top_k: int = 5
    size_mult_cap: float = 2.0
    ch_period: int = 20


def run_detail(cfg: DetailCfg, raw: dict, eval_start: int) -> dict:
    """Same logic as r4 sma pullback + soft Donchian TP, with per-coin stats."""
    p = r4.P(
        name=cfg.name,
        fast=cfg.fast,
        slow=cfg.slow,
        min_vol=cfg.min_vol,
        min_pot_rr=cfg.min_pot_rr,
        margin_pct=cfg.margin_pct,
        max_open=cfg.max_open,
        top_k=cfg.top_k,
        size_mult_cap=cfg.size_mult_cap,
        tp_mode="donchian",
        ch_period=cfg.ch_period,
    )
    dfs = {s: r4.enrich(df, p.fast, p.slow, p.ch_period) for s, df in raw.items()}
    common = sorted(set.intersection(*[set(d["ts"].tolist()) for d in dfs.values()]))
    indexed = {s: df.set_index("ts").loc[common] for s, df in dfs.items()}
    symbols = list(indexed)
    first = next((t for t in common if t >= eval_start), common[-1])
    days = max((common[-1] - first) / DAY_MS, 1e-9)

    cash = CAPITAL
    opens: list[dict] = []
    trades: list[dict] = []
    peak = CAPITAL
    maxdd = 0.0
    open_counts = []
    margin_books = []

    def equity(mark):
        eq = cash
        for t in opens:
            eq += t["margin"] + pvc.pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def close_trade(t, px, ts, reason: str):
        nonlocal cash
        pl = pvc.pnl(t["side"], t["entry"], px, t["qty"])
        cash += t["margin"] + pl
        if ts >= eval_start:
            trades.append(
                {
                    "pnl": pl,
                    "side": t["side"],
                    "sym": t["sym"],
                    "hold_h": (ts - t["entry_ts"]) / 3_600_000,
                    "reason": reason,
                }
            )

    for i, ts in enumerate(common):
        bar = {s: indexed[s].iloc[i] for s in symbols}
        mark = {s: float(bar[s]["close"]) for s in symbols}

        still = []
        for t in opens:
            b = bar[t["sym"]]
            hi, lo = float(b["high"]), float(b["low"])
            up, dn = float(b["ch_upper"]), float(b["ch_lower"])
            tp = up if t["side"] == "long" else dn
            if (t["side"] == "long" and hi >= tp) or (t["side"] == "short" and lo <= tp):
                close_trade(t, tp, ts, "tp")
            else:
                still.append(t)
        opens = still

        if ts >= eval_start:
            open_counts.append(len(opens))
            margin_books.append(sum(t["margin"] for t in opens))

        if ts < eval_start:
            continue

        cands = []
        for s in symbols:
            if any(t["sym"] == s for t in opens):
                continue
            b = bar[s]
            if np.isnan(b.get("atr", np.nan)) or np.isnan(b.get("vol_ratio", np.nan)):
                continue
            if np.isnan(b.get("sma_f", np.nan)) or np.isnan(b.get("sma_s", np.nan)):
                continue
            if float(b["vol_ratio"]) < p.min_vol:
                continue
            px, o = float(b["close"]), float(b["open"])
            hi, lo = float(b["high"]), float(b["low"])
            sma_f, sma_s = float(b["sma_f"]), float(b["sma_s"])
            up, dn = float(b["ch_upper"]), float(b["ch_lower"])
            side = None
            if sma_f > sma_s:
                if (lo <= sma_f <= hi) and px >= sma_f and px > o:
                    side = "long"
            elif sma_f < sma_s:
                if (lo <= sma_f <= hi) and px <= sma_f and px < o:
                    side = "short"
            if side is None:
                continue
            tp = up if side == "long" else dn
            sl = dn if side == "long" else up
            pot_rr = abs(tp - px) / max(abs(px - sl), 1e-12)
            if pot_rr < p.min_pot_rr:
                continue
            cands.append(
                {
                    "sym": s,
                    "side": side,
                    "entry": px,
                    "sl": sl,
                    "tp": tp,
                    "pot_rr": pot_rr,
                    "tp_mode": "donchian",
                    "entry_ts": ts,
                }
            )
        cands.sort(key=lambda x: x["pot_rr"], reverse=True)
        for cand in cands[: p.top_k]:
            if len(opens) >= p.max_open:
                break
            if any(t["sym"] == cand["sym"] for t in opens):
                continue
            eq = max(cash + sum(t["margin"] for t in opens), 1.0)
            margin = min(eq * p.margin_pct * min(cand["pot_rr"], p.size_mult_cap), eq * 0.15, cash)
            if margin < 1:
                continue
            qty = margin * LEVERAGE / cand["entry"]
            cash -= margin
            opens.append({**cand, "qty": qty, "margin": margin})

        eq = equity(mark)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)

    if opens:
        last = {s: indexed[s].iloc[-1] for s in symbols}
        for t in opens:
            close_trade(t, float(last[t["sym"]]["close"]), common[-1], "eod")

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in trades)
    by = {}
    for t in trades:
        by.setdefault(t["sym"], []).append(t)
    per = []
    for sym, xs in by.items():
        w = [t for t in xs if t["pnl"] > 0]
        l = [t for t in xs if t["pnl"] <= 0]
        gw_s = sum(t["pnl"] for t in w)
        gl_s = abs(sum(t["pnl"] for t in l))
        per.append(
            {
                "symbol": sym,
                "n": len(xs),
                "tpd": len(xs) / days,
                "wr": 100 * len(w) / len(xs) if xs else 0,
                "pf": gw_s / gl_s if gl_s > 0 else (999 if gw_s > 0 else 0),
                "net": sum(t["pnl"] for t in xs),
                "long": sum(1 for t in xs if t["side"] == "long"),
                "short": sum(1 for t in xs if t["side"] == "short"),
                "hold": float(np.mean([t["hold_h"] for t in xs])),
            }
        )
    per.sort(key=lambda x: x["net"], reverse=True)
    return {
        "name": cfg.name,
        "n": len(trades),
        "wr": 100 * len(wins) / len(trades) if trades else 0,
        "pf": gw / gl if gl > 0 else 0,
        "ret_pct": net / CAPITAL * 100,
        "pct_day": net / CAPITAL * 100 / days,
        "maxdd": maxdd * 100,
        "tpd": len(trades) / days,
        "final_eq": cash,
        "net": net,
        "days": days,
        "n_syms": len(symbols),
        "symbols": symbols,
        "eval_start": first,
        "eval_end": common[-1],
        "long_n": sum(1 for t in trades if t["side"] == "long"),
        "short_n": sum(1 for t in trades if t["side"] == "short"),
        "long_pnl": sum(t["pnl"] for t in trades if t["side"] == "long"),
        "short_pnl": sum(t["pnl"] for t in trades if t["side"] == "short"),
        "avg_win": float(np.mean([t["pnl"] for t in wins])) if wins else 0,
        "avg_loss": float(np.mean([t["pnl"] for t in losses])) if losses else 0,
        "hold_mean": float(np.mean([t["hold_h"] for t in trades])) if trades else 0,
        "hold_med": float(np.median([t["hold_h"] for t in trades])) if trades else 0,
        "tp_n": sum(1 for t in trades if t["reason"] == "tp"),
        "eod_n": sum(1 for t in trades if t["reason"] == "eod"),
        "open_mean": float(np.mean(open_counts)) if open_counts else 0,
        "open_p50": float(np.median(open_counts)) if open_counts else 0,
        "open_p95": float(np.percentile(open_counts, 95)) if open_counts else 0,
        "open_max": int(max(open_counts)) if open_counts else 0,
        "margin_mean": float(np.mean(margin_books)) if margin_books else 0,
        "margin_p95": float(np.percentile(margin_books, 95)) if margin_books else 0,
        "per_coin": per,
    }


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M %Z")


def write_report(
    majors_prof: pd.DataFrame,
    target: dict,
    screen_all: pd.DataFrame,
    picked: pd.DataFrame,
    results: dict[str, dict],
    baseline: dict[str, dict],
) -> Path:
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# sma34/144 v1.2 — alternate universe (disjoint from bot majors)",
        "",
        f"- Generated: **{now}**",
        "- Script: `scripts/research_sma34_alt_universe.py`",
        "- Mục tiêu: pool **không trùng** 20 majors bot chính, biên độ (ATR%/HL%) gần majors, backtest kỹ sma34/144 v1.2",
        "",
        "## 1) Biên độ pool bot chính (365d)",
        "",
        f"- **15m** ATR% median: **{target['atr15_med']:.3f}%** (P10–P90: {target['atr15_lo']:.3f}–{target['atr15_hi']:.3f})",
        f"- **1d** ATR% median (dùng screen): **{target['atr_med']:.2f}%** (P10–P90: {target['atr_lo']:.2f}–{target['atr_hi']:.2f})",
        f"- **1d** HL% median: **{target['hl_med']:.2f}%** (P10–P90: {target['hl_lo']:.2f}–{target['hl_hi']:.2f})",
        "",
        "| Symbol | ATR% 15m | HL% 15m | RV ann% |",
        "| --- | ---: | ---: | ---: |",
    ]
    for _, r in majors_prof.sort_values("atr_pct_med").iterrows():
        lines.append(
            f"| {r['symbol']} | {r['atr_pct_med']:.3f} | {r['hl_pct_med']:.3f} | {r['rv_ann']:.1f} |"
        )

    lines += [
        "",
        "## 2) Screening alt peers",
        "",
        f"- Screened top liquid USDT-M perps (exclude majors/stables/1000x)",
        f"- Amplitude filter vs majors band; pick top {len(picked)} by score+liquidity",
        "",
        "### Selected pool",
        "",
        "| Rank | Symbol | score↓ | ATR% (1d) | HL% (1d) | 24h quote $ |",
        "| ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for i, (_, r) in enumerate(picked.iterrows(), 1):
        lines.append(
            f"| {i} | {r['symbol']} | {r['score']:.3f} | {r['atr_pct_med']:.2f} | "
            f"{r['hl_pct_med']:.2f} | {r['qv_24h']:,.0f} |"
        )
    lines += [
        "",
        "<details><summary>All amplitude-matched candidates</summary>",
        "",
        "| Symbol | score | ATR% | HL% | 24h qv |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for _, r in screen_all.iterrows():
        lines.append(
            f"| {r['symbol']} | {r['score']:.3f} | {r['atr_pct_med']:.2f} | "
            f"{r['hl_pct_med']:.2f} | {r['qv_24h']:,.0f} |"
        )
    lines += ["", "</details>", ""]

    lines += [
        "## 3) Backtest sma34/144 v1.2 (shared wallet $1k · 10x · fee 0.04%)",
        "",
        "Logic identical to majors detail report: SMA34/144 trend + wick bounce + vol≥1.2 + soft Donchian TP.",
        "",
        "### Summary vs majors baseline",
        "",
        "| Window | Pool | n_syms | Return | PF | WR | MaxDD | t/d | Final $ |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for days in sorted(results):
        r = results[days]
        b = baseline.get(days)
        lines.append(
            f"| {days}d | **ALT** | {r['n_syms']} | {r['ret_pct']:+.1f}% | {r['pf']:.4f} | "
            f"{r['wr']:.1f}% | {r['maxdd']:.1f}% | {r['tpd']:.2f} | ${r['final_eq']:.0f} |"
        )
        if b:
            lines.append(
                f"| {days}d | majors | {b['n_syms']} | {b['ret_pct']:+.1f}% | {b['pf']:.4f} | "
                f"{b['wr']:.1f}% | {b['maxdd']:.1f}% | {b['tpd']:.2f} | ${b['final_eq']:.0f} |"
            )

    for days in sorted(results):
        r = results[days]
        lines += [
            "",
            f"## Window {days}d · ALT pool · thực tế **{r['days']:.0f}d** · **{r['n_syms']} coin**",
            "",
            f"- Eval: `{fmt_ts(r['eval_start'])}` → `{fmt_ts(r['eval_end'])}`",
            f"- Pool: `{', '.join(r['symbols'])}`",
            "",
            "### Hiệu quả",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Return | {r['ret_pct']:+.1f}% |",
            f"| %/ngày | {r['pct_day']:+.3f}% |",
            f"| PF | {r['pf']:.4f} |",
            f"| WR | {r['wr']:.1f}% |",
            f"| MaxDD | {r['maxdd']:.1f}% |",
            f"| Final equity | ${r['final_eq']:.0f} |",
            f"| Net PnL | ${r['net']:+.0f} |",
            f"| Trades | {r['n']} |",
            f"| Trades / ngày | **{r['tpd']:.2f}** |",
            f"| Long / Short | {r['long_n']} / {r['short_n']} |",
            f"| Long PnL / Short PnL | ${r['long_pnl']:+.0f} / ${r['short_pnl']:+.0f} |",
            f"| Avg win / Avg loss | ${r['avg_win']:+.2f} / ${r['avg_loss']:+.2f} |",
            f"| Hold TB / median (giờ) | {r['hold_mean']:.1f}h / {r['hold_med']:.1f}h |",
            f"| Exit TP / EOD | {r['tp_n']} / {r['eod_n']} |",
            "",
            "### Vốn đang dùng",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Open TB / P50 / P95 / max | {r['open_mean']:.2f} / {r['open_p50']:.0f} / {r['open_p95']:.0f} / {r['open_max']} |",
            f"| Margin book TB | ${r['margin_mean']:.0f} |",
            f"| Margin book P95 | ${r['margin_p95']:.0f} |",
            "",
            "### Theo coin (sort net PnL)",
            "",
            "| Symbol | Trades | t/d | WR | PF | Net $ | L/S | Hold TB h |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for c in r["per_coin"]:
            lines.append(
                f"| {c['symbol']} | {c['n']} | {c['tpd']:.2f} | {c['wr']:.1f}% | {c['pf']:.2f} | "
                f"{c['net']:+.0f} | {c['long']}/{c['short']} | {c['hold']:.1f} |"
            )

    lines += [
        "",
        "## Kết luận vận hành song song",
        "",
        "- ALT pool **disjoint** majors → tránh netting cùng symbol trên cùng account hedge mode.",
        "- Vẫn nên sub-account / wallet riêng nếu chạy 2 bot (shared risk + dashboard tách).",
        "- Chọn ALT nếu PF/MaxDD/t-d ổn định trên ≥2 cửa sổ và per-coin không bị 1–2 coin kéo cả pool.",
        "",
    ]
    path = DOCS / "research_sma34_alt_universe.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="90,180,365")
    ap.add_argument("--pick", type=int, default=20)
    ap.add_argument("--top-liq", type=int, default=80)
    ap.add_argument("--skip-screen", action="store_true", help="reuse last picked list from csv")
    ap.add_argument("--skip-fetch", action="store_true")
    args = ap.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]
    max_days = max(windows)

    print("== profile majors ==", flush=True)
    majors_prof, target, majors_1d = profile_majors(365)
    print(
        f"  15m ATR% med={target['atr15_med']:.3f} | "
        f"1d ATR% med={target['atr_med']:.2f} band={target['atr_lo']:.2f}-{target['atr_hi']:.2f} "
        f"HL%={target['hl_lo']:.2f}-{target['hl_hi']:.2f}",
        flush=True,
    )
    print(majors_prof[["symbol", "atr_pct_med", "hl_pct_med"]].to_string(index=False), flush=True)
    print("--- majors 1d ---", flush=True)
    print(majors_1d[["symbol", "atr_pct_med", "hl_pct_med"]].to_string(index=False), flush=True)

    pick_path = DOCS / "research_sma34_alt_universe_picked.csv"
    all_path = DOCS / "research_sma34_alt_universe_screen.csv"
    if args.skip_screen and pick_path.exists():
        picked = pd.read_csv(pick_path)
        screen_all = pd.read_csv(all_path) if all_path.exists() else picked
        print(f"== reuse picked ({len(picked)}) ==", flush=True)
    else:
        print("== screen alts ==", flush=True)
        screen_all, picked = screen_alts(target, top_liquidity=args.top_liq, pick=args.pick)
        screen_all.to_csv(all_path, index=False)
        picked.to_csv(pick_path, index=False)
        print(picked[["symbol", "score", "atr_pct_med", "hl_pct_med", "qv_24h"]].to_string(index=False), flush=True)

    symbols = picked["symbol"].tolist()
    print(f"\n== fetch 15m for {len(symbols)} alts (need ~{max_days}d) ==", flush=True)
    if args.skip_fetch:
        raw_all = {}
        for sym in symbols:
            p = best_cache(sym)
            if p:
                raw_all[sym] = pd.read_csv(p)
        print(f"  loaded from cache: {len(raw_all)}", flush=True)
    else:
        raw_all = fetch_15m_pool(symbols, max_days)

    # baseline majors for same windows
    baseline: dict[str, dict] = {}
    results: dict[str, dict] = {}
    cfg = DetailCfg()
    for days in windows:
        print(f"\n=== ALT BT {days}d ===", flush=True)
        pool, es = load_pool_from_dfs(raw_all, days)
        # if too few, drop to available
        if len(pool) < 8:
            print(f"  WARN only {len(pool)} symbols — continue", flush=True)
        r = run_detail(cfg, pool, es)
        results[days] = r
        print(
            f"  ALT PF={r['pf']:.4f} WR={r['wr']:.1f}% ret={r['ret_pct']:+.1f}% "
            f"DD={r['maxdd']:.1f}% tpd={r['tpd']:.2f} n={r['n']} syms={r['n_syms']}",
            flush=True,
        )

        print(f"=== majors baseline {days}d ===", flush=True)
        try:
            mraw, mes, _ = pvc.load_pool(days)
            b = run_detail(cfg, mraw, mes)
            baseline[days] = b
            print(
                f"  MAJ PF={b['pf']:.4f} WR={b['wr']:.1f}% ret={b['ret_pct']:+.1f}% "
                f"DD={b['maxdd']:.1f}% tpd={b['tpd']:.2f} n={b['n']}",
                flush=True,
            )
        except Exception as exc:
            print(f"  majors baseline skip: {exc}", flush=True)

    path = write_report(majors_prof, target, screen_all, picked, results, baseline)
    print(f"\nWrote {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
