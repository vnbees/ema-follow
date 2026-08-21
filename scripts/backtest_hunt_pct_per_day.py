#!/usr/bin/env python3
"""Hunt higher %/day paths — not 'just add capital'.

Builds on what already worked (body_size_rr05, size∝RR, multi-coin, optional mild
risk-up, shared-wallet concurrency). Reports %/day, maxDD, wipe, PF.
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
SYMBOLS = ["LINKUSDT", "HYPEUSDT", "SUIUSDT", "DOGEUSDT", "SOLUSDT"]  # no BTW
INTERVAL = "15m"
LOOKBACK_DAYS = 365
DONCHIAN_PERIOD = 20
SLOPE_LOOKBACK = 5
PARALLEL_TOL = 0.015
CAPITAL = 1000.0
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
                req = urllib.request.Request(url, headers={"User-Agent": "pctday-hunt/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rows = json.loads(resp.read().decode())
                break
            except Exception:
                if attempt < 5:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                raise
        time.sleep(0.08)
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

    def slope(series: np.ndarray, i: int, ref: float) -> float:
        if i < SLOPE_LOOKBACK or ref <= 0:
            return 0.0
        return (series[i] - series[i - SLOPE_LOOKBACK]) / SLOPE_LOOKBACK / ref * 100.0

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        ref = closes[i]
        parallel[i] = abs(slope(upper, i, ref) - slope(lower, i, ref)) <= PARALLEL_TOL
    out["bands_parallel"] = parallel
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


@dataclass(frozen=True)
class Cfg:
    name: str
    note: str
    margin_pct: float = 0.005
    max_open: int = 5  # across all symbols (shared wallet)
    body_filter: bool = True
    min_pot_rr: float = 0.5
    size_by_rr: bool = True
    mae_cut_r: float = 0.0
    # pyramid: on fresh SL-band edge touch, add 2x last (per symbol)
    pyramid_2x: bool = False
    max_stack_per_sym: int = 1
    # only take top-K signals by pot_rr each bar
    top_k_per_bar: int = 99


def shared_backtest(dfs: dict[str, pd.DataFrame], cfg: Cfg) -> dict:
    # Align on intersection of timestamps (closed bars)
    ts_sets = [set(df["ts"].tolist()) for df in dfs.values()]
    common = sorted(set.intersection(*ts_sets)) if ts_sets else []
    if len(common) < 500:
        return {"name": cfg.name, "error": "no common ts", "pct_day": 0, "net": 0, "maxdd": 0}

    # index maps
    indexed = {}
    for sym, df in dfs.items():
        d = df.set_index("ts")
        indexed[sym] = d.loc[common]

    symbols = list(indexed.keys())
    n = len(common)
    cash = CAPITAL
    # open lots: list of dicts
    opens: list[dict] = []
    trades: list[dict] = []
    peak = CAPITAL
    maxdd = 0.0
    # per-symbol signal state
    state = {sym: {"trend": None, "waiting": False, "prev_sl": False} for sym in symbols}

    def equity(mark: dict[str, float]) -> float:
        eq = cash
        for t in opens:
            eq += t["margin"] + _pnl(t["side"], t["entry"], mark[t["sym"]], t["qty"])
        return eq

    def locked() -> float:
        return sum(t["margin"] for t in opens)

    def stack(sym: str) -> int:
        return sum(1 for t in opens if t["sym"] == sym)

    for i, ts in enumerate(common):
        bar = {sym: indexed[sym].iloc[i] for sym in symbols}
        mark = {sym: float(bar[sym]["close"]) for sym in symbols}

        # --- exits ---
        still: list[dict] = []
        for t in opens:
            sym = t["sym"]
            b = bar[sym]
            if np.isnan(b["dc_upper"]) or np.isnan(b["dc_lower"]):
                still.append(t)
                continue
            hi, lo = float(b["high"]), float(b["low"])
            up, dn = float(b["dc_upper"]), float(b["dc_lower"])
            side = t["side"]
            risk = abs(t["entry"] - t["sl0"]) or 1e-12
            mae = (t["entry"] - lo) / risk if side == "long" else (hi - t["entry"]) / risk
            exit_px = None
            reason = None
            if cfg.mae_cut_r > 0 and mae >= cfg.mae_cut_r:
                exit_px = t["entry"] - cfg.mae_cut_r * risk if side == "long" else t["entry"] + cfg.mae_cut_r * risk
                reason = "MAE_CUT"
            tp = up if side == "long" else dn
            if exit_px is None and ((side == "long" and hi >= tp) or (side == "short" and lo <= tp)):
                # close ALL lots on this symbol at TP (pyramid style close-all)
                exit_px, reason = tp, "TP_BAND"
            if exit_px is not None and reason == "TP_BAND" and cfg.pyramid_2x:
                # close all same-symbol same-side
                for t2 in list(opens):
                    if t2["sym"] == sym and t2["side"] == side:
                        pnl = _pnl(t2["side"], t2["entry"], exit_px, t2["qty"])
                        cash += t2["margin"] + pnl
                        trades.append({"pnl": pnl, "sym": sym, "reason": reason})
                opens = [t2 for t2 in opens if not (t2["sym"] == sym and t2["side"] == side)]
                continue
            if exit_px is not None:
                pnl = _pnl(side, t["entry"], exit_px, t["qty"])
                cash += t["margin"] + pnl
                trades.append({"pnl": pnl, "sym": sym, "reason": reason})
            else:
                still.append(t)
        opens = still

        # --- signals / entries ---
        candidates: list[dict] = []
        for sym in symbols:
            b = bar[sym]
            if np.isnan(b["dc_upper"]) or np.isnan(b["dc_lower"]) or np.isnan(b["atr"]):
                continue
            px, o, hi, lo = float(b["close"]), float(b["open"]), float(b["high"]), float(b["low"])
            up, dn, mid = float(b["dc_upper"]), float(b["dc_lower"]), float(b["dc_middle"])
            w = float(b["dc_width"])
            a = float(b["atr"])
            pe = bool(b["parallel_exit"])
            par = bool(b["bands_parallel"])
            st = state[sym]

            # pyramid add on SL edge while holding
            if cfg.pyramid_2x and stack(sym) > 0:
                lot0 = next(t for t in opens if t["sym"] == sym)
                side = lot0["side"]
                sl_touch = (side == "long" and lo <= dn) or (side == "short" and hi >= up)
                prev_sl = st["prev_sl"]
                st["prev_sl"] = sl_touch
                if sl_touch and not prev_sl and stack(sym) < cfg.max_stack_per_sym and len(opens) < cfg.max_open:
                    last = [t for t in opens if t["sym"] == sym][-1]
                    margin = last["margin"] * 2.0
                    if cash >= margin - 1e-12:
                        notional = margin * LEVERAGE
                        cash -= margin
                        opens.append(
                            {
                                "sym": sym,
                                "side": side,
                                "entry": px,
                                "qty": notional / px,
                                "margin": margin,
                                "sl0": dn if side == "long" else up,
                            }
                        )
            else:
                st["prev_sl"] = False

            if pe:
                st["trend"] = "up" if px > mid else "down"
                st["waiting"] = True

            if st["waiting"] and st["trend"] and stack(sym) == 0:
                counter = (st["trend"] == "up" and px < o) or (st["trend"] == "down" and px > o)
                if counter and not par and w > 1e-12:
                    side = "long" if st["trend"] == "up" else "short"
                    tp_near = up if side == "long" else dn
                    sl_opp = dn if side == "long" else up
                    pot = abs(tp_near - px) / max(abs(px - sl_opp), 1e-12)
                    body = abs(px - o) / a if a > 0 else 0.0
                    ok = True
                    if cfg.body_filter and not (0.3 <= body <= 1.2):
                        ok = False
                    if pot < cfg.min_pot_rr:
                        ok = False
                    if ok:
                        candidates.append(
                            {
                                "sym": sym,
                                "side": side,
                                "px": px,
                                "pot": pot,
                                "sl0": sl_opp,
                                "body": body,
                            }
                        )
                        # don't clear waiting until filled

        candidates.sort(key=lambda x: x["pot"], reverse=True)
        candidates = candidates[: cfg.top_k_per_bar]

        for cand in candidates:
            if len(opens) >= cfg.max_open:
                break
            if stack(cand["sym"]) >= (cfg.max_stack_per_sym if not cfg.pyramid_2x else 1):
                # initial entry only if flat on symbol (pyramid adds handled above)
                if stack(cand["sym"]) > 0:
                    continue
            sm = float(np.clip(0.5 + cand["pot"], 0.5, 2.0)) if cfg.size_by_rr else 1.0
            eq = max(cash + locked(), 0.0)
            notional = min(eq * cfg.margin_pct * LEVERAGE * sm, cash * LEVERAGE)
            if notional < 1e-6:
                continue
            margin = notional / LEVERAGE
            if cash < margin - 1e-12:
                continue
            cash -= margin
            opens.append(
                {
                    "sym": cand["sym"],
                    "side": cand["side"],
                    "entry": cand["px"],
                    "qty": notional / cand["px"],
                    "margin": margin,
                    "sl0": cand["sl0"],
                }
            )
            state[cand["sym"]]["waiting"] = False

        # clear waiting if entered or still waiting
        for sym in symbols:
            if stack(sym) > 0:
                state[sym]["waiting"] = False

        eq = equity(mark)
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak if peak > 0 else 0.0)

    # EOD flat
    last_mark = {sym: float(indexed[sym].iloc[-1]["close"]) for sym in symbols}
    for t in list(opens):
        pnl = _pnl(t["side"], t["entry"], last_mark[t["sym"]], t["qty"])
        cash += t["margin"] + pnl
        trades.append({"pnl": pnl, "sym": t["sym"], "reason": "EOD"})
    opens = []

    days = max((common[-1] - common[0]) / 86400000.0, 1e-9)
    net = cash - CAPITAL
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    return {
        "name": cfg.name,
        "note": cfg.note,
        "n": len(trades),
        "wr": (len(wins) / len(trades) * 100) if trades else 0.0,
        "net": net,
        "pct_tot": net / CAPITAL * 100,
        "pct_day": (net / CAPITAL * 100) / days,
        "pct_month": (net / CAPITAL * 100) / days * 30.4375,
        "pct_year": (net / CAPITAL * 100) / days * 365,
        "maxdd": maxdd * 100,
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "wipe": (gl / gw) if gw > 0 else float("inf"),
        "days": days,
        "end": cash,
        "trades_per_day": len(trades) / days,
    }


def configs() -> list[Cfg]:
    return [
        Cfg("A_base_sep_like", "ref: body_rr, 0.5%, max_open5 shared", margin_pct=0.005, max_open=5),
        Cfg("B_more_slots", "0.5% + max_open10 (nhieu coin song song)", margin_pct=0.005, max_open=10),
        Cfg("C_top2_quality", "0.5% max10 nhung chi top-2 pot_rr/bar", margin_pct=0.005, max_open=10, top_k_per_bar=2),
        Cfg("D_margin_1pct", "margin 1% + body_rr max10", margin_pct=0.01, max_open=10),
        Cfg("E_margin_1.5", "margin 1.5% + body_rr max8", margin_pct=0.015, max_open=8),
        Cfg("F_margin_2_mae1", "margin 2% + MAE cut 1R + max6", margin_pct=0.02, max_open=6, mae_cut_r=1.0),
        Cfg("G_pyramid2x", "0.5% + pyramid×2 stack≤3 + close-all TP", margin_pct=0.005, max_open=10, pyramid_2x=True, max_stack_per_sym=3),
        Cfg("H_pyr_1pct", "1% + pyramid×2 stack≤3", margin_pct=0.01, max_open=10, pyramid_2x=True, max_stack_per_sym=3),
        Cfg("I_pyr_1pct_mae15", "1% + pyramid×2 + MAE1.5R", margin_pct=0.01, max_open=8, pyramid_2x=True, max_stack_per_sym=3, mae_cut_r=1.5),
        Cfg("J_concentrated", "2% + top1/bar + max3 (tap trung)", margin_pct=0.02, max_open=3, top_k_per_bar=1),
        Cfg("K_bal_path", "1% + top3/bar + max6 + MAE1R (can bang)", margin_pct=0.01, max_open=6, top_k_per_bar=3, mae_cut_r=1.0),
        Cfg("L_push", "1.5% + top2 + max5 + pyr stack2", margin_pct=0.015, max_open=5, top_k_per_bar=2, pyramid_2x=True, max_stack_per_sym=2),
    ]


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
        if len(df) >= 500:
            dfs[sym] = df
            print(f"  bars={len(df)}", flush=True)

    rows = []
    for cfg in configs():
        print(f"run {cfg.name}...", flush=True)
        st = shared_backtest(dfs, cfg)
        rows.append(st)
        if "error" in st:
            print(f"  ERR {st['error']}", flush=True)
        else:
            print(
                f"  %/day={st['pct_day']:+.3f}% net={st['net']:+.1f} maxDD={st['maxdd']:.1f}% "
                f"PF={st['pf']:.2f} n={st['n']} t/d={st['trades_per_day']:.1f}",
                flush=True,
            )

    ranked = sorted(rows, key=lambda r: r.get("pct_day", -999), reverse=True)
    lines = [
        "# Hunt higher %/day — shared-wallet multi-coin (no BTW)",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Coins: {', '.join(dfs.keys())} · capital **{CAPITAL:.0f}$ chung** · {INTERVAL} · {LOOKBACK_DAYS}d",
        "- Muc tieu: tim path nang %/ngay (khong chi nap von). 1%/ngay la moc tham chieu, khong bat buoc dat.",
        "- Moi config: body_size_rr05-style filter (tru khi note khac) tren **1 vi shared**.",
        "",
        "| Rank | Config | %/ngày | %/tháng | %/năm | Net | MaxDD | PF | Wipe | n | lệnh/ngày | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, r in enumerate(ranked, 1):
        if "error" in r:
            continue
        lines.append(
            f"| {i} | `{r['name']}` | **{r['pct_day']:+.3f}%** | {r['pct_month']:+.2f}% | {r['pct_year']:+.1f}% | "
            f"**{r['net']:+.1f}** | {r['maxdd']:.1f}% | {r['pf']:.2f} | {r['wipe']:.2f} | {r['n']} | "
            f"{r['trades_per_day']:.1f} | {r['note']} |"
        )

    best = ranked[0]
    # best risk-adjusted: pct_day / max(maxdd,1)
    scored = [r for r in rows if "error" not in r and r["maxdd"] < 40]
    best_safe = max(scored, key=lambda r: r["pct_day"] / max(r["maxdd"], 1.0)) if scored else best
    near_1 = [r for r in scored if r["pct_day"] >= 0.5]

    lines += [
        "",
        "## Ket luan",
        "",
        f"- Best %/day: `{best['name']}` → **{best['pct_day']:+.3f}%/ngày** (maxDD {best['maxdd']:.1f}%, net {best['net']:+.1f})",
        f"- Best risk-adjusted (%/day / DD): `{best_safe['name']}` → {best_safe['pct_day']:+.3f}%/day / DD {best_safe['maxdd']:.1f}%",
        f"- Config ≥0.5%/ngày: {', '.join('`'+r['name']+'`' for r in near_1) if near_1 else '(khong co trong bo test)'}",
        "",
        "### Cach hieu",
        "",
        "1. **Shared wallet + nhieu slot** tang %/ngay bang concurrency, khong can nap von.",
        "2. **Margin 1–1.5%** thuong la sweet spot truoc khi DD no.",
        "3. **Pyramid 2x** co the phong %/ngay manh nhung DD/wipe tang — can MAE cut.",
        "4. **1%/ngày** neu dat duoc chi trong BT high-risk; live can giam ky vong xuong 0.2–0.4%/ngày sustainable.",
        "",
    ]

    out = ROOT / "docs" / "backtest_MULTI_hunt_pct_per_day_shared_15m_365d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}", flush=True)


if __name__ == "__main__":
    main()
