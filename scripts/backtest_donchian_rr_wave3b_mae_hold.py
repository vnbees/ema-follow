#!/usr/bin/env python3
"""Wave-3b: MAE / max-hold cuts derived from wave-3 feature splits."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
SYMBOLS = ["LINKUSDT", "HYPEUSDT", "BTWUSDT", "SUIUSDT", "DOGEUSDT", "SOLUSDT"]
INTERVAL = "15m"
LOOKBACK_DAYS = 365
DONCHIAN_PERIOD = 20
SLOPE_LOOKBACK = 5
PARALLEL_TOL = 0.015
CAPITAL = 1000.0
MARGIN_PCT = 0.005
LEVERAGE = 10.0
FEE = 0.0004
ATR_PERIOD = 14
BAR_MS = 15 * 60 * 1000


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
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
        for attempt in range(6):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "donchian-w3b/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rows = json.loads(resp.read().decode())
                break
            except Exception:
                if attempt < 5:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        time.sleep(0.1)
        if not rows:
            break
        for r in rows:
            ts = int(r[0])
            if start_ms <= ts < end_ms:
                out.append({"ts": ts, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])})
        nxt = int(rows[-1][0]) + BAR_MS
        if nxt <= cursor:
            break
        cursor = nxt
    if not out:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close"])
    return pd.DataFrame(out).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["dc_upper"] = out["high"].rolling(DONCHIAN_PERIOD, min_periods=DONCHIAN_PERIOD).max()
    out["dc_lower"] = out["low"].rolling(DONCHIAN_PERIOD, min_periods=DONCHIAN_PERIOD).min()
    out["dc_middle"] = (out["dc_upper"] + out["dc_lower"]) / 2.0
    out["dc_width"] = out["dc_upper"] - out["dc_lower"]
    prev = out["close"].shift(1)
    tr = pd.concat(
        [out["high"] - out["low"], (out["high"] - prev).abs(), (out["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    upper = out["dc_upper"].to_numpy()
    lower = out["dc_lower"].to_numpy()
    closes = out["close"].to_numpy()
    n = len(out)
    parallel = np.zeros(n, dtype=bool)
    slope_abs = np.full(n, np.nan)

    def slope(series: np.ndarray, i: int, ref: float) -> float:
        if i < SLOPE_LOOKBACK or ref <= 0:
            return 0.0
        return (series[i] - series[i - SLOPE_LOOKBACK]) / SLOPE_LOOKBACK / ref * 100.0

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        ref = closes[i]
        su, sl = slope(upper, i, ref), slope(lower, i, ref)
        slope_abs[i] = abs(su) + abs(sl)
        parallel[i] = abs(su - sl) <= PARALLEL_TOL
    out["bands_parallel"] = parallel
    out["slope_abs"] = slope_abs
    prev_p = np.roll(parallel, 1)
    prev_p[0] = False
    out["parallel_exit"] = prev_p & (~parallel)
    return out


def _pnl(side: str, entry: float, exit_px: float, qty: float) -> float:
    if side == "long":
        gross = (exit_px - entry) * qty
    else:
        gross = (entry - exit_px) * qty
    return gross - (entry + exit_px) * qty * FEE


def simulate(df: pd.DataFrame, rule: SimpleNamespace) -> dict:
    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    tss = df["ts"].to_numpy()
    upper = df["dc_upper"].to_numpy()
    lower = df["dc_lower"].to_numpy()
    middle = df["dc_middle"].to_numpy()
    width = df["dc_width"].to_numpy()
    parallel = df["bands_parallel"].to_numpy()
    parallel_exit = df["parallel_exit"].to_numpy()
    atr = df["atr"].to_numpy()
    slope_abs = df["slope_abs"].to_numpy()
    n = len(df)

    trend = None
    trend_i = None
    trend_slope_abs = 0.0
    waiting = False
    pos = None
    trades: list[dict] = []
    cash = CAPITAL

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        px, hi, lo, o = float(closes[i]), float(highs[i]), float(lows[i]), float(opens[i])
        up, dn, mid = float(upper[i]), float(lower[i]), float(middle[i])
        w = float(width[i]) if not np.isnan(width[i]) else 0.0
        a = float(atr[i]) if not np.isnan(atr[i]) else 0.0

        if pos is not None:
            side = pos["side"]
            entry = pos["entry"]
            risk = abs(entry - pos["sl0"]) or 1e-12
            if side == "long":
                mfe = (hi - entry) / risk
                mae = (entry - lo) / risk
            else:
                mfe = (entry - lo) / risk
                mae = (hi - entry) / risk
            pos["mfe"] = max(pos["mfe"], mfe)
            pos["mae"] = max(pos["mae"], mae)

            exit_px = None
            reason = None
            bars_held = i - pos["entry_i"]

            if rule.mae_cut_r > 0 and pos["mae"] >= rule.mae_cut_r:
                # fill at stop level within bar extreme
                if side == "long":
                    stop = entry - rule.mae_cut_r * risk
                    exit_px = max(lo, stop) if lo <= stop else stop
                    exit_px = stop  # assume stop fill
                else:
                    stop = entry + rule.mae_cut_r * risk
                    exit_px = stop
                reason = "MAE_CUT"

            tp = up if side == "long" else dn
            if exit_px is None and ((side == "long" and hi >= tp) or (side == "short" and lo <= tp)):
                # same bar MAE+TP: pessimistic MAE already handled first
                exit_px, reason = tp, "TP_BAND"

            if exit_px is None and rule.soft_time_bars > 0 and bars_held >= rule.soft_time_bars:
                r_now = ((px - entry) if side == "long" else (entry - px)) / risk
                if r_now < rule.soft_time_min_r:
                    exit_px, reason = px, "SOFT_TIME"

            if exit_px is None and rule.max_hold > 0 and bars_held >= rule.max_hold:
                exit_px, reason = px, "MAX_HOLD"

            if exit_px is not None:
                pnl = _pnl(side, entry, exit_px, pos["qty"])
                cash += pos["margin"] + pnl
                trades.append({"pnl": pnl, "reason": reason})
                pos = None

        if parallel_exit[i]:
            trend = "up" if px > mid else "down"
            trend_i = i
            trend_slope_abs = float(slope_abs[i]) if not np.isnan(slope_abs[i]) else 0.0
            waiting = True

        if pos is None and waiting and trend is not None:
            counter = (trend == "up" and px < o) or (trend == "down" and px > o)
            if counter and not parallel[i] and w > 1e-12:
                side = "long" if trend == "up" else "short"
                wait_bars = i - trend_i if trend_i is not None else 0
                tp_near = up if side == "long" else dn
                sl_opp = dn if side == "long" else up
                dist_tp = abs(tp_near - px)
                dist_sl = abs(px - sl_opp)
                pot_rr = dist_tp / dist_sl if dist_sl > 1e-12 else 0.0
                body_atr = abs(px - o) / a if a > 0 else 0.0
                ok = True
                if pot_rr < rule.min_pot_rr:
                    ok = False
                if trend_slope_abs < rule.min_slope_abs:
                    ok = False
                if body_atr < rule.min_body_atr or body_atr > rule.max_body_atr:
                    ok = False
                if wait_bars < rule.min_wait or wait_bars > rule.max_wait:
                    ok = False
                if ok:
                    size_mult = float(np.clip(0.5 + pot_rr, 0.5, 2.0)) if rule.size_by_rr else 1.0
                    notional = min(max(cash, 0.0) * MARGIN_PCT * LEVERAGE * size_mult, cash * LEVERAGE)
                    if notional >= 1e-6:
                        margin = notional / LEVERAGE
                        if cash >= margin - 1e-12:
                            cash -= margin
                            pos = {
                                "side": side,
                                "entry": px,
                                "entry_i": i,
                                "qty": notional / px,
                                "margin": margin,
                                "sl0": sl_opp,
                                "mfe": 0.0,
                                "mae": 0.0,
                            }
                            waiting = False
        if pos is not None:
            waiting = False

    if pos is not None:
        px = float(closes[-1])
        pnl = _pnl(pos["side"], pos["entry"], px, pos["qty"])
        cash += pos["margin"] + pnl
        trades.append({"pnl": pnl, "reason": "EOD"})

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    avg_w = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_l = sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0
    rr = avg_w / abs(avg_l) if losses else float("inf")
    wr = len(wins) / len(trades) if trades else 0.0
    rr_be = (1 - wr) / wr if wr > 0 else float("inf")
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return {
        "name": rule.name,
        "note": rule.note,
        "n": len(trades),
        "wr": wr * 100,
        "rr": rr,
        "rr_edge": (rr - rr_be) if wr > 0 and losses else float("nan"),
        "pnl": cash - CAPITAL,
        "reasons": reasons,
    }


def R(**kw) -> SimpleNamespace:
    base = dict(
        name="",
        note="",
        min_pot_rr=0.0,
        min_slope_abs=0.0,
        min_body_atr=0.0,
        max_body_atr=99.0,
        min_wait=0,
        max_wait=9999,
        size_by_rr=False,
        mae_cut_r=0.0,
        max_hold=0,
        soft_time_bars=0,
        soft_time_min_r=0.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last = (now_ms // BAR_MS) * BAR_MS
    wf = last - LOOKBACK_DAYS * 86400 * 1000
    ff = wf - (DONCHIAN_PERIOD + SLOPE_LOOKBACK + ATR_PERIOD + 5) * BAR_MS

    dfs: dict[str, pd.DataFrame] = {}
    for sym in SYMBOLS:
        print(f"fetch {sym}...", flush=True)
        raw = fetch_klines(sym, ff, last)
        if raw.empty:
            continue
        df = prepare(raw)
        df = df[df["ts"] >= wf].copy().reset_index(drop=True)
        if len(df) >= 100:
            dfs[sym] = df

    rules = [
        R(name="baseline", note="ref"),
        R(name="mae_cut_1.0R", note="cut MAE≥1R (opp-band risk)", mae_cut_r=1.0),
        R(name="mae_cut_1.5R", note="cut MAE≥1.5R", mae_cut_r=1.5),
        R(name="mae_cut_2.0R", note="cut MAE≥2R", mae_cut_r=2.0),
        R(name="max_hold_16", note="force exit @16 bars", max_hold=16),
        R(name="max_hold_24", note="force exit @24 bars", max_hold=24),
        R(name="soft16_r0", note="after 16 bars if R<0 exit", soft_time_bars=16, soft_time_min_r=0.0),
        R(name="mae1_soft16", note="MAE≥1R + soft16 R<0", mae_cut_r=1.0, soft_time_bars=16, soft_time_min_r=0.0),
        R(name="mae15_soft16", note="MAE≥1.5R + soft16 R<0", mae_cut_r=1.5, soft_time_bars=16, soft_time_min_r=0.0),
        R(
            name="body_size_rr05",
            note="body[0.3,1.2]+size∝RR+minRR0.5",
            min_body_atr=0.3,
            max_body_atr=1.2,
            size_by_rr=True,
            min_pot_rr=0.5,
        ),
        R(
            name="mae15_body_size",
            note="MAE1.5R + body + size∝RR + RR≥0.5",
            mae_cut_r=1.5,
            min_body_atr=0.3,
            max_body_atr=1.2,
            size_by_rr=True,
            min_pot_rr=0.5,
        ),
        R(
            name="mae1_body_size",
            note="MAE1R + body + size∝RR",
            mae_cut_r=1.0,
            min_body_atr=0.3,
            max_body_atr=1.2,
            size_by_rr=True,
        ),
    ]

    rows = []
    for rule in rules:
        print(f"rule {rule.name}...", flush=True)
        details = []
        pnls, rrs, edges, wrs = [], [], [], []
        for sym, df in dfs.items():
            st = simulate(df, rule)
            st["symbol"] = sym
            details.append(st)
            pnls.append(st["pnl"])
            if st["n"] >= 30 and st["rr_edge"] == st["rr_edge"]:
                rrs.append(st["rr"])
                edges.append(st["rr_edge"])
                wrs.append(st["wr"])
        rows.append(
            {
                "name": rule.name,
                "note": rule.note,
                "sum": sum(pnls),
                "med_rr": float(np.median(rrs)) if rrs else float("nan"),
                "med_edge": float(np.median(edges)) if edges else float("nan"),
                "med_wr": float(np.median(wrs)) if wrs else float("nan"),
                "details": details,
            }
        )

    base = next(r for r in rows if r["name"] == "baseline")
    ranked = sorted(
        rows,
        key=lambda r: (r["med_edge"] if r["med_edge"] == r["med_edge"] else -999, r["sum"]),
        reverse=True,
    )

    lines = [
        "# Wave-3b — MAE / hold cuts (from wave-3 feature splits)",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "- Insight baseline: **MAE>1R** và **hold>16** là nơi đốt PnL (Σ loss rất lớn).",
        "",
        "| Rank | Rule | ΣPnL | Δbase | med RR | med Edge | med WR | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, r in enumerate(ranked, 1):
        lines.append(
            f"| {i} | `{r['name']}` | **{r['sum']:+.1f}** | {r['sum']-base['sum']:+.1f} | "
            f"{r['med_rr']:.3f} | **{r['med_edge']:+.3f}** | {r['med_wr']:.0f}% | {r['note']} |"
        )

    lines += ["", "## Per coin", ""]
    for r in ranked:
        lines += [
            f"### `{r['name']}` — {r['note']}",
            "",
            "| Symbol | n | WR | RR | Edge | PnL | Reasons |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for st in r["details"]:
            e = st["rr_edge"]
            es = f"{e:+.3f}" if e == e else "nan"
            rs = ", ".join(f"{k}:{v}" for k, v in sorted(st["reasons"].items(), key=lambda x: -x[1]))
            lines.append(
                f"| {st['symbol']} | {st['n']} | {st['wr']:.0f}% | {st['rr']:.3f} | {es} | **{st['pnl']:+.1f}** | {rs} |"
            )
        lines.append("")

    best_pnl = max(rows, key=lambda r: r["sum"])
    best_edge = max((r for r in rows if r["med_edge"] == r["med_edge"]), key=lambda r: r["med_edge"])
    tradeoffs = [
        r
        for r in rows
        if r["name"] != "baseline"
        and r["med_edge"] >= base["med_edge"]
        and r["sum"] >= base["sum"] * 0.9
    ]
    best_tf = max(tradeoffs, key=lambda r: (r["med_edge"], r["sum"])) if tradeoffs else best_pnl
    lines += [
        "## Ket luan 3b",
        "",
        f"- Baseline ΣPnL {base['sum']:+.1f}",
        f"- Best edge: `{best_edge['name']}` edge {best_edge['med_edge']:+.3f} Σ {best_edge['sum']:+.1f}",
        f"- Best PnL: `{best_pnl['name']}` Σ {best_pnl['sum']:+.1f}",
        f"- Best tradeoff: `{best_tf['name']}`",
        "",
    ]

    out = ROOT / "docs" / "backtest_MULTI_donchian_rr_wave3b_mae_hold_15m_365d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}", flush=True)
    print(
        f"base={base['sum']:+.1f} best_edge={best_edge['name']} best_pnl={best_pnl['name']} best_tf={best_tf['name']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
