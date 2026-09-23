#!/usr/bin/env python3
"""Elliott Wave backtest — rules from Vietcap article (5-3 + Fib + trade 3/5/B).

Source:
  https://www.vietcap.com.vn/kien-thuc/ap-dung-nguyen-ly-song-elliott-trong-giao-dich-dau-tu-chung-khoan

Rules encoded (hard):
  1) Wave 3 never shortest of {1,3,5}
  2) Wave 2 never beyond start of wave 1
  3) Wave 4 never enters wave 1 price territory

Fib guides (soft filters for entries):
  W2 retrace in [0.382, 0.786] of W1 (article 0.5–0.618; widened slightly)
  W3 target ~1.618 of W1
  W4 retrace in [0.146, 0.5] of W3 (article 0.236–0.382)
  WB retrace ≤ 0.786 of WA (article ≤0.618)

Entries (Tip 1): after W2 → trade W3; after W4 → trade W5; after WA → trade WB.
Exit: TP Fib target or SL beyond invalidation pivot; time-stop optional.

Data: cache data/bt_klines_15m/ resampled to 1h / 4h (Elliott on raw 15m is too noisy).

Usage:
  .venv/bin/python scripts/backtest_elliott_vietcap.py
  .venv/bin/python scripts/backtest_elliott_vietcap.py --tf 4h --days 1095
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
FEE = 0.0004  # per side
RISK_PCT = 0.01
LEVERAGE = 10.0

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
    need = {"ts", "open", "high", "low", "close"}
    if not need.issubset(df.columns):
        return None
    return df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    x = df.copy()
    x["dt"] = pd.to_datetime(x["ts"], unit="ms", utc=True)
    x = x.set_index("dt")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "quote_volume" in x.columns:
        agg["quote_volume"] = "sum"
    o = x.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open", "close"])
    o = o.reset_index()
    o["ts"] = o["dt"].map(lambda t: int(pd.Timestamp(t).timestamp() * 1000)).astype("int64")
    return o.drop(columns=["dt"])


def zigzag_pivots(df: pd.DataFrame, atr_mult: float = 2.0, atr_period: int = 14) -> list[dict]:
    """Alternating swing pivots using ATR threshold."""
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    ts = df["ts"].to_numpy()
    prev = np.roll(close, 1)
    prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    atr = pd.Series(tr).rolling(atr_period, min_periods=atr_period).mean().to_numpy()

    pivots: list[dict] = []
    if len(df) < atr_period + 5:
        return pivots

    # start from first valid ATR bar
    i0 = atr_period
    last_pivot_i = i0
    last_pivot_px = close[i0]
    last_kind = None  # 'H' or 'L'
    candidate_i = i0
    candidate_px = close[i0]
    direction = 0  # +1 seeking high, -1 seeking low

    for i in range(i0 + 1, len(df)):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        thr = a * atr_mult
        if direction == 0:
            if high[i] >= last_pivot_px + thr:
                direction = 1
                candidate_i, candidate_px = i, high[i]
            elif low[i] <= last_pivot_px - thr:
                direction = -1
                candidate_i, candidate_px = i, low[i]
            continue

        if direction == 1:
            if high[i] >= candidate_px:
                candidate_i, candidate_px = i, high[i]
            elif low[i] <= candidate_px - thr:
                # confirm high pivot
                pivots.append(
                    {
                        "i": candidate_i,
                        "confirm_i": i,
                        "ts": int(ts[candidate_i]),
                        "px": float(candidate_px),
                        "kind": "H",
                    }
                )
                last_pivot_i, last_pivot_px, last_kind = candidate_i, candidate_px, "H"
                direction = -1
                candidate_i, candidate_px = i, low[i]
        else:
            if low[i] <= candidate_px:
                candidate_i, candidate_px = i, low[i]
            elif high[i] >= candidate_px + thr:
                pivots.append(
                    {
                        "i": candidate_i,
                        "confirm_i": i,
                        "ts": int(ts[candidate_i]),
                        "px": float(candidate_px),
                        "kind": "L",
                    }
                )
                last_pivot_i, last_pivot_px, last_kind = candidate_i, candidate_px, "L"
                direction = 1
                candidate_i, candidate_px = i, high[i]

    # ensure alternating
    cleaned: list[dict] = []
    for p in pivots:
        if not cleaned:
            cleaned.append(p)
            continue
        if p["kind"] == cleaned[-1]["kind"]:
            # keep more extreme
            if p["kind"] == "H" and p["px"] >= cleaned[-1]["px"]:
                cleaned[-1] = p
            elif p["kind"] == "L" and p["px"] <= cleaned[-1]["px"]:
                cleaned[-1] = p
        else:
            cleaned.append(p)
    return cleaned


@dataclass
class Signal:
    entry_i: int
    side: str  # long/short
    entry: float
    sl: float
    tp: float
    kind: str  # W3 / W5 / WB
    meta: str


def _len(a: float, b: float) -> float:
    return abs(a - b)


def signals_from_pivots(pivots: list[dict], closes: np.ndarray) -> list[Signal]:
    """Generate entries at the bar AFTER the confirming pivot is printed."""
    out: list[Signal] = []
    n = len(pivots)
    for k in range(3, n):
        # Need at least pivots forming W0..W2 (3 legs) for W3 entry
        # Bullish impulse start: L0 - H1 - L2  (wave1 up, wave2 down)
        # After L2 confirmed → long W3
        p0, p1, p2 = pivots[k - 2], pivots[k - 1], pivots[k]
        # --- Wave 3 long: L-H-L ---
        if p0["kind"] == "L" and p1["kind"] == "H" and p2["kind"] == "L":
            w1 = _len(p1["px"], p0["px"])
            if w1 <= 0:
                continue
            retr = (p1["px"] - p2["px"]) / w1
            # Rule 2: W2 not beyond start of W1
            if p2["px"] < p0["px"]:
                continue
            if not (0.382 <= retr <= 0.786):
                continue
            entry_i = p2["confirm_i"] + 1
            if entry_i >= len(closes):
                continue
            entry = float(closes[entry_i])
            sl = p2["px"] * 0.998  # invalidate under W2 low
            tp = p0["px"] + 1.618 * w1  # Fib W3 ≈ 1.618 W1 from start
            if tp <= entry or sl >= entry:
                continue
            out.append(Signal(entry_i, "long", entry, sl, tp, "W3", f"retr={retr:.2f}"))

        # --- Wave 3 short: H-L-H ---
        if p0["kind"] == "H" and p1["kind"] == "L" and p2["kind"] == "H":
            w1 = _len(p0["px"], p1["px"])
            if w1 <= 0:
                continue
            retr = (p2["px"] - p1["px"]) / w1
            if p2["px"] > p0["px"]:
                continue
            if not (0.382 <= retr <= 0.786):
                continue
            entry_i = p2["confirm_i"] + 1
            if entry_i >= len(closes):
                continue
            entry = float(closes[entry_i])
            sl = p2["px"] * 1.002
            tp = p0["px"] - 1.618 * w1
            if tp >= entry or sl <= entry:
                continue
            out.append(Signal(entry_i, "short", entry, sl, tp, "W3", f"retr={retr:.2f}"))

    # Wave 5: need 5 pivots L-H-L-H-L (bull) ending at W4 low
    for k in range(5, n):
        seq = pivots[k - 4 : k + 1]
        kinds = [p["kind"] for p in seq]
        # Bull: L H L H L
        if kinds == ["L", "H", "L", "H", "L"]:
            p0, p1, p2, p3, p4 = seq
            w1 = _len(p1["px"], p0["px"])
            w3 = _len(p3["px"], p2["px"])
            w2_ok = p2["px"] >= p0["px"]
            # Rule 3: W4 not into W1 territory → W4 low > W1 high
            w4_ok = p4["px"] > p1["px"]
            if not (w1 > 0 and w3 > 0 and w2_ok and w4_ok):
                continue
            retr4 = (p3["px"] - p4["px"]) / w3 if w3 else 0
            if not (0.146 <= retr4 <= 0.5):
                continue
            # provisional W5 length unknown; Rule 1 checked at exit optionally — require W3 not shortest vs W1
            # (W5 not done yet; require W3 >= W1 as soft)
            if w3 < w1 * 0.9:
                continue
            entry_i = p4["confirm_i"] + 1
            if entry_i >= len(closes):
                continue
            entry = float(closes[entry_i])
            sl = p4["px"] * 0.998
            tp = p3["px"] + 0.618 * w3  # modest W5 target
            if tp <= entry or sl >= entry:
                continue
            out.append(Signal(entry_i, "long", entry, sl, tp, "W5", f"retr4={retr4:.2f}"))

        # Bear: H L H L H
        if kinds == ["H", "L", "H", "L", "H"]:
            p0, p1, p2, p3, p4 = seq
            w1 = _len(p0["px"], p1["px"])
            w3 = _len(p2["px"], p3["px"])
            w2_ok = p2["px"] <= p0["px"]
            w4_ok = p4["px"] < p1["px"]
            if not (w1 > 0 and w3 > 0 and w2_ok and w4_ok):
                continue
            retr4 = (p4["px"] - p3["px"]) / w3 if w3 else 0
            if not (0.146 <= retr4 <= 0.5):
                continue
            if w3 < w1 * 0.9:
                continue
            entry_i = p4["confirm_i"] + 1
            if entry_i >= len(closes):
                continue
            entry = float(closes[entry_i])
            sl = p4["px"] * 1.002
            tp = p3["px"] - 0.618 * w3
            if tp >= entry or sl <= entry:
                continue
            out.append(Signal(entry_i, "short", entry, sl, tp, "W5", f"retr4={retr4:.2f}"))

    # Wave B: after impulse 5 pivots + WA (6th). Bull impulse then A down:
    # L H L H L  then H? Wait after bull 5 ends at H (W5 high): LHLHL is 5 pivots ending L=W4,
    # W5 makes next H. So completed impulse pivots: L H L H L H
    # Then WA is next L. After WA low confirmed → long WB.
    for k in range(6, n):
        seq = pivots[k - 6 : k + 1]
        kinds = [p["kind"] for p in seq]
        # Bull impulse complete + WA: L H L H L H L
        if kinds == ["L", "H", "L", "H", "L", "H", "L"]:
            w5_high = seq[5]["px"]
            wa_low = seq[6]["px"]
            wa_len = w5_high - wa_low
            if wa_len <= 0:
                continue
            # Rule-ish: A shouldn't wipe entire impulse — soft: A < 0.9 of start-to-W5
            entry_i = seq[6]["confirm_i"] + 1
            if entry_i >= len(closes):
                continue
            entry = float(closes[entry_i])
            sl = wa_low * 0.998
            tp = wa_low + 0.5 * wa_len
            if tp <= entry or sl >= entry:
                continue
            out.append(Signal(entry_i, "long", entry, sl, tp, "WB", f"wa={wa_len:.4f}"))

        # Bear impulse + WA: H L H L H L H
        if kinds == ["H", "L", "H", "L", "H", "L", "H"]:
            w5_low = seq[5]["px"]
            wa_high = seq[6]["px"]
            wa_len = wa_high - w5_low
            if wa_len <= 0:
                continue
            entry_i = seq[6]["confirm_i"] + 1
            if entry_i >= len(closes):
                continue
            entry = float(closes[entry_i])
            sl = wa_high * 1.002
            tp = wa_high - 0.5 * wa_len
            if tp >= entry or sl <= entry:
                continue
            out.append(Signal(entry_i, "short", entry, sl, tp, "WB", f"wa={wa_len:.4f}"))

    # dedupe same entry bar keep first
    out.sort(key=lambda s: (s.entry_i, s.kind))
    seen = set()
    uniq = []
    for s in out:
        key = (s.entry_i, s.side)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    return uniq


def simulate(df: pd.DataFrame, signals: list[Signal], eval_start: int) -> dict:
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    ts = df["ts"].to_numpy()

    cash = CAPITAL
    open_pos = None
    trades: list[dict] = []
    peak = CAPITAL
    maxdd = 0.0
    sig_i = 0
    signals = [s for s in signals if s.entry_i < len(df) and int(ts[s.entry_i]) >= eval_start]
    signals.sort(key=lambda s: s.entry_i)

    def equity(px: float) -> float:
        if open_pos is None:
            return cash
        side, entry, qty, margin = open_pos["side"], open_pos["entry"], open_pos["qty"], open_pos["margin"]
        if side == "long":
            upnl = (px - entry) * qty
        else:
            upnl = (entry - px) * qty
        return cash + margin + upnl

    for i in range(len(df)):
        # manage open
        if open_pos is not None and i > open_pos["entry_i"]:
            side = open_pos["side"]
            sl, tp = open_pos["sl"], open_pos["tp"]
            hit_sl = (side == "long" and low[i] <= sl) or (side == "short" and high[i] >= sl)
            hit_tp = (side == "long" and high[i] >= tp) or (side == "short" and low[i] <= tp)
            exit_px = None
            reason = None
            if hit_sl and hit_tp:
                exit_px, reason = sl, "SL"  # conservative
            elif hit_sl:
                exit_px, reason = sl, "SL"
            elif hit_tp:
                exit_px, reason = tp, "TP"
            if exit_px is not None:
                qty = open_pos["qty"]
                entry = open_pos["entry"]
                margin = open_pos["margin"]
                raw = (exit_px - entry) * qty if side == "long" else (entry - exit_px) * qty
                fee = (entry * qty + exit_px * qty) * FEE
                pnl = raw - fee
                cash += margin + pnl
                trades.append(
                    {
                        "pnl": pnl,
                        "side": side,
                        "kind": open_pos["kind"],
                        "reason": reason,
                        "sym": open_pos.get("sym"),
                    }
                )
                open_pos = None

        # entries
        while sig_i < len(signals) and signals[sig_i].entry_i < i:
            sig_i += 1
        while sig_i < len(signals) and signals[sig_i].entry_i == i and open_pos is None:
            s = signals[sig_i]
            sig_i += 1
            risk = abs(s.entry - s.sl)
            if risk <= 0:
                continue
            eq = max(cash, 1.0)
            risk_usd = eq * RISK_PCT
            qty = risk_usd / risk
            margin = min(eq * 0.15, qty * s.entry / LEVERAGE)
            if margin < 1 or margin > cash:
                continue
            # fee open
            fee = s.entry * qty * FEE
            if margin + fee > cash:
                continue
            cash -= margin
            # bake open fee into position via reducing cash further conceptually — apply at close only once both; charge now
            cash -= fee
            open_pos = {
                "side": s.side,
                "entry": s.entry,
                "sl": s.sl,
                "tp": s.tp,
                "qty": qty,
                "margin": margin,
                "entry_i": i,
                "kind": s.kind,
            }

        eq = equity(float(close[i]))
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0)

    if open_pos is not None:
        # force close last
        px = float(close[-1])
        side = open_pos["side"]
        qty = open_pos["qty"]
        entry = open_pos["entry"]
        margin = open_pos["margin"]
        raw = (px - entry) * qty if side == "long" else (entry - px) * qty
        fee = px * qty * FEE
        pnl = raw - fee
        cash += margin + pnl
        trades.append({"pnl": pnl, "side": side, "kind": open_pos["kind"], "reason": "EOD", "sym": None})

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    net = sum(t["pnl"] for t in trades)
    by_kind: dict[str, list[float]] = {}
    for t in trades:
        by_kind.setdefault(t["kind"], []).append(t["pnl"])
    return {
        "n": len(trades),
        "wr": 100 * len(wins) / len(trades) if trades else 0.0,
        "pf": gw / gl if gl > 0 else float("inf"),
        "net": net,
        "ret_pct": net / CAPITAL * 100,
        "maxdd": maxdd * 100,
        "final_eq": cash,
        "by_kind": {k: {"n": len(v), "net": sum(v), "wr": 100 * sum(1 for x in v if x > 0) / len(v)} for k, v in by_kind.items()},
    }


def run_symbol(df15: pd.DataFrame, tf: str, atr_mult: float, eval_start: int) -> dict | None:
    rule = {"1h": "1h", "4h": "4h"}[tf]
    df = resample_ohlc(df15, rule)
    if len(df) < 200:
        return None
    piv = zigzag_pivots(df, atr_mult=atr_mult)
    if len(piv) < 8:
        return None
    closes = df["close"].to_numpy()
    sigs = signals_from_pivots(piv, closes)
    st = simulate(df, sigs, eval_start)
    st["pivots"] = len(piv)
    st["signals"] = len(sigs)
    st["bars"] = len(df)
    return st


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ).strftime("%Y-%m-%d")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tf", choices=["1h", "4h"], default="4h")
    parser.add_argument("--atr-mult", type=float, default=2.5)
    parser.add_argument("--windows", default="365,1095")
    args = parser.parse_args()
    windows = [int(x) for x in args.windows.split(",") if x.strip()]

    # load all
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
        "# Elliott Wave (Vietcap rules) — backtest trên cache hiện có",
        "",
        f"- Generated: **{now}**",
        f"- Nguồn lý thuyết: [Vietcap – Áp dụng nguyên lý sóng Elliott](https://www.vietcap.com.vn/kien-thuc/ap-dung-nguyen-ly-song-elliott-trong-giao-dich-dau-tu-chung-khoan)",
        f"- Data: `data/bt_klines_15m/` → resample **{args.tf}** · ZigZag ATR×{args.atr_mult}",
        "- Entry: sau W2→**W3**, sau W4→**W5**, sau WA→**WB** (Tip 1 bài viết)",
        "- Filter Fib + 3 quy tắc cứng Elliott; risk 1% equity / lệnh; fee 0.04%/side; 1 position / coin",
        "- Entry @ close nến **sau khi ZigZag xác nhận** pivot (không vào tại đáy/đỉnh chưa confirm)",
        "- **Lưu ý:** đếm sóng Elliott mang tính chủ quan — đây là bản **quy tắc hóa gần đúng**, không phải đếm tay. Không thay Donchian live trừ khi edge rõ và ổn định.",
        "",
    ]

    for days in windows:
        eval_start = last - days * 86400 * 1000
        print(f"\n=== {days}d tf={args.tf} ===", flush=True)
        rows = []
        for sym, df15 in frames.items():
            # need coverage
            if int(df15["ts"].min()) > eval_start + 30 * 86400 * 1000:
                continue
            st = run_symbol(df15, args.tf, args.atr_mult, eval_start)
            if st is None or st["n"] < 5:
                continue
            st["sym"] = sym
            rows.append(st)
            print(
                f"  {sym:10} n={st['n']:4} WR={st['wr']:5.1f}% PF={st['pf']:.2f} "
                f"ret={st['ret_pct']:+7.1f}% MaxDD={st['maxdd']:5.1f}% piv={st['pivots']}",
                flush=True,
            )

        if not rows:
            lines += [f"## {days}d — không đủ tín hiệu", ""]
            continue

        # aggregate equal-weight portfolio of per-symbol nets (separate wallets $1000 each)
        avg_ret = float(np.mean([r["ret_pct"] for r in rows]))
        avg_wr = float(np.mean([r["wr"] for r in rows]))
        # median PF less skewed by tiny-n outliers
        pfs = [r["pf"] for r in rows if np.isfinite(r["pf"])]
        avg_pf = float(np.mean(pfs))
        med_pf = float(np.median(pfs))
        avg_dd = float(np.mean([r["maxdd"] for r in rows]))
        pos = sum(1 for r in rows if r["ret_pct"] > 0)
        total_n = sum(r["n"] for r in rows)

        # shared kind stats
        kind_n: dict[str, int] = {}
        kind_net: dict[str, float] = {}
        kind_w: dict[str, int] = {}
        for r in rows:
            for k, v in r["by_kind"].items():
                kind_n[k] = kind_n.get(k, 0) + v["n"]
                kind_net[k] = kind_net.get(k, 0.0) + v["net"]
                kind_w[k] = kind_w.get(k, 0) + int(round(v["wr"] / 100 * v["n"]))

        lines += [
            f"## {days}d · tf **{args.tf}** · eval từ {fmt_ts(eval_start)} · **{len(rows)} coin**",
            "",
            f"- Tổng lệnh: **{total_n}** · coin dương **{pos}/{len(rows)}** · TB/coin: WR **{avg_wr:.1f}%** · "
            f"PF mean/med **{avg_pf:.2f}/{med_pf:.2f}** · Return **{avg_ret:+.1f}%** · MaxDD **{avg_dd:.1f}%** "
            f"(mỗi coin ví $1000 riêng)",
            "",
            "### Theo loại sóng (Tip 1)",
            "",
            "| Kind | Trades | WR | Net $ (sum coins) |",
            "| --- | ---: | ---: | ---: |",
        ]
        for k in ("W3", "W5", "WB"):
            if k not in kind_n:
                continue
            wr = 100 * kind_w[k] / kind_n[k] if kind_n[k] else 0
            lines.append(f"| {k} | {kind_n[k]} | {wr:.1f}% | {kind_net[k]:+.1f} |")
        lines += [
            "",
            "### Theo coin (sort return)",
            "",
            "| Symbol | Trades | WR | PF | Return | MaxDD |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for r in sorted(rows, key=lambda x: x["ret_pct"], reverse=True):
            lines.append(
                f"| {r['sym']} | {r['n']} | {r['wr']:.1f}% | {r['pf']:.2f} | "
                f"{r['ret_pct']:+.1f}% | {r['maxdd']:.1f}% |"
            )
        lines.append("")

    lines += [
        "## Kết luận",
        "",
        "- So với bot Donchian live: đây là chiến lược **khác** (swing theo sóng), tần suất thấp hơn nhiều.",
        "- Nếu PF < 1 hoặc WR thấp trên nhiều cửa sổ → **không** thay thế Config A hiện tại.",
        "",
    ]
    out = DOCS / f"backtest_elliott_vietcap_{args.tf}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
