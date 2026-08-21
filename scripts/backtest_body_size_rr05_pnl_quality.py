#!/usr/bin/env python3
"""body_size_rr05 detailed PnL quality report across previously backtested coins."""

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
MIN_BODY = 0.3
MAX_BODY = 1.2
MIN_POT_RR = 0.5


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
                req = urllib.request.Request(url, headers={"User-Agent": "body-size-rr05-report/1.0"})
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


def run(df: pd.DataFrame, *, use_filter: bool) -> tuple[list[dict], float]:
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

    trend = None
    waiting = False
    pos = None
    trades: list[dict] = []
    cash = CAPITAL
    nid = 1
    equity_curve: list[tuple[int, float]] = []

    for i in range(n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        px, hi, lo, o = float(closes[i]), float(highs[i]), float(lows[i]), float(opens[i])
        ts = int(tss[i])
        up, dn, mid = float(upper[i]), float(lower[i]), float(middle[i])
        w = float(width[i]) if not np.isnan(width[i]) else 0.0
        a = float(atr[i]) if not np.isnan(atr[i]) else 0.0

        if pos is not None:
            side = pos["side"]
            tp = up if side == "long" else dn
            if (side == "long" and hi >= tp) or (side == "short" and lo <= tp):
                pnl = _pnl(side, pos["entry"], tp, pos["qty"])
                cash += pos["margin"] + pnl
                trades.append(
                    {
                        "id": nid,
                        "side": side,
                        "entry_ts": pos["entry_ts"],
                        "exit_ts": ts,
                        "entry": pos["entry"],
                        "exit": tp,
                        "pnl": pnl,
                        "size_mult": pos["size_mult"],
                        "pot_rr": pos["pot_rr"],
                        "body_atr": pos["body_atr"],
                        "reason": "TP_BAND",
                    }
                )
                nid += 1
                pos = None

        if parallel_exit[i]:
            trend = "up" if px > mid else "down"
            waiting = True

        if pos is None and waiting and trend is not None:
            counter = (trend == "up" and px < o) or (trend == "down" and px > o)
            if counter and not parallel[i] and w > 1e-12:
                side = "long" if trend == "up" else "short"
                tp_near = up if side == "long" else dn
                sl_opp = dn if side == "long" else up
                dist_tp = abs(tp_near - px)
                dist_sl = abs(px - sl_opp)
                pot_rr = dist_tp / dist_sl if dist_sl > 1e-12 else 0.0
                body_atr = abs(px - o) / a if a > 0 else 0.0
                ok = True
                size_mult = 1.0
                if use_filter:
                    if body_atr < MIN_BODY or body_atr > MAX_BODY:
                        ok = False
                    if pot_rr < MIN_POT_RR:
                        ok = False
                    size_mult = float(np.clip(0.5 + pot_rr, 0.5, 2.0))
                if ok:
                    notional = min(max(cash, 0.0) * MARGIN_PCT * LEVERAGE * size_mult, cash * LEVERAGE)
                    if notional >= 1e-6:
                        margin = notional / LEVERAGE
                        if cash >= margin - 1e-12:
                            cash -= margin
                            pos = {
                                "side": side,
                                "entry": px,
                                "entry_ts": ts,
                                "qty": notional / px,
                                "margin": margin,
                                "size_mult": size_mult,
                                "pot_rr": pot_rr,
                                "body_atr": body_atr,
                            }
                            waiting = False
        if pos is not None:
            waiting = False

        # mark-to-market equity for curve (cash + margin + upnl)
        eq = cash
        if pos is not None:
            eq += pos["margin"] + _pnl(pos["side"], pos["entry"], px, pos["qty"])
        equity_curve.append((ts, eq))

    if pos is not None:
        px = float(closes[-1])
        ts = int(tss[-1])
        pnl = _pnl(pos["side"], pos["entry"], px, pos["qty"])
        cash += pos["margin"] + pnl
        trades.append(
            {
                "id": nid,
                "side": pos["side"],
                "entry_ts": pos["entry_ts"],
                "exit_ts": ts,
                "entry": pos["entry"],
                "exit": px,
                "pnl": pnl,
                "size_mult": pos["size_mult"],
                "pot_rr": pos["pot_rr"],
                "body_atr": pos["body_atr"],
                "reason": "EOD",
            }
        )

    return trades, cash


def summarize(trades: list[dict], cash: float, days: float, label: str) -> dict:
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    zeros = [t for t in trades if t["pnl"] == 0]
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in losses))
    net = cash - CAPITAL
    avg_w = gross_w / len(wins) if wins else 0.0
    avg_l = -gross_l / len(losses) if losses else 0.0
    rr = avg_w / abs(avg_l) if losses else float("inf")
    wr = len(wins) / len(trades) if trades else 0.0
    pf = gross_w / gross_l if gross_l > 0 else float("inf")
    # Can losses wipe wins?
    wipe_ratio = gross_l / gross_w if gross_w > 0 else float("inf")  # <1 means wins still dominate
    days = max(days, 1e-9)
    trades_per_day = len(trades) / days
    pnl_per_day = net / days
    pct_total = net / CAPITAL * 100
    pct_per_day = pct_total / days
    pct_per_month = pct_per_day * 30.4375  # avg month
    pct_per_year = pct_per_day * 365.0
    # compound-ish from equity path approximation: total return annualized
    ann_simple = pct_per_year
    return {
        "label": label,
        "n": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "zeros": len(zeros),
        "wr": wr * 100,
        "gross_w": gross_w,
        "gross_l": gross_l,
        "net": net,
        "wipe_ratio": wipe_ratio,
        "pf": pf,
        "rr": rr,
        "avg_w": avg_w,
        "avg_l": avg_l,
        "max_w": max((t["pnl"] for t in wins), default=0.0),
        "max_l": min((t["pnl"] for t in losses), default=0.0),
        "days": days,
        "trades_per_day": trades_per_day,
        "pnl_per_day": pnl_per_day,
        "pct_total": pct_total,
        "pct_day": pct_per_day,
        "pct_month": pct_per_month,
        "pct_year": ann_simple,
        "end_equity": cash,
        "expectancy": net / len(trades) if trades else 0.0,
    }


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last = (now_ms // BAR_MS) * BAR_MS
    wf = last - LOOKBACK_DAYS * 86400 * 1000
    ff = wf - (DONCHIAN_PERIOD + SLOPE_LOOKBACK + ATR_PERIOD + 5) * BAR_MS

    lines = [
        "# body_size_rr05 — lời vs lỗ, tần suất, % ngày/tháng/năm",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Rule: body ATR ∈ [{MIN_BODY},{MAX_BODY}] · pot RR ≥ {MIN_POT_RR} · size_mult = clip(0.5+pot_rr, 0.5, 2)",
        f"- Capital {CAPITAL:.0f} · base margin {MARGIN_PCT*100:.2f}%×{LEVERAGE:.0f}x · fee {FEE*100:.2f}%/side · MAX_OPEN=1",
        f"- Coins: {', '.join(SYMBOLS)}",
        "- **wipe_ratio** = gross_loss / gross_win ( &lt; 1 ⇒ tổng lời chưa bị lỗ nuốt hết )",
        "- % ngày/tháng/năm = linear từ total return / số ngày data (không compound phức tạp)",
        "",
        "## 1. Bang tong hop body_size_rr05",
        "",
        "| Symbol | Days | n | /ngày | WR | Wins | Losses | GrossW | GrossL | Wipe | PF | RR | Net | %tot | %/ngày | %/tháng | %/năm |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    rows_f = []
    rows_b = []
    for sym in SYMBOLS:
        print(f"=== {sym} ===", flush=True)
        raw = fetch_klines(sym, ff, last)
        if raw.empty:
            continue
        df = prepare(raw)
        df = df[df["ts"] >= wf].copy().reset_index(drop=True)
        if len(df) < 100:
            continue
        first, last_row = df.iloc[0], df.iloc[-1]
        days = (int(last_row.ts) - int(first.ts)) / 86400000.0
        print(f"  days={days:.1f}", flush=True)

        tr_f, cash_f = run(df, use_filter=True)
        tr_b, cash_b = run(df, use_filter=False)
        sf = summarize(tr_f, cash_f, days, sym)
        sb = summarize(tr_b, cash_b, days, sym)
        sf["from"] = _local(int(first.ts))
        sf["to"] = _local(int(last_row.ts))
        sf["px_chg"] = (float(last_row.close) / float(first.close) - 1) * 100
        rows_f.append(sf)
        rows_b.append(sb)

        wipe_txt = f"{sf['wipe_ratio']:.2f}" if sf["wipe_ratio"] != float("inf") else "inf"
        lines.append(
            f"| {sym} | {sf['days']:.0f} | {sf['n']} | **{sf['trades_per_day']:.2f}** | {sf['wr']:.0f}% | "
            f"{sf['wins']} | {sf['losses']} | {sf['gross_w']:+.1f} | {sf['gross_l']:+.1f} | **{wipe_txt}** | "
            f"{sf['pf']:.2f} | {sf['rr']:.3f} | **{sf['net']:+.1f}** | {sf['pct_total']:+.1f}% | "
            f"**{sf['pct_day']:+.3f}%** | **{sf['pct_month']:+.2f}%** | **{sf['pct_year']:+.1f}%** |"
        )
        print(
            f"  filtered n={sf['n']} wipe={sf['wipe_ratio']:.2f} PF={sf['pf']:.2f} "
            f"net={sf['net']:+.1f} %/d={sf['pct_day']:+.3f}% %/y={sf['pct_year']:+.1f}%",
            flush=True,
        )

    # aggregates
    def agg(rows: list[dict], name: str) -> dict:
        days = max(np.mean([r["days"] for r in rows]), 1e-9)
        # for multi-coin equal capital each: sum nets, avg rates
        net = sum(r["net"] for r in rows)
        gw = sum(r["gross_w"] for r in rows)
        gl = sum(r["gross_l"] for r in rows)
        n = sum(r["n"] for r in rows)
        wins = sum(r["wins"] for r in rows)
        losses = sum(r["losses"] for r in rows)
        # equal-weight portfolio of 6×1000 = 6000
        cap = CAPITAL * len(rows)
        pct_tot = net / cap * 100
        # use mean days for rate (BTW shorter — also report sum of per-coin daily)
        pct_day_ew = np.mean([r["pct_day"] for r in rows])  # equal-weight %/day per coin book
        return {
            "name": name,
            "n": n,
            "wins": wins,
            "losses": losses,
            "wr": wins / n * 100 if n else 0,
            "gross_w": gw,
            "gross_l": gl,
            "wipe": gl / gw if gw else float("inf"),
            "pf": gw / gl if gl else float("inf"),
            "net": net,
            "cap": cap,
            "pct_tot": pct_tot,
            "pct_day_ew": pct_day_ew,
            "pct_month_ew": pct_day_ew * 30.4375,
            "pct_year_ew": pct_day_ew * 365,
            "trades_per_day_sum": sum(r["trades_per_day"] for r in rows),
            "avg_trades_per_day_per_coin": np.mean([r["trades_per_day"] for r in rows]),
        }

    af = agg(rows_f, "body_size_rr05")
    ab = agg(rows_b, "baseline")

    lines += [
        "",
        "## 2. Lời có bị lỗ nuốt hết không?",
        "",
        "| | Gross win | Gross loss | Wipe (L/W) | Profit factor | Net |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| **body_size_rr05 (SUM 6 coin)** | {af['gross_w']:+.1f} | {af['gross_l']:+.1f} | **{af['wipe']:.2f}** | **{af['pf']:.2f}** | **{af['net']:+.1f}** |",
        f"| baseline (SUM 6 coin) | {ab['gross_w']:+.1f} | {ab['gross_l']:+.1f} | {ab['wipe']:.2f} | {ab['pf']:.2f} | {ab['net']:+.1f} |",
        "",
        f"- Wipe &lt; 1 ⇒ **không** bị lỗ nuốt hết lời. body_size_rr05 wipe=**{af['wipe']:.2f}** "
        f"(lỗ chỉ bằng ~{af['wipe']*100:.0f}% tổng lời).",
        f"- PF={af['pf']:.2f} ⇒ mỗi 1 USDT lỗ, kiếm được ~{af['pf']:.2f} USDT lời.",
        "",
        "## 3. Tan suat & profit % (equal-weight moi coin 1000$)",
        "",
        "| Metric | body_size_rr05 | baseline |",
        "| --- | --- | --- |",
        f"| Trades tong | {af['n']} | {ab['n']} |",
        f"| WR | {af['wr']:.1f}% | {ab['wr']:.1f}% |",
        f"| Lenh/ngay / coin (TB) | **{af['avg_trades_per_day_per_coin']:.2f}** | {ab['avg_trades_per_day_per_coin']:.2f} |",
        f"| Lenh/ngay neu chay ca 6 coin | **{af['trades_per_day_sum']:.1f}** | {ab['trades_per_day_sum']:.1f} |",
        f"| Net SUM | **{af['net']:+.1f}** / {af['cap']:.0f} | {ab['net']:+.1f} / {ab['cap']:.0f} |",
        f"| % tong (tren tong capital) | {af['pct_tot']:+.2f}% | {ab['pct_tot']:+.2f}% |",
        f"| %/ngay (EW TB cac coin) | **{af['pct_day_ew']:+.3f}%** | {ab['pct_day_ew']:+.3f}% |",
        f"| %/thang (×30.44) | **{af['pct_month_ew']:+.2f}%** | {ab['pct_month_ew']:+.2f}% |",
        f"| %/nam (×365) | **{af['pct_year_ew']:+.1f}%** | {ab['pct_year_ew']:+.1f}% |",
        "",
        "## 4. So sanh tung coin vs baseline",
        "",
        "| Symbol | Filter n | Base n | Filter net | Base net | Filter wipe | Base wipe | Filter %/năm | Base %/năm |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for sf, sb in zip(rows_f, rows_b):
        lines.append(
            f"| {sf['label']} | {sf['n']} | {sb['n']} | {sf['net']:+.1f} | {sb['net']:+.1f} | "
            f"{sf['wipe_ratio']:.2f} | {sb['wipe_ratio']:.2f} | {sf['pct_year']:+.1f}% | {sb['pct_year']:+.1f}% |"
        )

    lines += [
        "",
        "## 5. Chi tiet tung coin (body_size_rr05)",
        "",
    ]
    for sf in rows_f:
        lines += [
            f"### {sf['label']}",
            "",
            f"- Cua so: {sf['from']} → {sf['to']} ({sf['days']:.1f}d) · px {sf['px_chg']:+.1f}%",
            f"- Lenh: **{sf['n']}** (~**{sf['trades_per_day']:.2f}**/ngày) · WR {sf['wr']:.1f}% · "
            f"W/L = {sf['wins']}/{sf['losses']}",
            f"- Gross win **{sf['gross_w']:+.2f}** · Gross loss **{sf['gross_l']:+.2f}** · "
            f"Wipe **{sf['wipe_ratio']:.2f}** · PF **{sf['pf']:.2f}**",
            f"- Avg win {sf['avg_w']:+.3f} · Avg loss {sf['avg_l']:+.3f} · RR {sf['rr']:.3f} · "
            f"MaxW {sf['max_w']:+.2f} · MaxL {sf['max_l']:+.2f}",
            f"- Net **{sf['net']:+.2f}** ({sf['pct_total']:+.2f}% / {CAPITAL:.0f}$) · "
            f"Expectancy/lenh {sf['expectancy']:+.4f}",
            f"- **%/ngày {sf['pct_day']:+.3f}%** · **%/tháng {sf['pct_month']:+.2f}%** · "
            f"**%/năm {sf['pct_year']:+.1f}%** (linear)",
            "",
        ]

    lines += [
        "## 6. Doc ket qua",
        "",
        "- Neu wipe_ratio ≈ 0.55–0.70 ⇒ lỗ bằng hơn nửa tổng lời, van con du loi rong.",
        "- %/nam linear ≠ compound; live multi-coin dong thoi can chia capital / correl.",
        "- BTW window ngan (~2–3 thang) → %/nam extrapolate de lac quan.",
        "",
    ]

    out = ROOT / "docs" / "backtest_MULTI_body_size_rr05_pnl_quality_15m_365d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}", flush=True)
    print(
        f"SUM wipe={af['wipe']:.2f} PF={af['pf']:.2f} net={af['net']:+.1f} "
        f"trades/day/coin={af['avg_trades_per_day_per_coin']:.2f} %/y_ew={af['pct_year_ew']:+.1f}%",
        flush=True,
    )


if __name__ == "__main__":
    main()
