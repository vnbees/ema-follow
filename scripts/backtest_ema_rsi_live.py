#!/usr/bin/env python3
"""Backtest logic bot EMA-RSI live trên LINKUSDT."""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ema_rsi.config import EMA_PERIOD, LEVERAGE, MARGIN_MIN_USDT, MARGIN_PCT, RR  # noqa: E402
from src.ema_rsi.signals import compute_ema_series, ema_cross_dir, evaluate_last_bar  # noqa: E402
from src.exchange.types import Candle  # noqa: E402
from src.rsi import compute_rsi_series  # noqa: E402

SYMBOL = "LINKUSDT"
FEE = 0.0004
BAR_MS = 5 * 60 * 1000
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CAPITAL = 1000.0
WINDOWS = (7, 90, 180, 365)
LABELS = {7: "7 ngày", 90: "3 tháng", 180: "6 tháng", 365: "1 năm"}


@dataclass
class Pos:
    side: str
    entry: float
    sl: float
    tp: float
    size: float
    margin: float
    opened_ts: int
    entry_fee: float
    r: float


@dataclass
class Closed:
    side: str
    entry: float
    exit: float
    sl: float
    tp: float
    size: float
    reason: str
    pnl: float
    fee: float
    opened_ts: int
    closed_ts: int
    r: float


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def fetch_klines(start_ms: int, end_ms: int) -> list[Candle]:
    out: list[Candle] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": "5m",
            "startTime": str(cursor),
            "endTime": str(end_ms),
            "limit": "1500",
        }
        url = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "ema-rsi-backtest/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            rows = json.loads(resp.read().decode())
        if not rows:
            break
        for row in rows:
            ts = int(row[0])
            if start_ms <= ts < end_ms:
                out.append(
                    Candle(
                        timestamp=ts,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                    )
                )
        last_ts = int(rows[-1][0])
        nxt = last_ts + BAR_MS
        if nxt <= cursor:
            break
        cursor = nxt
    return out


def _margin_for(equity: float) -> float:
    return max(MARGIN_MIN_USDT, equity * MARGIN_PCT / 100.0)


def _mtm(pos: Pos, px: float) -> float:
    if pos.side == "long":
        return (px - pos.entry) * pos.size
    return (pos.entry - px) * pos.size


def _exit_bar(pos: Pos, bar: Candle) -> tuple[float, str] | None:
    if pos.side == "long":
        if bar.low <= pos.sl:
            return pos.sl, "SL"
        if bar.high >= pos.tp:
            return pos.tp, "TP"
    else:
        if bar.high >= pos.sl:
            return pos.sl, "SL"
        if bar.low <= pos.tp:
            return pos.tp, "TP"
    return None


def _close(pos: Pos, exit_px: float, ts: int, reason: str) -> Closed:
    exit_fee = (pos.entry + exit_px) * pos.size * FEE
    net = _mtm(pos, exit_px) - pos.entry_fee - exit_fee
    return Closed(
        side=pos.side,
        entry=pos.entry,
        exit=exit_px,
        sl=pos.sl,
        tp=pos.tp,
        size=pos.size,
        reason=reason,
        pnl=net,
        fee=pos.entry_fee + exit_fee,
        opened_ts=pos.opened_ts,
        closed_ts=ts,
        r=pos.r,
    )


