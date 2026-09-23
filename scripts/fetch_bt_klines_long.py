#!/usr/bin/env python3
"""Fetch max-available Binance USDT-M 15m klines for SYMBOLS_20.

Merges with existing 3y cache when present. Writes:
  data/bt_klines_15m/{SYM}_15m_{first_ts}_{last_ts}.csv

Usage:
  .venv/bin/python scripts/fetch_bt_klines_long.py
  .venv/bin/python scripts/fetch_bt_klines_long.py --symbol BTCUSDT
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "bt_klines_15m"
THREE_Y_START_MS = 1692828900000
BAR_MS = 15 * 60 * 1000
SYMBOL_SLEEP = 3.0
EARLIEST_PROBE = int(datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

_bt = importlib.util.spec_from_file_location("bt", ROOT / "scripts/backtest_sr_rr_30d.py")
bt = importlib.util.module_from_spec(_bt)
assert _bt.loader is not None
sys.modules["bt"] = bt
_bt.loader.exec_module(bt)

_shared = importlib.util.spec_from_file_location("shared_vol", ROOT / "scripts/backtest_donchian_20coin_shared_d.py")
shared = importlib.util.module_from_spec(_shared)
assert _shared.loader is not None
sys.modules["shared_vol"] = shared
_shared.loader.exec_module(shared)

shared.PAGE_SLEEP = 0.35
shared.SYMBOL_SLEEP = SYMBOL_SLEEP


def earliest_ts(symbol: str) -> int | None:
    r = requests.get(
        "https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": symbol, "interval": "15m", "startTime": EARLIEST_PROBE, "limit": 1},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return int(data[0][0]) if data else None


def load_existing_3y(symbol: str) -> pd.DataFrame | None:
    files = sorted(
        CACHE_DIR.glob(f"{symbol}_15m_{THREE_Y_START_MS}_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return None
    df = pd.read_csv(files[0])
    if not {"volume", "quote_volume"}.issubset(df.columns):
        return None
    return df


def longest_existing(symbol: str) -> pd.DataFrame | None:
    best = None
    best_n = 0
    for p in CACHE_DIR.glob(f"{symbol}_15m_*.csv"):
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        if not {"ts", "volume", "quote_volume"}.issubset(df.columns):
            continue
        if len(df) > best_n:
            best, best_n = df, len(df)
    return best


def merge_frames(*frames: pd.DataFrame) -> pd.DataFrame:
    parts = [f for f in frames if f is not None and not f.empty]
    if not parts:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "quote_volume"])
    out = pd.concat(parts, ignore_index=True)
    out = out.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return out


def fetch_symbol(symbol: str, end_ms: int, force: bool = False) -> Path | None:
    first = earliest_ts(symbol)
    if first is None:
        print(f"  {symbol}: no listing", flush=True)
        return None
    first_d = datetime.fromtimestamp(first / 1000, tz=timezone.utc).date()
    print(f"  {symbol}: listing from {first_d}", flush=True)

    existing = longest_existing(symbol)
    if existing is not None and not force:
        ex0, ex1 = int(existing["ts"].iloc[0]), int(existing["ts"].iloc[-1])
        cover_ok = ex0 <= first + BAR_MS * 10 and ex1 >= end_ms - 2 * 86400 * 1000
        if cover_ok and len(existing) > 50_000:
            out_path = CACHE_DIR / f"{symbol}_15m_{ex0}_{ex1}.csv"
            if not out_path.exists():
                existing.to_csv(out_path, index=False)
            print(f"  {symbol}: already long bars={len(existing)} → skip fetch", flush=True)
            return out_path

    frames: list[pd.DataFrame] = []
    if existing is not None:
        frames.append(existing)
        ex0 = int(existing["ts"].iloc[0])
        ex1 = int(existing["ts"].iloc[-1])
        # prefix before existing
        if first < ex0 - BAR_MS:
            print(f"  {symbol}: fetch prefix {first} → {ex0}", flush=True)
            prefix = shared.fetch_klines_slow(symbol, first, ex0, force=True)
            frames.append(prefix)
        # suffix after existing
        if ex1 + BAR_MS < end_ms:
            print(f"  {symbol}: fetch suffix {ex1 + BAR_MS} → {end_ms}", flush=True)
            suffix = shared.fetch_klines_slow(symbol, ex1 + BAR_MS, end_ms, force=True)
            frames.append(suffix)
    else:
        print(f"  {symbol}: fetch full {first} → {end_ms}", flush=True)
        frames.append(shared.fetch_klines_slow(symbol, first, end_ms, force=True))

    # also try 3y merge if longest was something else
    y3 = load_existing_3y(symbol)
    if y3 is not None:
        frames.append(y3)

    merged = merge_frames(*frames)
    if merged.empty:
        print(f"  {symbol}: empty after merge", flush=True)
        return None
    t0, t1 = int(merged["ts"].iloc[0]), int(merged["ts"].iloc[-1])
    out_path = CACHE_DIR / f"{symbol}_15m_{t0}_{t1}.csv"
    merged.to_csv(out_path, index=False)
    days = (t1 - t0) / 86400000
    print(f"  {symbol}: saved bars={len(merged)} ~{days:.0f}d → {out_path.name}", flush=True)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", action="append", help="only these symbols")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    symbols = bt.SYMBOLS_20
    if args.symbol:
        want = {s.upper() for s in args.symbol}
        symbols = [s for s in symbols if s in want]

    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Fetch long 15m for {len(symbols)} symbols → end={end_ms}", flush=True)

    ok = 0
    for i, sym in enumerate(symbols):
        print(f"[{i + 1}/{len(symbols)}] {sym}", flush=True)
        try:
            path = fetch_symbol(sym, end_ms, force=args.force)
            if path is not None:
                ok += 1
        except Exception as exc:
            print(f"  ERROR {sym}: {exc}", flush=True)
        if i + 1 < len(symbols):
            time.sleep(SYMBOL_SLEEP)

    print(f"Done: {ok}/{len(symbols)}", flush=True)
    return 0 if ok == len(symbols) else 1


if __name__ == "__main__":
    raise SystemExit(main())
