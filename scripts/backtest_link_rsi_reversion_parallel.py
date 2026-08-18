#!/usr/bin/env python3
"""RSI-anchor mean reversion: 3 zones, parallel long/short, every 0.5% leave enters.

- TP: quay lai vung gia luc RSI cham moc
- Sau 7 ngay: neu chua TP, dong khi gia ve entry (break-even)
- Toi da giu 30 ngay: dong theo close
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
LOOKBACK_DAYS = 365
RSI_PERIOD = 14
ZONE_PCT = 0.0025
MOVE_AWAY_PCT = 0.0050
MARGIN_PCT = 0.01
LEVERAGE = 10.0
FEE = 0.0004
CAPITAL = 1000.0
BE_BARS = 7 * 24 * 12  # 7 ngay * 12 nen 5m/gio
MAX_BARS = 30 * 24 * 12  # 30 ngay

spec = importlib.util.spec_from_file_location("rsi_rev", ROOT / "scripts" / "backtest_link_rsi_price_revisit.py")
rsi_rev = importlib.util.module_from_spec(spec)
sys.modules["rsi_rev"] = rsi_rev
spec.loader.exec_module(rsi_rev)


def _local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d %H:%M")


def _event_flags(df, mode: str):
    if mode == "rsi70":
        return df["evt_rsi_ge70"].to_numpy()
    if mode == "rsi30":
        return df["evt_rsi_le30"].to_numpy()
    if mode == "rsi50":
        return df["evt_rsi_50_zone"].to_numpy()
    return (
        df["evt_rsi_ge70"].to_numpy()
        | df["evt_rsi_le30"].to_numpy()
        | df["evt_rsi_50_zone"].to_numpy()
    )


def _pnl(side: str, entry: float, exit_px: float, qty: float) -> tuple[float, float]:
    if side == "long":
        gross = (exit_px - entry) * qty
    else:
        gross = (entry - exit_px) * qty
    fee = (entry + exit_px) * qty * FEE
    return gross - fee, fee


def run(df, mode: str) -> tuple[list[dict], dict]:
    flags = _event_flags(df, mode)
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    rsis = df["rsi"].to_numpy()
    tss = df["ts"].to_numpy()
    n = len(df)

    pending: list[dict] = []
    opens: list[dict] = []
    trades: list[dict] = []
    cash = CAPITAL
    nid = 1
    peak_open = 0
    peak_long = 0
    peak_short = 0
    cap_skips = 0
    max_notional = 0.0
    min_equity = CAPITAL
    max_equity = CAPITAL
    max_dd = 0.0
    peak_eq = CAPITAL

    def locked_margin() -> float:
        return sum(t["margin"] for t in opens)

    def mtm(px: float) -> float:
        total = 0.0
        for t in opens:
            if t["side"] == "long":
                gross = (px - t["entry"]) * t["qty"]
            else:
                gross = (t["entry"] - px) * t["qty"]
            total += gross - (t["entry"] + px) * t["qty"] * FEE
        return total

    def equity(px: float) -> float:
        return cash + locked_margin() + mtm(px)

    def mark(px: float) -> None:
        nonlocal peak_eq, max_dd, min_equity, max_equity, max_notional
        eq = equity(px)
        peak_eq = max(peak_eq, eq)
        max_dd = min(max_dd, eq - peak_eq)
        min_equity = min(min_equity, eq)
        max_equity = max(max_equity, eq)
        notion = sum(t["entry"] * t["qty"] for t in opens)
        max_notional = max(max_notional, notion)

    def close_trade(t: dict, exit_px: float, ts: int, reason: str) -> None:
        nonlocal cash
        pnl, fee = _pnl(t["side"], t["entry"], exit_px, t["qty"])
        cash += t["margin"] + pnl
        trades.append(
            {
                **t,
                "exit_ts": ts,
                "exit_px": exit_px,
                "reason": reason,
                "pnl": pnl,
                "fee": fee,
            }
        )

    for i in range(n):
        px = float(closes[i])
        hi = float(highs[i])
        lo = float(lows[i])
        ts = int(tss[i])

        still_open: list[dict] = []
        for t in opens:
            held = i - t["entry_idx"]
            side = t["side"]
            hit_tp = (side == "long" and hi >= t["tp"]) or (side == "short" and lo <= t["tp"])
            hit_be = False
            if held >= BE_BARS:
                hit_be = (side == "long" and lo <= t["entry"]) or (side == "short" and hi >= t["entry"])
            timed_out = held >= MAX_BARS

            if hit_tp:
                close_trade(t, t["tp"], ts, "TP")
            elif hit_be:
                close_trade(t, t["entry"], ts, "BE_AFTER_7D")
            elif timed_out:
                close_trade(t, px, ts, "TIMEOUT_30D")
            else:
                still_open.append(t)
        opens = still_open

        if flags[i]:
            pending.append(
                {
                    "anchor_ts": ts,
                    "anchor_idx": i,
                    "anchor_price": px,
                    "anchor_rsi": float(rsis[i]),
                }
            )

        still_pending: list[dict] = []
        for p in pending:
            if i <= p["anchor_idx"]:
                still_pending.append(p)
                continue
            anchor = p["anchor_price"]
            up = hi >= anchor * (1 + MOVE_AWAY_PCT)
            dn = lo <= anchor * (1 - MOVE_AWAY_PCT)
            side = None
            if up and dn:
                side = "short" if px >= anchor else "long"
            elif up:
                side = "short"
            elif dn:
                side = "long"
            if side is None:
                still_pending.append(p)
                continue

            eq = max(equity(px), 0.0)
            notional = eq * MARGIN_PCT
            notional = min(notional, cash * LEVERAGE)
            if notional < 1e-6:
                cap_skips += 1
                still_pending.append(p)
                continue
            margin = notional / LEVERAGE
            if cash < margin - 1e-12:
                cap_skips += 1
                still_pending.append(p)
                continue

            tp = anchor * (1 + ZONE_PCT) if side == "short" else anchor * (1 - ZONE_PCT)
            qty = notional / px
            cash -= margin
            opens.append(
                {
                    "id": nid,
                    "mode": mode,
                    "side": side,
                    "anchor_ts": p["anchor_ts"],
                    "anchor_price": anchor,
                    "anchor_rsi": p["anchor_rsi"],
                    "entry_idx": i,
                    "entry_ts": ts,
                    "entry": px,
                    "tp": tp,
                    "qty": qty,
                    "margin": margin,
                    "notional": notional,
                }
            )
            nid += 1
        pending = still_pending

        peak_open = max(peak_open, len(opens))
        n_long = sum(1 for t in opens if t["side"] == "long")
        n_short = len(opens) - n_long
        peak_long = max(peak_long, n_long)
        peak_short = max(peak_short, n_short)
        mark(px)

    last_px = float(closes[-1])
    last_ts = int(tss[-1])
    for t in list(opens):
        close_trade(t, last_px, last_ts, "EOD_OPEN")
    opens = []

    wins = [t for t in trades if t["pnl"] > 0]
    def sub(reason: str) -> list[dict]:
        return [t for t in trades if t["reason"] == reason]

    tp_t, be_t, to_t, eod = sub("TP"), sub("BE_AFTER_7D"), sub("TIMEOUT_30D"), sub("EOD_OPEN")
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]
    stats = {
        "mode": mode,
        "n": len(trades),
        "wr": (len(wins) / len(trades) * 100) if trades else 0.0,
        "tp_count": len(tp_t),
        "be_count": len(be_t),
        "timeout_count": len(to_t),
        "eod_count": len(eod),
        "pnl": cash - CAPITAL,
        "pnl_pct": (cash - CAPITAL) / CAPITAL * 100,
        "avg_pnl": (sum(t["pnl"] for t in trades) / len(trades)) if trades else 0.0,
        "avg_tp": (sum(t["pnl"] for t in tp_t) / len(tp_t)) if tp_t else 0.0,
        "avg_be": (sum(t["pnl"] for t in be_t) / len(be_t)) if be_t else 0.0,
        "avg_timeout": (sum(t["pnl"] for t in to_t) / len(to_t)) if to_t else 0.0,
        "worst_timeout": min((t["pnl"] for t in to_t), default=0.0),
        "pnl_tp": sum(t["pnl"] for t in tp_t),
        "pnl_be": sum(t["pnl"] for t in be_t),
        "pnl_timeout": sum(t["pnl"] for t in to_t),
        "long_n": len(longs),
        "short_n": len(shorts),
        "long_pnl": sum(t["pnl"] for t in longs),
        "short_pnl": sum(t["pnl"] for t in shorts),
        "peak_open": peak_open,
        "peak_long": peak_long,
        "peak_short": peak_short,
        "cap_skips": cap_skips,
        "max_dd": max_dd,
        "min_equity": min_equity,
        "max_notional": max_notional,
        "fee": sum(t["fee"] for t in trades),
        "end_equity": cash,
    }
    return trades, stats


def main() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    bar_ms = 5 * 60 * 1000
    last_closed = (now_ms // bar_ms) * bar_ms
    warmup_ms = (RSI_PERIOD + 5) * bar_ms
    fetch_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000 - warmup_ms

    print(f"Fetching LINKUSDT 5m from {_local(fetch_from)}...", flush=True)
    df = rsi_rev.smc_fetch.fetch_klines("5m", fetch_from, last_closed)
    df["rsi"] = rsi_rev.compute_rsi(df["close"], RSI_PERIOD)
    window_from = last_closed - LOOKBACK_DAYS * 24 * 3600 * 1000
    df = df[df["ts"] >= window_from].copy().reset_index(drop=True)
    df = rsi_rev.mark_events(df)

    first, last = df.iloc[0], df.iloc[-1]
    px_chg = (last.close / first.close - 1) * 100
    modes = [
        ("rsi70", "RSI >= 70"),
        ("rsi30", "RSI <= 30"),
        ("rsi50", "RSI 48-52"),
        ("combined", "Ca 3 vung"),
    ]

    lines = [
        "# RSI reversion song song + BE sau 7 ngay + max 30 ngay — LINK 5m 365d",
        "",
        f"- Sinh luc: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Cua so: **{LOOKBACK_DAYS} ngay** · Gia {first.close:.4f} -> {last.close:.4f} ({px_chg:+.2f}%)",
        f"- Entry: moi lan gia roi xa `{MOVE_AWAY_PCT*100:.2f}%` khoi gia luc RSI cham moc",
        f"- TP: quay lai vung anchor `{ZONE_PCT*100:.2f}%`",
        "- Sau **7 ngay** neu chua TP: dong khi gia ve **entry** (break-even, van tru phi)",
        "- Toi da giu **30 ngay**: dong theo close",
        "- Long va short **chay song song**, khong gioi han 1 lenh",
        f"- Size: {MARGIN_PCT*100:.1f}% equity / lenh · {LEVERAGE:.0f}x · phi {FEE*100:.2f}%/side",
        "",
        "## Tong hop",
        "",
        "| Strategy | Trades | WR | TP | BE 7d | Timeout 30d | PnL | % | Avg TP | Avg BE | Avg timeout | Peak open | Max DD | Phi |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    all_results: dict[str, tuple[list[dict], dict]] = {}
    for mode, label in modes:
        print(f"Running {mode}...", flush=True)
        trades, st = run(df, mode)
        all_results[mode] = (trades, st)
        print(
            f"  n={st['n']} WR={st['wr']:.0f}% TP={st['tp_count']} BE={st['be_count']} "
            f"TO={st['timeout_count']} pnl={st['pnl']:+.2f} peak={st['peak_open']} dd={st['max_dd']:+.2f}",
            flush=True,
        )
        lines.append(
            f"| {label} | {st['n']} | {st['wr']:.0f}% | {st['tp_count']} | {st['be_count']} | "
            f"{st['timeout_count']} | **{st['pnl']:+.2f}** | **{st['pnl_pct']:+.2f}%** | "
            f"{st['avg_tp']:+.3f} | {st['avg_be']:+.3f} | {st['avg_timeout']:+.3f} | "
            f"{st['peak_open']} | {st['max_dd']:+.2f} | {st['fee']:.1f} |"
        )

    lines += [
        "",
        "## Long vs Short",
        "",
        "| Strategy | Long n | Long PnL | Short n | Short PnL | Peak long | Peak short | Skip het von |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for mode, label in modes:
        st = all_results[mode][1]
        lines.append(
            f"| {label} | {st['long_n']} | {st['long_pnl']:+.2f} | {st['short_n']} | "
            f"{st['short_pnl']:+.2f} | {st['peak_long']} | {st['peak_short']} | {st['cap_skips']} |"
        )

    lines += [
        "",
        "## PnL theo ly do dong",
        "",
        "| Strategy | PnL TP | PnL BE 7d | PnL timeout 30d | Worst timeout |",
        "| --- | --- | --- | --- | --- |",
    ]
    for mode, label in modes:
        st = all_results[mode][1]
        lines.append(
            f"| {label} | {st['pnl_tp']:+.2f} | {st['pnl_be']:+.2f} | "
            f"{st['pnl_timeout']:+.2f} | {st['worst_timeout']:+.3f} |"
        )

    for mode, label in modes:
        trades, st = all_results[mode]
        lines += [
            "",
            f"## Mau lenh — {label}",
            "",
            "| # | Side | Anchor | Gia anchor | Vao | Entry | Ra | Exit | PnL | Reason |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        sample = trades[:20]
        for t in sample:
            lines.append(
                f"| {t['id']} | {t['side']} | {_local(t['anchor_ts'])} | {t['anchor_price']:.4f} | "
                f"{_local(t['entry_ts'])} | {t['entry']:.4f} | {_local(t['exit_ts'])} | "
                f"{t['exit_px']:.4f} | {t['pnl']:+.3f} | {t['reason']} |"
            )
        if len(trades) > 20:
            lines += ["", f"({len(trades) - 20} lenh khac khong hien thi)", ""]

    out = ROOT / "docs" / "backtest_LINK_rsi_reversion_parallel_365d.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
