#!/usr/bin/env python3
"""Re-fetch 3y kline cache files to add volume + quote_volume.

Finds `data/bt_klines_15m/*_15m_1692828900000_*.csv` (20-major 3y window)
and refetches any file missing volume columns. Respects bot rate-limit cooldown file.

Usage:
  python scripts/refresh_bt_klines_volume_3y.py          # only stale (no volume)
  python scripts/refresh_bt_klines_volume_3y.py --force    # refetch all 3y files
  python scripts/refresh_bt_klines_volume_3y.py --symbol BTCUSDT
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "bt_klines_15m"
RATE_LIMIT_FILE = ROOT / "data" / "binance_rate_limit_until_ms"
THREE_Y_START_MS = 1692828900000
SYMBOL_SLEEP = 5.0


def _load_shared():
    path = ROOT / "scripts" / "backtest_donchian_20coin_shared_d.py"
    spec = importlib.util.spec_from_file_location("shared_vol", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["shared_vol"] = mod
    spec.loader.exec_module(mod)
    return mod


def _wait_if_bot_rate_limited() -> None:
    if not RATE_LIMIT_FILE.exists():
        return
    try:
        until_ms = float(RATE_LIMIT_FILE.read_text().strip())
    except (OSError, ValueError):
        return
    now_ms = time.time() * 1000
    if until_ms > now_ms:
        wait_s = (until_ms - now_ms) / 1000.0 + 5.0
        print(f"bot REST cooldown — sleep {wait_s:.0f}s", flush=True)
        time.sleep(wait_s)


def list_3y_cache_files() -> list[tuple[str, int, int, Path]]:
    out: list[tuple[str, int, int, Path]] = []
    for p in sorted(CACHE_DIR.glob(f"*_15m_{THREE_Y_START_MS}_*.csv")):
        parts = p.stem.split("_")
        if len(parts) != 4:
            continue
        sym, interval, start_s, end_s = parts
        if interval != "15m":
            continue
        out.append((sym, int(start_s), int(end_s), p))
    return out


def needs_refresh(path: Path, force: bool) -> bool:
    if force:
        return True
    if not path.exists():
        return True
    df = pd.read_csv(path, nrows=5)
    return not {"volume", "quote_volume"}.issubset(df.columns)


def main() -> int:
    parser = argparse.ArgumentParser(description="Add volume to 3y backtest kline cache")
    parser.add_argument("--force", action="store_true", help="refetch even if volume present")
    parser.add_argument("--symbol", action="append", help="only these symbols (repeatable)")
    args = parser.parse_args()

    shared = _load_shared()
    shared.PAGE_SLEEP = 1.0
    shared.SYMBOL_SLEEP = SYMBOL_SLEEP

    files = list_3y_cache_files()
    if args.symbol:
        want = {s.upper() for s in args.symbol}
        files = [t for t in files if t[0] in want]

    if not files:
        print("No 3y cache files found.", flush=True)
        return 1

    todo = [(sym, start, end, path) for sym, start, end, path in files if needs_refresh(path, args.force)]
    print(f"3y cache: {len(files)} files · refresh {len(todo)}", flush=True)
    if not todo:
        print("All files already have volume.", flush=True)
        return 0

    ok = 0
    for i, (sym, start_ms, end_ms, path) in enumerate(todo):
        _wait_if_bot_rate_limited()
        print(f"[{i + 1}/{len(todo)}] {sym} {path.name}", flush=True)
        df = shared.fetch_klines_slow(sym, start_ms, end_ms, force=True)
        if df.empty:
            print(f"  ERROR empty", flush=True)
            continue
        if not {"volume", "quote_volume"}.issubset(df.columns):
            print(f"  ERROR still no volume columns", flush=True)
            continue
        print(f"  ok bars={len(df)} vol_mean={df['volume'].mean():.1f}", flush=True)
        ok += 1
        if i + 1 < len(todo):
            time.sleep(SYMBOL_SLEEP)

    print(f"Done: {ok}/{len(todo)} refreshed", flush=True)
    return 0 if ok == len(todo) else 1


if __name__ == "__main__":
    raise SystemExit(main())
