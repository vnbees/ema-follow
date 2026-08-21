#!/usr/bin/env python3
"""Donchian parallel-trend + DCA 2x on SL-band touch — LINK clone.

Clone of pyramid variant (does not modify original / equal-size pyramid).

Same entry when flat. While holding:
- Fresh SL-band touch → add same-side lot with **margin/notional = 2× previous lot**
- TP band → close ALL at band price

Tracks peak margin / liquidation proxies (cross-style approx).
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
INTERVAL = "15m"
LOOKBACK_DAYS = 365
DONCHIAN_PERIOD = 20
SLOPE_LOOKBACK = 5
PARALLEL_SLOPE_TOL = 0.015
CAPITAL = 1000.0
MARGIN_PCT = 0.005
LEVERAGE = 10.0
FEE = 0.0004
MAX_OPEN = 5
# Approx Binance USDT-M maintenance margin rate for LINK small notional brackets
MAINT_MARGIN_RATE = 0.005
DCA_MULT = 2.0

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
                req = urllib.request.Request(url, headers={"User-Agent": "donchian-backtest-dca2x/1.0"})
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


def _u_pnl(side: str, entry: float, mark: float, qty: float) -> float:
    if side == "long":
        return (mark - entry) * qty
    return (entry - mark) * qty


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
    skipped_cash = 0
    pyramid_adds = 0
    stack_sizes_at_tp: list[int] = []

    # Peak risk / margin trackers
    peak_im_pct = 0.0  # locked_IM / equity_mtm (close)
    peak_im_pct_worst = 0.0  # locked_IM / equity at bar adverse extreme
    peak_liq_ratio = 0.0  # maint / equity_worst * 100 (Binance-style proxy)
    peak_upnl_pct = 0.0  # |adverse uPnL| / equity_wallet_no_upnl
    peak_notional_lev = 0.0  # total_notional / equity_mtm
    peak_stack = 0
    peak_im_abs = 0.0
    peak_event: dict | None = None
    liq_hits = 0

    def locked_margin() -> float:
        return sum(t["margin"] for t in opens_pos)

    def total_notional() -> float:
        return sum(t["notional"] for t in opens_pos)

    def equity_wallet() -> float:
        return cash + locked_margin()

    def mark_metrics(mark: float) -> tuple[float, float, float]:
        upnl = sum(_u_pnl(t["side"], t["entry"], mark, t["qty"]) for t in opens_pos)
        eq = equity_wallet() + upnl
        im = locked_margin()
        notional = total_notional()
        return upnl, eq, notional if notional else 0.0

    def track_risk(ts: int, hi: float, lo: float, px: float) -> None:
        nonlocal peak_im_pct, peak_im_pct_worst, peak_liq_ratio, peak_upnl_pct
        nonlocal peak_notional_lev, peak_stack, peak_im_abs, peak_event, liq_hits
        if not opens_pos:
            return
        side = opens_pos[0]["side"]
        adverse = lo if side == "long" else hi
        upnl_c, eq_c, _ = mark_metrics(px)
        upnl_w, eq_w, notional = mark_metrics(adverse)
        im = locked_margin()
        wallet = equity_wallet()
        peak_stack = max(peak_stack, len(opens_pos))
        peak_im_abs = max(peak_im_abs, im)

        im_pct = (im / eq_c * 100.0) if eq_c > 1e-9 else 999.0
        im_pct_w = (im / eq_w * 100.0) if eq_w > 1e-9 else 999.0
        upnl_pct = (abs(min(upnl_w, 0.0)) / wallet * 100.0) if wallet > 1e-9 else 999.0
        lev = (notional / eq_c) if eq_c > 1e-9 else 999.0
        maint = notional * MAINT_MARGIN_RATE
        liq_ratio = (maint / eq_w * 100.0) if eq_w > 1e-9 else 999.0

        if liq_ratio >= 100.0 or eq_w <= 0:
            liq_hits += 1

        is_new_peak = (
            im_pct_w > peak_im_pct_worst
            or liq_ratio > peak_liq_ratio
            or im_pct > peak_im_pct
        )
        peak_im_pct = max(peak_im_pct, im_pct)
        peak_im_pct_worst = max(peak_im_pct_worst, im_pct_w)
        peak_liq_ratio = max(peak_liq_ratio, liq_ratio)
        peak_upnl_pct = max(peak_upnl_pct, upnl_pct)
        peak_notional_lev = max(peak_notional_lev, lev)

        if is_new_peak:
            peak_event = {
                "ts": ts,
                "side": side,
                "stack": len(opens_pos),
                "im": im,
                "notional": notional,
                "wallet": wallet,
                "eq_close": eq_c,
                "eq_worst": eq_w,
                "upnl_worst": upnl_w,
                "im_pct_close": im_pct,
                "im_pct_worst": im_pct_w,
                "liq_ratio": liq_ratio,
                "upnl_pct": upnl_pct,
                "eff_lev": lev,
                "mark_close": px,
                "mark_adverse": adverse,
            }

    def try_open(side: str, px: float, ts: int, *, how: str, up_band: float, lo_band: float, mid_band: float) -> bool:
        nonlocal cash, nid, pyramid_adds, skipped_cash
        if len(opens_pos) >= MAX_OPEN:
            return False

        if how == "pyramid_sl" and opens_pos:
            margin = opens_pos[-1]["margin"] * DCA_MULT
            notional = margin * LEVERAGE
        else:
            eq = max(equity_wallet(), 0.0)
            notional = min(eq * MARGIN_PCT * LEVERAGE, cash * LEVERAGE)
            margin = notional / LEVERAGE

        if notional < 1e-6 or margin < 1e-9:
            return False
        if cash < margin - 1e-12:
            skipped_cash += 1
            return False

        qty = notional / px
        cash -= margin
        size_mult = DCA_MULT ** len(opens_pos)  # 1,2,4,... before append
        opens_pos.append(
            {
                "id": nid,
                "side": side,
                "trend": trend,
                "trend_ts": trend_ts,
                "entry_ts": ts,
                "entry": px,
                "how": how,
                "tp_band": up_band if side == "long" else lo_band,
                "sl_band": lo_band if side == "long" else up_band,
                "qty": qty,
                "margin": margin,
                "notional": notional,
                "dc_upper": up_band,
                "dc_lower": lo_band,
                "dc_middle": mid_band,
                "size_mult": size_mult,
            }
        )
        nid += 1
        if how == "pyramid_sl":
            pyramid_adds += 1
        return True

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

        if opens_pos:
            side = opens_pos[0]["side"]
            tp_hit = (side == "long" and hi >= up_band) or (side == "short" and lo <= lo_band)
            sl_touch = (side == "long" and lo <= lo_band) or (side == "short" and hi >= up_band)
            prev_sl = False
            if i > 0 and not np.isnan(upper[i - 1]) and not np.isnan(lower[i - 1]):
                if side == "long":
                    prev_sl = float(lows[i - 1]) <= float(lower[i - 1])
                else:
                    prev_sl = float(highs[i - 1]) >= float(upper[i - 1])

            if tp_hit:
                exit_px = up_band if side == "long" else lo_band
                stack_sizes_at_tp.append(len(opens_pos))
                for t in list(opens_pos):
                    close_trade(t, exit_px, ts, "TP_BAND")
                opens_pos = []
            elif sl_touch and not prev_sl:
                try_open(side, px, ts, how="pyramid_sl", up_band=up_band, lo_band=lo_band, mid_band=mid_band)

        if parallel_exit[i]:
            trend = "up" if px > mid_band else "down"
            trend_ts = ts
            waiting_entry = True
            trend_events += 1

        if not opens_pos and waiting_entry and trend is not None:
            is_red = px < o
            is_green = px > o
            counter = (trend == "up" and is_red) or (trend == "down" and is_green)
            if counter:
                if parallel[i]:
                    skipped_parallel_entry += 1
                else:
                    side = "long" if trend == "up" else "short"
                    if try_open(side, px, ts, how="signal", up_band=up_band, lo_band=lo_band, mid_band=mid_band):
                        waiting_entry = False

        if opens_pos:
            waiting_entry = False
            track_risk(ts, hi, lo, px)

    last_px = float(closes[-1])
    last_ts = int(tss[-1])
    for t in list(opens_pos):
        close_trade(t, last_px, last_ts, "EOD_OPEN")
    opens_pos = []

    wins = [t for t in trades if t["pnl"] > 0]
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]
    tp_t = [t for t in trades if t["reason"] == "TP_BAND"]
    eod_t = [t for t in trades if t["reason"] == "EOD_OPEN"]
    signal_entries = sum(1 for t in trades if t.get("how") == "signal")
    pyramid_entries = sum(1 for t in trades if t.get("how") == "pyramid_sl")

    avg_w = (sum(t["pnl"] for t in wins) / len(wins)) if wins else 0.0
    losses = [t for t in trades if t["pnl"] < 0]
    avg_l = (sum(t["pnl"] for t in losses) / len(losses)) if losses else 0.0
    rr = (avg_w / abs(avg_l)) if losses else float("inf")

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
        "eod_count": len(eod_t),
        "trend_events": trend_events,
        "skipped_parallel_entry": skipped_parallel_entry,
        "skipped_cash": skipped_cash,
        "fee": sum(t["fee"] for t in trades),
        "end_equity": cash,
        "avg_pnl": (sum(t["pnl"] for t in trades) / len(trades)) if trades else 0.0,
        "signal_entries": signal_entries,
        "pyramid_entries": pyramid_entries,
        "pyramid_adds": pyramid_adds,
        "avg_stack_at_tp": (sum(stack_sizes_at_tp) / len(stack_sizes_at_tp)) if stack_sizes_at_tp else 0.0,
        "max_stack_at_tp": max(stack_sizes_at_tp) if stack_sizes_at_tp else 0,
        "avg_win": avg_w,
        "avg_loss": avg_l,
        "rr": rr,
        "peak_im_pct": peak_im_pct,
        "peak_im_pct_worst": peak_im_pct_worst,
        "peak_liq_ratio": peak_liq_ratio,
        "peak_upnl_pct": peak_upnl_pct,
        "peak_notional_lev": peak_notional_lev,
        "peak_stack": peak_stack,
        "peak_im_abs": peak_im_abs,
        "peak_event": peak_event,
        "liq_hits": liq_hits,
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

    print("Running Donchian DCA 2x pyramid backtest...", flush=True)
    trades, st = run(df)

    trades_per_day = st["n"] / LOOKBACK_DAYS if LOOKBACK_DAYS > 0 else 0.0
    pnl_per_day = st["pnl"] / LOOKBACK_DAYS if LOOKBACK_DAYS > 0 else 0.0
    pe = st["peak_event"] or {}

    lines = [
        f"# Donchian parallel-trend + DCA 2x on SL — {SYMBOL} {INTERVAL} {LOOKBACK_DAYS}d",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Clone: cham band SL → **them 1 lenh margin ×{DCA_MULT:.0f} lenh truoc**; TP → dong het",
        f"- Cua so: **{LOOKBACK_DAYS} ngay** · {_local(int(first.ts))} -> {_local(int(last_row.ts))}",
        f"- Gia {first.close:.4f} -> {last_row.close:.4f} ({px_chg:+.2f}%)",
        f"- Donchian period: {DONCHIAN_PERIOD} · slope lookback: {SLOPE_LOOKBACK} · parallel tol: {PARALLEL_SLOPE_TOL}%/bar",
        f"- Size lenh 1: {MARGIN_PCT*100:.2f}% equity · {LEVERAGE:.0f}x · DCA ×{DCA_MULT:.0f} · max stack {MAX_OPEN} (1+2+4+8+16 = 31× base)",
        f"- Maint margin proxy (liq): {MAINT_MARGIN_RATE*100:.2f}% notional (approx Binance LINK bracket)",
        f"- Phi {FEE*100:.2f}%/side",
        "",
        "## Tong hop",
        "",
        "| Trades | Lenh/ngay | WR | PnL | % | PnL/ngay | Signal | DCA | RR | Avg stack | Max stack | Skip cash | Phi |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        f"| {st['n']} | **{trades_per_day:.1f}** | {st['wr']:.0f}% | **{st['pnl']:+.2f}** | **{st['pnl_pct']:+.2f}%** | **{pnl_per_day:+.3f}** | "
        f"{st['signal_entries']} | {st['pyramid_entries']} | {st['rr']:.3f} | {st['avg_stack_at_tp']:.2f} | {st['max_stack_at_tp']} | "
        f"{st['skipped_cash']} | {st['fee']:.2f} |",
        "",
        f"- Long: {st['long_n']} ({st['long_pnl']:+.2f}) · Short: {st['short_n']} ({st['short_pnl']:+.2f})",
        f"- Avg win {st['avg_win']:+.4f} · Avg loss {st['avg_loss']:+.4f}",
        "",
        "## Margin / thanh ly (peak)",
        "",
        "| Metric | Peak | Giai thich |",
        "| --- | --- | --- |",
        f"| **IM / equity (close)** | **{st['peak_im_pct']:.2f}%** | Margin khoa / equity MTM @ close |",
        f"| **IM / equity (worst bar)** | **{st['peak_im_pct_worst']:.2f}%** | Cung nhung @ gia adverse trong nen (hi/lo) |",
        f"| **Liq margin ratio** | **{st['peak_liq_ratio']:.2f}%** | Maint≈{MAINT_MARGIN_RATE*100:.2f}%×notional / equity_worst; ≥100% ≈ thanh ly |",
        f"| Adverse uPnL / wallet | {st['peak_upnl_pct']:.2f}% | |uPnL| adverse / (cash+IM) |",
        f"| Eff. leverage (notional/eq) | {st['peak_notional_lev']:.2f}x | |",
        f"| Peak IM abs | {st['peak_im_abs']:.2f} USDT | |",
        f"| Peak stack | {st['peak_stack']} | |",
        f"| Bars liq-ratio≥100% (proxy) | {st['liq_hits']} | So nen cham nguong proxy |",
        "",
    ]

    if pe:
        lines += [
            "### Peak event (worst IM/equity or liq ratio)",
            "",
            f"- Luc: {_local(int(pe['ts']))} · side {pe['side']} · stack {pe['stack']}",
            f"- IM {pe['im']:.2f} · notional {pe['notional']:.2f} · wallet {pe['wallet']:.2f}",
            f"- Equity close {pe['eq_close']:.2f} · equity worst {pe['eq_worst']:.2f} · uPnL worst {pe['upnl_worst']:+.2f}",
            f"- IM% close {pe['im_pct_close']:.2f}% · IM% worst {pe['im_pct_worst']:.2f}% · liq ratio {pe['liq_ratio']:.2f}%",
            f"- Mark close {pe['mark_close']:.4f} · adverse {pe['mark_adverse']:.4f}",
            "",
        ]

    lines += [
        "## Tat ca lenh",
        "",
        "| # | Side | How | Mult | Trend | Vao | Entry | Margin | Ra | Exit | PnL | Reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for t in trades:
        lines.append(
            f"| {t['id']} | {t['side']} | {t.get('how', '')} | ×{t.get('size_mult', 1):.0f} | {t['trend']} | "
            f"{_local(t['entry_ts'])} | {t['entry']:.4f} | {t['margin']:.3f} | "
            f"{_local(t['exit_ts'])} | {t['exit_px']:.4f} | {t['pnl']:+.3f} | {t['reason']} |"
        )

    if not trades:
        lines += ["", "_Khong co lenh trong cua so backtest._", ""]

    out = ROOT / "docs" / f"backtest_{SYMBOL}_donchian_parallel_trend_pyramid_dca2x_{INTERVAL}_{LOOKBACK_DAYS}d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    print(
        f"  n={st['n']} WR={st['wr']:.0f}% pnl={st['pnl']:+.2f} ({st['pnl_pct']:+.2f}%) "
        f"RR={st['rr']:.3f} peak_IM%_worst={st['peak_im_pct_worst']:.2f}% "
        f"peak_liq_ratio={st['peak_liq_ratio']:.2f}% peak_stack={st['peak_stack']} "
        f"liq_hits={st['liq_hits']} skip_cash={st['skipped_cash']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
