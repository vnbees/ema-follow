"""Download public 1m futures klines for the live symbol list.

Reads the date span from data/bt_klines_15m. Writes data/bt_klines_1m/{SYMBOL}.npz.
Does not call the trading API and does not touch the VPS.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from src.donchian.config import FIXED_SCAN_SYMBOLS

ROOT = Path(__file__).resolve().parents[1]
CACHE_15 = ROOT / "data" / "bt_klines_15m"
OUT = ROOT / "data" / "bt_klines_1m"
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"


def _span(symbol: str) -> tuple[int, int]:
    files = list(CACHE_15.glob(f"{symbol}_15m_*.csv"))
    path = max(files, key=lambda f: int(f.stem.split("_")[-1]) - int(f.stem.split("_")[-2]))
    start, end = (int(part) for part in path.stem.split("_")[-2:])
    return start, end


def _months(start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    start = datetime.fromtimestamp(start_ms / 1000, timezone.utc)
    end = datetime.fromtimestamp(end_ms / 1000, timezone.utc)
    y, m = start.year, start.month
    out = []
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _download(symbol: str, year: int, month: int) -> tuple[str, int, int, bytes | None]:
    name = f"{symbol}-1m-{year:04d}-{month:02d}.zip"
    dest = OUT / "zips" / symbol / name
    if dest.exists() and dest.stat().st_size > 0:
        return symbol, year, month, dest.read_bytes()
    url = f"{BASE}/{symbol}/1m/{name}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return symbol, year, month, None
        raise
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return symbol, year, month, payload


def _rows(payload: bytes) -> list[tuple[int, float, float, float, float]]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        raw = archive.read(archive.namelist()[0]).decode()
    rows = []
    for line in raw.splitlines():
        if not line or line[0].isalpha():
            continue
        parts = line.split(",")
        rows.append((int(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])))
    return rows


def _build(symbol: str, months: list[tuple[int, int]]) -> None:
    chunks = []
    for year, month in months:
        path = OUT / "zips" / symbol / f"{symbol}-1m-{year:04d}-{month:02d}.zip"
        if not path.exists():
            continue
        got = _rows(path.read_bytes())
        if got:
            chunks.append(got)
    if not chunks:
        raise RuntimeError(f"no 1m rows for {symbol}")
    rows = [row for chunk in chunks for row in chunk]
    rows.sort()
    uniq = []
    last = -1
    for row in rows:
        if row[0] == last:
            continue
        last = row[0]
        uniq.append(row)
    arr = np.array(uniq, dtype=np.float64)
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT / f"{symbol}.npz",
        ts=arr[:, 0].astype(np.int64),
        open=arr[:, 1],
        high=arr[:, 2],
        low=arr[:, 3],
        close=arr[:, 4],
    )
    print(f"built {symbol} minutes={len(uniq)}", flush=True)


def main() -> None:
    jobs = []
    spans = {}
    for symbol in FIXED_SCAN_SYMBOLS:
        start, end = _span(symbol)
        spans[symbol] = (start, end)
        jobs.extend((symbol, year, month) for year, month in _months(start, end))
    print(f"files {len(jobs)}", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(_download, *job) for job in jobs]
        for future in as_completed(futures):
            symbol, year, month, payload = future.result()
            done += 1
            if done % 50 == 0 or payload is None:
                state = "missing" if payload is None else "ok"
                print(f"{done}/{len(jobs)} {symbol} {year:04d}-{month:02d} {state}", flush=True)
    for symbol in FIXED_SCAN_SYMBOLS:
        _build(symbol, _months(*spans[symbol]))


if __name__ == "__main__":
    main()
