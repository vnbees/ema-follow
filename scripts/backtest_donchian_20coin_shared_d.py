#!/usr/bin/env python3
"""Shared-wallet Donchian body_size_rr05 — ~20 majors like live bot (config D).

Rate-limit safe: disk cache + slow kline pagination + pause between symbols.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "bt_klines_15m"
DOCS = ROOT / "docs"
TZ_NAME = "Asia/Ho_Chi_Minh"

# Live-like major pool (20) — established listings, mirrors bot MAJOR list subset
SYMBOLS_20 = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "TRXUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "DOTUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "XLMUSDT",
    "ATOMUSDT",
    "NEARUSDT",
    "APTUSDT",
    "SUIUSDT",
    "ARBUSDT",
    "OPUSDT",
    "UNIUSDT",
]
SYMBOLS_5_REF = ["LINKUSDT", "HYPEUSDT", "SUIUSDT", "DOGEUSDT", "SOLUSDT"]

INTERVAL = "15m"
LOOKBACK_DAYS = 365
BAR_MS = 15 * 60 * 1000
# Live bot may share this IP — stay gentle on fapi weight
PAGE_SLEEP = 0.5
SYMBOL_SLEEP = 2.0
MIN_BARS = 8000  # ~83 days; prefer near-full year after prepare


def _load_hunt():
    path = ROOT / "scripts" / "backtest_hunt_pct_per_day.py"
    spec = importlib.util.spec_from_file_location("hunt_pct", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["hunt_pct"] = mod
    spec.loader.exec_module(mod)
    return mod


def fetch_klines_slow(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Paginated klines with backoff on 418/429 and disk cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{symbol}_{INTERVAL}_{start_ms}_{end_ms}.csv"
    if cache_path.exists() and cache_path.stat().st_size > 1000:
        df = pd.read_csv(cache_path)
        if len(df) >= 100:
            print(f"  cache hit {symbol} bars={len(df)}", flush=True)
            return df

    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": INTERVAL,
            "startTime": str(cursor),
            "endTime": str(end_ms),
            "limit": "1500",
        }
        url = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(params)
        rows = None
        for attempt in range(8):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "donchian-20coin-bt/1.0"})
                with urllib.request.urlopen(req, timeout=45) as resp:
                    rows = json.loads(resp.read().decode())
                break
            except urllib.error.HTTPError as exc:
                wait = min(120.0, 5.0 * (attempt + 1))
                if exc.code in (418, 429, 403):
                    print(f"  HTTP {exc.code} {symbol} — sleep {wait:.0f}s", flush=True)
                    time.sleep(wait)
                    continue
                if attempt < 7:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
            except Exception:
                if attempt < 7:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
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
                    }
                )
        nxt = int(rows[-1][0]) + BAR_MS
        if nxt <= cursor:
            break
        cursor = nxt

    if not out:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close"])
    df = pd.DataFrame(out).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df.to_csv(cache_path, index=False)
    print(f"  fetched {symbol} bars={len(df)} → {cache_path.name}", flush=True)
    return df


def load_universe(hunt, symbols: list[str], ff: int, wf: int, last: int) -> dict[str, pd.DataFrame]:
    dfs: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols):
        print(f"[{i + 1}/{len(symbols)}] {sym}", flush=True)
        raw = fetch_klines_slow(sym, ff, last)
        if raw.empty:
            print(f"  skip {sym} — empty", flush=True)
            time.sleep(SYMBOL_SLEEP)
            continue
        df = hunt.prepare(raw)
        df = df[df["ts"] >= wf].copy().reset_index(drop=True)
        if len(df) < MIN_BARS:
            print(f"  skip {sym} — only {len(df)} bars (<{MIN_BARS})", flush=True)
            time.sleep(SYMBOL_SLEEP)
            continue
        dfs[sym] = df
        print(f"  ready bars={len(df)}", flush=True)
        if i + 1 < len(symbols):
            time.sleep(SYMBOL_SLEEP)
    return dfs


def fmt_row(st: dict) -> str:
    return (
        f"| `{st['name']}` | {st.get('n_syms', '?')} | **{st['pct_day']:+.3f}%** | "
        f"{st['pct_month']:+.2f}% | {st['pct_year']:+.1f}% | **{st['net']:+.1f}** | "
        f"{st['maxdd']:.1f}% | {st['pf']:.2f} | {st['wipe']:.2f} | {st['wr']:.1f}% | "
        f"{st['n']} | {st['trades_per_day']:.1f} | {st['note']} |"
    )


def main() -> None:
    from zoneinfo import ZoneInfo

    hunt = _load_hunt()
    Cfg = hunt.Cfg
    shared_backtest = hunt.shared_backtest

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last = (now_ms // BAR_MS) * BAR_MS
    wf = last - LOOKBACK_DAYS * 86400 * 1000
    ff = wf - (hunt.DONCHIAN_PERIOD + hunt.SLOPE_LOOKBACK + hunt.ATR_PERIOD + 5) * BAR_MS

    need = sorted(set(SYMBOLS_20) | set(SYMBOLS_5_REF))
    print(f"Loading {len(need)} symbols (cache + slow REST)...", flush=True)
    all_dfs = load_universe(hunt, need, ff, wf, last)

    dfs20 = {s: all_dfs[s] for s in SYMBOLS_20 if s in all_dfs}
    dfs5 = {s: all_dfs[s] for s in SYMBOLS_5_REF if s in all_dfs}
    print(f"\n20-pool ready: {len(dfs20)}/{len(SYMBOLS_20)} → {', '.join(dfs20)}", flush=True)
    print(f"5-ref ready: {len(dfs5)}/{len(SYMBOLS_5_REF)} → {', '.join(dfs5)}", flush=True)

    cfgs = [
        (
            dfs5,
            Cfg("D5_ref", "ref: 5 coin, margin 1%, max_open10", margin_pct=0.01, max_open=10),
        ),
        (
            dfs20,
            Cfg("D20_like_bot", "20 majors, margin 1%, max_open20 (live-like)", margin_pct=0.01, max_open=20),
        ),
        (
            dfs20,
            Cfg("D20_max10", "20 majors, margin 1%, max_open10 (slot cap)", margin_pct=0.01, max_open=10),
        ),
        (
            dfs20,
            Cfg("A20_half", "20 majors, margin 0.5%, max_open20", margin_pct=0.005, max_open=20),
        ),
    ]

    rows = []
    for dfs, cfg in cfgs:
        if len(dfs) < 3:
            print(f"skip {cfg.name} — not enough symbols", flush=True)
            continue
        print(f"run {cfg.name} on {len(dfs)} symbols...", flush=True)
        st = shared_backtest(dfs, cfg)
        st["n_syms"] = len(dfs)
        st["syms"] = ",".join(sorted(dfs))
        rows.append(st)
        if "error" in st:
            print(f"  ERROR {st['error']}", flush=True)
        else:
            print(
                f"  %/day={st['pct_day']:+.3f}% net={st['net']:+.1f} maxDD={st['maxdd']:.1f}% "
                f"PF={st['pf']:.2f} WR={st['wr']:.1f}% n={st['n']} t/d={st['trades_per_day']:.1f}",
                flush=True,
            )

    tz = ZoneInfo(TZ_NAME)
    lines = [
        "# Donchian body_size_rr05 — 20 majors shared wallet vs 5-coin ref",
        "",
        f"- Sinh luc: {datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Interval **15m** · lookback **{LOOKBACK_DAYS}d** · capital **1000$** chung · fee 0.04%/side · 10x",
        "- Filter: body ATR ∈ [0.3, 1.2], pot_rr ≥ 0.5, size_mult = clip(0.5+pot_rr, 0.5, 2)",
        "- Pool 20: " + ", ".join(SYMBOLS_20),
        f"- Co data: {', '.join(sorted(dfs20)) or '(none)'}",
        "- Ref 5: " + ", ".join(SYMBOLS_5_REF),
        "- Kline cache: `data/bt_klines_15m/` (tránh fetch lại / giảm rate-limit)",
        "- **Lưu ý:** `shared_backtest` align theo **intersection** timestamp → cửa sổ = coin ngắn nhất trong pool.",
        "",
        "| Config | #coin | %/ngày | %/tháng | %/năm | Net | MaxDD | PF | Wipe | WR | n | lệnh/ngày | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for st in rows:
        if "error" in st:
            lines.append(f"| `{st['name']}` | {st.get('n_syms', '?')} | ERROR | | | | | | | | | | {st.get('error')} |")
        else:
            lines.append(fmt_row(st))

    lines += [
        "",
        "## Ket luan",
        "",
    ]
    ok = [r for r in rows if "error" not in r]
    d5 = next((r for r in ok if r["name"] == "D5_ref"), None)
    d20 = next((r for r in ok if r["name"] == "D20_like_bot"), None)
    if d5 and d20:
        lines.append(
            f"- 5-coin ref D: **{d5['pct_day']:+.3f}%/ngày**, MaxDD {d5['maxdd']:.1f}%, "
            f"{d5['trades_per_day']:.1f} lệnh/ngày, PF {d5['pf']:.2f}"
        )
        lines.append(
            f"- 20-major live-like D: **{d20['pct_day']:+.3f}%/ngày**, MaxDD {d20['maxdd']:.1f}%, "
            f"{d20['trades_per_day']:.1f} lệnh/ngày, PF {d20['pf']:.2f}"
        )
        ratio = d20["pct_day"] / d5["pct_day"] if abs(d5["pct_day"]) > 1e-9 else float("nan")
        lines.append(f"- Ty le %/ngày (20 / 5): **{ratio:.2f}x** (khong tuyen tinh voi so coin).")
    lines += [
        "",
        "Paper only — live top-N theo volume co the khac pool majors co dinh nay.",
        "",
    ]

    out = DOCS / "backtest_MULTI_donchian_20major_shared_D_15m_365d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
