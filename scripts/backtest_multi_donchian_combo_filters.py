#!/usr/bin/env python3
"""Multi-coin combo backtest: wait_2/4 + min_rr≥0.5 + (width≥2% OR TP≥1.5ATR).

Coins = those previously Donchian-backtested in this repo/chat:
LINK, HYPE, BTW, SUI, DOGE, SOL.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
PARALLEL_SLOPE_TOL = 0.015
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
                req = urllib.request.Request(url, headers={"User-Agent": "donchian-combo-multi/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rows = json.loads(resp.read().decode())
                break
            except Exception:
                if attempt < 5:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
        time.sleep(0.12)
        if not rows:
            break
        for r in rows:
            ts = int(r[0])
            if start_ms <= ts < end_ms:
                out.append({"ts": ts, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])})
        last_ts = int(rows[-1][0])
        nxt = last_ts + BAR_MS
        if nxt <= cursor:
            break
        cursor = nxt
    if not out:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close"])
    return pd.DataFrame(out).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


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

    def slope(series: np.ndarray, i: int, ref: float) -> float:
        if i < SLOPE_LOOKBACK or ref <= 0:
            return 0.0
        return (series[i] - series[i - SLOPE_LOOKBACK]) / SLOPE_LOOKBACK / ref * 100.0

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        ref = closes[i]
        parallel[i] = abs(slope(upper, i, ref) - slope(lower, i, ref)) <= PARALLEL_SLOPE_TOL
    out["bands_parallel"] = parallel
    prev_p = np.roll(parallel, 1)
    prev_p[0] = False
    out["parallel_exit"] = prev_p & (~parallel)
    return out


def _pnl(side: str, entry: float, exit_px: float, qty: float) -> tuple[float, float]:
    if side == "long":
        gross = (exit_px - entry) * qty
    else:
        gross = (entry - exit_px) * qty
    fee = (entry + exit_px) * qty * FEE
    return gross - fee, fee


@dataclass(frozen=True)
class Combo:
    name: str
    note: str
    wait_bars: int = 0
    min_rr: float = 0.0
    min_width_pct: float = 0.0
    min_tp_atr: float = 0.0
    width_or_tp: bool = False  # if True: pass if width OR tp_atr filter


def run_combo(df: pd.DataFrame, c: Combo) -> dict:
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
    n = len(df)

    trend: str | None = None
    trend_i: int | None = None
    waiting = False
    opens_pos: list[dict] = []
    trades: list[dict] = []
    cash = CAPITAL
    nid = 1
    skip = {"rr": 0, "width": 0, "tp_atr": 0, "geom": 0, "wait": 0, "parallel": 0}

    def locked() -> float:
        return sum(t["margin"] for t in opens_pos)

    def close_trade(t: dict, exit_px: float, ts: int, reason: str) -> None:
        nonlocal cash
        pnl, fee = _pnl(t["side"], t["entry"], exit_px, t["qty"])
        cash += t["margin"] + pnl
        risk = abs(t["entry"] - t["sl0"]) or 1e-12
        r_mult = ((exit_px - t["entry"]) if t["side"] == "long" else (t["entry"] - exit_px)) / risk
        trades.append({**t, "exit_ts": ts, "exit_px": exit_px, "reason": reason, "pnl": pnl, "fee": fee, "r_mult": r_mult})

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        px, hi, lo, o = float(closes[i]), float(highs[i]), float(lows[i]), float(opens[i])
        ts = int(tss[i])
        up_b, lo_b, mid_b = float(upper[i]), float(lower[i]), float(middle[i])
        w = float(width[i]) if not np.isnan(width[i]) else 0.0
        a = float(atr[i]) if not np.isnan(atr[i]) else 0.0

        if opens_pos:
            t = opens_pos[0]
            side = t["side"]
            tp = up_b if side == "long" else lo_b
            if (side == "long" and hi >= tp) or (side == "short" and lo <= tp):
                close_trade(t, tp, ts, "TP_BAND")
                opens_pos = []

        if parallel_exit[i]:
            trend = "up" if px > mid_b else "down"
            trend_i = i
            waiting = True

        if not opens_pos and waiting and trend is not None:
            is_red, is_green = px < o, px > o
            counter = (trend == "up" and is_red) or (trend == "down" and is_green)
            if counter:
                if parallel[i]:
                    skip["parallel"] += 1
                else:
                    if c.wait_bars > 0 and trend_i is not None and (i - trend_i) < c.wait_bars:
                        skip["wait"] += 1
                    elif w <= 1e-12:
                        skip["geom"] += 1
                    else:
                        side = "long" if trend == "up" else "short"
                        tp_near = up_b if side == "long" else lo_b
                        sl_opp = lo_b if side == "long" else up_b
                        dist_tp = abs(tp_near - px)
                        dist_sl = abs(px - sl_opp)
                        pot_rr = dist_tp / dist_sl if dist_sl > 1e-12 else 0.0
                        width_pct = w / px * 100.0
                        ok_rr = c.min_rr <= 0 or pot_rr >= c.min_rr
                        ok_w = c.min_width_pct <= 0 or width_pct >= c.min_width_pct
                        ok_tp = c.min_tp_atr <= 0 or (a > 0 and dist_tp >= c.min_tp_atr * a)

                        if not ok_rr:
                            skip["rr"] += 1
                            enter = False
                        elif c.width_or_tp and c.min_width_pct > 0 and c.min_tp_atr > 0:
                            enter = ok_w or ok_tp
                            if not enter:
                                skip["geom"] += 1
                        else:
                            enter = True
                            if c.min_width_pct > 0 and not ok_w:
                                skip["width"] += 1
                                enter = False
                            if enter and c.min_tp_atr > 0 and not ok_tp:
                                skip["tp_atr"] += 1
                                enter = False

                        if enter:
                            eq = max(cash + locked(), 0.0)
                            notional = min(eq * MARGIN_PCT * LEVERAGE, cash * LEVERAGE)
                            if notional >= 1e-6:
                                margin = notional / LEVERAGE
                                if cash >= margin - 1e-12:
                                    cash -= margin
                                    opens_pos.append(
                                        {
                                            "id": nid,
                                            "side": side,
                                            "entry": px,
                                            "entry_ts": ts,
                                            "qty": notional / px,
                                            "margin": margin,
                                            "sl0": sl_opp,
                                            "pot_rr": pot_rr,
                                            "width_pct": width_pct,
                                        }
                                    )
                                    nid += 1
                                    waiting = False

        if opens_pos:
            waiting = False

    last_px, last_ts = float(closes[-1]), int(tss[-1])
    for t in list(opens_pos):
        close_trade(t, last_px, last_ts, "EOD")
    opens_pos = []

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    avg_w = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
    avg_l = sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0
    rr = avg_w / abs(avg_l) if losses else float("inf")
    wr = len(wins) / len(trades) if trades else 0.0
    rr_be = (1 - wr) / wr if wr > 0 else float("inf")
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    pf = gross_w / gross_l if gross_l > 0 else float("inf")
    days = max((int(tss[-1]) - int(tss[0])) / (86400 * 1000), 1e-9)

    return {
        "name": c.name,
        "note": c.note,
        "n": len(trades),
        "wr": wr * 100,
        "rr": rr,
        "rr_be": rr_be,
        "rr_edge": (rr - rr_be) if wr > 0 and losses else float("nan"),
        "pf": pf,
        "exp": (sum(t["pnl"] for t in trades) / len(trades)) if trades else 0.0,
        "pnl": cash - CAPITAL,
        "pnl_pct": (cash - CAPITAL) / CAPITAL * 100,
        "avg_w": avg_w,
        "avg_l": avg_l,
        "max_l": min((t["pnl"] for t in losses), default=0.0),
        "fee": sum(t["fee"] for t in trades),
        "days": days,
        "skip": skip,
        "long_n": sum(1 for t in trades if t["side"] == "long"),
        "short_n": sum(1 for t in trades if t["side"] == "short"),
        "long_pnl": sum(t["pnl"] for t in trades if t["side"] == "long"),
        "short_pnl": sum(t["pnl"] for t in trades if t["side"] == "short"),
    }


def combos() -> list[Combo]:
    return [
        Combo("baseline", "No filter (ref)"),
        Combo("w2_rr05_width2", "wait2 + RR≥0.5 + width≥2%", wait_bars=2, min_rr=0.5, min_width_pct=2.0),
        Combo("w4_rr05_width2", "wait4 + RR≥0.5 + width≥2%", wait_bars=4, min_rr=0.5, min_width_pct=2.0),
        Combo("w2_rr05_tp15", "wait2 + RR≥0.5 + TP≥1.5ATR", wait_bars=2, min_rr=0.5, min_tp_atr=1.5),
        Combo("w4_rr05_tp15", "wait4 + RR≥0.5 + TP≥1.5ATR", wait_bars=4, min_rr=0.5, min_tp_atr=1.5),
        Combo(
            "w2_rr05_w2_OR_tp15",
            "wait2 + RR≥0.5 + (width≥2% OR TP≥1.5ATR)",
            wait_bars=2,
            min_rr=0.5,
            min_width_pct=2.0,
            min_tp_atr=1.5,
            width_or_tp=True,
        ),
        Combo(
            "w4_rr05_w2_OR_tp15",
            "wait4 + RR≥0.5 + (width≥2% OR TP≥1.5ATR)",
            wait_bars=4,
            min_rr=0.5,
            min_width_pct=2.0,
            min_tp_atr=1.5,
            width_or_tp=True,
        ),
    ]


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_closed = (now_ms // BAR_MS) * BAR_MS
    warmup = (DONCHIAN_PERIOD + SLOPE_LOOKBACK + ATR_PERIOD + 5) * BAR_MS
    window_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000
    fetch_from = window_from - warmup

    all_rows: list[dict] = []
    per_coin: dict[str, list[dict]] = {}
    meta: dict[str, dict] = {}

    for sym in SYMBOLS:
        print(f"=== {sym} ===", flush=True)
        df = fetch_klines(sym, fetch_from, last_closed)
        if df.empty:
            print(f"  no data", flush=True)
            continue
        df = prepare(df)
        df = df[df["ts"] >= window_from].copy().reset_index(drop=True)
        # if listing late, keep whatever remains after warmup NaNs drop naturally in loop
        if len(df) < 50:
            print(f"  too short n={len(df)}", flush=True)
            continue
        first, last = df.iloc[0], df.iloc[-1]
        days = (int(last.ts) - int(first.ts)) / (86400 * 1000)
        meta[sym] = {
            "from": _local(int(first.ts)),
            "to": _local(int(last.ts)),
            "days": days,
            "px0": float(first.close),
            "px1": float(last.close),
            "chg": (float(last.close) / float(first.close) - 1) * 100,
            "bars": len(df),
        }
        print(f"  bars={len(df)} days={days:.1f} px {first.close}->{last.close}", flush=True)
        coin_stats = []
        for c in combos():
            st = run_combo(df, c)
            st["symbol"] = sym
            coin_stats.append(st)
            all_rows.append(st)
            print(
                f"  {c.name}: n={st['n']} WR={st['wr']:.0f}% RR={st['rr']:.3f} "
                f"edge={st['rr_edge']:+.3f} PnL={st['pnl']:+.1f}",
                flush=True,
            )
        per_coin[sym] = coin_stats

    # Build report
    lines = [
        f"# Combo multi-coin — wait + min_rr≥0.5 + width≥2% / TP≥1.5ATR",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Interval: {INTERVAL} · lookback yeu cau {LOOKBACK_DAYS}d · capital {CAPITAL:.0f} · {MARGIN_PCT*100:.2f}%×{LEVERAGE:.0f}x",
        f"- Coins (da tung Donchian BT): {', '.join(SYMBOLS)}",
        "- Combos: baseline; wait2/4 + RR≥0.5 + width≥2%; wait2/4 + RR≥0.5 + TP≥1.5ATR; wait2/4 + RR≥0.5 + (width OR TP)",
        "",
        "## 1. Tong hop nhanh (moi coin × combo tot nhat theo RR edge)",
        "",
        "| Symbol | Days | Px chg | Best combo | n | WR | RR | Edge | PnL | vs baseline PnL |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for sym in SYMBOLS:
        if sym not in per_coin:
            continue
        m = meta[sym]
        base = next(s for s in per_coin[sym] if s["name"] == "baseline")
        cands = [s for s in per_coin[sym] if s["name"] != "baseline" and s["n"] >= 20 and s["rr_edge"] == s["rr_edge"]]
        best = max(cands, key=lambda s: (s["rr_edge"], s["pnl"])) if cands else base
        lines.append(
            f"| {sym} | {m['days']:.0f} | {m['chg']:+.1f}% | `{best['name']}` | {best['n']} | {best['wr']:.0f}% | "
            f"**{best['rr']:.3f}** | **{best['rr_edge']:+.3f}** | **{best['pnl']:+.1f}** | "
            f"{best['pnl']-base['pnl']:+.1f} (base {base['pnl']:+.1f}) |"
        )

    lines += [
        "",
        "## 2. Bang day du moi coin",
        "",
    ]

    for sym in SYMBOLS:
        if sym not in per_coin:
            continue
        m = meta[sym]
        lines += [
            f"### {sym}",
            "",
            f"- Cua so: {m['from']} → {m['to']} ({m['days']:.1f}d, {m['bars']} bars)",
            f"- Gia: {m['px0']:.6g} → {m['px1']:.6g} ({m['chg']:+.2f}%)",
            "",
            "| Combo | n | WR% | RR | RR_BE | Edge | PF | Exp | PnL | % | AvgW | AvgL | MaxL | L/S |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for s in per_coin[sym]:
            edge = s["rr_edge"]
            edge_s = f"{edge:+.3f}" if edge == edge else "nan"
            lines.append(
                f"| `{s['name']}` | {s['n']} | {s['wr']:.1f} | **{s['rr']:.3f}** | {s['rr_be']:.3f} | **{edge_s}** | "
                f"{s['pf']:.2f} | {s['exp']:+.4f} | **{s['pnl']:+.1f}** | {s['pnl_pct']:+.1f}% | "
                f"{s['avg_w']:+.3f} | {s['avg_l']:+.3f} | {s['max_l']:+.2f} | "
                f"{s['long_n']}/{s['short_n']} ({s['long_pnl']:+.1f}/{s['short_pnl']:+.1f}) |"
            )
        lines.append("")
        # skip detail for recommended combos
        lines.append("Skip counts:")
        for s in per_coin[sym]:
            if s["name"] == "baseline":
                continue
            sk = ", ".join(f"{k}:{v}" for k, v in s["skip"].items() if v)
            lines.append(f"- `{s['name']}`: {sk or '(none)'}")
        lines.append("")

    # Cross-coin matrix for each combo
    lines += [
        "## 3. Ma tran theo combo (PnL / RR / Edge)",
        "",
    ]
    for c in combos():
        lines += [
            f"### `{c.name}` — {c.note}",
            "",
            "| Symbol | n | WR | RR | Edge | PnL | ΔPnL vs base |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for sym in SYMBOLS:
            if sym not in per_coin:
                continue
            s = next(x for x in per_coin[sym] if x["name"] == c.name)
            base = next(x for x in per_coin[sym] if x["name"] == "baseline")
            edge = s["rr_edge"]
            edge_s = f"{edge:+.3f}" if edge == edge else "nan"
            lines.append(
                f"| {sym} | {s['n']} | {s['wr']:.0f}% | {s['rr']:.3f} | {edge_s} | **{s['pnl']:+.1f}** | {s['pnl']-base['pnl']:+.1f} |"
            )
        # aggregate equal-weight sum of PnL
        pnls = [next(x for x in per_coin[sym] if x["name"] == c.name)["pnl"] for sym in SYMBOLS if sym in per_coin]
        bases = [next(x for x in per_coin[sym] if x["name"] == "baseline")["pnl"] for sym in SYMBOLS if sym in per_coin]
        lines += [
            f"| **SUM** | | | | | **{sum(pnls):+.1f}** | {sum(pnls)-sum(bases):+.1f} |",
            "",
        ]

    lines += [
        "## 4. Ket luan",
        "",
        "- So sanh RR/edge/PnL combo vs baseline tren tung coin (BTW co the <365d neu list muon).",
        "- Combo OR (width≥2% **hoac** TP≥1.5ATR) thuong giu nhieu lenh hon AND rieng le.",
        "- Neu RR tang nhung PnL giam manh → loc qua chat cho coin do.",
        "",
    ]

    out = ROOT / "docs" / f"backtest_MULTI_donchian_combo_wait_rr_width_tp_{INTERVAL}_{LOOKBACK_DAYS}d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}", flush=True)


if __name__ == "__main__":
    main()