def run_backtest(
    candles: list[Candle], window_from: int, window_to: int, *, rr: float = 1.0
) -> tuple[list[Closed], dict]:
    rsi = compute_rsi_series(candles)
    ema = compute_ema_series([c.close for c in candles], EMA_PERIOD)
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}

    cash = CAPITAL
    pos: Pos | None = None
    closed: list[Closed] = []
    stats = {
        "signals": 0,
        "skipped_invalid": 0,
        "opened": 0,
        "max_dd": 0.0,
        "min_equity": CAPITAL,
        "max_equity": CAPITAL,
        "liquidated": False,
    }
    peak = CAPITAL

    bars = [c for c in candles if window_from <= c.timestamp < window_to]
    for bar in bars:
        i = by_ts[bar.timestamp]
        if pos is not None:
            eq = cash + pos.margin + _mtm(pos, bar.close)
        else:
            eq = cash
        peak = max(peak, eq)
        stats["max_dd"] = min(stats["max_dd"], eq - peak)
        stats["min_equity"] = min(stats["min_equity"], eq)
        stats["max_equity"] = max(stats["max_equity"], eq)
        if eq <= 0 and pos is not None:
            c = _close(pos, bar.close, bar.timestamp, "LIQUIDATED")
            closed.append(c)
            cash += pos.margin + c.pnl
            pos = None
            stats["liquidated"] = True
            break

        if pos is not None:
            hit = _exit_bar(pos, bar)
            if hit is not None:
                exit_px, reason = hit
                c = _close(pos, exit_px, bar.timestamp, reason)
                closed.append(c)
                cash += pos.margin + c.pnl
                pos = None

        if pos is not None or i < EMA_PERIOD + 1:
            continue
        prev_ema, curr_ema = ema[i - 1], ema[i]
        if prev_ema is None or curr_ema is None:
            continue
        if ema_cross_dir(candles[i - 1].close, prev_ema, bar.close, curr_ema) is None:
            continue

        sig = evaluate_last_bar(candles[: i + 1], rsi[: i + 1], ema[: i + 1], rr=rr)
        if sig is None:
            continue
        stats["signals"] += 1
        if sig.skip_reason:
            stats["skipped_invalid"] += 1
            continue
        if sig.signal_ts != bar.timestamp:
            continue

        eq = cash
        margin = _margin_for(eq)
        notional = margin * LEVERAGE
        if cash < margin - 1e-9:
            continue
        size = notional / sig.entry
        entry_fee = 2 * sig.entry * size * FEE
        cash -= margin
        pos = Pos(
            side=sig.side,
            entry=sig.entry,
            sl=sig.sl,
            tp=sig.tp,
            size=size,
            margin=margin,
            opened_ts=bar.timestamp,
            entry_fee=entry_fee,
            r=sig.r,
        )
        stats["opened"] += 1

    if pos is not None and bars:
        last = bars[-1]
        c = _close(pos, last.close, last.timestamp, "EOD_OPEN")
        closed.append(c)
        cash += pos.margin + c.pnl

    stats["end_equity"] = cash
    stats["end_pnl"] = cash - CAPITAL
    return closed, stats


def _summ(closed: list[Closed]) -> dict:
    if not closed:
        return {
            "n": 0,
            "wins": 0,
            "wr": 0.0,
            "pnl": 0.0,
            "fee": 0.0,
            "tp": 0,
            "sl": 0,
            "eod": 0,
            "long": 0,
            "short": 0,
            "avg_r_pct": 0.0,
        }
    wins = [c for c in closed if c.pnl > 0]
    rs = [c.r / c.entry * 100 for c in closed if c.entry]
    return {
        "n": len(closed),
        "wins": len(wins),
        "wr": len(wins) / len(closed) * 100,
        "pnl": sum(c.pnl for c in closed),
        "fee": sum(c.fee for c in closed),
        "tp": sum(1 for c in closed if c.reason == "TP"),
        "sl": sum(1 for c in closed if c.reason == "SL"),
        "eod": sum(1 for c in closed if c.reason == "EOD_OPEN"),
        "long": sum(1 for c in closed if c.side == "long"),
        "short": sum(1 for c in closed if c.side == "short"),
        "avg_r_pct": sum(rs) / len(rs) if rs else 0.0,
    }


