#!/usr/bin/env python3
"""B-Xtrender (@Puppytherapy) + EMA 21/89/200 — backtest on existing cache.

Indicator source: ~/.cursor/plans/b-xtrender (TradingView //@version=4)

  shortTerm = RSI(EMA(close,5)-EMA(close,20), 15) - 50
  longTerm  = RSI(EMA(close,20), 15) - 50
  maShort   = T3(shortTerm, 5)
  turn_up   = maShort > maShort[1] and maShort[1] < maShort[2]
  turn_dn   = maShort < maShort[1] and maShort[1] > maShort[2]

Entries (with EMA stack filter):
  LONG  @ close when turn_up  and EMA21 > EMA89 > EMA200 and longTerm > 0
  SHORT @ close when turn_dn  and EMA21 < EMA89 < EMA200 and longTerm < 0
Exit: opposite turn, or EMA21 cross against, or SL 1.5×ATR / TP 2.5×ATR

Usage:
  .venv/bin/python scripts/backtest_bxtrender_ema.py
  .venv/bin/python scripts/backtest_bxtrender_ema.py --tf 1h --windows 365,1095
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
SL_ATR = 1.5
TP_ATR = 2.5

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


def rsi(s: pd.Series, length: int) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def t3(src: pd.Series, length: int, b: float = 0.7) -> pd.Series:
    """Tillson T3 as in B-Xtrender Pine."""
    xe1 = ema(src, length)
    xe2 = ema(xe1, length)
    xe3 = ema(xe2, length)
    xe4 = ema(xe3, length)
    xe5 = ema(xe4, length)
    xe6 = ema(xe5, length)
    c1 = -b * b * b
    c2 = 3 * b * b + 3 * b * b * b
    c3 = -6 * b * b - 3 * b - 3 * b * b * b
    c4 = 1 + 3 * b + b * b * b + 3 * b * b
    return c1 * xe6 + c2 * xe5 + c3 * xe4 + c4 * xe3


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c = out["close"]
    # B-Xtrender defaults from Pine
    short_term = rsi(ema(c, 5) - ema(c, 20), 15) - 50
    long_term = rsi(ema(c, 20), 15) - 50
    ma_short = t3(short_term, 5)
    out["xt_short"] = short_term
    out["xt_long"] = long_term
    out["xt_ma"] = ma_short
    out["turn_up"] = (ma_short > ma_short.shift(1)) & (ma_short.shift(1) < ma_short.shift(2))
    out["turn_dn"] = (ma_short < ma_short.shift(1)) & (ma_short.shift(1) > ma_short.shift(2))
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
    return out


@dataclass
class Signal:
    i: int
    side: str
    entry: float
    sl: float
    tp: float


def build_signals(df: pd.DataFrame) -> list[Signal]:
    sigs: list[Signal] = []
    for i in range(2, len(df)):
        row = df.iloc[i]
        if not np.isfinite(row["atr"]) or row["atr"] <= 0:
            continue
        if not np.isfinite(row["ema200"]) or not np.isfinite(row["xt_ma"]):
            continue
        entry = float(row["close"])
        atr = float(row["atr"])
        if bool(row["turn_up"]) and bool(row["bull_stack"]) and float(row["xt_long"]) > 0:
            sl = entry - SL_ATR * atr
            tp = entry + TP_ATR * atr
            if sl < entry < tp:
                sigs.append(Signal(i, "long", entry, sl, tp))
        elif bool(row["turn_dn"]) and bool(row["bear_stack"]) and float(row["xt_long"]) < 0:
            sl = entry + SL_ATR * atr
            tp = entry - TP_ATR * atr
            if tp < entry < sl:
                sigs.append(Signal(i, "short", entry, sl, tp))
    return sigs


def simulate(df: pd.DataFrame, signals: list[Signal], eval_start: int) -> dict:
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    ts = df["ts"].to_numpy()
    ema21 = df["ema21"].to_numpy()
    turn_up = df["turn_up"].to_numpy()
    turn_dn = df["turn_dn"].to_numpy()

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
            # opposite xtrender turn
            flip = (side == "long" and bool(turn_dn[i])) or (side == "short" and bool(turn_up[i]))
            # EMA21 adverse cross
            ema_flip = (side == "long" and close[i] < ema21[i]) or (side == "short" and close[i] > ema21[i])
            exit_px = reason = None
            if hit_sl and hit_tp:
                exit_px, reason = sl, "SL"
            elif hit_sl:
                exit_px, reason = sl, "SL"
            elif hit_tp:
                exit_px, reason = tp, "TP"
            elif flip:
                exit_px, reason = float(close[i]), "XT_FLIP"
            elif ema_flip:
                exit_px, reason = float(close[i]), "EMA21"
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
    by_reason: dict[str, list[float]] = {}
    for t in trades:
        by_reason.setdefault(t["reason"], []).append(t["pnl"])
    return {
        "n": len(trades),
        "wr": 100 * len(wins) / len(trades) if trades else 0.0,
        "pf": gw / gl if gl > 0 else float("inf"),
        "ret_pct": net / CAPITAL * 100,
        "maxdd": maxdd * 100,
        "final_eq": cash,
        "long_n": sum(1 for t in trades if t["side"] == "long"),
        "short_n": sum(1 for t in trades if t["side"] == "short"),
        "by_reason": {k: {"n": len(v), "net": sum(v)} for k, v in by_reason.items()},
    }


def run_symbol(df15: pd.DataFrame, tf: str, eval_start: int) -> dict | None:
    df = resample_ohlc(df15, tf)
    if len(df) < 300:
        return None
    df = enrich(df)
    sigs = build_signals(df)
    st = simulate(df, sigs, eval_start)
    st["signals"] = len([s for s in sigs if int(df["ts"].iloc[s.i]) >= eval_start])
    st["bars"] = len(df)
    return st


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", choices=["15m", "1h", "4h"], default="1h")
    parser.add_argument("--windows", default="365,1095")
    args = parser.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]

    frames: dict[str, pd.DataFrame] = {}
    for sym in SYMBOLS:
        df = load_15m(sym)
        if df is not None and len(df) > 1000:
            frames[sym] = df
    if not frames:
        print("No cache", flush=True)
        return 1

    last = min(int(d["ts"].max()) for d in frames.values())
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        "# B-Xtrender + EMA 21/89/200 — backtest",
        "",
        f"- Generated: **{now}**",
        "- Indicator: `~/.cursor/plans/b-xtrender` (B-Xtrender @Puppytherapy)",
        f"- TF: **{args.tf}** từ cache `data/bt_klines_15m/`",
        "- Long: T3 short **turn_up** + EMA21>89>200 + longTermXtrender>0",
        "- Short: T3 short **turn_dn** + EMA21<89<200 + longTermXtrender<0",
        f"- Exit: SL {SL_ATR}×ATR / TP {TP_ATR}×ATR / XT flip / close vs EMA21; risk 1% equity; fee 0.04%/side",
        "",
    ]

    for days in windows:
        eval_start = last - days * 86400 * 1000
        print(f"\n=== {days}d tf={args.tf} ===", flush=True)
        rows = []
        for sym, df15 in frames.items():
            if int(df15["ts"].min()) > eval_start + 30 * 86400 * 1000:
                continue
            st = run_symbol(df15, args.tf, eval_start)
            if st is None or st["n"] < 5:
                continue
            st["sym"] = sym
            rows.append(st)
            print(
                f"  {sym:10} n={st['n']:4} WR={st['wr']:5.1f}% PF={st['pf']:.2f} "
                f"ret={st['ret_pct']:+7.1f}% MaxDD={st['maxdd']:5.1f}%",
                flush=True,
            )

        if not rows:
            lines += [f"## {days}d — không đủ lệnh", ""]
            continue

        rets = [r["ret_pct"] for r in rows]
        pfs = [r["pf"] for r in rows if np.isfinite(r["pf"])]
        pos = sum(1 for r in rets if r > 0)
        lines += [
            f"## {days}d · tf **{args.tf}** · từ {fmt_ts(eval_start)} · **{len(rows)} coin**",
            "",
            f"- Tổng lệnh: **{sum(r['n'] for r in rows)}** · coin dương **{pos}/{len(rows)}**",
            f"- TB/coin: WR **{np.mean([r['wr'] for r in rows]):.1f}%** · "
            f"PF mean/med **{np.mean(pfs):.2f}/{np.median(pfs):.2f}** · "
            f"Return **{np.mean(rets):+.1f}%** · MaxDD **{np.mean([r['maxdd'] for r in rows]):.1f}%**",
            "",
            "| Symbol | Trades | WR | PF | Return | MaxDD | L/S |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for r in sorted(rows, key=lambda x: x["ret_pct"], reverse=True):
            lines.append(
                f"| {r['sym']} | {r['n']} | {r['wr']:.1f}% | {r['pf']:.2f} | "
                f"{r['ret_pct']:+.1f}% | {r['maxdd']:.1f}% | {r['long_n']}/{r['short_n']} |"
            )
        lines.append("")

    lines += [
        "## Kết luận",
        "",
        "- So với Donchian Config A (WR~71% PF~1.5): chỉ đáng xem nếu PF median > 1 và đa số coin dương.",
        "- Paper only; đếm tín hiệu phụ thuộc TF (15m nhiễu hơn 1h/4h).",
        "",
    ]
    out = DOCS / f"backtest_bxtrender_ema21_89_200_{args.tf}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
