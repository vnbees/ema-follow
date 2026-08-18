#!/usr/bin/env python3
"""3 coin vốn hoá lớn — regime switch shared 1k/10x/0.5%, cap lot."""

from __future__ import annotations

import importlib.util
import json
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
spec = importlib.util.spec_from_file_location("tp2", ROOT / "scripts" / "backtest_link_tp2_3tf.py")
tp2 = importlib.util.module_from_spec(spec)
sys.modules["tp2"] = tp2
spec.loader.exec_module(tp2)

rspec = importlib.util.spec_from_file_location("regime", ROOT / "scripts" / "backtest_link_regime_switch.py")
regime = importlib.util.module_from_spec(rspec)
sys.modules["regime"] = regime
rspec.loader.exec_module(regime)

FEE = regime.FEE
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CAPITAL = 1000.0
LEVERAGE = 10.0
SIZE_PCT = 0.005
MAX_LOT_CAPS = (5, 10)
WINDOWS = (90, 365)
MODES = ("regime", "long_only", "short_only")
# Top market cap perpetuals on Binance USDT-M (stable majors)
TOP_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT")


@dataclass
class CoinLot:
    symbol: str
    lot: tp2.Lot


CACHE_DIR = ROOT / "data" / "backtest_cache"


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{symbol}_{interval}_{start_ms}_{end_ms}.pkl"
    if cache_path.exists():
        return pd.read_pickle(cache_path)
    bar_ms = tp2.mtf.TF[interval]
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": str(cursor),
            "endTime": str(end_ms),
            "limit": "1500",
        }
        url = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "ema-rsi-mtf/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rows = json.loads(resp.read().decode())
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 5:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
        time.sleep(0.15)
        if not rows:
            break
        for row in rows:
            ts = int(row[0])
            if start_ms <= ts < end_ms:
                out.append(
                    {
                        "ts": ts,
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                    }
                )
        last_ts = int(rows[-1][0])
        nxt = last_ts + bar_ms
        if nxt <= cursor:
            break
        cursor = nxt
    df = pd.DataFrame(out).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df.to_pickle(cache_path)
    return df


