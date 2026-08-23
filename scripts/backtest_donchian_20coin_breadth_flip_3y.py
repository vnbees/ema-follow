#!/usr/bin/env python3
"""D20 breadth_flip — extended lookback 3 years (1095d). Paper only.

Fetches klines via slow REST (separate cache keys vs 365d files).
Does not modify live bot or existing backtest scripts/docs.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")

LOOKBACK_DAYS = 1095  # ~3 years
BAR_MS = 15 * 60 * 1000
MIN_BARS = 30_000  # ~312 days after prepare; drop illiquid/new listings
# Gentler than shared_d (0.5s/2s) — live bot shares IP / fapi weight
PAGE_SLEEP = 1.0
SYMBOL_SLEEP = 5.0
RATE_LIMIT_FILE = ROOT / "data" / "binance_rate_limit_until_ms"


def _wait_if_bot_rate_limited() -> None:
    """Defer BT fetch while live bot REST cooldown file is active."""
    if not RATE_LIMIT_FILE.exists():
        return
    try:
        until_ms = float(RATE_LIMIT_FILE.read_text().strip())
    except (OSError, ValueError):
        return
    now_ms = time.time() * 1000
    if until_ms > now_ms:
        wait_s = (until_ms - now_ms) / 1000.0 + 5.0
        print(f"  bot REST cooldown active — sleep {wait_s:.0f}s", flush=True)
        time.sleep(wait_s)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    hunt = _load_module("hunt_3y", ROOT / "scripts" / "backtest_hunt_pct_per_day.py")
    shared = _load_module("shared_3y", ROOT / "scripts" / "backtest_donchian_20coin_shared_d.py")
    bflip = _load_module("bflip_3y", ROOT / "scripts" / "backtest_donchian_20coin_breadth_flip.py")

    symbols = bflip.SYMBOLS_20
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last = (now_ms // BAR_MS) * BAR_MS
    wf = last - LOOKBACK_DAYS * 86400 * 1000
    ff = wf - (hunt.DONCHIAN_PERIOD + hunt.SLOPE_LOOKBACK + hunt.ATR_PERIOD + 5) * BAR_MS

    print(f"Lookback {LOOKBACK_DAYS}d · window from {datetime.fromtimestamp(wf/1000, tz=timezone.utc).date()}", flush=True)
    print(
        f"Fetching {len(symbols)} symbols — public klines only, "
        f"page_sleep={PAGE_SLEEP}s symbol_sleep={SYMBOL_SLEEP}s",
        flush=True,
    )
    # Override shared_d pacing for this run only (do not edit shared_d.py)
    shared.PAGE_SLEEP = PAGE_SLEEP
    shared.SYMBOL_SLEEP = SYMBOL_SLEEP

    dfs: dict = {}
    for i, sym in enumerate(symbols):
        _wait_if_bot_rate_limited()
        print(f"[{i + 1}/{len(symbols)}] {sym}", flush=True)
        raw = shared.fetch_klines_slow(sym, ff, last)
        if raw.empty:
            print(f"  skip {sym} — empty", flush=True)
            time.sleep(SYMBOL_SLEEP)
            continue
        df = hunt.prepare(raw)
        df = df[df["ts"] >= wf].copy().reset_index(drop=True)
        if len(df) < MIN_BARS:
            print(f"  skip {sym} — {len(df)} bars (<{MIN_BARS})", flush=True)
            time.sleep(SYMBOL_SLEEP)
            continue
        dfs[sym] = df
        print(f"  ready bars={len(df)}", flush=True)
        if i + 1 < len(symbols):
            time.sleep(SYMBOL_SLEEP)

    if len(dfs) < 10:
        print("Too few symbols with 3y data", flush=True)
        return 1

    # Common window info
    ts_sets = [set(df["ts"].tolist()) for df in dfs.values()]
    common = sorted(set.intersection(*ts_sets)) if ts_sets else []
    days = max((common[-1] - common[0]) / 86400000.0, 1e-9) if common else 0
    print(f"\nPool: {len(dfs)} symbols · common bars={len(common)} · ~{days:.0f} calendar days", flush=True)

    cfgs = [
        bflip.Cfg("D20_base", "không breadth (baseline)", "none"),
        bflip.Cfg("breadth_flip", "flip side (live default)", "flip"),
        bflip.Cfg("breadth_hard", "skip ngược vote", "hard"),
    ]

    print(f"run {len(cfgs)} configs...", flush=True)
    rows = bflip.run_all(hunt, dfs, cfgs)

    ref365 = {
        "D20_base": (+33.450, 49.9),
        "breadth_flip": (+22.567, 25.3),
        "breadth_hard": (+8.751, 19.0),
    }

    for st in rows:
        if "error" in st:
            print(f"  ERROR {st}", flush=True)
            continue
        print(
            f"  {st['name']:16s} %/d={st['pct_day']:+7.3f}% DD={st['maxdd']:5.1f}% "
            f"PF={st['pf']:.2f} WR={st['wr']:.1f}% n={st['n']:5d} t/d={st['trades_per_day']:.1f} "
            f"flip_ok={st['n_flip_ok']}",
            flush=True,
        )

    ok = [r for r in rows if "error" not in r]
    lines = [
        f"# D20 breadth — lookback **{LOOKBACK_DAYS}d (~3 năm)** vs 365d ref",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Script: `scripts/backtest_donchian_20coin_breadth_flip_3y.py`",
        f"- Interval **15m** · lookback **{LOOKBACK_DAYS}d** · capital **1000$** · fee 0.04%/side · 10x",
        f"- Pool: **{len(dfs)}** majors có đủ data · common window **~{days:.0f} ngày**",
        f"- Coins: {', '.join(sorted(dfs))}",
        "- Rule giống live: body ATR, pot_rr, margin 1%×size_mult, max_open 20, breadth flip/hard",
        f"- Fetch: public `/fapi/v1/klines` only · page_sleep={PAGE_SLEEP}s · symbol_sleep={SYMBOL_SLEEP}s · "
        "respect `data/binance_rate_limit_until_ms` if bot in cooldown",
        "",
        "| Config | %/ngày | MaxDD | PF | WR | n | t/d | flip_ok | pnlN/F | Note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for st in ok:
        lines.append(
            f"| `{st['name']}` | **{st['pct_day']:+.3f}%** | **{st['maxdd']:.1f}%** | {st['pf']:.2f} | "
            f"{st['wr']:.1f}% | {st['n']} | {st['trades_per_day']:.1f} | {st['n_flip_ok']} | "
            f"{st['pnl_natural']:+.0f}/{st['pnl_flip']:+.0f} | {st['note']} |"
        )

    lines += [
        "",
        "## So với backtest 365d (cùng pool logic, cửa sổ ngắn hơn)",
        "",
        "| Config | 365d %/ngày | 365d MaxDD | 3y %/ngày | 3y MaxDD | Δ%/ngày | ΔMaxDD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for st in ok:
        ref = ref365.get(st["name"])
        if not ref:
            continue
        d_pct = st["pct_day"] - ref[0]
        d_dd = st["maxdd"] - ref[1]
        lines.append(
            f"| `{st['name']}` | {ref[0]:+.3f}% | {ref[1]:.1f}% | **{st['pct_day']:+.3f}%** | "
            f"**{st['maxdd']:.1f}%** | {d_pct:+.3f} | {d_dd:+.1f}pp |"
        )

    lines += ["", "Paper only — không ảnh hưởng bot live.", ""]
    out = DOCS / f"backtest_MULTI_donchian_20major_breadth_flip_15m_{LOOKBACK_DAYS}d.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
