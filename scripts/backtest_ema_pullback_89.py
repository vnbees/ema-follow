#!/usr/bin/env python3
"""EMA 21/89/200 pullback-to-89 — sweep RR > 1.

Long:
  - EMA21 > EMA89 > EMA200
  - Price pulls back and touches EMA89 (low <= ema89 <= high, close >= ema89)
  - First touch from above (prev close > prev ema89)
Short: mirror

SL variants:
  - ema200: SL just beyond EMA200
  - atr:    SL beyond touch extreme by 0.5×ATR
  - swing:  SL = swing low/high lookback 20

TP = rr × R · Exit also when EMA stack breaks

Usage:
  .venv/bin/python scripts/backtest_ema_pullback_89.py --tf 1h --rr 1.5,2,2.5,3,4,5
  .venv/bin/python scripts/backtest_ema_pullback_89.py --tf 15m --windows 365 --sl ema200,swing
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "bt_klines_15m"
DOCS = ROOT / "docs"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")

CAPITAL = 1000.0
FEE = 0.0004
RISK_PCT = 0.01
LEVERAGE = 10.0
ATR_PERIOD = 14
SWING_LB = 20
MIN_RISK_PCT = 0.0015
MAX_RISK_PCT = 0.08

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "TRXUSDT", "ADAUSDT",
    "AVAXUSDT", "DOTUSDT", "LINKUSDT", "LTCUSDT", "BCHUSDT", "XLMUSDT", "ATOMUSDT",
    "NEARUSDT", "APTUSDT", "SUIUSDT", "ARBUSDT", "OPUSDT", "UNIUSDT",
]


def best_cache(sym: str) -> Path | None:
    files = list(CACHE_DIR.glob(f"{sym}_15m_*.csv"))
    if not files:
        return None

    def score(p: Path) -> tuple[int, float]:
        parts = p.stem.split("_")
        span = 0
        if len(parts) >= 4 and parts[-2].isdigit() and parts[-1].isdigit():
            span = int(parts[-1]) - int(parts[-2])
        return (span, p.stat().st_mtime)

    return max(files, key=score)


def load_15m(sym: str) -> pd.DataFrame | None:
    path = best_cache(sym)
    if path is None:
        return None
    df = pd.read_csv(path)
    if not {"ts", "open", "high", "low", "close"}.issubset(df.columns):
        return None
    return df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if rule == "15m":
        return df.copy()
    x = df.copy()
    x["dt"] = pd.to_datetime(x["ts"], unit="ms", utc=True)
    x = x.set_index("dt")
    o = x.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna(subset=["open", "close"]).reset_index()
    o["ts"] = o["dt"].map(lambda t: int(pd.Timestamp(t).timestamp() * 1000)).astype("int64")
    return o.drop(columns=["dt"])


def ema(s: pd.Series, length: int) -> pd.Series:
    return s.ewm(span=length, adjust=False).mean()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c = out["close"]
    out["ema21"] = ema(c, 21)
    out["ema89"] = ema(c, 89)
    out["ema200"] = ema(c, 200)
    out["bull_stack"] = (out["ema21"] > out["ema89"]) & (out["ema89"] > out["ema200"])
    out["bear_stack"] = (out["ema21"] < out["ema89"]) & (out["ema89"] < out["ema200"])
    prev = c.shift(1)
    tr = pd.concat(
        [out["high"] - out["low"], (out["high"] - prev).abs(), (out["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    out["swing_lo"] = out["low"].rolling(SWING_LB, min_periods=SWING_LB).min()
    out["swing_hi"] = out["high"].rolling(SWING_LB, min_periods=SWING_LB).max()
    return out


@dataclass
class Signal:
    i: int
    side: str
    entry: float
    sl: float
    tp: float
    risk: float


def build_base_signals(df: pd.DataFrame, sl_mode: str) -> list[tuple[int, str, float, float, float]]:
    """Return list of (i, side, entry, sl, risk) — TP filled later by RR."""
    n = len(df)
    start = max(SWING_LB, 200)
    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    e89 = df["ema89"].to_numpy(dtype=float)
    e200 = df["ema200"].to_numpy(dtype=float)
    atr = df["atr"].to_numpy(dtype=float)
    swing_lo = df["swing_lo"].to_numpy(dtype=float)
    swing_hi = df["swing_hi"].to_numpy(dtype=float)
    bull = df["bull_stack"].to_numpy(dtype=bool)
    bear = df["bear_stack"].to_numpy(dtype=bool)

    out: list[tuple[int, str, float, float, float]] = []
    for i in range(start, n):
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if not np.isfinite(e89[i]) or not np.isfinite(e200[i]):
            continue
        entry = close[i]
        # Long touch from above
        if (
            bull[i]
            and low[i] <= e89[i] <= high[i]
            and entry >= e89[i]
            and close[i - 1] > e89[i - 1]
        ):
            if sl_mode == "ema200":
                sl = min(e200[i], low[i]) - 0.25 * atr[i]
            elif sl_mode == "atr":
                sl = min(low[i], e89[i]) - 0.5 * atr[i]
            else:
                sl = min(swing_lo[i], low[i]) - 0.1 * atr[i]
            if sl < entry:
                risk = entry - sl
                pct = risk / entry
                if MIN_RISK_PCT <= pct <= MAX_RISK_PCT:
                    out.append((i, "long", entry, sl, risk))
        elif (
            bear[i]
            and low[i] <= e89[i] <= high[i]
            and entry <= e89[i]
            and close[i - 1] < e89[i - 1]
        ):
            if sl_mode == "ema200":
                sl = max(e200[i], high[i]) + 0.25 * atr[i]
            elif sl_mode == "atr":
                sl = max(high[i], e89[i]) + 0.5 * atr[i]
            else:
                sl = max(swing_hi[i], high[i]) + 0.1 * atr[i]
            if sl > entry:
                risk = sl - entry
                pct = risk / entry
                if MIN_RISK_PCT <= pct <= MAX_RISK_PCT:
                    out.append((i, "short", entry, sl, risk))
    return out


def with_rr(base: list[tuple[int, str, float, float, float]], rr: float) -> list[Signal]:
    sigs: list[Signal] = []
    for i, side, entry, sl, risk in base:
        tp = entry + rr * risk if side == "long" else entry - rr * risk
        sigs.append(Signal(i, side, entry, sl, tp, risk))
    return sigs


def simulate(df: pd.DataFrame, signals: list[Signal], eval_start: int) -> dict:
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    ts = df["ts"].to_numpy()
    bull = df["bull_stack"].to_numpy()
    bear = df["bear_stack"].to_numpy()

    cash = CAPITAL
    pos = None
    trades: list[dict] = []
    peak = CAPITAL
    maxdd = 0.0
    signals = [s for s in signals if int(ts[s.i]) >= eval_start]
    sig_i = 0

    def equity(px: float) -> float:
        if pos is None:
            return cash
        if pos["side"] == "long":
            upnl = (px - pos["entry"]) * pos["qty"]
        else:
            upnl = (pos["entry"] - px) * pos["qty"]
        return cash + pos["margin"] + upnl

    for i in range(len(df)):
        if pos is not None and i > pos["entry_i"]:
            side = pos["side"]
            sl, tp = pos["sl"], pos["tp"]
            hit_sl = (side == "long" and low[i] <= sl) or (side == "short" and high[i] >= sl)
            hit_tp = (side == "long" and high[i] >= tp) or (side == "short" and low[i] <= tp)
            stack_ok = bool(bull[i]) if side == "long" else bool(bear[i])
            exit_px = reason = None
            if hit_sl and hit_tp:
                exit_px, reason = sl, "SL"
            elif hit_sl:
                exit_px, reason = sl, "SL"
            elif hit_tp:
                exit_px, reason = tp, "TP"
            elif not stack_ok:
                exit_px, reason = float(close[i]), "STACK_BREAK"
            if exit_px is not None:
                qty, entry, margin = pos["qty"], pos["entry"], pos["margin"]
                raw = (exit_px - entry) * qty if side == "long" else (entry - exit_px) * qty
                fee = (entry + exit_px) * qty * FEE
                pnl = raw - fee
                cash += margin + pnl
                trades.append({"pnl": pnl, "side": side, "reason": reason})
                pos = None

        while sig_i < len(signals) and signals[sig_i].i < i:
            sig_i += 1
        while sig_i < len(signals) and signals[sig_i].i == i and pos is None:
            s = signals[sig_i]
            sig_i += 1
            risk = abs(s.entry - s.sl)
            if risk <= 0:
                continue
            eq = max(cash, 1.0)
            qty = (eq * RISK_PCT) / risk
            margin = min(eq * 0.15, qty * s.entry / LEVERAGE)
            fee = s.entry * qty * FEE
            if margin < 1 or margin + fee > cash:
                continue
            cash -= margin + fee
            pos = {
                "side": s.side,
                "entry": s.entry,
                "sl": s.sl,
                "tp": s.tp,
                "qty": qty,
                "margin": margin,
                "entry_i": i,
            }

        eq = equity(float(close[i]))
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)

    if pos is not None:
        px = float(close[-1])
        side = pos["side"]
        raw = (px - pos["entry"]) * pos["qty"] if side == "long" else (pos["entry"] - px) * pos["qty"]
        fee = px * pos["qty"] * FEE
        pnl = raw - fee
        cash += pos["margin"] + pnl
        trades.append({"pnl": pnl, "side": side, "reason": "EOD"})

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in trades)
    tot = len(trades) or 1
    return {
        "n": len(trades),
        "wr": 100 * len(wins) / len(trades) if trades else 0.0,
        "pf": (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "ret_pct": net / CAPITAL * 100,
        "maxdd": maxdd * 100,
        "tp_n": sum(1 for t in trades if t["reason"] == "TP"),
        "sl_n": sum(1 for t in trades if t["reason"] == "SL"),
        "stack_n": sum(1 for t in trades if t["reason"] == "STACK_BREAK"),
        "tp_pct": 100 * sum(1 for t in trades if t["reason"] == "TP") / tot,
        "sl_pct": 100 * sum(1 for t in trades if t["reason"] == "SL") / tot,
        "stack_pct": 100 * sum(1 for t in trades if t["reason"] == "STACK_BREAK") / tot,
    }


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")


def aggregate(rows: list[dict]) -> dict:
    rets = [r["ret_pct"] for r in rows]
    pfs = [r["pf"] for r in rows if np.isfinite(r["pf"])]
    tot_n = sum(r["n"] for r in rows) or 1
    return {
        "coins": len(rows),
        "pos_coins": sum(1 for r in rets if r > 0),
        "n": sum(r["n"] for r in rows),
        "wr": float(np.mean([r["wr"] for r in rows])),
        "pf_mean": float(np.mean(pfs)) if pfs else 0.0,
        "pf_med": float(np.median(pfs)) if pfs else 0.0,
        "ret_mean": float(np.mean(rets)),
        "maxdd_mean": float(np.mean([r["maxdd"] for r in rows])),
        "tp_pct": 100 * sum(r["tp_n"] for r in rows) / tot_n,
        "sl_pct": 100 * sum(r["sl_n"] for r in rows) / tot_n,
        "stack_pct": 100 * sum(r["stack_n"] for r in rows) / tot_n,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", choices=["15m", "1h", "4h"], default="1h")
    parser.add_argument("--windows", default="365,1095")
    parser.add_argument("--sl", default="ema200,atr,swing")
    parser.add_argument("--rr", default="1.5,2,2.5,3,4,5,6", help="TP multiples of R, all > 1")
    args = parser.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]
    sl_modes = [x.strip() for x in args.sl.split(",") if x.strip()]
    rr_list = [float(x) for x in args.rr.split(",") if x.strip()]
    if any(r <= 1 for r in rr_list):
        print("All RR must be > 1", flush=True)
        return 1

    frames: dict[str, pd.DataFrame] = {}
    for sym in SYMBOLS:
        df = load_15m(sym)
        if df is not None and len(df) > 1000:
            frames[sym] = df
    if not frames:
        print("No cache", flush=True)
        return 1

    # Pre-enrich once per symbol
    enriched: dict[str, pd.DataFrame] = {}
    for sym, df15 in frames.items():
        d = resample_ohlc(df15, args.tf)
        if len(d) >= 300:
            enriched[sym] = enrich(d)
    print(f"Enriched {len(enriched)} symbols @ {args.tf}", flush=True)

    # Precompute base signals per (sym, sl_mode)
    base_cache: dict[tuple[str, str], list] = {}
    for sl_mode in sl_modes:
        for sym, df in enriched.items():
            base_cache[(sym, sl_mode)] = build_base_signals(df, sl_mode)
        print(f"  base signals SL={sl_mode} done", flush=True)

    last = min(int(d["ts"].max()) for d in enriched.values())
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# EMA pullback 89 — RR sweep (>1)",
        "",
        f"- Generated: **{now}**",
        f"- Script: `scripts/backtest_ema_pullback_89.py`",
        f"- TF: **{args.tf}** · RR tested: **{', '.join(str(r) for r in rr_list)}**",
        "- Entry: stack + touch EMA89 · Exit: SL / TP(rr×R) / STACK_BREAK",
        f"- SL modes: {', '.join(f'`{s}`' for s in sl_modes)}",
        "",
    ]

    for days in windows:
        eval_start = last - days * 86400 * 1000
        lines += [f"## {days}d · tf **{args.tf}** · từ {fmt_ts(eval_start)}", ""]
        print(f"\n=== {days}d ===", flush=True)

        # Summary matrix per SL
        for sl_mode in sl_modes:
            lines += [
                f"### SL=`{sl_mode}`",
                "",
                "| RR | Trades | WR | PF mean/med | Return | MaxDD | coin+ | TP% | SL% | Stack% |",
                "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
            print(f"  SL={sl_mode}", flush=True)
            best = None
            for rr in rr_list:
                rows = []
                for sym, df in enriched.items():
                    if int(df["ts"].min()) > eval_start + 30 * 86400 * 1000:
                        continue
                    base = base_cache[(sym, sl_mode)]
                    st = simulate(df, with_rr(base, rr), eval_start)
                    if st["n"] < 3:
                        continue
                    st["sym"] = sym
                    rows.append(st)
                if not rows:
                    lines.append(f"| {rr:g} | — | — | — | — | — | — | — | — | — |")
                    continue
                a = aggregate(rows)
                lines.append(
                    f"| {rr:g} | {a['n']} | {a['wr']:.1f}% | {a['pf_mean']:.2f}/{a['pf_med']:.2f} | "
                    f"{a['ret_mean']:+.1f}% | {a['maxdd_mean']:.1f}% | "
                    f"{a['pos_coins']}/{a['coins']} | {a['tp_pct']:.0f}% | {a['sl_pct']:.0f}% | {a['stack_pct']:.0f}% |"
                )
                print(
                    f"    RR={rr:g}: n={a['n']} WR={a['wr']:.1f}% PF={a['pf_med']:.2f} "
                    f"ret={a['ret_mean']:+.1f}% DD={a['maxdd_mean']:.1f}% "
                    f"+={a['pos_coins']}/{a['coins']} TP={a['tp_pct']:.0f}%",
                    flush=True,
                )
                score = (a["pf_med"], a["ret_mean"], -a["maxdd_mean"])
                if best is None or score > best[0]:
                    best = (score, rr, a)
            if best is not None:
                _, brr, ba = best
                lines += [
                    "",
                    f"- Best by (PF med → return → −DD): **RR={brr:g}** · "
                    f"PF med **{ba['pf_med']:.2f}** · ret **{ba['ret_mean']:+.1f}%** · "
                    f"MaxDD **{ba['maxdd_mean']:.1f}%** · coin+ **{ba['pos_coins']}/{ba['coins']}**",
                    "",
                ]
            else:
                lines.append("")

    lines += [
        "## Đọc kết quả",
        "",
        "- RR thấp hơn → TP% cao hơn, WR cao hơn, nhưng reward/lệnh nhỏ hơn.",
        "- RR cao hơn → ít chạm TP, STACK_BREAK/SL chiếm phần lớn → PF thường không tăng tuyến tính.",
        "- Chọn RR có PF med cao nhất và coin+ nhiều; so với Config A (PF~1.5) để quyết định có đáng live.",
        "",
    ]
    out = DOCS / f"backtest_ema_pullback_89_rr_sweep_{args.tf}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