def fetch_labeled_frames(symbol: str, lookback_days: int) -> dict[str, pd.DataFrame]:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_closed = (now_ms // tp2.mtf.TF["5m"]) * tp2.mtf.TF["5m"]
    window_from = last_closed - lookback_days * 24 * 3600 * 1000
    warmup_ms = {
        "5m": 7 * 24 * 3600 * 1000,
        "1h": 20 * 24 * 3600 * 1000,
        "4h": 40 * 24 * 3600 * 1000,
    }
    frames: dict[str, pd.DataFrame] = {}
    for interval in ("5m", "1h", "4h"):
        start = window_from - warmup_ms[interval]
        print(f"  {symbol} fetch {interval}...", flush=True)
        raw = fetch_klines(symbol, interval, start, last_closed)
        frames[interval] = tp2.label_tf_fast(raw)
    return frames


def slice_m5(symbol: str, frames: dict[str, pd.DataFrame], lookback_days: int) -> pd.DataFrame:
    last_closed = int(frames["5m"]["ts"].iloc[-1]) + tp2.mtf.TF["5m"]
    window_from = last_closed - lookback_days * 24 * 3600 * 1000
    m5 = frames["5m"]
    m5 = m5[m5["ts"] >= window_from].copy()
    m5 = tp2.mtf.attach_htf(m5, frames["1h"], "h1", tp2.mtf.TF["1h"])
    m5 = tp2.mtf.attach_htf(m5, frames["4h"], "h4", tp2.mtf.TF["4h"])
    m5 = m5.dropna(subset=["h1_dir", "h4_dir"]).copy()
    up = (m5["dir"] == "UP") & (m5["h1_dir"] == "UP") & (m5["h4_dir"] == "UP")
    down = (m5["dir"] == "DOWN") & (m5["h1_dir"] == "DOWN") & (m5["h4_dir"] == "DOWN")
    m5["aligned"] = np.select([up, down], ["TREND_UP", "TREND_DOWN"], default="NO_TREND")
    m5["symbol"] = symbol
    return m5


def _all_lots(books: dict[str, dict[str, list[CoinLot]]]) -> list[CoinLot]:
    out: list[CoinLot] = []
    for sym, book in books.items():
        out.extend(book["longs"])
        out.extend(book["shorts"])
    return out


def _mtm_portfolio(books: dict[str, dict[str, list[CoinLot]]], prices: dict[str, float]) -> float:
    total = 0.0
    for sym, book in books.items():
        px = prices.get(sym, 0.0)
        for cl in book["longs"] + book["shorts"]:
            lot = cl.lot
            if lot.side == "short":
                gross = (lot.entry - px) * lot.qty
            else:
                gross = (px - lot.entry) * lot.qty
            total += gross - (lot.entry + px) * lot.qty * FEE
    return total


def _locked_portfolio(books: dict[str, dict[str, list[CoinLot]]], lev: float) -> float:
    return sum(regime._margin(cl.lot, lev) for cl in _all_lots(books))


def _notional_portfolio(books: dict[str, dict[str, list[CoinLot]]]) -> float:
    return sum(cl.lot.entry * cl.lot.qty for cl in _all_lots(books))


def run_multi(
    m5_by_symbol: dict[str, pd.DataFrame],
    *,
    mode: str,
    max_lots: int,
    capital: float = CAPITAL,
    leverage: float = LEVERAGE,
    size_pct: float = SIZE_PCT,
    mmr: float = 0.004,
) -> dict:
    symbols = list(m5_by_symbol.keys())
    books: dict[str, dict[str, list[CoinLot]]] = {
        s: {"longs": [], "shorts": []} for s in symbols
    }
    last_prices: dict[str, float] = {s: float(m5_by_symbol[s].iloc[0].close) for s in symbols}
    fills: list[dict] = []
    rounds: list[dict] = []
    nid = 1
    cash = float(capital)
    halted = False
    lev = float(leverage)
    peak_eq = capital
    coin_pnl: dict[str, float] = {s: 0.0 for s in symbols}

    stats = {
        "mode": mode,
        "max_lots_cap": max_lots,
        "symbols": symbols,
        "long_adds": 0,
        "short_adds": 0,
        "long_skips": 0,
        "short_skips": 0,
        "cap_skips": 0,
        "max_long": 0,
        "max_short": 0,
        "max_notional": 0.0,
        "max_margin": 0.0,
        "max_dd": 0.0,
        "min_equity": capital,
        "max_equity": capital,
        "min_mtm": 0.0,
        "liquidated": False,
        "end_equity": capital,
        "n_rounds": 0,
        "round_wins": 0,
        "coin_pnl": coin_pnl,
    }

    events: list[tuple] = []
    for sym, m5 in m5_by_symbol.items():
        for row in m5.itertuples(index=False):
            events.append((int(row.ts), sym, row))
    events.sort(key=lambda x: (x[0], x[1]))

    def _equity() -> tuple[float, float]:
        mtm = _mtm_portfolio(books, last_prices)
        return cash + _locked_portfolio(books, lev) + mtm, mtm

    def _mark() -> None:
        nonlocal peak_eq
        eq, mtm = _equity()
        peak_eq = max(peak_eq, eq)
        stats["max_dd"] = min(stats["max_dd"], eq - peak_eq)
        stats["min_equity"] = min(stats["min_equity"], eq)
        stats["max_equity"] = max(stats["max_equity"], eq)
        stats["min_mtm"] = min(stats["min_mtm"], mtm)
        stats["max_notional"] = max(stats["max_notional"], _notional_portfolio(books))
        stats["max_margin"] = max(stats["max_margin"], _locked_portfolio(books, lev))

    def _close_side(sym: str, side: str, px: float, ts: int, reason: str) -> None:
        nonlocal cash, nid
        key = "longs" if side == "long" else "shorts"
        book = books[sym][key]
        if not book:
            return
        rnd_pnl = 0.0
        rnd_fee = 0.0
        for cl in book:
            fill = tp2._settle(cl.lot, px, ts, reason)
            rnd_pnl += fill.pnl
            rnd_fee += fill.fee
            cash += regime._margin(cl.lot, lev) + fill.pnl
            fills.append({"symbol": sym, "side": side, **fill.__dict__})
        coin_pnl[sym] += rnd_pnl
        rounds.append(
            {
                "symbol": sym,
                "side": side,
                "n_lots": len(book),
                "avg": regime._avg_entry([cl.lot for cl in book]),
                "exit_px": px,
                "pnl": rnd_pnl,
                "fee": rnd_fee,
                "reason": reason,
            }
        )
        stats["n_rounds"] += 1
        if rnd_pnl > 0:
            stats["round_wins"] += 1
        books[sym][key] = []
        peak_l = len(books[sym]["longs"])
        peak_s = len(books[sym]["shorts"])
        stats["max_long"] = max(stats["max_long"], peak_l)
        stats["max_short"] = max(stats["max_short"], peak_s)

    def _maybe_liq(sym: str, wick: float, ts: int) -> bool:
        nonlocal halted
        if halted:
            return True
        eq, _ = _equity()
        thresh = _notional_portfolio(books) * mmr
        if eq > thresh:
            return False
        for s in symbols:
            px = wick if s == sym else last_prices[s]
            for side in ("long", "short"):
                key = "longs" if side == "long" else "shorts"
                if books[s][key]:
                    _close_side(s, side, px, ts, "LIQUIDATED")
        stats["liquidated"] = True
        halted = True
        _mark()
        return True

    def _try_add(sym: str, side: str, px: float, ts: int) -> None:
        nonlocal nid, cash
        if mode == "long_only" and side == "short":
            return
        if mode == "short_only" and side == "long":
            return
        key = "longs" if side == "long" else "shorts"
        book = books[sym][key]
        if len(book) >= max_lots:
            stats["cap_skips"] += 1
            return
        lots = [cl.lot for cl in book]
        avg = regime._avg_entry(lots)
        if book and (
            (side == "long" and px >= avg - 1e-12) or (side == "short" and px <= avg + 1e-12)
        ):
            if side == "long":
                stats["long_skips"] += 1
            else:
                stats["short_skips"] += 1
            return
        eq_now, _ = _equity()
        notional = max(eq_now, 0.0) * size_pct
        notional = min(notional, cash * lev)
        if notional < 1e-6:
            stats["cap_skips"] += 1
            return
        margin = notional / lev
        if cash < margin - 1e-12:
            stats["cap_skips"] += 1
            return
        qty = notional / px
        lot = tp2.Lot(nid, side, ts, px, qty, 0.0)
        nid += 1
        cash -= margin
        book.append(CoinLot(sym, lot))
        books[sym][key] = book
        if side == "long":
            stats["long_adds"] += 1
        else:
            stats["short_adds"] += 1
        stats["max_long"] = max(stats["max_long"], len(books[sym]["longs"]))
        stats["max_short"] = max(stats["max_short"], len(books[sym]["shorts"]))

    for ts, sym, row in events:
        aligned = row.aligned
        px = float(row.close)
        is_red = float(row.close) < float(row.open)
        is_green = float(row.close) > float(row.open)
        last_prices[sym] = px

        if halted:
            _mark()
            continue

        if aligned == "TREND_DOWN" and books[sym]["longs"] and mode in ("regime", "long_only"):
            _close_side(sym, "long", px, ts, "TREND_DOWN")
        if aligned == "TREND_UP" and books[sym]["shorts"] and mode in ("regime", "short_only"):
            _close_side(sym, "short", px, ts, "TREND_UP")

        net = sum(cl.lot.qty for cl in books[sym]["longs"]) - sum(
            cl.lot.qty for cl in books[sym]["shorts"]
        )
        wick = float(row.low) if net > 0 else float(row.high) if net < 0 else px
        if _maybe_liq(sym, wick, ts):
            continue

        if mode == "regime":
            if aligned == "TREND_UP" and books[sym]["shorts"]:
                _close_side(sym, "short", px, ts, "REGIME_FLIP")
            if aligned == "TREND_DOWN" and books[sym]["longs"]:
                _close_side(sym, "long", px, ts, "REGIME_FLIP")

        _mark()

        if aligned == "TREND_UP" and is_red and mode in ("regime", "long_only"):
            _try_add(sym, "long", px, ts)
        elif aligned == "TREND_DOWN" and is_green and mode in ("regime", "short_only"):
            _try_add(sym, "short", px, ts)

        _mark()

    if not halted:
        for sym in symbols:
            m5 = m5_by_symbol[sym]
            last = m5.iloc[-1]
            last_ts, last_c = int(last.ts), float(last.close)
            last_prices[sym] = last_c
            if books[sym]["longs"]:
                _close_side(sym, "long", last_c, last_ts, "EOD_OPEN")
            if books[sym]["shorts"]:
                _close_side(sym, "short", last_c, last_ts, "EOD_OPEN")

    stats["end_equity"] = cash
    stats["pnl"] = cash - capital
    stats["pnl_pct"] = stats["pnl"] / capital * 100
    stats["fee"] = sum(f["fee"] for f in fills)
    stats["rounds"] = rounds
    closed = [r for r in rounds if r["reason"] != "EOD_OPEN"]
    stats["wr"] = (stats["round_wins"] / len(closed) * 100) if closed else 0.0
    stats["coin_pnl"] = dict(coin_pnl)
    return stats


def main() -> None:
    lookback = max(WINDOWS)
    print(f"Fetching top-3 symbols for {lookback}d...", flush=True)
    all_frames: dict[str, dict[str, pd.DataFrame]] = {}
    for sym in TOP_SYMBOLS:
        print(f"\n{sym}:", flush=True)
        all_frames[sym] = fetch_labeled_frames(sym, lookback)

    summary_lines = [
        "# Regime switch — 3 coin top vốn hoá (shared vốn)",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Coins: **{', '.join(TOP_SYMBOLS)}**",
        f"- Vốn **{CAPITAL:.0f} USDT dùng chung** · {LEVERAGE}x · {SIZE_PCT*100:.1f}% equity/lệnh · cap lot 5/10",
        "- Skip avg lời · đóng khi 3 khung đảo · NO_TREND giữ không add",
        "",
    ]

    link_frames = tp2.fetch_labeled_frames(lookback)

    for days in WINDOWS:
        print(f"\n=== {days}d portfolio ===", flush=True)
        m5_by_symbol = {sym: slice_m5(sym, all_frames[sym], days) for sym in TOP_SYMBOLS}
        px_lines = []
        for sym in TOP_SYMBOLS:
            m5 = m5_by_symbol[sym]
            first, last = m5.iloc[0], m5.iloc[-1]
            chg = (last.close / first.close - 1) * 100
            px_lines.append(f"  - {sym}: {first.close:.2f} → {last.close:.2f} ({chg:+.1f}%)")

        window_results: dict = {}
        for mode in MODES:
            for cap in MAX_LOT_CAPS:
                key = (mode, cap)
                print(f"  {mode} cap={cap}...", flush=True)
                st = run_multi(m5_by_symbol, mode=mode, max_lots=cap)
                window_results[key] = st
                print(
                    f"    equity={st['end_equity']:.2f} pnl={st['pnl']:+.2f} "
                    f"rounds={st['n_rounds']} dd={st['max_dd']:+.0f}"
                )

        # LINK-only baseline same window
        link_m5 = tp2.slice_m5(link_frames, days)
        link_st = {}
        for mode in MODES:
            for cap in MAX_LOT_CAPS:
                _, st = regime.run(link_m5, mode=mode, max_lots=cap)
                link_st[(mode, cap)] = st

        window_header = [
            f"## {days} ngày",
            "",
            "### Giá các coin",
            "",
            *px_lines,
            "",
            "### Portfolio 3 coin (vốn shared 1k)",
            "",
            "| Mode | Cap | Vốn cuối | PnL | % | Round | WR | Max DD | Phí |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for mode in MODES:
            for cap in MAX_LOT_CAPS:
                st = window_results[(mode, cap)]
                window_header.append(
                    f"| {regime.MODE_LABELS[mode]} | {cap} | **{st['end_equity']:.2f}** | "
                    f"{st['pnl']:+.2f} | {st['pnl_pct']:+.1f}% | {st['n_rounds']} | "
                    f"{st['wr']:.0f}% | {st['max_dd']:+.0f} | {st['fee']:.1f} |"
                )

        window_header += [
            "",
            "### LINK-only (cùng setup, so sánh)",
            "",
            "| Mode | Cap | Vốn cuối | PnL | % | Round | Max DD |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for mode in MODES:
            for cap in MAX_LOT_CAPS:
                st = link_st[(mode, cap)]
                window_header.append(
                    f"| {regime.MODE_LABELS[mode]} | {cap} | {st['end_equity']:.2f} | "
                    f"{st['pnl']:+.2f} | {st['pnl_pct']:+.1f}% | {st['n_rounds']} | {st['max_dd']:+.0f} |"
                )

        st10 = window_results[("regime", 10)]
        window_header += [
            "",
            "### PnL theo coin — Regime switch cap 10",
            "",
            "| Coin | PnL | Round |",
            "| --- | --- | --- |",
        ]
        for sym in TOP_SYMBOLS:
            sym_rounds = [r for r in st10["rounds"] if r["symbol"] == sym]
            window_header.append(
                f"| {sym} | {st10['coin_pnl'][sym]:+.2f} | {len(sym_rounds)} |"
            )
        window_header.append("")

        summary_lines += window_header

        detail = ROOT / "docs" / f"backtest_TOP3_regime_switch_{days}d.md"
        detail.write_text(
            "\n".join(summary_lines[:6] + window_header) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {detail}")

    out = ROOT / "docs" / "backtest_TOP3_regime_switch_90_365.md"
    out.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
