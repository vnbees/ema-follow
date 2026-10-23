#!/usr/bin/env python3
"""Replay predict5m 5m signals; report the longest 1-2-4-8 losing streak.

Unlimited wallet (no 1h pause) so the max doubling is the raw signal streak.
One open bet, BTC then ETH then BNB. Matches live evaluate-closed / enter-next-open.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.predict5m.config import KLINE_LIMIT, M5_MS, SYMBOLS  # noqa: E402
from src.predict5m.engine import (  # noqa: E402
    after_result,
    pick_first,
    resolve_window,
    signal_on_close,
    stake_usdt,
)

VN = timezone(timedelta(hours=7))
START = datetime(2026, 9, 28, 7, 0, tzinfo=VN)
END = datetime.now(VN)
FAPI = "https://fapi.binance.com/fapi/v1/klines"
WARM_BARS = KLINE_LIMIT + 10
CACHE = ROOT / "data" / "bt_klines_5m" / "predict5m"


def fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, VN).strftime("%d/%m %H:%M")


def fetch_5m(symbol: str, start_ms: int, end_ms: int) -> list[list]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_5m.json"
    if path.is_file():
        saved = json.loads(path.read_text())
        rows = [r for r in saved if start_ms <= int(r[0]) <= end_ms]
        if rows and int(rows[0][0]) <= start_ms + M5_MS and int(rows[-1][0]) >= end_ms - 2 * M5_MS:
            return rows
    rows: list[list] = []
    cur = start_ms
    while cur < end_ms:
        url = (
            f"{FAPI}?symbol={symbol}&interval=5m"
            f"&startTime={cur}&endTime={end_ms}&limit=1500"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "predict5m-bt/1"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            chunk = json.loads(resp.read())
        if not chunk:
            break
        rows.extend(chunk)
        nxt = int(chunk[-1][0]) + M5_MS
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.2)
    path.write_text(json.dumps(rows))
    return rows


def arrays(rows: list[list]):
    ts = np.array([int(r[0]) for r in rows], dtype=np.int64)
    o = np.array([float(r[1]) for r in rows])
    h = np.array([float(r[2]) for r in rows])
    l = np.array([float(r[3]) for r in rows])
    c = np.array([float(r[4]) for r in rows])
    close_ts = np.array([int(r[6]) for r in rows], dtype=np.int64)
    return ts, o, h, l, c, close_ts


def main() -> None:
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    fetch_from = start_ms - WARM_BARS * M5_MS
    print(
        f"window {START.strftime('%d/%m %H:%M')} → {END.strftime('%d/%m %H:%M')} +07",
        flush=True,
    )
    by_sym: dict[str, tuple] = {}
    index: dict[str, dict[int, int]] = {}
    for sym in SYMBOLS:
        rows = fetch_5m(sym, fetch_from, end_ms)
        arr = arrays(rows)
        by_sym[sym] = arr
        index[sym] = {int(t): i for i, t in enumerate(arr[0])}
        print(f"  {sym} {len(rows)} bars {fmt(int(arr[0][0]))} → {fmt(int(arr[0][-1]))}", flush=True)

    opens = sorted(
        {int(t) for arr in by_sym.values() for t in arr[0] if start_ms <= int(t) <= end_ms}
    )
    step = 0
    open_bet: dict | None = None
    trades: list[dict] = []
    streak: list[dict] = []
    worst: list[dict] = []
    max_step = 0

    for t in opens:
        if open_bet is not None and t >= int(open_bet["entry_ts"]) + M5_MS:
            sym = open_bet["symbol"]
            ts, o, h, l, c, close_ts = by_sym[sym]
            i = index[sym].get(int(open_bet["entry_ts"]))
            if i is None:
                open_bet = None
            else:
                won, reason = resolve_window(open_bet["side"], float(o[i]), float(c[i]))
                row = {
                    **open_bet,
                    "won": won,
                    "reason": reason,
                    "settle_ts": t,
                    "entry_open": float(o[i]),
                    "entry_close": float(c[i]),
                }
                trades.append(row)
                if won:
                    if len(streak) > len(worst):
                        worst = list(streak)
                    streak = []
                    step = after_result(True, int(open_bet["double_step"]))
                else:
                    streak.append(row)
                    step = after_result(False, int(open_bet["double_step"]))
                    max_step = max(max_step, int(open_bet["double_step"]), step)
                    if len(streak) > len(worst):
                        worst = list(streak)
                open_bet = None

        if open_bet is not None:
            continue

        found = []
        for sym in SYMBOLS:
            ts, o, h, l, c, close_ts = by_sym[sym]
            i = index[sym].get(t)
            if i is None or i < 1:
                found.append(None)
                continue
            lo = max(0, i - KLINE_LIMIT)
            sig = signal_on_close(
                sym, ts[lo:i], o[lo:i], h[lo:i], l[lo:i], c[lo:i], close_ts[lo:i], t
            )
            found.append(sig)
        chosen = pick_first(found)
        if chosen is None:
            continue
        if int(chosen.entry_ts) != t:
            continue
        stake = stake_usdt(step)
        open_bet = {
            "symbol": chosen.symbol,
            "side": "UP" if chosen.side == 1 else "DOWN",
            "stake": stake,
            "double_step": step,
            "bar_ts": chosen.bar_ts,
            "entry_ts": chosen.entry_ts,
            "label": chosen.label,
        }

    if len(streak) > len(worst):
        worst = list(streak)

    n = len(trades)
    wins = sum(1 for x in trades if x["won"])
    max_losses = len(worst)
    max_stake = stake_usdt(max(0, max_losses - 1)) if max_losses else 1
    cycles: list[int] = []
    cur = 0
    for x in trades:
        if x["won"]:
            cycles.append(cur)
            cur = 0
        else:
            cur += 1
    if cur:
        cycles.append(cur)
    lose_only = [k for k in cycles if k > 0]
    cnt = Counter(cycles)
    avg_cycle = sum(cycles) / len(cycles) if cycles else 0.0
    med_cycle = sorted(cycles)[len(cycles) // 2] if cycles else 0
    avg_lose = sum(lose_only) / len(lose_only) if lose_only else 0.0
    peak_stakes = [stake_usdt(k - 1) if k else 1 for k in cycles]
    avg_peak = sum(peak_stakes) / len(peak_stakes) if peak_stakes else 0.0
    cap8 = sum(stake_usdt(i) for i in range(8))

    print()
    print(f"bets={n}  win={wins}  lose={n - wins}  wr={100.0 * wins / n:.1f}%" if n else "bets=0")
    print(f"capital for 8-loss streak (1+2+…+128)={cap8:.0f} USDT")
    print(f"max consecutive losses={max_losses}  max stake placed={max_stake:.0f} USDT")
    print(
        f"cycles={len(cycles)}  avg losses before win={avg_cycle:.2f}  "
        f"median={med_cycle}  avg peak stake={avg_peak:.1f} USDT"
    )
    print(f"losing streaks only: n={len(lose_only)}  avg length={avg_lose:.2f}")
    print("how far each cycle went (consecutive losses, then win/reset):")
    for k in range(0, max(cnt) + 1 if cnt else 1):
        n_k = cnt.get(k, 0)
        reached = sum(1 for x in cycles if x >= k)
        pct = 100.0 * n_k / len(cycles) if cycles else 0.0
        pct_r = 100.0 * reached / len(cycles) if cycles else 0.0
        stake = stake_usdt(k - 1) if k else 1
        print(
            f"  chuỗi {k}: {n_k} vòng ({pct:.1f}%)  "
            f"đạt ≥{k}: {reached} ({pct_r:.1f}%)  "
            f"{'thắng lệnh 1' if k == 0 else f'cược cuối {stake:.0f} USDT'}"
        )
    if worst:
        print("longest losing streak:")
        for x in worst:
            print(
                f"  {fmt(x['entry_ts'])} {x['symbol']} {x['side']} "
                f"stake={x['stake']:.0f} {x['reason']}"
            )
        print(f"  {fmt(worst[0]['entry_ts'])} → {fmt(worst[-1]['entry_ts'])} +07")


if __name__ == "__main__":
    main()
