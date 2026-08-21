#!/usr/bin/env python3
"""Donchian parallel-band trend + pullback entry + opposite-band SL — LINK clone.

Clone of backtest_link_donchian_parallel_trend.py (does not modify the original).

Logic (same entry as original):
1. Donchian upper / middle / lower (rolling high/low).
2. Upper & lower bands "parallel" when normalized slopes over SLOPE_LOOKBACK bars
   differ by less than PARALLEL_SLOPE_TOL (%/bar).
3. On transition parallel -> non-parallel: trend = up if close > middle else down.
4. After trend set: wait counter-trend candle (red in uptrend, green in downtrend).
   If bands still non-parallel at that bar -> enter long/short at close.
5. TP (same as original): long when high touches upper; short when low touches lower.
6. SL (new, symmetric to TP): long when low touches lower; short when high touches upper.
   Exit price = the band touched. Same bar both hit → SL first (pessimistic).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
SYMBOL = "LINKUSDT"
INTERVAL = "15m"          # "5m" hoặc "15m"
LOOKBACK_DAYS = 365
DONCHIAN_PERIOD = 20
SLOPE_LOOKBACK = 5
PARALLEL_SLOPE_TOL = 0.015  # %/bar — max |slope_upper - slope_lower|
CAPITAL = 1000.0
MARGIN_PCT = 0.005
LEVERAGE = 10.0
FEE = 0.0004
MAX_OPEN = 1

_INTERVAL_MS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
}


def fetch_klines(interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    bar_ms = _INTERVAL_MS[interval]
    out: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": interval,
            "startTime": str(cursor),
            "endTime": str(end_ms),
            "limit": "1500",
        }
        url = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "donchian-backtest/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rows = json.loads(resp.read().decode())
                break
            except Exception:
                if attempt < 5:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
        time.sleep(0.15)
        if not rows:
            break
        for r in rows:
            ts = int(r[0])
            if start_ms <= ts < end_ms:
                out.append({"ts": ts, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])})
        last_ts = int(rows[-1][0])
        nxt = last_ts + bar_ms
        if nxt <= cursor:
            break
        cursor = nxt
    return pd.DataFrame(out).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def compute_donchian(df: pd.DataFrame, period: int) -> pd.DataFrame:
    out = df.copy()
    out["dc_upper"] = out["high"].rolling(period, min_periods=period).max()
    out["dc_lower"] = out["low"].rolling(period, min_periods=period).min()
    out["dc_middle"] = (out["dc_upper"] + out["dc_lower"]) / 2.0
    return out


def _norm_slope(series: np.ndarray, i: int, lookback: int, ref_px: float) -> float:
    if i < lookback or ref_px <= 0:
        return 0.0
    return (series[i] - series[i - lookback]) / lookback / ref_px * 100.0


def add_parallel_flags(df: pd.DataFrame, lookback: int, tol: float) -> pd.DataFrame:
    out = df.copy()
    n = len(out)
    upper = out["dc_upper"].to_numpy()
    lower = out["dc_lower"].to_numpy()
    closes = out["close"].to_numpy()
    parallel = np.zeros(n, dtype=bool)
    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        ref = closes[i]
        su = _norm_slope(upper, i, lookback, ref)
        sl = _norm_slope(lower, i, lookback, ref)
        parallel[i] = abs(su - sl) <= tol
    out["bands_parallel"] = parallel
    prev = np.roll(parallel, 1)
    prev[0] = False
    out["parallel_exit"] = prev & (~parallel)
    return out


def _pnl(side: str, entry: float, exit_px: float, qty: float) -> tuple[float, float]:
    if side == "long":
        gross = (exit_px - entry) * qty
    else:
        gross = (entry - exit_px) * qty
    fee = (entry + exit_px) * qty * FEE
    return gross - fee, fee


def run(df: pd.DataFrame) -> tuple[list[dict], dict]:
    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    tss = df["ts"].to_numpy()
    upper = df["dc_upper"].to_numpy()
    lower = df["dc_lower"].to_numpy()
    middle = df["dc_middle"].to_numpy()
    parallel = df["bands_parallel"].to_numpy()
    parallel_exit = df["parallel_exit"].to_numpy()
    n = len(df)

    trend: str | None = None
    trend_ts: int | None = None
    waiting_entry = False
    opens_pos: list[dict] = []
    trades: list[dict] = []
    cash = CAPITAL
    nid = 1
    trend_events = 0
    skipped_parallel_entry = 0

    def locked_margin() -> float:
        return sum(t["margin"] for t in opens_pos)

    def equity(px: float) -> float:
        total = cash + locked_margin()
        for t in opens_pos:
            pnl, _ = _pnl(t["side"], t["entry"], px, t["qty"])
            total += pnl + t["margin"] - t["margin"]
        return total

    def close_trade(t: dict, exit_px: float, ts: int, reason: str) -> None:
        nonlocal cash
        pnl, fee = _pnl(t["side"], t["entry"], exit_px, t["qty"])
        cash += t["margin"] + pnl
        trades.append({**t, "exit_ts": ts, "exit_px": exit_px, "reason": reason, "pnl": pnl, "fee": fee})

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue

        px = float(closes[i])
        hi = float(highs[i])
        lo = float(lows[i])
        ts = int(tss[i])
        o = float(opens[i])
        up_band = float(upper[i])
        lo_band = float(lower[i])
        mid_band = float(middle[i])

        still_open: list[dict] = []
        for t in opens_pos:
            side = t["side"]
            # SL first (pessimistic if both bands touched on same bar)
            sl_hit = (side == "long" and lo <= lo_band) or (side == "short" and hi >= up_band)
            tp_hit = (side == "long" and hi >= up_band) or (side == "short" and lo <= lo_band)
            if sl_hit:
                exit_px = lo_band if side == "long" else up_band
                close_trade(t, exit_px, ts, "SL_BAND")
            elif tp_hit:
                exit_px = up_band if side == "long" else lo_band
                close_trade(t, exit_px, ts, "TP_BAND")
            else:
                still_open.append(t)
        opens_pos = still_open

        if parallel_exit[i]:
            trend = "up" if px > mid_band else "down"
            trend_ts = ts
            waiting_entry = True
            trend_events += 1

        if waiting_entry and trend is not None and len(opens_pos) < MAX_OPEN:
            is_red = px < o
            is_green = px > o
            counter = (trend == "up" and is_red) or (trend == "down" and is_green)
            if counter:
                if parallel[i]:
                    skipped_parallel_entry += 1
                else:
                    side = "long" if trend == "up" else "short"
                    eq = max(cash + locked_margin(), 0.0)
                    notional = min(eq * MARGIN_PCT * LEVERAGE, cash * LEVERAGE)
                    if notional >= 1e-6:
                        margin = notional / LEVERAGE
                        if cash >= margin - 1e-12:
                            qty = notional / px
                            cash -= margin
                            opens_pos.append(
                                {
                                    "id": nid,
                                    "side": side,
                                    "trend": trend,
                                    "trend_ts": trend_ts,
                                    "entry_idx": i,
                                    "entry_ts": ts,
                                    "entry": px,
                                    "tp_band": up_band if side == "long" else lo_band,
                                    "sl_band": lo_band if side == "long" else up_band,
                                    "qty": qty,
                                    "margin": margin,
                                    "notional": notional,
                                    "dc_upper": up_band,
                                    "dc_lower": lo_band,
                                    "dc_middle": mid_band,
                                }
                            )
                            nid += 1
                            waiting_entry = False

        if len(opens_pos) >= MAX_OPEN:
            waiting_entry = False

    last_px = float(closes[-1])
    last_ts = int(tss[-1])
    for t in list(opens_pos):
        close_trade(t, last_px, last_ts, "EOD_OPEN")
    opens_pos = []

    wins = [t for t in trades if t["pnl"] > 0]
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]
    tp_t = [t for t in trades if t["reason"] == "TP_BAND"]
    sl_t = [t for t in trades if t["reason"] == "SL_BAND"]
    eod_t = [t for t in trades if t["reason"] == "EOD_OPEN"]

    stats = {
        "n": len(trades),
        "wr": (len(wins) / len(trades) * 100) if trades else 0.0,
        "pnl": cash - CAPITAL,
        "pnl_pct": (cash - CAPITAL) / CAPITAL * 100,
        "long_n": len(longs),
        "short_n": len(shorts),
        "long_pnl": sum(t["pnl"] for t in longs),
        "short_pnl": sum(t["pnl"] for t in shorts),
        "tp_count": len(tp_t),
        "sl_count": len(sl_t),
        "sl_pnl": sum(t["pnl"] for t in sl_t),
        "eod_count": len(eod_t),
        "trend_events": trend_events,
        "skipped_parallel_entry": skipped_parallel_entry,
        "fee": sum(t["fee"] for t in trades),
        "end_equity": cash,
        "avg_pnl": (sum(t["pnl"] for t in trades) / len(trades)) if trades else 0.0,
    }
    return trades, stats


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    bar_ms = _INTERVAL_MS[INTERVAL]
    last_closed = (now_ms // bar_ms) * bar_ms
    warmup_bars = DONCHIAN_PERIOD + SLOPE_LOOKBACK + 2
    warmup_ms = warmup_bars * bar_ms
    fetch_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000 - warmup_ms
    window_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000

    print(f"Fetching {SYMBOL} {INTERVAL} from {_local(fetch_from)}...", flush=True)
    df = fetch_klines(INTERVAL, fetch_from, last_closed)
    df = compute_donchian(df, DONCHIAN_PERIOD)
    df = add_parallel_flags(df, SLOPE_LOOKBACK, PARALLEL_SLOPE_TOL)
    df = df[df["ts"] >= window_from].copy().reset_index(drop=True)

    first, last_row = df.iloc[0], df.iloc[-1]
    px_chg = (last_row.close / first.close - 1) * 100

    print("Running Donchian parallel-trend + SL backtest...", flush=True)
    trades, st = run(df)

    trades_per_day = st["n"] / LOOKBACK_DAYS if LOOKBACK_DAYS > 0 else 0.0
    pnl_per_day = st["pnl"] / LOOKBACK_DAYS if LOOKBACK_DAYS > 0 else 0.0

    lines = [
        f"# Donchian parallel-band trend + SL — {SYMBOL} {INTERVAL} {LOOKBACK_DAYS}d",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Clone cua `backtest_link_donchian_parallel_trend.py` + SL band doi xung TP",
        f"- Cua so: **{LOOKBACK_DAYS} ngay** · {_local(int(first.ts))} -> {_local(int(last_row.ts))}",
        f"- Gia {first.close:.4f} -> {last_row.close:.4f} ({px_chg:+.2f}%)",
        f"- Donchian period: {DONCHIAN_PERIOD} · slope lookback: {SLOPE_LOOKBACK} · parallel tol: {PARALLEL_SLOPE_TOL}%/bar",
        "- Trend: parallel -> non-parallel; close > middle = UP, else DOWN",
        "- Entry: nen nguoc chieu (do trong uptrend / xanh trong downtrend) khi band khong song song",
        "- TP long: cham band tren · TP short: cham band duoi (gia = band)",
        "- SL long: cham band duoi · SL short: cham band tren (gia = band; cung nen uu tien SL)",
        f"- Size: {MARGIN_PCT*100:.2f}% equity · {LEVERAGE:.0f}x · phi {FEE*100:.2f}%/side · max {MAX_OPEN} lenh",
        "",
        "## Tong hop",
        "",
        f"| Trades | Lenh/ngay | WR | PnL | % | PnL/ngay | Long | Short | TP | SL | SL PnL | EOD | Trend | Skip | Phi |",
        f"| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        f"| {st['n']} | **{trades_per_day:.1f}** | {st['wr']:.0f}% | **{st['pnl']:+.2f}** | **{st['pnl_pct']:+.2f}%** | **{pnl_per_day:+.3f}** | "
        f"{st['long_n']} ({st['long_pnl']:+.2f}) | {st['short_n']} ({st['short_pnl']:+.2f}) | "
        f"{st['tp_count']} | {st['sl_count']} | {st['sl_pnl']:+.2f} | {st['eod_count']} | {st['trend_events']} | "
        f"{st['skipped_parallel_entry']} | {st['fee']:.2f} |",
        "",
        "## Tat ca lenh",
        "",
        "| # | Side | Trend | Trend luc | Vao | Entry | Band TP | Band SL | Ra | Exit | PnL | Reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for t in trades:
        lines.append(
            f"| {t['id']} | {t['side']} | {t['trend']} | {_local(t['trend_ts'])} | "
            f"{_local(t['entry_ts'])} | {t['entry']:.4f} | {t['tp_band']:.4f} | {t['sl_band']:.4f} | "
            f"{_local(t['exit_ts'])} | {t['exit_px']:.4f} | {t['pnl']:+.3f} | {t['reason']} |"
        )

    if not trades:
        lines += ["", "_Khong co lenh trong cua so backtest._", ""]

    avg_move_pct = (
        sum(abs(t["exit_px"] - t["entry"]) / t["entry"] * 100 for t in trades) / len(trades)
        if trades else 0.0
    )
    lines += [
        "",
        "## Thong ke move (doc lap voi sizing)",
        "",
        f"| Avg move/lenh | Avg move long | Avg move short |",
        f"| --- | --- | --- |",
    ]
    longs_t = [t for t in trades if t["side"] == "long"]
    shorts_t = [t for t in trades if t["side"] == "short"]
    avg_long_move = (sum(abs(t["exit_px"] - t["entry"]) / t["entry"] * 100 for t in longs_t) / len(longs_t)) if longs_t else 0.0
    avg_short_move = (sum(abs(t["exit_px"] - t["entry"]) / t["entry"] * 100 for t in shorts_t) / len(shorts_t)) if shorts_t else 0.0
    lines.append(f"| {avg_move_pct:.3f}% | {avg_long_move:.3f}% | {avg_short_move:.3f}% |")

    out = ROOT / "docs" / f"backtest_{SYMBOL}_donchian_parallel_trend_sl_{INTERVAL}_{LOOKBACK_DAYS}d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    print(
        f"  n={st['n']} lenh/ngay={trades_per_day:.1f} WR={st['wr']:.0f}% "
        f"pnl={st['pnl']:+.2f} pnl/ngay={pnl_per_day:+.3f} "
        f"tp={st['tp_count']} sl={st['sl_count']} sl_pnl={st['sl_pnl']:+.2f} "
        f"trend_events={st['trend_events']} skipped={st['skipped_parallel_entry']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