def _trade_table(closed: list[Closed]) -> list[str]:
    lines = [
        "| # | Side | Vào | Ra | Entry | SL | TP | Exit | PnL | Reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, c in enumerate(closed, 1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    c.side,
                    _local(c.opened_ts),
                    _local(c.closed_ts + BAR_MS),
                    f"{c.entry:.4f}",
                    f"{c.sl:.4f}",
                    f"{c.tp:.4f}",
                    f"{c.exit:.4f}",
                    f"{c.pnl:+.4f}",
                    c.reason,
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    last_closed = (now_ms // BAR_MS) * BAR_MS
    warmup_ms = (EMA_PERIOD + 50) * BAR_MS
    fetch_from = last_closed - max(WINDOWS) * 86400 * 1000 - warmup_ms
    print(f"fetch {SYMBOL} 5m from {_local(fetch_from)} …", flush=True)
    candles = fetch_klines(fetch_from, last_closed)
    print(f"bars={len(candles)}", flush=True)

    path = ROOT / "docs" / "backtest_EMA_RSI_LINK_rr1.md"
    lines = [
        "# Backtest EMA-RSI LINK — RR 1:1 vs 1:2",
        "",
        f"- Sinh lúc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Symbol: **{SYMBOL}** 5m · vốn **{CAPITAL:.0f}** · {LEVERAGE}x · margin {MARGIN_PCT}%",
        "- Cùng tín hiệu EMA200 + RSI zone. Chỉ đổi TP: **RR 1:1** vs RR 1:2 (bot live).",
        "",
        "## So sánh vốn cuối",
        "",
        "| Cửa sổ | Giá | RR 1:2 | **RR 1:1** | Δ | WR 1:2 | WR 1:1 | TP 1:1 | SL 1:1 | DD 1:1 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    details: dict[int, tuple[list[Closed], dict, dict]] = {}
    for days in WINDOWS:
        wfrom = last_closed - days * 86400 * 1000
        wbars = [c for c in candles if wfrom <= c.timestamp < last_closed]
        px = (wbars[-1].close / wbars[0].close - 1) * 100
        c2, st2 = run_backtest(candles, wfrom, last_closed, rr=2.0)
        c1, st1 = run_backtest(candles, wfrom, last_closed, rr=1.0)
        s2, s1 = _summ(c2), _summ(c1)
        details[days] = (c1, st1, s1)
        p2, p1 = st2["end_pnl"], st1["end_pnl"]
        lines.append(
            f"| {LABELS[days]} ({_local(wfrom)[:10]}→{_local(last_closed)[:10]}) | "
            f"{px:+.1f}% | {st2['end_equity']:.2f} ({p2:+.1f}) | "
            f"**{st1['end_equity']:.2f} ({p1:+.1f})** | {p1 - p2:+.1f} | "
            f"{s2['wr']:.0f}% | {s1['wr']:.0f}% | {s1['tp']} | {s1['sl']} | {st1['max_dd']:+.2f} |"
        )
        print(
            f"{days}d RR2={st2['end_equity']:.2f} ({p2:+.1f}) wr={s2['wr']:.0f}% tp={s2['tp']}/{s2['sl']} | "
            f"RR1={st1['end_equity']:.2f} ({p1:+.1f}) wr={s1['wr']:.0f}% tp={s1['tp']}/{s1['sl']} "
            f"n={s1['n']} dd={st1['max_dd']:+.2f}",
            flush=True,
        )

    lines += [
        "",
        "## Chi tiết RR 1:1",
        "",
        "| Cửa sổ | Vốn cuối | PnL % | Lệnh | Long/Short | Avg R | Phí | EOD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for days in WINDOWS:
        closed, st, s = details[days]
        lines.append(
            f"| {LABELS[days]} | **{st['end_equity']:.2f}** | {st['end_pnl']/CAPITAL*100:+.1f}% | "
            f"{s['n']} | {s['long']}/{s['short']} | {s['avg_r_pct']:.1f}% | {s['fee']:.2f} | {s['eod']} |"
        )

    for days in WINDOWS:
        closed, _st, s = details[days]
        lines += [f"## {LABELS[days]} — từng lệnh (RR 1:1)", ""]
        if not closed:
            lines += ["Không có lệnh.", ""]
            continue
        if s["n"] > 80:
            lines += [f"({s['n']} lệnh — bỏ bảng chi tiết.)", ""]
            continue
        lines += _trade_table(closed)

    lines += [
        "## Ghi chú",
        "",
        "- RR 1:1: TP = entry ± 1R (cùng khoảng với SL). Cần WR > ~50% (+phí) mới lãi.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
